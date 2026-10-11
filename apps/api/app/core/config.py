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

    # Auth (ADR 0007)
    jwt_secret: str = DEV_JWT_SECRET
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_days: int = 30
    refresh_cookie_secure: bool = False
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    @model_validator(mode="after")
    def _require_real_secrets_in_prod(self) -> "Settings":
        if self.environment is Environment.PROD and self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError("SMARTBIZ_JWT_SECRET must be set in production")
        return self

    @property
    def product(self) -> ProductInfo:
        return load_product(self.product_config_path)


@lru_cache
def load_product(path: Path) -> ProductInfo:
    return ProductInfo.model_validate(json.loads(path.read_text(encoding="utf-8")))


@lru_cache
def get_settings() -> Settings:
    return Settings()
