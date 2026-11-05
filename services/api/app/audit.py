from enum import StrEnum
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import AuditLog, User


class Action(StrEnum):
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"  # noqa: S105
    SEARCH_PROBE = "SEARCH_PROBE"
    SEARCH = "SEARCH"
    SEARCH_VIEW = "SEARCH_VIEW"
    CASE_ATTACH = "CASE_ATTACH"
    RECORD_VIEW = "RECORD_VIEW"
    RECORD_EDIT = "RECORD_EDIT"
    REVEAL = "REVEAL"
    ENROLL = "ENROLL"
    ENROLL_BULK = "ENROLL_BULK"
    EXPORT = "EXPORT"
    THRESHOLD_CHANGE = "THRESHOLD_CHANGE"
    USER_CHANGE = "USER_CHANGE"
    PROBE_PURGE = "PROBE_PURGE"


def client_ip(request: Request | None) -> str | None:
    # uvicorn resolves X-Forwarded-For from the trusted edge proxy only.
    return request.client.host if request and request.client else None


def write(
    db: Session,
    action: Action,
    *,
    actor: User | None = None,
    actor_label: str | None = None,
    request: Request | None = None,
    case_number: str | None = None,
    probe_hash: str | None = None,
    results_returned: int | None = None,
    calibration_version: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the caller's transaction, so the action and its row commit together."""
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            actor_officer_id=actor.officer_id if actor else (actor_label or "SYSTEM"),
            actor_role=actor.role.value if actor else None,
            action=action.value,
            ip=client_ip(request),
            case_number=case_number,
            probe_hash=probe_hash,
            results_returned=results_returned,
            calibration_version=calibration_version,
            target_type=target_type,
            target_id=target_id,
            detail=detail or {},
        )
    )
