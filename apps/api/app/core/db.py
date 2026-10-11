"""Async database engine, sessions and the per-transaction tenant context (ADR 0002).

Every pooled connection switches to the app role (``SET ROLE smartbiz_app``) as soon as it is opened, so
row-level security applies even when the login is a superuser (development). Each transaction then sets
``app.tenant_id`` / ``app.user_id`` / ``app.node_id`` with ``set_config(..., is_local => true)``; nothing
leaks to the next transaction on the same connection.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


def create_engine(settings: Settings, *, as_app_role: bool = True) -> AsyncEngine:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={"timeout": settings.database_connect_timeout_s},
    )
    if as_app_role:
        role = settings.app_db_role  # validated as a plain identifier by Settings

        @event.listens_for(engine.sync_engine, "connect")
        def _switch_role(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute(f"SET ROLE {role}")
            cursor.close()

    return engine


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def apply_context(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    node_id: uuid.UUID | None = None,
) -> None:
    """Scopes the current transaction to one tenant. Must be called inside a transaction."""
    await session.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant, true), "
            "set_config('app.user_id', :user, true), "
            "set_config('app.node_id', :node, true)"
        ),
        {"tenant": _s(tenant_id), "user": _s(user_id), "node": _s(node_id)},
    )


def _s(value: uuid.UUID | None) -> str:
    return str(value) if value else ""


async def session_scope(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session, session.begin():
        yield session
