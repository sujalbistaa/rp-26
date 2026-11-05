from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db.models import Band, BatchStatus, ImageSource, Role, SearchStatus

LEAD_NOTICE = "Investigative lead only. Verify manually before any action."

CaseNumber = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=40, pattern=r"^[A-Za-z0-9/\-. ]+$"
    ),
]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=500)]
RevealField = Literal["address", "phone", "id_number"]


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginIn(BaseModel):
    officer_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]
    password: Annotated[str, StringConstraints(max_length=256)]
    totp: Annotated[str, StringConstraints(strip_whitespace=True, max_length=6)]


class DevLoginIn(BaseModel):
    officer_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]


class DevAccount(Orm):
    officer_id: str
    name: str
    role: Role


class PasswordIn(BaseModel):
    current: Annotated[str, StringConstraints(max_length=256)]
    new: Annotated[str, StringConstraints(min_length=12, max_length=256)]


class UserOut(Orm):
    id: UUID
    officer_id: str
    name: str
    role: Role
    active: bool
    must_change_password: bool


class FaceOut(BaseModel):
    index: int
    box: list[float]
    quality: dict


class ProbeOut(BaseModel):
    id: UUID
    status: SearchStatus
    width: int
    height: int
    faces: list[FaceOut]
    gallery_size: int
    case_number: str


class RunIn(BaseModel):
    face_index: int | None = Field(default=None, ge=0)


class MatchImage(BaseModel):
    image_id: UUID
    confidence: float
    band: Band
    source: ImageSource
    enrolled_at: datetime
    position: int


class MatchPerson(BaseModel):
    rank: int
    person_id: UUID
    record_id: str
    band: Band
    confidence: float
    images_matched: int
    images_total: int
    images: list[MatchImage]


class ResultsOut(BaseModel):
    id: UUID
    status: SearchStatus
    case_number: str
    reason: str
    officer: str
    created_at: datetime
    duration_ms: int | None
    gallery_size: int | None
    width: int
    height: int
    faces: list[FaceOut]
    face_index: int | None
    low_quality: bool
    attached_to_case: bool
    purged: bool
    calibration_version: int | None
    calibration_synthetic: bool
    floor: float | None
    people: list[MatchPerson]
    notice: str = LEAD_NOTICE


class SearchRow(BaseModel):
    id: UUID
    case_number: str
    status: SearchStatus
    created_at: datetime
    people: int
    low_quality: bool
    attached_to_case: bool
    purged: bool


class PersonCaseIn(BaseModel):
    case_number: CaseNumber
    section: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] | None = None
    station: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    status: Annotated[str, StringConstraints(strip_whitespace=True, max_length=24)] = "OPEN"


class PersonCaseOut(Orm):
    id: UUID
    case_number: str
    section: str | None
    station: str | None
    status: str
    created_at: datetime


class NoteIn(BaseModel):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class NoteOut(BaseModel):
    id: UUID
    body: str
    author: str
    created_at: datetime


class RecordRow(BaseModel):
    id: UUID
    record_id: str
    full_name: str
    cover_image_id: UUID | None
    images: int
    cases: int
    updated_at: datetime


class RecordPage(BaseModel):
    total: int
    items: list[RecordRow]


class RecordImage(Orm):
    id: UUID
    source: ImageSource
    enrolled_at: datetime
    low_quality: bool


class RecordOut(BaseModel):
    id: UUID
    record_id: str
    full_name: str
    gender: str | None
    birth_year: int | None
    has_address: bool
    has_phone: bool
    has_id_number: bool
    created_at: datetime
    updated_at: datetime
    images: list[RecordImage]
    cases: list[PersonCaseOut]
    notes: list[NoteOut]


class RecordPatch(BaseModel):
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)] | None = None  # noqa: E501
    gender: Annotated[str, StringConstraints(strip_whitespace=True, max_length=16)] | None = None
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    address: Annotated[str, StringConstraints(strip_whitespace=True, max_length=400)] | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None
    id_number: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None


class RevealIn(BaseModel):
    field: RevealField
    search_id: UUID | None = None


class RevealOut(BaseModel):
    field: RevealField
    value: str | None


class LogRow(BaseModel):
    at: datetime
    officer: str
    action: str
    case_number: str | None
    detail: dict


class DuplicateHit(BaseModel):
    record_id: str
    confidence: float


class ImageReport(BaseModel):
    filename: str
    status: Literal["ENROLLED", "REJECTED", "SKIPPED"]
    reason: str | None = None
    record_id: str | None = None
    low_quality: bool = False
    face_px: int | None = None
    blur: float | None = None
    yaw: float | None = None
    duplicates: list[DuplicateHit] = []


class EnrollOut(BaseModel):
    record_id: str | None
    person_id: UUID | None
    report: list[ImageReport]


class BatchOut(Orm):
    id: UUID
    status: BatchStatus
    total: int
    processed: int
    report: list[dict]
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class AuditRow(Orm):
    id: int
    at: datetime
    actor_officer_id: str
    actor_role: str | None
    action: str
    ip: str | None
    case_number: str | None
    probe_hash: str | None
    results_returned: int | None
    calibration_version: int | None
    target_type: str | None
    target_id: str | None


class AuditPage(BaseModel):
    total: int
    items: list[AuditRow]


class UserIn(BaseModel):
    officer_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=32)
    ]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    role: Role


class UserCreated(BaseModel):
    user: UserOut
    temporary_password: str
    totp_secret: str
    totp_uri: str


class UserPatch(BaseModel):
    role: Role | None = None
    active: bool | None = None


class CalibrationOut(BaseModel):
    version: int
    embedder_version: str
    active: bool
    synthetic: bool
    n_genuine: int
    n_impostor: int
    floor: float
    possible: float
    strong: float
    floor_calibrated: float
    note: str | None
    created_at: datetime


class FloorIn(BaseModel):
    floor: float | None = Field(default=None, ge=0, le=1)
