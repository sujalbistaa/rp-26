from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.crypto import EncryptedText

EMBEDDING_DIM = 512


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Role(StrEnum):
    OFFICER = "OFFICER"
    ANALYST = "ANALYST"
    ADMIN = "ADMIN"


class RoleRank(IntEnum):
    OFFICER = 1
    ANALYST = 2
    ADMIN = 3


class ImageSource(StrEnum):
    MUGSHOT = "MUGSHOT"
    CCTV = "CCTV"
    PHONE = "PHONE"
    OTHER = "OTHER"


class Band(StrEnum):
    STRONG = "STRONG"
    POSSIBLE = "POSSIBLE"
    WEAK = "WEAK"


class SearchStatus(StrEnum):
    DETECTED = "DETECTED"
    NO_FACE = "NO_FACE"
    COMPLETE = "COMPLETE"


class BatchStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


def _enum(e: type[StrEnum]) -> SAEnum:
    return SAEnum(e, native_enum=False, length=16, validate_strings=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    officer_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(_enum(Role))
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    totp_secret: Mapped[str] = mapped_column(EncryptedText)
    totp_last_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    record_id: Mapped[str] = mapped_column(String(20), unique=True)
    full_name: Mapped[str] = mapped_column(String(160), index=True)
    gender: Mapped[str | None] = mapped_column(String(16))
    birth_year: Mapped[int | None] = mapped_column(Integer)
    address: Mapped[str | None] = mapped_column(EncryptedText)
    phone: Mapped[str | None] = mapped_column(EncryptedText)
    id_number: Mapped[str | None] = mapped_column(EncryptedText)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    images: Mapped[list["PersonImage"]] = relationship(
        back_populates="person",
        primaryjoin="and_(Person.id == PersonImage.person_id, PersonImage.removed_at.is_(None))",
        order_by="PersonImage.enrolled_at, PersonImage.id",
    )
    cases: Mapped[list["PersonCase"]] = relationship(
        back_populates="person", order_by="PersonCase.created_at.desc()"
    )
    notes: Mapped[list["Note"]] = relationship(
        back_populates="person", order_by="Note.created_at.desc()"
    )


class PersonImage(Base):
    __tablename__ = "person_images"
    __table_args__ = (
        Index(
            "ix_person_images_embedding_v1_hnsw",
            "embedding_v1",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 200},
            postgresql_ops={"embedding_v1": "vector_cosine_ops"},
        ),
        Index(
            "ix_person_images_embedding_v2_hnsw",
            "embedding_v2",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 200},
            postgresql_ops={"embedding_v2": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), index=True)
    storage_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[ImageSource] = mapped_column(_enum(ImageSource))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    face_box: Mapped[list[float]] = mapped_column(ARRAY(Float))
    landmarks: Mapped[list[float]] = mapped_column(ARRAY(Float))
    low_quality: Mapped[bool] = mapped_column(Boolean, default=False)
    quality: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    embedding_v1: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_v2: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedder_version: Mapped[str] = mapped_column(String(64))
    enrolled_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Kept as a tombstone so past search results and audit rows still resolve.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    person: Mapped[Person] = relationship(back_populates="images")


class PersonCase(Base):
    __tablename__ = "person_cases"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), index=True)
    case_number: Mapped[str] = mapped_column(String(40), index=True)
    section: Mapped[str | None] = mapped_column(String(120))
    station: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    person: Mapped[Person] = relationship(back_populates="cases")


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), index=True)
    author_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    person: Mapped[Person] = relationship(back_populates="notes")
    author: Mapped[User] = relationship(lazy="joined")


class Calibration(Base):
    __tablename__ = "calibration"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    embedder_version: Mapped[str] = mapped_column(String(64))
    # Isotonic curve as knots. Confidence is the linear interpolation between them.
    cos_knots: Mapped[list[float]] = mapped_column(ARRAY(Float))
    conf_knots: Mapped[list[float]] = mapped_column(ARRAY(Float))
    floor_cos: Mapped[float] = mapped_column(Float)
    possible_cos: Mapped[float] = mapped_column(Float)
    strong_cos: Mapped[float] = mapped_column(Float)
    # Set by an admin. Can only raise the display floor, never lower it.
    floor_override_cos: Mapped[float | None] = mapped_column(Float)
    n_genuine: Mapped[int] = mapped_column(Integer)
    n_impostor: Mapped[int] = mapped_column(Integer)
    # The gallery size the per-search bands were set on, and their FPIR and FNIR report.
    gallery_size: Mapped[int | None] = mapped_column(Integer)
    fpir_report: Mapped[dict | None] = mapped_column(JSONB)
    # True when fitted on synthetic fixtures. Production refuses to search with these.
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index(
            "uq_calibration_single_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class Search(Base):
    __tablename__ = "searches"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    officer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    case_number: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[SearchStatus] = mapped_column(_enum(SearchStatus))
    probe_key: Mapped[str] = mapped_column(String(255))
    probe_sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    faces: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    face_index: Mapped[int | None] = mapped_column(Integer)
    low_quality: Mapped[bool] = mapped_column(Boolean, default=False)
    calibration_version: Mapped[int | None] = mapped_column(ForeignKey("calibration.version"))
    embedder_version: Mapped[str | None] = mapped_column(String(64))
    gallery_size: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    attached_to_case: Mapped[bool] = mapped_column(Boolean, default=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    officer: Mapped[User] = relationship(lazy="joined")
    calibration: Mapped[Calibration | None] = relationship(lazy="joined")
    results: Mapped[list["SearchResult"]] = relationship(
        back_populates="search", order_by="SearchResult.person_rank, SearchResult.cosine.desc()"
    )


class SearchResult(Base):
    """One gallery image above the display floor, with the calibration in force at the time."""

    __tablename__ = "search_results"
    __table_args__ = (UniqueConstraint("search_id", "image_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    search_id: Mapped[UUID] = mapped_column(ForeignKey("searches.id"), index=True)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"))
    image_id: Mapped[UUID] = mapped_column(ForeignKey("person_images.id"))
    person_rank: Mapped[int] = mapped_column(Integer)
    cosine: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    band: Mapped[Band] = mapped_column(_enum(Band))
    calibration_version: Mapped[int] = mapped_column(ForeignKey("calibration.version"))

    search: Mapped[Search] = relationship(back_populates="results")
    person: Mapped[Person] = relationship(lazy="joined")
    image: Mapped[PersonImage] = relationship(lazy="joined")


class EnrollBatch(Base):
    __tablename__ = "enroll_batches"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[BatchStatus] = mapped_column(_enum(BatchStatus), default=BatchStatus.QUEUED)
    csv_key: Mapped[str] = mapped_column(String(255))
    zip_key: Mapped[str] = mapped_column(String(255))
    default_source: Mapped[ImageSource] = mapped_column(_enum(ImageSource))
    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Append-only. A database trigger rejects UPDATE, DELETE and TRUNCATE."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    actor_officer_id: Mapped[str] = mapped_column(String(32))
    actor_role: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(32), index=True)
    ip: Mapped[str | None] = mapped_column(INET)
    case_number: Mapped[str | None] = mapped_column(String(40), index=True)
    probe_hash: Mapped[str | None] = mapped_column(String(64))
    results_returned: Mapped[int | None] = mapped_column(Integer)
    calibration_version: Mapped[int | None] = mapped_column(Integer)
    target_type: Mapped[str | None] = mapped_column(String(24))
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
