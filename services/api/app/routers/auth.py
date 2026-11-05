from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, security
from app.audit import Action
from app.config import get_settings
from app.db.models import AuthSession, User
from app.db.session import get_db
from app.schemas import DevAccount, DevLoginIn, LoginIn, PasswordIn, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
# Only mounted when RKL_DEV_LOGIN is on, which production refuses to start with.
dev_router = APIRouter(prefix="/auth", tags=["auth"])

BAD_LOGIN = "Wrong ID, password or code."


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=settings.session_max_hours * 3600,
    )


@router.post("/login", response_model=UserOut, dependencies=[Depends(security.check_csrf)])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    user = db.scalar(select(User).where(User.officer_id == body.officer_id).with_for_update())
    password_ok = security.verify_password(user.password_hash if user else None, body.password)
    locked = bool(user and user.locked_until and user.locked_until > datetime.now(UTC))

    if not user or not user.active or locked or not password_ok or not security.verify_totp(
        user, body.totp
    ):
        if user and not locked:
            user.failed_logins += 1
            if user.failed_logins >= settings.login_max_failures:
                user.locked_until = datetime.now(UTC) + timedelta(
                    minutes=settings.login_lock_minutes
                )
                user.failed_logins = 0
        audit.write(
            db,
            Action.LOGIN_FAILED,
            actor=user,
            actor_label=body.officer_id[:32] or "UNKNOWN",
            request=request,
            detail={"locked": locked},
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    user.failed_logins = 0
    user.locked_until = None
    token = security.open_session(db, user)
    audit.write(db, Action.LOGIN, actor=user, request=request)
    db.commit()
    _set_session_cookie(response, token)
    return user


@dev_router.get("/dev-accounts", response_model=list[DevAccount])
def dev_accounts(db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.active).order_by(User.officer_id)).all()


@dev_router.post("/dev-login", response_model=UserOut, dependencies=[Depends(security.check_csrf)])
def dev_login(
    body: DevLoginIn, request: Request, response: Response, db: Session = Depends(get_db)
):
    user = db.scalar(select(User).where(User.officer_id == body.officer_id, User.active))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such account.")
    token = security.open_session(db, user)
    audit.write(db, Action.LOGIN, actor=user, request=request, detail={"mock": True})
    db.commit()
    _set_session_cookie(response, token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    user: User = Depends(security.session_user),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    security.close_session(db, request.cookies.get(settings.session_cookie, ""))
    audit.write(db, Action.LOGOUT, actor=user, request=request)
    db.commit()
    response.delete_cookie(settings.session_cookie, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(security.session_user)):
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordIn,
    request: Request,
    user: User = Depends(security.session_user),
    db: Session = Depends(get_db),
):
    actor = db.get(User, user.id)
    if not security.verify_password(actor.password_hash, body.current):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong.")
    if body.new == body.current:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a different password.")
    actor.password_hash = security.hash_password(body.new)
    actor.must_change_password = False
    # Sign out every other session.
    current = security._token_hash(request.cookies.get(get_settings().session_cookie, ""))
    for session in db.scalars(select(AuthSession).where(AuthSession.user_id == actor.id)):
        if session.token_hash != current:
            db.delete(session)
    audit.write(db, Action.PASSWORD_CHANGE, actor=actor, request=request)
    db.commit()
