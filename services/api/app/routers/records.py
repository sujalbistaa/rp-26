from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi import status as http
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import audit, images
from app.audit import Action
from app.db.models import (
    AuditLog,
    Note,
    Person,
    PersonCase,
    PersonImage,
    Role,
    Search,
    SearchResult,
    User,
)
from app.db.session import get_db
from app.schemas import (
    ImageRemoveIn,
    LogRow,
    NoteIn,
    NoteOut,
    PersonCaseIn,
    PersonCaseOut,
    RecordImage,
    RecordOut,
    RecordPage,
    RecordPatch,
    RecordRow,
    RevealIn,
    RevealOut,
)
from app.security import current_user, require
from app.storage import Storage, get_storage

router = APIRouter(prefix="/records", tags=["records"])


def _person(db: Session, person_id: UUID, *options) -> Person:
    person = db.get(Person, person_id, options=options)
    if person is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No such record.")
    return person


def _context(db: Session, search_id: UUID | None, user: User) -> Search | None:
    """The search this access came from. Only your own search can label your audit row."""
    search = db.get(Search, search_id) if search_id else None
    return search if search and search.officer_id == user.id else None


def _out(person: Person) -> RecordOut:
    return RecordOut(
        id=person.id,
        record_id=person.record_id,
        full_name=person.full_name,
        gender=person.gender,
        birth_year=person.birth_year,
        has_address=person.address is not None,
        has_phone=person.phone is not None,
        has_id_number=person.id_number is not None,
        created_at=person.created_at,
        updated_at=person.updated_at,
        images=[RecordImage.model_validate(i) for i in person.images],
        cases=[PersonCaseOut.model_validate(c) for c in person.cases],
        notes=[
            NoteOut(id=n.id, body=n.body, author=n.author.officer_id, created_at=n.created_at)
            for n in person.notes
        ],
    )


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=RecordPage)
def list_records(
    q: str = Query(default="", max_length=80),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    term = f"%{_escape_like(q.strip())}%"
    match = or_(
        Person.record_id.ilike(term, escape="\\"), Person.full_name.ilike(term, escape="\\")
    )
    images_count = (
        select(func.count())
        .where(PersonImage.person_id == Person.id, PersonImage.removed_at.is_(None))
        .scalar_subquery()
    )
    cases_count = select(func.count()).where(PersonCase.person_id == Person.id).scalar_subquery()
    cover = (
        select(PersonImage.id)
        .where(PersonImage.person_id == Person.id, PersonImage.removed_at.is_(None))
        .order_by(PersonImage.enrolled_at, PersonImage.id)
        .limit(1)
        .scalar_subquery()
    )
    total = db.scalar(select(func.count()).select_from(Person).where(match)) or 0
    rows = db.execute(
        select(Person, images_count, cases_count, cover)
        .where(match)
        .order_by(Person.updated_at.desc(), Person.record_id)
        .offset(offset)
        .limit(limit)
    )
    return RecordPage(
        total=total,
        items=[
            RecordRow(
                id=p.id,
                record_id=p.record_id,
                full_name=p.full_name,
                cover_image_id=first,
                images=i,
                cases=c,
                updated_at=p.updated_at,
            )
            for p, i, c, first in rows
        ],
    )


@router.get("/images/{image_id}")
def gallery_image(
    image_id: UUID,
    size: int = Query(default=480, ge=64, le=1600),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    image = db.get(PersonImage, image_id)
    if image is None or image.removed_at is not None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No such image.")
    return Response(
        images.render_jpeg(storage.get(image.storage_key), size),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{person_id}", response_model=RecordOut)
def get_record(
    person_id: UUID,
    request: Request,
    search_id: UUID | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    person = _person(
        db,
        person_id,
        selectinload(Person.images),
        selectinload(Person.cases),
        selectinload(Person.notes),
    )
    search = _context(db, search_id, user)
    audit.write(
        db,
        Action.RECORD_VIEW,
        actor=user,
        request=request,
        case_number=search.case_number if search else None,
        probe_hash=search.probe_sha256 if search else None,
        target_type="person",
        target_id=str(person.id),
        detail={"record_id": person.record_id},
    )
    db.commit()
    return _out(person)


@router.post("/{person_id}/reveal", response_model=RevealOut)
def reveal(
    person_id: UUID,
    body: RevealIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    person = _person(db, person_id)
    search = _context(db, body.search_id, user)
    audit.write(
        db,
        Action.REVEAL,
        actor=user,
        request=request,
        case_number=search.case_number if search else None,
        target_type="person",
        target_id=str(person.id),
        detail={"field": body.field, "record_id": person.record_id},
    )
    db.commit()
    return RevealOut(field=body.field, value=getattr(person, body.field))


@router.get("/{person_id}/log", response_model=list[LogRow])
def record_log(
    person_id: UUID,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(AuditLog)
        .where(AuditLog.target_type == "person", AuditLog.target_id == str(person_id))
        .order_by(AuditLog.id.desc())
        .limit(200)
    )
    return [
        LogRow(
            at=r.at,
            officer=r.actor_officer_id,
            action=r.action,
            case_number=r.case_number,
            detail={k: v for k, v in r.detail.items() if k == "field"},
        )
        for r in rows
    ]


@router.patch("/{person_id}", response_model=RecordOut)
def edit_record(
    person_id: UUID,
    body: RecordPatch,
    request: Request,
    user: User = Depends(require(Role.ANALYST)),
    db: Session = Depends(get_db),
):
    person = _person(db, person_id)
    changed = body.model_dump(exclude_unset=True)
    for field, value in changed.items():
        setattr(person, field, value)
    audit.write(
        db,
        Action.RECORD_EDIT,
        actor=user,
        request=request,
        target_type="person",
        target_id=str(person.id),
        # Field names only. Values stay out of the audit log.
        detail={"fields": sorted(changed), "record_id": person.record_id},
    )
    db.commit()
    return _out(person)


@router.post("/{person_id}/cases", response_model=PersonCaseOut, status_code=http.HTTP_201_CREATED)
def add_case(
    person_id: UUID,
    body: PersonCaseIn,
    request: Request,
    user: User = Depends(require(Role.ANALYST)),
    db: Session = Depends(get_db),
):
    person = _person(db, person_id)
    case = PersonCase(person_id=person.id, **body.model_dump())
    db.add(case)
    audit.write(
        db,
        Action.RECORD_EDIT,
        actor=user,
        request=request,
        case_number=body.case_number,
        target_type="person",
        target_id=str(person.id),
        detail={"fields": ["cases"], "record_id": person.record_id},
    )
    db.commit()
    return case


@router.post("/{person_id}/notes", response_model=NoteOut, status_code=http.HTTP_201_CREATED)
def add_note(
    person_id: UUID,
    body: NoteIn,
    request: Request,
    user: User = Depends(require(Role.ANALYST)),
    db: Session = Depends(get_db),
):
    person = _person(db, person_id)
    note = Note(person_id=person.id, author_id=user.id, body=body.body)
    db.add(note)
    audit.write(
        db,
        Action.RECORD_EDIT,
        actor=user,
        request=request,
        target_type="person",
        target_id=str(person.id),
        detail={"fields": ["notes"], "record_id": person.record_id},
    )
    db.commit()
    return NoteOut(id=note.id, body=note.body, author=user.officer_id, created_at=note.created_at)


@router.post("/{person_id}/images/{image_id}/remove", status_code=http.HTTP_204_NO_CONTENT)
def remove_image(
    person_id: UUID,
    image_id: UUID,
    body: ImageRemoveIn,
    request: Request,
    user: User = Depends(require(Role.ANALYST)),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    """Take a wrongly enrolled image out of the gallery and out of past results."""
    image = db.get(PersonImage, image_id)
    if image is None or image.person_id != person_id or image.removed_at is not None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No such image.")
    affected = db.scalar(
        select(func.count(func.distinct(SearchResult.search_id))).where(
            SearchResult.image_id == image_id
        )
    )
    original_sha256 = image.sha256
    storage.delete(image.storage_key)
    image.removed_at = datetime.now(UTC)
    image.embedding_v1 = None
    image.embedding_v2 = None
    # Frees the hash, so the same photo can go on the right record.
    image.sha256 = f"removed-{image.id.hex}"
    audit.write(
        db,
        Action.IMAGE_REMOVE,
        actor=user,
        request=request,
        probe_hash=original_sha256,
        results_returned=affected,
        target_type="person",
        target_id=str(person_id),
        detail={"image_id": str(image_id), "reason": body.reason, "searches_affected": affected},
    )
    db.commit()
