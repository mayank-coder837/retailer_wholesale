"""Application settings.

Environment variables use the ``SMARTBIZ_`` prefix. The product name is never configured here directly: it is
read from ``packages/config/product.json`` so it lives in exactly one place.
"""

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PRODUCT_CONFIG = REPO_ROOT / "packages" / "config" / "product.json"
DEV_JWT_SECRET = "dev-only-insecure-jwt-secret-change-me-0123456789"  # noqa: S105 - rejected in prod


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class JobsBackend(StrEnum):
    INPROCESS = "inprocess"
    CELERY = "celery"


class ProductInfo(BaseModel):
    name: str
    slug: str
    app_id: str = Field(alias="appId")
    service_prefix: str = Field(alias="servicePrefix")
    local_db_port: int = Field(alias="localDbPort")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMARTBIZ_", env_file=".env", extra="ignore")

    environment: Environment = Environment.DEV
    database_url: str = "postgresql+asyncpg://smartbiz:smartbiz@localhost:5440/smartbiz"
    database_connect_timeout_s: float = 3.0
    jobs_backend: JobsBackend = JobsBackend.INPROCESS
    product_config_path: Path = DEFAULT_PRODUCT_CONFIG
    cors_origins: list[str] = ["http://localhost:5173"]

    # The API's database login switches to this role on every connection, so row-level security always applies
    # (ADR 0002). Migrations run as the owner and create it.
    app_db_role: str = Field(default="smartbiz_app", pattern=r"^[a-z_][a-z0-9_]*$")

    # Attachments: a folder on the main PC (ADR 0008); the cloud S3 adapter arrives with M2.1.
    files_dir: Path = REPO_ROOT / "var" / "files"
    max_upload_bytes: int = 20 * 1024 * 1024
    # Background jobs: the API runs the in-process worker unless disabled (tests drive it directly).
    worker_enabled: bool = True

    # Local runtime (M0.6, ADR 0008). Set by the desktop app's service configuration on the main PC.
    web_dir: Path | None = None  # built SPA served by the API (same origin for main and office PCs)
    secrets_file: Path | None = None  # DPAPI-encrypted JSON: jwt_secret, db_password (Windows)
    tls_cert_file: Path | None = None  # self-signed certificate office PCs pin when pairing
    pairing_file: Path | None = None  # the current pairing code, written by the desktop app on the main PC
    server_name: str | None = None  # shown to office PCs while pairing
    api_port: int | None = None  # the port office PCs use (sent in discovery answers)
    discovery_port: int | None = None  # UDP: office PCs find the main PC again after an IP change
    backup_dir: Path | None = None  # nightly pg_dump target; no backups when unset
    # pg_dump login: read-only with BYPASSRLS (the API's own login cannot see past row-level security)
    backup_database_url: str | None = None
    # Every new backup is also copied here (USB drive, NAS share), same rotation; the owner chooses it (F1)
    backup_copy_dir: Path | None = None
    pg_bin_dir: Path | None = None  # folder holding pg_dump
    backup_keep: int = 14
    # Daily automatic backup: 15:00 Dubai time by default (founder, 12 Oct 2026), whatever the PC's clock zone
    backup_hour: int = Field(default=15, ge=0, le=23)
    backup_time_zone: str = "Asia/Dubai"

    # Auth (ADR 0007)
    jwt_secret: str = DEV_JWT_SECRET
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_days: int = 30
    refresh_cookie_secure: bool = False
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    @model_validator(mode="after")
    def _load_local_secrets(self) -> "Settings":
        """On the main PC, secrets come from a DPAPI-encrypted file, never from plain configuration."""
        if self.secrets_file is not None:
            from app.core.local_secrets import read_secrets

            secrets = read_secrets(self.secrets_file)
            self.jwt_secret = secrets["jwt_secret"]
            self.database_url = with_password(self.database_url, secrets["db_password"])
            if self.backup_database_url is not None and "backup_password" in secrets:
                self.backup_database_url = with_password(self.backup_database_url, secrets["backup_password"])
        return self

    @model_validator(mode="after")
    def _require_real_secrets_in_prod(self) -> "Settings":
        if self.environment is Environment.PROD and self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError("SMARTBIZ_JWT_SECRET must be set in production")
        return self

    @property
    def product(self) -> ProductInfo:
        return load_product(self.product_config_path)


def with_password(url: str, password: str) -> str:
    from sqlalchemy import make_url

    return make_url(url).set(password=password).render_as_string(hide_password=False)


@lru_cache
def load_product(path: Path) -> ProductInfo:
    return ProductInfo.model_validate(json.loads(path.read_text(encoding="utf-8")))


@lru_cache
def get_settings() -> Settings:
    return Settings()
