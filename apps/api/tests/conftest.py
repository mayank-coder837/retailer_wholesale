"""Test fixtures.

Tests run against a dedicated database (``smartbiz_test`` on the dev PostgreSQL, port 5440), recreated and
migrated once per session. The API under test connects exactly like production: it switches to the app role,
so row-level security is real. ``owner_engine`` (no role switch) is only for setup and cleanup.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Environment, Settings
from app.core.db import create_engine, create_sessionmaker
from app.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "SMARTBIZ_TEST_DATABASE_URL", "postgresql+asyncpg://smartbiz:smartbiz@localhost:5440/smartbiz_test"
)
API_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(environment=Environment.TEST, database_url=TEST_DATABASE_URL)


@pytest.fixture(scope="session")
async def migrated_db(settings: Settings) -> str:
    url = make_url(TEST_DATABASE_URL)
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    await admin.dispose()

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    await asyncio.to_thread(command.upgrade, cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
async def owner_engine(settings: Settings, migrated_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings, as_app_role=False)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
async def app_engine(settings: Settings, migrated_db: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings)
    yield engine
    await engine.dispose()


@pytest.fixture
def app_sessionmaker(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_sessionmaker(app_engine)


@pytest.fixture
async def clean_db(owner_engine: AsyncEngine) -> None:
    async with owner_engine.begin() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
            )
        ).scalars()
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def app(settings: Settings, clean_db: None) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
