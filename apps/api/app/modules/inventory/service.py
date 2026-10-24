"""Inventory services other modules call, and the stock read models (M1.6).

- ``stock_of``: quantity on hand of a variant (availability for sales and purchasing).
- ``begin_posting`` / ``receive`` / ``issue`` / ``finish_posting``: how purchasing and sales move stock
  through the cost pools (rule 27 on arrival, rule 28 after a backdated move).
- Balances, an item's stock ledger, the negative-stock report, batches (earliest expiry first) and the
  valuation check: every figure is read from ``stock_move`` / ``stock_balance``, never typed in (rule 5).
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableError
from app.modules.accounting import posting
from app.modules.accounting import service as accounting
from app.modules.accounting.models import Role
from app.modules.catalog import products as catalog
from app.modules.documents.models import Document, DocumentLine
from app.modules.inventory import posting as inventory_posting
from app.modules.inventory import stock
from app.modules.inventory.models import AverageCost, Batch, MoveType, StockBalance, StockMove
from app.modules.inventory.posting import batch_by_no, ensure_batch, recost
from app.modules.inventory.schemas import BalanceOut, BatchOut, ItemLedgerLine, ItemLedgerOut, ValuationOut
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Business

ZERO = Decimal("0.0000")
QTY_ZERO = Decimal("0.000000")


async def stock_of(
    session: AsyncSession,
    business_id: uuid.UUID,
    variant_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = None,
) -> Decimal:
    stmt = select(func.coalesce(func.sum(StockBalance.qty_base), QTY_ZERO)).where(
        StockBalance.business_id == business_id, StockBalance.variant_id == variant_id
    )
    if warehouse_id is not None:
        stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
    qty: Decimal = await session.scalar(stmt) or QTY_ZERO
    return qty


async def _items(session: AsyncSession, variant_ids: set[uuid.UUID]) -> dict[uuid.UUID, catalog.StockItem]:
    return {v: await catalog.stock_item(session, v) for v in variant_ids}


async def balances(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID | None = None,
    variant_id: uuid.UUID | None = None,
    q: str | None = None,
    negative_only: bool = False,
) -> list[BalanceOut]:
    await tenancy.get_business(session, business_id)
    stmt = (
        select(StockBalance, Batch)
        .outerjoin(Batch, Batch.id == StockBalance.batch_id)
        .where(StockBalance.business_id == business_id)
    )
    if warehouse_id is not None:
        stmt = stmt.where(StockBalance.warehouse_id == warehouse_id)
    if variant_id is not None:
        stmt = stmt.where(StockBalance.variant_id == variant_id)
    if negative_only:
        stmt = stmt.where(StockBalance.qty_base < 0)
    else:
        stmt = stmt.where((StockBalance.qty_base != 0) | (StockBalance.value != 0))
    rows = (await session.execute(stmt)).all()
    items = await _items(session, {b.variant_id for b, _ in rows})
    needle = (q or "").strip().lower()
    out = [
        BalanceOut(
            variant_id=b.variant_id,
            sku=items[b.variant_id].sku,
            name_en=items[b.variant_id].name_en,
            name_ar=items[b.variant_id].name_ar,
            base_unit=items[b.variant_id].base_unit,
            warehouse_id=b.warehouse_id,
            batch_id=b.batch_id,
            batch_no=batch.batch_no if batch else None,
            expiry_date=batch.expiry_date if batch else None,
            qty=b.qty_base,
            value=b.value,
        )
        for b, batch in rows
        if not needle
        or needle in items[b.variant_id].sku.lower()
        or needle in items[b.variant_id].name_en.lower()
        or needle in items[b.variant_id].name_ar
    ]
    out.sort(key=lambda r: (r.sku, str(r.warehouse_id), r.batch_no or ""))
    return out


async def item_ledger(
    session: AsyncSession,
    business_id: uuid.UUID,
    variant_id: uuid.UUID,
    *,
    warehouse_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ItemLedgerOut:
    await tenancy.get_business(session, business_id)
    await catalog.stock_item(session, variant_id)
    filters = [StockMove.business_id == business_id, StockMove.variant_id == variant_id]
    if warehouse_id is not None:
        filters.append(StockMove.warehouse_id == warehouse_id)
    opening_qty, opening_value = QTY_ZERO, ZERO
    if date_from is not None:
        row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(StockMove.qty_base), QTY_ZERO),
                    func.coalesce(func.sum(StockMove.value), ZERO),
                ).where(*filters, StockMove.move_date < date_from)
            )
        ).one()
        opening_qty, opening_value = row[0], row[1]
    in_range = list(filters)
    if date_from is not None:
        in_range.append(StockMove.move_date >= date_from)
    if date_to is not None:
        in_range.append(StockMove.move_date <= date_to)
    rows = (
        await session.execute(
            select(StockMove, Document.number, Batch.batch_no)
            .outerjoin(Document, Document.id == StockMove.document_id)
            .outerjoin(Batch, Batch.id == StockMove.batch_id)
            .where(*in_range)
            .order_by(StockMove.move_date, StockMove.seq)
        )
    ).all()
    qty, value = opening_qty, opening_value
    lines: list[ItemLedgerLine] = []
    for move, number, batch_no in rows:
        qty += move.qty_base
        value += move.value
        lines.append(
            ItemLedgerLine(
                move_date=move.move_date,
                move_type=move.move_type,
                document_id=move.document_id,
                number=number,
                warehouse_id=move.warehouse_id,
                batch_no=batch_no,
                qty=move.qty_base,
                unit_cost=move.unit_cost,
                value=move.value,
                qty_balance=qty,
                value_balance=value,
            )
        )
    avg = await session.scalar(
        select(AverageCost.avg_cost).where(
            AverageCost.business_id == business_id, AverageCost.variant_id == variant_id
        )
    )
    return ItemLedgerOut(
        variant_id=variant_id,
        opening_qty=opening_qty,
        opening_value=opening_value,
        lines=lines,
        closing_qty=qty,
        closing_value=value,
        avg_cost=avg,
    )


async def valuation(session: AsyncSession, business_id: uuid.UUID) -> ValuationOut:
    """The stock ledger's value and the inventory account's balance: always equal (CLAUDE.md rule 5)."""
    await tenancy.get_business(session, business_id)
    stock_value = await session.scalar(
        select(func.coalesce(func.sum(StockMove.value), ZERO)).where(StockMove.business_id == business_id)
    )
    inventory = (await accounting.role_accounts(session, business_id)).get(Role.INVENTORY)
    account = await accounting.account_balance(session, business_id, inventory) if inventory else ZERO
    stock_value = stock_value or ZERO
    return ValuationOut(
        business_id=business_id,
        stock_value=stock_value,
        inventory_account=account,
        difference=stock_value - account,
    )


async def batches(
    session: AsyncSession,
    business_id: uuid.UUID,
    variant_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = None,
) -> list[BatchOut]:
    """A variant's batches, earliest expiry first (FEFO), with the quantity on hand."""
    await tenancy.get_business(session, business_id)
    qty = func.coalesce(func.sum(StockBalance.qty_base), QTY_ZERO)
    join = (StockBalance.batch_id == Batch.id) & (StockBalance.business_id == business_id)
    if warehouse_id is not None:
        join = join & (StockBalance.warehouse_id == warehouse_id)
    rows = await session.execute(
        select(Batch, qty)
        .outerjoin(StockBalance, join)
        .where(Batch.variant_id == variant_id)
        .group_by(Batch.id)
        .order_by(Batch.expiry_date.asc().nulls_last(), Batch.batch_no)
    )
    return [
        BatchOut(
            id=b.id,
            variant_id=b.variant_id,
            batch_no=b.batch_no,
            mfg_date=b.mfg_date,
            expiry_date=b.expiry_date,
            qty=q,
        )
        for b, q in rows
    ]


