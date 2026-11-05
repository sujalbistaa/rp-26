"""RQ jobs: bulk enrollment, probe purge and re-embedding."""

import csv
import io
import zipfile
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import numpy as np
from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, images
from app.audit import Action
from app.config import Settings, get_settings
from app.db.models import BatchStatus, EnrollBatch, ImageSource, Person, PersonImage, Search, User
from app.db.session import new_session
from app.enrollment import enroll_image, next_record_id
from app.ml.align import align
from app.ml.embed import embed_crop
from app.pipeline import ML, get_ml
from app.schemas import ImageReport, RecordPatch
from app.storage import Storage, get_storage

REQUIRED_COLUMNS = {"group", "full_name", "image_filename"}
OPTIONAL_COLUMNS = {"gender", "birth_year", "address", "phone", "id_number", "source"}


class BadBatch(ValueError):
    pass


def queue() -> Queue:
    return Queue("rkl", connection=Redis.from_url(get_settings().redis_url))


def parse_manifest(data: bytes) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        columns = set(reader.fieldnames or [])
        rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as e:
        raise BadBatch("That CSV isn't readable as UTF-8.") from e
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise BadBatch(f"CSV is missing columns: {', '.join(sorted(missing))}.")
    if not rows:
        raise BadBatch("That CSV has no rows.")
    return rows


def check_archive(data: bytes, settings: Settings) -> None:
    """Reject zip bombs before they reach a worker. Members are read by name, never extracted."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except zipfile.BadZipFile as e:
        raise BadBatch("That zip can't be read.") from e
    if len(infos) > settings.bulk_max_files:
        raise BadBatch(f"That zip has over {settings.bulk_max_files} files.")
    if any(i.file_size > settings.max_upload_bytes for i in infos):
        raise BadBatch("A file in that zip is over 15 MB.")


def enqueue_bulk(batch_id: UUID) -> None:
    queue().enqueue(process_bulk, str(batch_id), job_timeout="6h")


def _source(value: str | None, default: ImageSource) -> ImageSource:
    try:
        return ImageSource((value or "").strip().upper())
    except ValueError:
        return default


def run_bulk(
    db: Session, batch: EnrollBatch, ml: ML, storage: Storage, settings: Settings
) -> None:
    user = db.get(User, batch.created_by)
    rows = parse_manifest(storage.get(batch.csv_key))
    report: list[dict] = []
    people: dict[str, Person] = {}

    with zipfile.ZipFile(io.BytesIO(storage.get(batch.zip_key))) as archive:
        names = set(archive.namelist())
        for row in rows:
            filename = row["image_filename"].strip()
            group = row["group"].strip()
            if filename not in names:
                entry = ImageReport(
                    filename=filename, status="REJECTED", reason="Not in the zip."
                )
            else:
                person = people.get(group)
                if person is None:
                    fields = RecordPatch(
                        full_name=row["full_name"].strip(),
                        gender=(row.get("gender") or "").strip() or None,
                        birth_year=int(row["birth_year"]) if row.get("birth_year") else None,
                        address=(row.get("address") or "").strip() or None,
                        phone=(row.get("phone") or "").strip() or None,
                        id_number=(row.get("id_number") or "").strip() or None,
                    )
                    person = Person(
                        id=uuid4(),
                        record_id=next_record_id(db, datetime.now(UTC).year),
                        created_by=user.id,
                        **fields.model_dump(),
                    )
                    db.add(person)
                    db.flush()
                    people[group] = person
                entry = enroll_image(
                    db,
                    data=archive.read(filename),
                    filename=filename,
                    person=person,
                    source=_source(row.get("source"), batch.default_source),
                    user=user,
                    ml=ml,
                    storage=storage,
                    settings=settings,
                )
            report.append(entry.model_dump())
            batch.processed = len(report)
            batch.report = list(report)
            db.commit()

    for person in people.values():
        if not person.images:
            db.delete(person)
    batch.status = BatchStatus.DONE
    batch.finished_at = datetime.now(UTC)
    audit.write(
        db,
        Action.ENROLL,
        actor=user,
        target_type="batch",
        target_id=str(batch.id),
        results_returned=sum(1 for r in report if r["status"] == "ENROLLED"),
        detail={"rows": len(report)},
    )
    db.commit()


def process_bulk(batch_id: str) -> None:
    with new_session() as db:
        batch = db.get(EnrollBatch, UUID(batch_id))
        batch.status = BatchStatus.RUNNING
        db.commit()
        try:
            run_bulk(db, batch, get_ml(), get_storage(), get_settings())
        except Exception as e:
            db.rollback()
            batch = db.get(EnrollBatch, UUID(batch_id))
            batch.status = BatchStatus.FAILED
            batch.error = str(e)[:500]
            batch.finished_at = datetime.now(UTC)
            db.commit()
            raise


def purge_expired_probes(db: Session, storage: Storage, settings: Settings) -> int:
    """Delete probe images past retention unless they're attached to a case."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.probe_retention_days)
    expired = db.scalars(
        select(Search).where(
            Search.created_at < cutoff,
            Search.purged_at.is_(None),
            Search.attached_to_case.is_(False),
        )
    ).all()
    for search in expired:
        storage.delete(search.probe_key)
        search.purged_at = datetime.now(UTC)
        audit.write(
            db,
            Action.PROBE_PURGE,
            case_number=search.case_number,
            probe_hash=search.probe_sha256,
            target_type="search",
            target_id=str(search.id),
        )
        db.commit()
    return len(expired)


def purge_probes() -> int:
    with new_session() as db:
        return purge_expired_probes(db, get_storage(), get_settings())


def reembed_gallery(
    db: Session, ml: ML, storage: Storage, settings: Settings, column: str, batch_size: int = 64
) -> int:
    """Fill `column` from the stored originals, reusing the stored landmarks."""
    done = 0
    target = getattr(PersonImage, column)
    while True:
        rows = db.scalars(select(PersonImage).where(target.is_(None)).limit(batch_size)).all()
        if not rows:
            return done
        for row in rows:
            bgr = images.decode(storage.get(row.storage_key)).bgr
            landmarks = np.array(row.landmarks, dtype=np.float32).reshape(5, 2)
            setattr(row, column, embed_crop(ml.embedder, align(bgr, landmarks)))
            row.embedder_version = ml.embedder.version
            done += 1
        db.commit()
