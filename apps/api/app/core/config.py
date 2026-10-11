"""Application settings.

Environment variables use the ``SMARTBIZ_`` prefix. The product name is never configured here directly: it is
read from ``packages/config/product.json`` so it lives in exactly one place.
"""

import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PRODUCT_CONFIG = REPO_ROOT / "packages" / "config" / "product.json"


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

    @property
    def product(self) -> ProductInfo:
        return load_product(self.product_config_path)


@lru_cache
def load_product(path: Path) -> ProductInfo:
    return ProductInfo.model_validate(json.loads(path.read_text(encoding="utf-8")))


@lru_cache
def get_settings() -> Settings:
    return Settings()
