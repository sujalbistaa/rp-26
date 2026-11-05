import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi import status as http
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app import audit, images
from app.audit import Action
from app.config import Settings, get_settings
from app.db.models import (
    Band,
    Calibration,
    PersonImage,
    Role,
    RoleRank,
    Search,
    SearchResult,
    SearchStatus,
    User,
)
from app.db.session import get_db
from app.ml.align import align
from app.ml.calibrate import Curve, gallery_drifted
from app.ml.embed import embed_crop
from app.pipeline import ML, analyse, get_ml
from app.schemas import (
    CaseNumber,
    FaceOut,
    MatchImage,
    MatchPerson,
    ProbeOut,
    Reason,
    ResultsOut,
    RunIn,
    SearchRow,
)
from app.security import current_user
from app.storage import Storage, get_storage

router = APIRouter(prefix="/search", tags=["search"])

NO_FACE = "Couldn't find a face in this photo."
RATE_LIMITED = "Too many searches in the last hour. Try again later."
NO_CALIBRATION = "No active calibration. An admin has to fit and activate one before searching."


def read_upload(file: UploadFile, settings: Settings) -> bytes:
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(http.HTTP_413_CONTENT_TOO_LARGE, "That photo is over 15 MB.")
    return data


def active_curve(db: Session, settings: Settings) -> Curve:
    row = db.scalar(select(Calibration).where(Calibration.active))
    if row is None:
        raise HTTPException(http.HTTP_409_CONFLICT, NO_CALIBRATION)
    if row.embedder_version != settings.embedder_version:
        raise HTTPException(
            http.HTTP_409_CONFLICT,
            "Active calibration was fitted on a different model. Refit before searching.",
        )
    if row.synthetic and settings.env == "production":
        raise HTTPException(
            http.HTTP_409_CONFLICT,
            "Active calibration was fitted on synthetic data. Refit on the real gallery.",
        )
    return Curve(row)


def nearest_images(
    db: Session, vector: np.ndarray, settings: Settings
) -> list[tuple[UUID, UUID, float]]:
    """Top-k gallery images by cosine, as (image_id, person_id, cosine).

    pgvector stops an HNSW scan at ef_search candidates, so ef_search=100 alone could never
    fill a top 200. Iterative scan keeps walking the graph, in exact distance order.
    """
    column = getattr(PersonImage, settings.embedding_column)
    distance = column.cosine_distance(vector)
    db.execute(text(f"SET LOCAL hnsw.ef_search = {int(settings.hnsw_ef_search)}"))
    db.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
    rows = db.execute(
        select(PersonImage.id, PersonImage.person_id, distance.label("distance"))
        .where(column.is_not(None))
        .order_by(distance)
        .limit(settings.search_top_k)
    )
    return [(r.id, r.person_id, 1 - float(r.distance)) for r in rows]


def gallery_size(db: Session, settings: Settings) -> int:
    column = getattr(PersonImage, settings.embedding_column)
    return db.scalar(select(func.count()).where(column.is_not(None))) or 0


def _owned(db: Session, search_id: UUID, user: User, *options) -> Search:
    search = db.get(Search, search_id, options=options)
    if search is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No such search.")
    if search.officer_id != user.id and RoleRank[user.role.value] < RoleRank[Role.ANALYST.value]:
        raise HTTPException(http.HTTP_403_FORBIDDEN, "That search belongs to another officer.")
    return search


def _faces(search: Search) -> list[FaceOut]:
    return [
        FaceOut(index=i, box=f["box"], quality=f["quality"]) for i, f in enumerate(search.faces)
    ]


