"""Inventory services other modules call, and the stock read models (M1.6).

- ``stock_of``: quantity on hand of a variant (availability for sales and purchasing).
- Balances, an item's stock ledger, the negative-stock report, batches (earliest expiry first) and the
  valuation check: every figure is read from ``stock_move`` / ``stock_balance``, never typed in (rule 5).
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting import service as accounting
from app.modules.accounting.models import Role
from app.modules.catalog import products as catalog
from app.modules.documents.models import Document
from app.modules.inventory.models import AverageCost, Batch, StockBalance, StockMove
from app.modules.inventory.schemas import BalanceOut, BatchOut, ItemLedgerLine, ItemLedgerOut, ValuationOut
from app.modules.tenancy import service as tenancy

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
