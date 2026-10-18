"""The database keeps the goods ledger honest (CLAUDE.md rules 3 and 5): stock moves are insert-only for
the app and the owner, and the lines of a posted document never change."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import apply_context
from app.modules.inventory.models import StockMove
from tests.factories import _stocked, seed_tenant

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_stock_moves_are_permanent_and_posted_lines_frozen(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        doc_id = await _stocked(s, seed)
        move_id = await s.scalar(select(StockMove.id).where(StockMove.document_id == doc_id))

    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        for statement, message in (
            ("UPDATE stock_move SET value = 0 WHERE id = :id", "permission denied"),
            ("DELETE FROM stock_move WHERE id = :id", "permission denied"),
        ):
            with pytest.raises(DBAPIError, match=message):
                async with s.begin_nested():
                    await s.execute(text(statement), {"id": move_id})
        for statement in (
            "UPDATE document_line SET qty_entered = 99 WHERE document_id = :id",
            "DELETE FROM document_line WHERE document_id = :id",
        ):
            with pytest.raises(DBAPIError, match="cannot change"):
                async with s.begin_nested():
                    await s.execute(text(statement), {"id": doc_id})

    async with owner_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="permanent"):
            await conn.execute(text("UPDATE stock_move SET value = 0 WHERE id = :id"), {"id": move_id})
