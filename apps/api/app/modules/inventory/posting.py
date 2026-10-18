"""Posting helpers every stock-moving document shares (inventory documents now; purchasing and sales next).

- ``ValueJournal`` nets what stock moves did to the inventory account against their counter accounts.
- ``recost`` is rule 28: after a backdated move, later moves of the same items take their new costs, as new
  rows and journal entries dated on the moves they correct.
- ``ensure_batch`` creates a line's new batch by number when its document is posted.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.modules.accounting import posting
from app.modules.accounting import service as accounting
from app.modules.accounting.models import Role
from app.modules.documents.models import DocumentLine
from app.modules.inventory import costing, stock
from app.modules.inventory.models import Batch, MoveType, StockMove
from app.modules.tenancy.models import Business

ZERO = Decimal(0)


class RecostLockedError(ConflictError):
    code = "recost_into_locked_period"
    title = "A later stock move is in a locked period, so this backdated document cannot be posted"


class ValueJournal:
    """Changes to the inventory account and what balances them, netted per account."""

    def __init__(self, inventory: uuid.UUID) -> None:
        self.inventory = inventory
        self.net: dict[uuid.UUID, Decimal] = {}

    def add(self, change: Decimal, counter: uuid.UUID | None) -> None:
        """``change`` (signed: + raises stock value) against ``counter``; no counter (transfers): nothing."""
        if not change or counter is None:
            return
        self.net[self.inventory] = self.net.get(self.inventory, ZERO) + change
        self.net[counter] = self.net.get(counter, ZERO) - change

    def lines(self, description: str | None = None) -> list[posting.JournalLine]:
        out: list[posting.JournalLine] = []
        for account, amount in self.net.items():
            if amount > 0:
                out.append(posting.JournalLine(account, amount, ZERO, description=description))
            elif amount < 0:
                out.append(posting.JournalLine(account, ZERO, -amount, description=description))
        return out


async def batch_by_no(session: AsyncSession, variant_id: uuid.UUID, batch_no: str) -> Batch | None:
    return (
        await session.scalars(select(Batch).where(Batch.variant_id == variant_id, Batch.batch_no == batch_no))
    ).first()


async def ensure_batch(session: AsyncSession, tenant_id: uuid.UUID, row: DocumentLine) -> uuid.UUID | None:
    """The line's batch, creating it by number the first time (while the document is still a draft)."""
    if row.batch_id is not None or row.batch_no is None or row.variant_id is None:
        return row.batch_id
    batch = await batch_by_no(session, row.variant_id, row.batch_no)
    if batch is None:
        batch = Batch(
            tenant_id=tenant_id,
            variant_id=row.variant_id,
            batch_no=row.batch_no,
            expiry_date=row.expiry_date,
        )
        session.add(batch)
        await session.flush()
    row.batch_id = batch.id
    row.batch_no = None
    row.expiry_date = None
    await session.flush()
    return batch.id


async def recost(
    session: AsyncSession,
    ctx: stock.Context,
    business: Business,
    branch_id: uuid.UUID,
    document_id: uuid.UUID,
    number: str,
    roles: dict[Role, uuid.UUID],
) -> None:
    """Rule 28: later moves of the backdated variants take their new costs, as new rows and journal entries
    dated on the moves they correct. Item Transfers carry a change on to the target items."""
    by_day: dict[date, ValueJournal] = {}
    queue = list(ctx.backdated)
    rounds = 0
    while queue:
        rounds += 1
        if rounds > 1000:
            raise RuntimeError("recost did not settle")
        variant = queue.pop(0)
        replayed = await stock.replay(session, business.id, variant)
        for correction in replayed.corrections:
            await stock.write_correction(session, ctx, correction, roles[Role.STOCK_COST_ADJUSTMENT])
            counter = (
                correction.move.counter_account_id
                if correction.kind == MoveType.RECOST
                else roles[Role.STOCK_COST_ADJUSTMENT]
            )
            if counter is not None:
                by_day.setdefault(correction.move.move_date, ValueJournal(roles[Role.INVENTORY])).add(
                    correction.delta, counter
                )
        await stock.set_pool(session, ctx, variant, replayed.state)
        for out_move, delta in replayed.item_transfer_deltas:
            targets = (
                await session.scalars(
                    select(StockMove)
                    .where(
                        StockMove.document_id == out_move.document_id,
                        StockMove.move_type == MoveType.ITEM_TRANSFER_IN.value,
                    )
                    .order_by(StockMove.seq)
                )
            ).all()
            for target, share in zip(
                targets, costing.split(-delta, [t.qty_base for t in targets]), strict=True
            ):
                if share:
                    await stock.write_correction(
                        session, ctx, stock.Correction(target, MoveType.RECOST, share), None
                    )
                    if target.variant_id not in queue:
                        queue.append(target.variant_id)
    for day, journal in sorted(by_day.items()):
        lines = journal.lines(f"Recost after {number}")
        if not lines:
            continue
        if not await accounting.is_open(session, business, day):
            raise RecostLockedError(date=day.isoformat())
        _year, period = await accounting.posting_period(session, business, day)
        await accounting.write_journal(
            session,
            business=business,
            branch_id=branch_id,
            day=day,
            period_id=period.id,
            number=number,
            narration=f"Recost after {number}",
            source_document_id=document_id,
            lines=lines,
        )