# --- posting stock from other modules (purchasing M1.7, sales M1.8) ----------------------------------------


class InvalidBatchError(UnprocessableError):
    code = "invalid_batch"
    title = "The batch is not valid for this item"


async def begin_posting(
    session: AsyncSession, business: Business, day: date, document_id: uuid.UUID
) -> stock.Context:
    """Serialises this business's stock postings for the transaction and opens a posting context."""
    await stock.lock_business(session, business.id)
    roles = await accounting.role_accounts(session, business.id)
    return stock.Context(
        business.tenant_id, business.id, day, document_id, roles.get(Role.STOCK_COST_ADJUSTMENT)
    )


async def receive(
    session: AsyncSession,
    ctx: stock.Context,
    *,
    warehouse_id: uuid.UUID,
    variant_id: uuid.UUID,
    qty_base: Decimal,
    value: Decimal,
    batch_id: uuid.UUID | None,
    line_id: uuid.UUID | None,
    counter_account_id: uuid.UUID | None,
    move_type: MoveType = MoveType.RECEIPT,
) -> stock.Applied:
    """Goods in at an exact value (a purchase receipt or a direct bill line); rule 27 applies on arrival."""
    spec = stock.Spec(
        move_type,
        warehouse_id,
        variant_id,
        qty_base,
        batch_id,
        value=value,
        purchase=move_type == MoveType.RECEIPT,
        counter_account_id=counter_account_id,
        document_line_id=line_id,
    )
    return await stock.apply(session, ctx, spec)


