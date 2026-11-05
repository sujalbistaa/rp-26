import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import status as http
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit, security
from app.audit import Action
from app.db.models import AuthSession, Calibration, Role, User
from app.db.session import get_db
from app.ml.calibrate import Curve
from app.schemas import (
    CalibrationOut,
    FloorIn,
    UserCreated,
    UserIn,
    UserOut,
    UserPatch,
)
from app.security import require

router = APIRouter(prefix="/admin", tags=["admin"])
admin_only = require(Role.ADMIN)


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.officer_id)).all()


@router.post("/users", response_model=UserCreated, status_code=http.HTTP_201_CREATED)
def create_user(
    body: UserIn,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    password = secrets.token_urlsafe(12)
    secret = security.new_totp_secret()
    user = User(
        officer_id=body.officer_id,
        name=body.name,
        role=body.role,
        password_hash=security.hash_password(password),
        totp_secret=secret,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as e:
        raise HTTPException(http.HTTP_409_CONFLICT, "That officer ID is taken.") from e
    audit.write(
        db,
        Action.USER_CHANGE,
        actor=admin,
        request=request,
        target_type="user",
        target_id=str(user.id),
        detail={"created": body.officer_id, "role": body.role.value},
    )
    db.commit()
    # Shown once. Neither value can be read back later.
    return UserCreated(
        user=UserOut.model_validate(user),
        temporary_password=password,
        totp_secret=secret,
        totp_uri=security.totp_uri(secret, user.officer_id),
    )


@router.patch("/users/{user_id}", response_model=UserOut)
def edit_user(
    user_id: UUID,
    body: UserPatch,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No such user.")
    if user.id == admin.id:
        raise HTTPException(http.HTTP_400_BAD_REQUEST, "Another admin has to change your account.")
    changed = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changed.items():
        setattr(user, field, value)
    if changed.get("active") is False:
        for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)):
            db.delete(session)
    audit.write(
        db,
        Action.USER_CHANGE,
        actor=admin,
        request=request,
        target_type="user",
        target_id=str(user.id),
        detail={k: str(v) for k, v in changed.items()},
    )
    db.commit()
    return user


def _calibration_out(row: Calibration) -> CalibrationOut:
    curve = Curve(row)
    return CalibrationOut(
        version=row.version,
        embedder_version=row.embedder_version,
        active=row.active,
        synthetic=row.synthetic,
        n_genuine=row.n_genuine,
        n_impostor=row.n_impostor,
        floor=curve.confidence(curve.floor),
        possible=curve.confidence(row.possible_cos),
        strong=curve.confidence(row.strong_cos),
        floor_calibrated=curve.confidence(row.floor_cos),
        note=row.note,
        created_at=row.created_at,
    )


@router.get("/calibrations", response_model=list[CalibrationOut])
def list_calibrations(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    rows = db.scalars(select(Calibration).order_by(Calibration.version.desc()))
    return [_calibration_out(r) for r in rows]


@router.post("/calibrations/{version}/activate", response_model=CalibrationOut)
def activate_calibration(
    version: int,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    row = db.get(Calibration, version)
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No such calibration.")
    db.execute(update(Calibration).where(Calibration.active).values(active=False))
    row.active = True
    audit.write(
        db,
        Action.THRESHOLD_CHANGE,
        actor=admin,
        request=request,
        calibration_version=version,
        target_type="calibration",
        target_id=str(version),
        detail={"activated": version},
    )
    db.commit()
    return _calibration_out(row)


@router.patch("/calibrations/{version}/floor", response_model=CalibrationOut)
def set_floor(
    version: int,
    body: FloorIn,
    request: Request,
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    """Raise the display floor above the calibrated one. It can't go lower."""
    row = db.get(Calibration, version)
    if row is None:
        raise HTTPException(http.HTTP_404_NOT_FOUND, "No such calibration.")
    before = row.floor_override_cos
    if body.floor is None:
        row.floor_override_cos = None
    else:
        curve = Curve(row)
        if body.floor < curve.confidence(row.floor_cos):
            raise HTTPException(
                http.HTTP_422_UNPROCESSABLE_CONTENT, "The floor can't go below the calibrated one."
            )
        # Confidence has flat steps, so take the lowest cosine that reaches the target.
        reached = [
            c for c, y in zip(row.cos_knots, row.conf_knots, strict=True) if y >= body.floor
        ]
        if not reached:
            raise HTTPException(http.HTTP_422_UNPROCESSABLE_CONTENT, "That floor is out of range.")
        row.floor_override_cos = reached[0]
    audit.write(
        db,
        Action.THRESHOLD_CHANGE,
        actor=admin,
        request=request,
        calibration_version=version,
        target_type="calibration",
        target_id=str(version),
        detail={"floor_override_before": before, "floor_override_after": row.floor_override_cos},
    )
    db.commit()
    return _calibration_out(row)
