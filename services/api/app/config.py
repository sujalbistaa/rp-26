from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_FIELD_KEY = "ZGV2LW9ubHktZmllbGQta2V5LW5vdC1mb3ItcHJvZCE="  # 32 bytes, base64
DEV_STORAGE_KEY = "ZGV2LW9ubHktaW1hZ2Uta2V5LW5vdC1mb3ItcHJvZCE="  # 32 bytes, base64
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RKL_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "production"] = "dev"

    database_url: str = "postgresql+psycopg://rkl:rkl@localhost:5544/rkl"
    redis_url: str = "redis://localhost:6390/0"

    # "file" is an encrypted local volume. "s3" is any S3-compatible server.
    storage_backend: Literal["file", "s3"] = "file"
    storage_dir: Path = REPO_ROOT / "data" / "evidence"
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "rkl-evidence"
    s3_ca_bundle: str | None = None
    # Ask the server to encrypt too. Objects are already encrypted before they leave the API.
    s3_sse: bool = False

    # AES-256-GCM keys, base64. One for sensitive columns (address, phone, ID number, TOTP
    # secret), one for stored images.
    field_key: str = DEV_FIELD_KEY
    storage_key: str = DEV_STORAGE_KEY

    session_cookie: str = "rkl_session"
    session_idle_minutes: int = 30
    session_max_hours: int = 12
    cookie_secure: bool = True
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    # One-click sign-in with no password or 2FA, for local development only.
    dev_login: bool = False

    probe_retention_days: int = 90
    max_upload_bytes: int = 15 * 1024 * 1024
    max_image_pixels: int = 50_000_000
    bulk_max_bytes: int = 1024 * 1024 * 1024
    bulk_max_files: int = 20_000

    model_dir: Path = REPO_ROOT / "data" / "models"
    detector_file: str = "det_10g.onnx"
    embedder_file: str = "w600k_r50.onnx"
    embedder_version: str = "arcface-w600k-r50"
    embedding_column: Literal["embedding_v1", "embedding_v2"] = "embedding_v1"
    # buffalo_l weights are non-commercial research only. See CLAUDE.md, Licensing.
    model_licence_confirmed: bool = False

    detect_threshold: float = 0.5
    quality_min_face_px: int = 80
    quality_min_blur: float = 200.0
    quality_max_yaw: float = 45.0

    search_top_k: int = 200
    hnsw_ef_search: int = 400
    require_gpu: bool = False
    search_limit_per_hour: int = 120

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> "Settings":
        if self.env != "production":
            return self
        problems = []
        if self.dev_login:
            problems.append("RKL_DEV_LOGIN skips password and 2FA")
        if self.field_key == DEV_FIELD_KEY:
            problems.append("RKL_FIELD_KEY is the dev key")
        if not self.cookie_secure:
            problems.append("RKL_COOKIE_SECURE must be true")
        if not self.model_licence_confirmed:
            problems.append("RKL_MODEL_LICENCE_CONFIRMED is false: see CLAUDE.md, Licensing")
        if self.storage_key == DEV_STORAGE_KEY:
            problems.append("RKL_STORAGE_KEY is the dev key")
        if self.storage_backend == "s3" and not self.s3_endpoint.startswith("https://"):
            problems.append("RKL_S3_ENDPOINT must use TLS")
        if "sslmode=verify" not in self.database_url:
            problems.append("RKL_DATABASE_URL must set sslmode=verify-full or verify-ca")
        if not self.redis_url.startswith("rediss://"):
            problems.append("RKL_REDIS_URL must use TLS (rediss://)")
        if problems:
            raise ValueError("refusing to start in production: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
