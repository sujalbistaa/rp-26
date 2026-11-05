import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuthSession, Role, RoleRank, User
from app.db.session import get_db

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
MIN_PASSWORD_LENGTH = 12
TOTP_PERIOD = 30


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    # Unknown officers still pay for a hash so response time doesn't reveal who exists.
    try:
        return _hasher.verify(stored or _DUMMY_HASH, password) and stored is not None
    except VerificationError:
        return False


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, officer_id: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=officer_id, issuer_name="Rourkela Police")


def verify_totp(user: User, code: str) -> bool:
    """Accept the current step or one either side. A used step can't be replayed."""
    if not (code.isdigit() and len(code) == 6):
        return False
    totp = pyotp.TOTP(user.totp_secret)
    now = int(datetime.now(UTC).timestamp())
    for offset in (0, -1, 1):
        at = now + offset * TOTP_PERIOD
        counter = at // TOTP_PERIOD
        if secrets.compare_digest(totp.at(at), code) and counter > user.totp_last_counter:
            user.totp_last_counter = counter
            return True
    return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def open_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=_token_hash(token), user_id=user.id))
    return token


def close_session(db: Session, token: str) -> None:
    session = db.get(AuthSession, _token_hash(token))
    if session:
        db.delete(session)


def _load_user(request: Request, db: Session) -> User:
    settings = get_settings()
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in again.")
    token = request.cookies.get(settings.session_cookie)
    if not token:
        raise unauthorized
    session = db.get(AuthSession, _token_hash(token))
    if session is None:
        raise unauthorized
    now = datetime.now(UTC)
    expired = (
        now - session.last_seen_at > timedelta(minutes=settings.session_idle_minutes)
        or now - session.created_at > timedelta(hours=settings.session_max_hours)
        or not session.user.active
    )
    if expired:
        db.delete(session)
        db.commit()
        raise unauthorized
    session.last_seen_at = now
    db.commit()
    return session.user


def check_csrf(request: Request) -> None:
    """SameSite=Strict does the main work. This also blocks plain cross-site form posts."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if request.headers.get("x-rkl-client") != "web":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Request refused.")


def session_user(
    request: Request, db: Session = Depends(get_db), _: None = Depends(check_csrf)
) -> User:
    """Any signed-in user, including one who still has to change their password."""
    return _load_user(request, db)


def current_user(user: User = Depends(session_user)) -> User:
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "PASSWORD_CHANGE_REQUIRED")
    return user


def require(role: Role) -> Callable[[User], User]:
    def check(user: User = Depends(current_user)) -> User:
        if RoleRank[user.role.value] < RoleRank[role.value]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role can't do that.")
        return user

    return check
