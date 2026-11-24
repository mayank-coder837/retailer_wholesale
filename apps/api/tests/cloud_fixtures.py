"""A second database standing in for the cloud (sync tests): migrated like the first, emptied per test."""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.db import create_engine, create_sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "SMARTBIZ_TEST_DATABASE_URL", "postgresql+asyncpg://smartbiz:smartbiz@localhost:5440/smartbiz_test"
)
API_DIR = Path(__file__).resolve().parents[1]

CLOUD_DATABASE_URL = make_url(TEST_DATABASE_URL).set(database="smartbiz_test_cloud").render_as_string(False)


@pytest.fixture(scope="session")
async def cloud_db(settings: Settings, migrated_db: str) -> str:
    url = make_url(CLOUD_DATABASE_URL)
    admin = create_async_engine(
        make_url(TEST_DATABASE_URL).set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    await admin.dispose()
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["database_url"] = CLOUD_DATABASE_URL
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return CLOUD_DATABASE_URL


@pytest.fixture
async def cloud(settings: Settings, cloud_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on the cloud database as the app role (row-level security applies), emptied first."""
    cloud_settings = settings.model_copy(update={"database_url": cloud_db})
    owner = create_engine(cloud_settings, as_app_role=False)
    async with owner.begin() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
        ).scalars()
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await owner.dispose()
    engine = create_engine(cloud_settings)
    yield create_sessionmaker(engine)
    await engine.dispose()
