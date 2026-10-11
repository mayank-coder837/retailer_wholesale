"""Async database engine and sessions.

Tenant context (``SET LOCAL app.tenant_id``) is added in M0.2; every session here is opened inside a
transaction so that setting can be scoped to it.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"timeout": settings.database_connect_timeout_s},
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session, session.begin():
        yield session