def _results(db: Session, search: Search) -> ResultsOut:
    totals = dict(
        db.execute(
            select(PersonImage.person_id, func.count())
            .where(
                PersonImage.person_id.in_({r.person_id for r in search.results}),
                PersonImage.removed_at.is_(None),
            )
            .group_by(PersonImage.person_id)
        ).all()
    )
    grouped: dict[int, list[SearchResult]] = defaultdict(list)
    # A removed image was never evidence, so it drops out of past results too.
    for result in search.results:
        if result.image.removed_at is None:
            grouped[result.person_rank].append(result)

    people = []
    for rank in sorted(grouped):
        rows = grouped[rank]
        top = rows[0]
        all_images = [
            row.id
            for row in db.execute(
                select(PersonImage.id)
                .where(PersonImage.person_id == top.person_id, PersonImage.removed_at.is_(None))
                .order_by(PersonImage.enrolled_at, PersonImage.id)
            )
        ]
        people.append(
            MatchPerson(
                rank=rank,
                person_id=top.person_id,
                record_id=top.person.record_id,
                band=top.band,
                confidence=top.confidence,
                images_matched=len(rows),
                images_total=totals.get(top.person_id, len(rows)),
                images=[
                    MatchImage(
                        image_id=row.image_id,
                        confidence=row.confidence,
                        band=row.band,
                        source=row.image.source,
                        enrolled_at=row.image.enrolled_at,
                        position=all_images.index(row.image_id) + 1,
                    )
                    for row in rows
                ],
            )
        )

    floor = None
    if search.calibration is not None:
        curve = Curve(search.calibration)
        floor = curve.confidence(curve.floor)
    return ResultsOut(
        id=search.id,
        status=search.status,
        case_number=search.case_number,
        reason=search.reason,
        officer=search.officer.officer_id,
        created_at=search.created_at,
        duration_ms=search.duration_ms,
        gallery_size=search.gallery_size,
        width=search.width,
        height=search.height,
        faces=_faces(search),
        face_index=search.face_index,
        low_quality=search.low_quality,
        attached_to_case=search.attached_to_case,
        purged=search.purged_at is not None,
        calibration_version=search.calibration_version,
        calibration_synthetic=bool(search.calibration and search.calibration.synthetic),
        gallery_drifted=bool(
            search.calibration
            and gallery_drifted(search.calibration.gallery_size, search.gallery_size)
        ),
        floor=floor,
        people=people,
    )


@router.post("/probe", response_model=ProbeOut, status_code=http.HTTP_201_CREATED)
def create_probe(
    request: Request,
    file: UploadFile = File(...),
    case_number: str = Form(...),
    reason: str = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    ml: ML = Depends(get_ml),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
):
    try:
        case_number = TypeAdapter(CaseNumber).validate_python(case_number)
    except ValidationError as e:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "Enter a case number.") from e
    try:
        reason = TypeAdapter(Reason).validate_python(reason)
    except ValidationError as e:
        raise HTTPException(
            http.HTTP_422_UNPROCESSABLE_CONTENT, "Give a reason of at least 10 characters."
        ) from e

    recent = db.scalar(
        select(func.count())
        .select_from(Search)
        .where(
            Search.officer_id == user.id,
            Search.created_at > datetime.now(UTC) - timedelta(hours=1),
        )
    )
    if (recent or 0) >= settings.search_limit_per_hour:
        audit.write(
            db,
            Action.SEARCH_REFUSED,
            actor=user,
            request=request,
            case_number=case_number,
            detail={"reason": "rate limit", "limit_per_hour": settings.search_limit_per_hour},
        )
        db.commit()
        raise HTTPException(http.HTTP_429_TOO_MANY_REQUESTS, RATE_LIMITED)

    data = read_upload(file, settings)
    try:
        decoded = images.decode(data)
    except images.BadImage as e:
        raise HTTPException(http.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from e

    faces = analyse(decoded.bgr, ml, settings)
    search_id = uuid4()
    key = f"probes/{search_id}"
    storage.put(key, data)
    search = Search(
        id=search_id,
        officer_id=user.id,
        case_number=case_number,
        reason=reason,
        status=SearchStatus.DETECTED if faces else SearchStatus.NO_FACE,
        probe_key=key,
        probe_sha256=decoded.sha256,
        width=decoded.width,
        height=decoded.height,
        faces=[f.as_dict() for f in faces],
    )
    db.add(search)
    audit.write(
        db,
        Action.SEARCH_PROBE,
        actor=user,
        request=request,
        case_number=case_number,
        probe_hash=decoded.sha256,
        target_type="search",
        target_id=str(search_id),
        detail={"faces": len(faces), "reason": reason},
    )
    db.commit()
    return ProbeOut(
        id=search.id,
        status=search.status,
        width=search.width,
        height=search.height,
        faces=_faces(search),
        gallery_size=gallery_size(db, settings),
        case_number=case_number,
    )


@router.post("/{search_id}/run", response_model=ResultsOut)
def run_search(
    search_id: UUID,
    body: RunIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    ml: ML = Depends(get_ml),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
):
    started = time.perf_counter()
    search = _owned(db, search_id, user)
    if search.status == SearchStatus.NO_FACE:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, NO_FACE)
    if search.status == SearchStatus.COMPLETE:
        raise HTTPException(http.HTTP_409_CONFLICT, "This search already ran. Start a new one.")
    if search.purged_at is not None:
        raise HTTPException(http.HTTP_410_GONE, "This probe was purged.")

    # Never auto-pick. One face needs no choice; two or more need the officer's.
    if body.face_index is None and len(search.faces) > 1:
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "Pick a face first.")
    index = body.face_index if body.face_index is not None else 0
    if index >= len(search.faces):
        raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "That face isn't in this photo.")

    curve = active_curve(db, settings)
    face = search.faces[index]
    decoded = images.decode(storage.get(search.probe_key))
    landmarks = np.array(face["landmarks"], dtype=np.float32).reshape(5, 2)
    vector = embed_crop(ml.embedder, align(decoded.bgr, landmarks))

    hits = nearest_images(db, vector, settings)
    best: dict[UUID, float] = {}
    by_person: dict[UUID, list[tuple[UUID, float]]] = defaultdict(list)
    for image_id, person_id, cosine in hits:
        if curve.band(cosine) is None:
            continue
        by_person[person_id].append((image_id, cosine))
        best[person_id] = max(best.get(person_id, -1.0), cosine)

    ranked = sorted(best, key=lambda p: best[p], reverse=True)
    for rank, person_id in enumerate(ranked, start=1):
        person_band = curve.band(best[person_id])
        for position, (image_id, cosine) in enumerate(by_person[person_id]):
            db.add(
                SearchResult(
                    search_id=search.id,
                    person_id=person_id,
                    image_id=image_id,
                    person_rank=rank,
                    cosine=cosine,
                    confidence=curve.confidence(cosine),
                    # The first row carries the person's band: the max across their images.
                    band=person_band if position == 0 else curve.band(cosine),
                    calibration_version=curve.version,
                )
            )

    search.status = SearchStatus.COMPLETE
    search.face_index = index
    search.low_quality = bool(face["quality"]["low"])
    search.calibration_version = curve.version
    search.embedder_version = ml.embedder.version
    search.gallery_size = gallery_size(db, settings)
    search.duration_ms = int((time.perf_counter() - started) * 1000)
    returned = sum(len(v) for v in by_person.values())
    audit.write(
        db,
        Action.SEARCH,
        actor=user,
        request=request,
        case_number=search.case_number,
        probe_hash=search.probe_sha256,
        results_returned=returned,
        calibration_version=curve.version,
        target_type="search",
        target_id=str(search.id),
        detail={
            "people": len(ranked),
            "face_index": index,
            "low_quality": search.low_quality,
            "strong": sum(1 for p in ranked if curve.band(best[p]) == Band.STRONG),
        },
    )
    db.commit()
    db.refresh(search)
    return _results(db, search)


