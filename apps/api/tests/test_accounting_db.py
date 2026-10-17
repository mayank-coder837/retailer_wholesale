"""The database itself keeps the ledger honest (CLAUDE.md rules 3 and 4), whatever the code does.

- An unbalanced journal entry, or one without lines, cannot be committed (deferred trigger).
- Journal entries, journal lines and bill allocations are insert-only: the app role has no UPDATE/DELETE and a
  trigger refuses even the owner.
- A posted document cannot be deleted or edited, its voucher lines cannot change, and a bill changes only
  in its open amount.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.db import apply_context
from app.core.ids import new_id
from app.modules.accounting.models import Bill, FiscalPeriod, JournalEntry, JournalLine
from app.modules.accounting.models import Role as LedgerRole
from app.modules.documents.models import Document
from tests.factories import (
    TenantSeed,
    _billed_and_paid,
    _fiscal_period,
    role_account,
    seed_tenant,
)

pytestmark = pytest.mark.usefixtures("clean_db")


async def _seeded(sm: async_sessionmaker[AsyncSession]) -> TenantSeed:
    async with sm() as s, s.begin():
        return await seed_tenant(s)


async def _insert_entry(s: AsyncSession, seed: TenantSeed, lines: list[tuple[str, str]]) -> None:
    """A raw journal entry, bypassing the service's own balance check."""
    period_id = await _fiscal_period(s, seed)
    period = await s.get(FiscalPeriod, period_id)
    assert period is not None
    sales = await role_account(s, seed, LedgerRole.SALES)
    entry = JournalEntry(
        id=new_id(),
        tenant_id=seed.tenant_id,
        business_id=seed.business_id,
        branch_id=seed.branch_id,
        entry_date=period.start_date,
        fiscal_period_id=period.id,
    )
    s.add(entry)
    await s.flush()
    for n, (debit, credit) in enumerate(lines, start=1):
        s.add(
            JournalLine(
                tenant_id=seed.tenant_id,
                entry_id=entry.id,
                business_id=seed.business_id,
                entry_date=entry.entry_date,
                line_no=n,
                account_id=sales,
                debit=Decimal(debit),
                credit=Decimal(credit),
            )
        )
    await s.flush()


@pytest.mark.parametrize(
    ("lines", "message"),
    [([("100", "0"), ("0", "99.99")], "does not balance"), ([], "has no lines")],
)
async def test_an_unbalanced_or_empty_entry_cannot_be_committed(
    app_sessionmaker: async_sessionmaker[AsyncSession], lines: list[tuple[str, str]], message: str
) -> None:
    seed = await _seeded(app_sessionmaker)
    with pytest.raises(DBAPIError, match=message):  # noqa: PT012 - the failure comes at commit
        async with app_sessionmaker() as s, s.begin():
            await apply_context(s, tenant_id=seed.tenant_id)
            await _insert_entry(s, seed, lines)


async def test_a_balanced_entry_commits(app_sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    seed = await _seeded(app_sessionmaker)
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        await _insert_entry(s, seed, [("100", "0"), ("0", "60"), ("0", "40")])
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        assert len((await s.scalars(select(JournalLine))).all()) == 3


async def _ledger_rows(sm: async_sessionmaker[AsyncSession]) -> tuple[TenantSeed, dict[str, object]]:
    async with sm() as s, s.begin():
        seed = await seed_tenant(s)
        receipt_id = await _billed_and_paid(s, seed)
        line_id = await s.scalar(select(JournalLine.id).limit(1))
        entry_id = await s.scalar(select(JournalEntry.id).limit(1))
        allocation_id = await s.scalar(text("SELECT id FROM bill_allocation LIMIT 1"))
        bill_id = await s.scalar(select(Bill.id).limit(1))
    return seed, {
        "receipt": receipt_id,
        "journal_line": line_id,
        "journal_entry": entry_id,
        "bill_allocation": allocation_id,
        "bill": bill_id,
    }


@pytest.mark.parametrize("table", ["journal_entry", "journal_line", "bill_allocation"])
async def test_ledger_rows_are_permanent_for_the_app_and_for_the_owner(
    table: str, app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    seed, ids = await _ledger_rows(app_sessionmaker)
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        for statement in (
            f"UPDATE {table} SET created_at = now() WHERE id = :id",
            f"DELETE FROM {table} WHERE id = :id",
        ):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with s.begin_nested():
                    await s.execute(text(statement), {"id": ids[table]})
    async with owner_engine.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(seed.tenant_id)})
        for statement in (
            f"UPDATE {table} SET created_at = now() WHERE id = :id",
            f"DELETE FROM {table} WHERE id = :id",
        ):
            with pytest.raises(DBAPIError, match="permanent"):
                await conn.execute(text(statement), {"id": ids[table]})
            await conn.rollback()
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(seed.tenant_id)}
            )


async def test_a_bill_changes_only_in_its_open_amount(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seed, ids = await _ledger_rows(app_sessionmaker)
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        for statement in (
            "UPDATE bill SET amount = amount + 1 WHERE id = :id",
            "DELETE FROM bill WHERE id = :id",
        ):
            with pytest.raises(DBAPIError, match="only the open amount"):
                async with s.begin_nested():
                    await s.execute(text(statement), {"id": ids["bill"]})
        await s.execute(
            text("UPDATE bill SET amount_open = amount_open - 1 WHERE id = :id"), {"id": ids["bill"]}
        )


async def test_a_posted_voucher_and_its_lines_cannot_change(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seed, ids = await _ledger_rows(app_sessionmaker)
    receipt = ids["receipt"]
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        for statement, message in (
            ("UPDATE document SET narration = 'edited' WHERE id = :id", "posted document cannot be changed"),
            ("UPDATE document SET status = 'draft' WHERE id = :id", "posted document cannot become draft"),
            ("DELETE FROM document WHERE id = :id", "posted document cannot be deleted"),
            (
                "UPDATE voucher_line SET description = 'x' WHERE document_id = :id",
                "posted voucher cannot change",
            ),
            ("DELETE FROM voucher_line WHERE document_id = :id", "posted voucher cannot change"),
        ):
            with pytest.raises(DBAPIError, match=message):
                async with s.begin_nested():
                    await s.execute(text(statement), {"id": receipt})
        # The move to cancelled (with its reason) is the one change a posted document allows (M1.8 uses it).
        await s.execute(
            text("UPDATE document SET status = 'cancelled', cancel_reason = 'test' WHERE id = :id"),
            {"id": receipt},
        )
        doc = await s.get(Document, receipt)
        assert doc is not None
