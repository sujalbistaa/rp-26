from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi import status as http
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, jobs
from app.audit import Action
from app.config import Settings, get_settings
from app.db.models import EnrollBatch, ImageSource, Person, Role, User
from app.db.session import get_db
from app.enrollment import enroll_image, next_record_id
from app.pipeline import ML, get_ml
from app.routers.search import read_upload
from app.schemas import BatchOut, EnrollOut, RecordPatch
from app.security import require
from app.storage import Storage, get_storage

router = APIRouter(prefix="/enroll", tags=["enroll"])


@router.post("", response_model=EnrollOut, status_code=http.HTTP_201_CREATED)
def enroll_single(
    request: Request,
    files: list[UploadFile] = File(...),
    source: ImageSource = Form(ImageSource.MUGSHOT),
    record_id: str | None = Form(None),
    full_name: str | None = Form(None),
    gender: str | None = Form(None),
    birth_year: int | None = Form(None),
    address: str | None = Form(None),
    phone: str | None = Form(None),
    id_number: str | None = Form(None),
    user: User = Depends(require(Role.ANALYST)),
    db: Session = Depends(get_db),
    ml: ML = Depends(get_ml),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
):
    if record_id:
        person = db.scalar(select(Person).where(Person.record_id == record_id.strip()))
        if person is None:
            raise HTTPException(http.HTTP_404_NOT_FOUND, "No such record.")
    else:
        if not (full_name and full_name.strip()):
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "A new record needs a name.")
        fields = RecordPatch(
            full_name=full_name,
            gender=gender or None,
            birth_year=birth_year,
            address=address or None,
            phone=phone or None,
            id_number=id_number or None,
        )
        person = Person(
            id=uuid4(),
            record_id=next_record_id(db, datetime.now(UTC).year),
            created_by=user.id,
            **fields.model_dump(),
        )
        db.add(person)
        db.flush()

    report = [
        enroll_image(
            db,
            data=read_upload(f, settings),
            filename=f.filename or "upload",
            person=person,
            source=source,
            user=user,
            ml=ml,
            storage=storage,
            settings=settings,
        )
        for f in files
    ]
    enrolled = sum(1 for r in report if r.status == "ENROLLED")
    if enrolled == 0 and not record_id:
        # A record with no usable photo can't ever match, so don't keep it.
        db.rollback()
        return EnrollOut(record_id=None, person_id=None, report=report)

    audit.write(
        db,
        Action.ENROLL,
        actor=user,
        request=request,
        results_returned=enrolled,
        target_type="person",
        target_id=str(person.id),
        detail={
            "record_id": person.record_id,
            "images": len(report),
            "enrolled": enrolled,
            "duplicates": sum(1 for r in report if r.duplicates),
        },
    )
    db.commit()
    return EnrollOut(record_id=person.record_id, person_id=person.id, report=report)


@router.post("/bulk", response_model=BatchOut, status_code=http.HTTP_202_ACCEPTED)
def enroll_bulk(
    request: Request,
    manifest: UploadFile = File(...),
    archive: UploadFile = File(...),
    source: ImageSource = Form(ImageSource.MUGSHOT),
    user: User = Depends(require(Role.ANALYST)),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
):
    zip_data = archive.file.read(settings.bulk_max_bytes + 1)
    if len(zip_data) > settings.bulk_max_bytes:
        raise HTTPException(http.HTTP_413_CONTENT_TOO_LARGE, "That zip is too large.")
    csv_data = manifest.file.read(32 * 1024 * 1024)
    try:
        rows = jobs.parse_manifest(csv_data)
        jobs.check_archive(zip_data, settings)
    except jobs.BadBatch as e:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    batch_id = uuid4()
    csv_key, zip_key = f"bulk/{batch_id}/manifest.csv", f"bulk/{batch_id}/archive.zip"
    storage.put(csv_key, csv_data)
    storage.put(zip_key, zip_data)
    batch = EnrollBatch(
        id=batch_id,
        created_by=user.id,
        csv_key=csv_key,
        zip_key=zip_key,
        default_source=source,
        total=len(rows),
    )
    db.add(batch)
    audit.write(
        db,
        Action.ENROLL_BULK,
        actor=user,
        request=request,
        target_type="batch",
        target_id=str(batch_id),
        detail={"rows": len(rows)},
    )
    db.commit()
    jobs.enqueue_bulk(batch_id)
    return batch


@router.get("/batches/{batch_id}", response_model=BatchOut)
def get_batch(
    batch_id: UUID,
    _: User = Depends(require(Role.ANALYST)),
    db: Session = Depends(get_db),
):
    batch = db.get(EnrollBatch, batch_id)
    if batch is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No such batch.")
    return batch
