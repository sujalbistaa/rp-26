import csv
import io
from collections.abc import Iterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app import audit as audit_log
from app.audit import Action
from app.db.models import AuditLog, Role, User
from app.db.session import get_db
from app.schemas import AuditPage, AuditRow
from app.security import require

router = APIRouter(prefix="/audit", tags=["audit"])

CSV_COLUMNS = [
    "id",
    "at",
    "actor_officer_id",
    "actor_role",
    "action",
    "ip",
    "case_number",
    "probe_hash",
    "results_returned",
    "calibration_version",
    "target_type",
    "target_id",
]


def _filtered(
    action: str | None,
    officer: str | None,
    case: str | None,
    since: datetime | None,
    until: datetime | None,
) -> Select:
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action == action)
    if officer:
        query = query.where(AuditLog.actor_officer_id == officer)
    if case:
        query = query.where(AuditLog.case_number == case)
    if since:
        query = query.where(AuditLog.at >= since)
    if until:
        query = query.where(AuditLog.at <= until)
    return query


def _row(entry: AuditLog) -> AuditRow:
    return AuditRow.model_validate(
        {
            **{c: getattr(entry, c) for c in CSV_COLUMNS},
            "ip": str(entry.ip) if entry.ip is not None else None,
        }
    )


def csv_safe(value: object) -> str:
    """Stop a crafted case number from running as a formula when the export is opened."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@router.get("", response_model=AuditPage)
def list_audit(
    action: str | None = None,
    officer: str | None = None,
    case: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require(Role.ADMIN)),
    db: Session = Depends(get_db),
):
    query = _filtered(action, officer, case, since, until)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(AuditLog.id.desc()).offset(offset).limit(limit))
    return AuditPage(total=total, items=[_row(r) for r in rows])


@router.get("/actions", response_model=list[str])
def list_actions(_: User = Depends(require(Role.ADMIN))):
    return [a.value for a in Action]


@router.get("/export.csv")
def export_audit(
    request: Request,
    action: str | None = None,
    officer: str | None = None,
    case: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    user: User = Depends(require(Role.ADMIN)),
    db: Session = Depends(get_db),
):
    query = _filtered(action, officer, case, since, until)
    count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    audit_log.write(
        db,
        Action.EXPORT,
        actor=user,
        request=request,
        results_returned=count,
        target_type="audit",
        detail={"action": action, "officer": officer, "case": case},
    )
    db.commit()

    def stream() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_COLUMNS)
        for entry in db.scalars(query.order_by(AuditLog.id).execution_options(yield_per=500)):
            writer.writerow([csv_safe(getattr(entry, c)) for c in CSV_COLUMNS])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
        yield buffer.getvalue()

    return StreamingResponse(
        stream(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="rkl-audit.csv"'},
    )