async def issue(
    session: AsyncSession,
    ctx: stock.Context,
    *,
    warehouse_id: uuid.UUID,
    variant_id: uuid.UUID,
    qty_base: Decimal,
    batch_id: uuid.UUID | None,
    line_id: uuid.UUID | None,
    counter_account_id: uuid.UUID | None,
    move_type: MoveType = MoveType.ISSUE,
) -> stock.Applied:
    """Goods out at the current average (a sale, a delivery, a return to the supplier)."""
    spec = stock.Spec(
        move_type,
        warehouse_id,
        variant_id,
        qty_base,
        batch_id,
        counter_account_id=counter_account_id,
        document_line_id=line_id,
    )
    return await stock.apply(session, ctx, spec)


async def finish_posting(
    session: AsyncSession,
    ctx: stock.Context,
    business: Business,
    *,
    branch_id: uuid.UUID,
    document_id: uuid.UUID,
    number: str,
    narration: str | None = None,
) -> None:
    """After the document's own moves: recost later moves when it was backdated (rule 28)."""
    if ctx.backdated:
        roles = await accounting.role_accounts(session, business.id)
        await recost(session, ctx, business, branch_id, document_id, number, roles, narration)


async def undo_moves(
    session: AsyncSession, ctx: stock.Context, document_id: uuid.UUID, kind: MoveType
) -> list[posting.JournalLine]:
    """Rules 32 / 33 for the goods: a ``cancel`` / ``reversal`` row for each quantity move of the document.

    Returns the inventory lines the mirror of the document's posting entries does not cover (recosts it
    took since, rule 27, cost differences); post them dated ``ctx.day``, then call ``finish_posting``.
    """
    roles = await accounting.role_accounts(session, ctx.business_id)
    moves = (
        await session.scalars(
            select(StockMove)
            .where(StockMove.document_id == document_id, StockMove.move_type.in_(stock.QUANTITY_TYPES))
            .order_by(StockMove.seq)
        )
    ).all()
    journal = inventory_posting.ValueJournal(roles[Role.INVENTORY])
    for m in moves:
        undone = await stock.undo(session, ctx, m, kind, roles.get(Role.STOCK_COST_ADJUSTMENT))
        for amount, counter in undone.journal:
            journal.add(amount, counter)
    return journal.lines()


async def has_moves(session: AsyncSession, document_id: uuid.UUID) -> bool:
    found = await session.scalar(select(StockMove.id).where(StockMove.document_id == document_id).limit(1))
    return found is not None


async def check_batch(
    session: AsyncSession,
    item: catalog.StockItem,
    *,
    batch_id: uuid.UUID | None,
    batch_no: str | None,
    expiry_date: date | None,
    outgoing: bool,
) -> uuid.UUID | None:
    """The line's batch (or None: not tracked, or a new batch created at post). Raises when it doesn't fit."""
    if not item.track_batch:
        if batch_id or batch_no or expiry_date:
            raise InvalidBatchError(f"{item.sku} is not tracked by batch.")
        return None
    if batch_id is not None:
        batch = await session.get(Batch, batch_id)
        if batch is None or batch.variant_id != item.variant_id:
            raise InvalidBatchError("There is no such batch of this item.")
        return batch.id
    if outgoing or not batch_no:
        raise InvalidBatchError(f"Choose the batch of {item.sku}.")
    existing = await batch_by_no(session, item.variant_id, batch_no)
    if existing is not None:
        return existing.id
    if item.track_expiry and expiry_date is None:
        raise InvalidBatchError(f"A new batch of {item.sku} needs its expiry date.")
    return None


async def batch_for_post(session: AsyncSession, tenant_id: uuid.UUID, row: DocumentLine) -> uuid.UUID | None:
    return await ensure_batch(session, tenant_id, row)


async def warehouse_stock(
    session: AsyncSession, warehouse_id: uuid.UUID, variant_id: uuid.UUID, batch_id: uuid.UUID | None
) -> Decimal:
    return await stock.warehouse_qty(session, warehouse_id, variant_id, batch_id)


async def batch_no(session: AsyncSession, batch_id: uuid.UUID) -> str | None:
    batch = await session.get(Batch, batch_id)
    return batch.batch_no if batch else None
