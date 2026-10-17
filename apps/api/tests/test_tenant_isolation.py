"""Row-level security is the safety net for tenant data (CLAUDE.md rule 1, ADR 0002).

These tests run with the API's own connection role, never as the owner.
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.db import apply_context, create_engine
from app.core.ids import new_id
from app.models_registry import GLOBAL_TABLES
from app.modules.tenancy.models import Branch
from tests.factories import (
    APPEND_ONLY,
    GUARDED,
    ISOLATION_FACTORIES,
    NOT_SYNCED,
    TENANT_COLUMN,
    TenantSeed,
    branch_in,
    seed_tenant,
)

pytestmark = pytest.mark.usefixtures("clean_db")


async def _tenant_tables(owner_engine: AsyncEngine) -> set[str]:
    async with owner_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'tenant_id'"
            )
        )
        return {r[0] for r in rows} | {"tenant"}


async def test_every_table_is_either_global_or_a_fully_protected_tenant_table(
    owner_engine: AsyncEngine,
) -> None:
    tenant_tables = await _tenant_tables(owner_engine)
    async with owner_engine.connect() as conn:
        all_tables = set(
            (
                await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
            ).scalars()
        )
        protected = {
            r[0]: (r[1], r[2])
            for r in await conn.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
                )
            )
        }
        policies = set(
            (
                await conn.execute(text("SELECT tablename FROM pg_policies WHERE schemaname = 'public'"))
            ).scalars()
        )
        synced = set(
            (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                        "WHERE t.tgname = 'sync_outbox_capture'"
                    )
                )
            ).scalars()
        )

    assert all_tables - tenant_tables == set(GLOBAL_TABLES), "a table is neither a tenant table nor global"
    assert not (tenant_tables & GLOBAL_TABLES), "a table with tenant_id is listed as global"
    for table in tenant_tables:
        assert protected[table] == (True, True), f"{table}: RLS must be enabled and forced"
        assert table in policies, f"{table}: missing tenant policy"
        assert table in ISOLATION_FACTORIES, f"{table}: add an isolation factory in tests/factories.py"
        if table in NOT_SYNCED:
            assert table not in synced, f"{table}: local-only table must not be synced"
        else:
            assert table in synced, f"{table}: missing sync_outbox_capture trigger"


def _claim_by_copy(table: str, col: str) -> str:
    """Insert a copy of one of our rows that claims to belong to another tenant."""
    return (
        f"INSERT INTO {table} SELECT (jsonb_populate_record(NULL::{table}, to_jsonb(t) || "
        f"jsonb_build_object('id', CAST(:id AS uuid), '{col}', CAST(:a AS uuid)))).* "
        f"FROM {table} t WHERE id = :b"
    )


async def _make_tenant_with_row(
    sm: async_sessionmaker[AsyncSession], table: str, name: str
) -> tuple[TenantSeed, Any]:
    async with sm() as s, s.begin():
        seed = await seed_tenant(s, name)
        row_id = await ISOLATION_FACTORIES[table](s, seed)
    return seed, row_id


@pytest.mark.parametrize("table", sorted(ISOLATION_FACTORIES))
async def test_tenant_cannot_read_change_or_claim_another_tenants_rows(
    table: str, app_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    col = TENANT_COLUMN.get(table, "tenant_id")
    seed_a, row_a = await _make_tenant_with_row(app_sessionmaker, table, "A")
    seed_b, row_b = await _make_tenant_with_row(app_sessionmaker, table, "B")

    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed_b.tenant_id)
        visible = await s.execute(text(f"SELECT count(*) FROM {table} WHERE id = :id"), {"id": row_a})
        assert visible.scalar_one() == 0

        if table in APPEND_ONLY:
            # No UPDATE/DELETE for the app role; a row claiming another tenant cannot be inserted either.
            for statement in (f"UPDATE {table} SET {col} = {col}", f"DELETE FROM {table}"):
                with pytest.raises(DBAPIError, match="permission denied"):
                    async with s.begin_nested():
                        await s.execute(text(statement))
            if table == "audit_log":
                claim = (
                    f"INSERT INTO {table} (id, tenant_id, action, entity_type, entity_id) "
                    "VALUES (:id, :a, 'create', 'business', :b)"
                )
                params = {"id": new_id(), "a": seed_a.tenant_id, "b": seed_a.business_id}
            else:
                claim = _claim_by_copy(table, col)
                params = {"id": new_id(), "a": seed_a.tenant_id, "b": row_b}
            with pytest.raises(DBAPIError, match="row-level security"):
                async with s.begin_nested():
                    await s.execute(text(claim), params)
        elif table in GUARDED:
            # Other tenants' rows are invisible (nothing changes); a copy claiming them is refused.
            updated = await s.execute(text(f"UPDATE {table} SET {col} = {col} WHERE id = :id"), {"id": row_a})
            assert updated.rowcount == 0  # type: ignore[attr-defined]
            deleted = await s.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_a})
            assert deleted.rowcount == 0  # type: ignore[attr-defined]
            with pytest.raises(DBAPIError, match="row-level security"):
                async with s.begin_nested():
                    await s.execute(
                        text(_claim_by_copy(table, col)), {"id": new_id(), "a": seed_a.tenant_id, "b": row_b}
                    )
        else:
            updated = await s.execute(text(f"UPDATE {table} SET {col} = {col} WHERE id = :id"), {"id": row_a})
            assert updated.rowcount == 0  # type: ignore[attr-defined]
            deleted = await s.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_a})
            assert deleted.rowcount == 0  # type: ignore[attr-defined]
            with pytest.raises(DBAPIError, match="row-level security"):
                async with s.begin_nested():
                    await s.execute(
                        text(f"UPDATE {table} SET {col} = :a WHERE id = :id"),
                        {"a": seed_a.tenant_id, "id": row_b},
                    )

    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed_a.tenant_id)
        mine = await s.execute(text(f"SELECT count(*) FROM {table} WHERE id = :id"), {"id": row_a})
        assert mine.scalar_one() == 1, "the owning tenant must still see its row (test is not vacuous)"


async def test_no_tenant_context_sees_nothing(app_sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with app_sessionmaker() as s, s.begin():
        await seed_tenant(s)

    async with app_sessionmaker() as s, s.begin():
        for table in ISOLATION_FACTORIES:
            count = (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
            assert count == 0, f"{table} leaked rows without a tenant context"


async def test_api_connections_run_as_the_app_role(app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as conn:
        role, bypass = (
            await conn.execute(
                text("SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert role == "smartbiz_app"
    assert bypass is False


async def test_app_role_survives_a_failed_first_transaction(settings: Settings) -> None:
    """Regression: SET ROLE must not live inside the connection's first transaction.

    Otherwise a rollback undoes it and the pooled connection silently becomes the superuser login (no RLS).
    """
    engine = create_engine(settings)
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError):
                await conn.execute(text("SELECT 1 / 0"))
            await conn.rollback()
            assert (await conn.execute(text("SELECT current_user"))).scalar_one() == "smartbiz_app"
        async with engine.connect() as conn:  # the same pooled connection, checked out again
            assert (await conn.execute(text("SELECT current_user"))).scalar_one() == "smartbiz_app"
    finally:
        await engine.dispose()


async def test_child_cannot_point_at_another_tenants_parent(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Foreign keys skip RLS, so composite (tenant_id, parent_id) keys must stop cross-tenant references."""
    async with app_sessionmaker() as s, s.begin():
        seed_a = await seed_tenant(s, "A")
    async with app_sessionmaker() as s, s.begin():
        seed_b = await seed_tenant(s, "B")
        with pytest.raises(IntegrityError):  # noqa: PT012 - the savepoint keeps the outer transaction usable
            async with s.begin_nested():
                s.add(
                    Branch(
                        id=new_id(),
                        tenant_id=seed_b.tenant_id,
                        business_id=seed_a.business_id,
                        **branch_in("X").model_dump(),
                    )
                )
                await s.flush()


async def test_every_write_is_captured_in_the_sync_outbox(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        await s.execute(
            text("UPDATE business SET legal_name_en = 'Renamed LLC' WHERE id = :id"), {"id": seed.business_id}
        )
        rows = (
            await s.execute(
                text(
                    "SELECT table_name, op, node_id, payload ->> 'legal_name_en' FROM sync_outbox "
                    "WHERE row_id = :id ORDER BY id"
                ),
                {"id": seed.business_id},
            )
        ).all()
        tables = set((await s.execute(text("SELECT DISTINCT table_name FROM sync_outbox"))).scalars())

    assert [(r[0], r[1]) for r in rows] == [("business", "insert"), ("business", "update")]
    assert all(r[2] == seed.node_id for r in rows), "outbox rows record the writing node"
    assert rows[-1][3] == "Renamed LLC"
    assert {"tenant", "business", "branch", "warehouse", "node", "membership"} <= tables


async def test_audit_columns_come_from_the_request_context(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        row = (
            await s.execute(
                text("SELECT created_by, origin_node_id FROM business WHERE id = :id"),
                {"id": seed.business_id},
            )
        ).one()
    assert row.created_by == seed.user_id
    assert row.origin_node_id == seed.node_id