@router.get("", response_model=list[SearchRow])
def my_searches(
    limit: int = 20, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    people = (
        select(func.count(func.distinct(SearchResult.person_id)))
        .where(SearchResult.search_id == Search.id)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Search, people)
        .where(Search.officer_id == user.id)
        .order_by(Search.created_at.desc())
        .limit(min(limit, 100))
    )
    return [
        SearchRow(
            id=s.id,
            case_number=s.case_number,
            status=s.status,
            created_at=s.created_at,
            people=count,
            low_quality=s.low_quality,
            attached_to_case=s.attached_to_case,
            purged=s.purged_at is not None,
        )
        for s, count in rows
    ]


@router.get("/{search_id}", response_model=ResultsOut)
def get_search(
    search_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    search = _owned(db, search_id, user, selectinload(Search.results))
    audit.write(
        db,
        Action.SEARCH_VIEW,
        actor=user,
        request=request,
        case_number=search.case_number,
        probe_hash=search.probe_sha256,
        results_returned=len(search.results),
        calibration_version=search.calibration_version,
        target_type="search",
        target_id=str(search.id),
    )
    db.commit()
    return _results(db, search)


@router.get("/{search_id}/probe")
def probe_image(
    search_id: UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
):
    search = _owned(db, search_id, user)
    if search.purged_at is not None:
        raise HTTPException(http.HTTP_410_GONE, "This probe was purged.")
    return Response(
        images.render_jpeg(storage.get(search.probe_key), 1600),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/{search_id}/attach", status_code=http.HTTP_204_NO_CONTENT)
def attach_to_case(
    search_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Keep this probe past the retention window, because it's now part of a case."""
    search = _owned(db, search_id, user)
    if search.purged_at is not None:
        raise HTTPException(http.HTTP_410_GONE, "This probe was purged.")
    search.attached_to_case = True
    audit.write(
        db,
        Action.CASE_ATTACH,
        actor=user,
        request=request,
        case_number=search.case_number,
        probe_hash=search.probe_sha256,
        target_type="search",
        target_id=str(search.id),
    )
    db.commit()


