"""Enroll one image into a record. Used by single and bulk enrollment and the seed script."""

from uuid import uuid4

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import images
from app.config import Settings
from app.db.models import Calibration, ImageSource, Person, PersonImage, User
from app.ml.calibrate import Curve
from app.pipeline import ML, analyse, embed
from app.schemas import DuplicateHit, ImageReport
from app.storage import Storage


def next_record_id(db: Session, year: int) -> str:
    # The advisory lock is released at commit, so two enrollments can't take the same number.
    db.execute(select(func.pg_advisory_xact_lock(year)))
    prefix = f"RKL-{year}-"
    last = db.scalar(
        select(func.max(Person.record_id)).where(Person.record_id.like(f"{prefix}%"))
    )
    number = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{number:06d}"


def _near_duplicates(
    db: Session, vector: np.ndarray, person_id, settings: Settings
) -> list[DuplicateHit]:
    """The same face already enrolled under a different record."""
    row = db.scalar(select(Calibration).where(Calibration.active))
    if row is None or row.embedder_version != settings.embedder_version:
        return []
    curve = Curve(row)
    column = getattr(PersonImage, settings.embedding_column)
    distance = column.cosine_distance(vector)
    hits = db.execute(
        select(Person.record_id, distance.label("distance"))
        .join(Person, PersonImage.person_id == Person.id)
        .where(column.is_not(None), PersonImage.person_id != person_id)
        .order_by(distance)
        .limit(10)
    )
    seen: dict[str, float] = {}
    for record_id, d in hits:
        cosine = 1 - float(d)
        if cosine >= curve.strong and record_id not in seen:
            seen[record_id] = curve.confidence(cosine)
    return [DuplicateHit(record_id=k, confidence=v) for k, v in seen.items()]


def enroll_image(
    db: Session,
    *,
    data: bytes,
    filename: str,
    person: Person,
    source: ImageSource,
    user: User | None,
    ml: ML,
    storage: Storage,
    settings: Settings,
) -> ImageReport:
    report = ImageReport(filename=filename, status="REJECTED", record_id=person.record_id)
    try:
        decoded = images.decode(data)
    except images.BadImage as e:
        report.reason = str(e)
        return report

    existing = db.scalar(select(PersonImage).where(PersonImage.sha256 == decoded.sha256))
    if existing is not None:
        report.status = "SKIPPED"
        report.reason = "Exact copy of an image already in the gallery."
        report.duplicates = [DuplicateHit(record_id=existing.person.record_id, confidence=1.0)]
        return report

    faces = analyse(decoded.bgr, ml, settings)
    if not faces:
        report.reason = "Couldn't find a face in this photo."
        return report
    if len(faces) > 1:
        # Enrolling the wrong face poisons the gallery. Same rule as search: never auto-pick.
        report.reason = f"{len(faces)} faces in this photo. Crop to one person."
        return report

    face = faces[0]
    vector = embed(face, ml)
    report.face_px = face.quality.face_px
    report.blur = face.quality.blur
    report.yaw = face.quality.yaw
    report.low_quality = face.quality.low
    report.duplicates = _near_duplicates(db, vector, person.id, settings)

    image_id = uuid4()
    key = f"gallery/{person.id}/{image_id}"
    storage.put(key, data)
    db.add(
        PersonImage(
            id=image_id,
            person_id=person.id,
            storage_key=key,
            sha256=decoded.sha256,
            source=source,
            width=decoded.width,
            height=decoded.height,
            face_box=list(face.face.box),
            landmarks=[float(v) for v in face.face.landmarks.reshape(-1)],
            low_quality=face.quality.low,
            quality=face.quality.as_dict(),
            embedder_version=ml.embedder.version,
            enrolled_by=user.id if user else None,
            **{settings.embedding_column: vector},
        )
    )
    db.flush()
    report.status = "ENROLLED"
    return report
