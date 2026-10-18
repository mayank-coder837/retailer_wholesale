"""The stock ledger engine: applying moves, keeping the caches, and recosting backdated moves (rule 28).

``apply`` posts one move through the costing rules (``costing.py``) against the variant's cost pool,
writes the ``stock_move`` rows (plus a ``negative_revaluation`` row when rule 27 applies), and updates
``average_cost`` and ``stock_balance`` in the same transaction. Moves of one business are serialised by a
transaction-level advisory lock, so ``seq`` (the order moves happened in) is gap-free and pools never race.

``replay`` recomputes a variant from its moves in date order. Run after a backdated move, it returns the value
differences of later moves (``Correction``) — written as new ``recost`` / ``negative_revaluation`` rows, never
by changing posted ones.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory import costing
from app.modules.inventory.models import INBOUND, OUTBOUND, AverageCost, MoveType, StockBalance, StockMove

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Spec:
    """One quantity move to apply. ``qty`` is positive; the move type says which way it goes."""

    move_type: MoveType
    warehouse_id: uuid.UUID
    variant_id: uuid.UUID
    qty: Decimal
    batch_id: uuid.UUID | None = None
    unit_cost: Decimal | None = None  # inbound: per base unit (default: the pool's current cost)
    value: Decimal | None = None  # inbound: the exact value to bring in (transfers, entered costs)
    purchase: bool = False  # a purchase receipt: becomes the last purchase cost
    counter_account_id: uuid.UUID | None = None
    document_line_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Applied:
    spec: Spec
    move: StockMove
    value: Decimal  # signed: + in, - out
    revaluation: Decimal  # signed change to inventory value from rule 27 (0 when none)
    revaluation_move: StockMove | None
    no_cost: bool
    backdated: bool


@dataclass(frozen=True, slots=True)
class Correction:
    """A value difference found by a replay: on a quantity move's own value, or on its revaluation."""

    move: StockMove
    kind: MoveType  # RECOST (own value) or NEGATIVE_REVALUATION
    delta: Decimal  # signed change to inventory value


async def lock_business(session: AsyncSession, business_id: uuid.UUID) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"stock:{business_id}"}
    )


async def _next_seq(session: AsyncSession, business_id: uuid.UUID) -> int:
    current = await session.scalar(
        select(func.max(StockMove.seq)).where(StockMove.business_id == business_id)
    )
    return (current or 0) + 1


async def pool(
    session: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID, variant_id: uuid.UUID
) -> AverageCost:
    """The variant's cost pool, locked for this transaction (created empty the first time)."""
    await session.execute(
        insert(AverageCost)
        .values(
            tenant_id=tenant_id,
            business_id=business_id,
            variant_id=variant_id,
            qty_base=ZERO,
            value=ZERO,
        )
        .on_conflict_do_nothing(index_elements=["business_id", "variant_id"])
    )
    return (
        await session.scalars(
            select(AverageCost)
            .where(AverageCost.business_id == business_id, AverageCost.variant_id == variant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()


def state_of(row: AverageCost) -> costing.State:
    return costing.State(row.qty_base, row.value, row.avg_cost, row.last_purchase_cost)


def _store(row: AverageCost, state: costing.State) -> None:
    row.qty_base = state.qty
    row.value = state.value
    row.avg_cost = state.avg
    row.last_purchase_cost = state.last_purchase


async def add_balance(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    business_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    variant_id: uuid.UUID,
    batch_id: uuid.UUID | None,
    qty: Decimal,
    value: Decimal,
) -> Decimal:
    """Adds to the warehouse balance and returns its new quantity."""
    stmt = insert(StockBalance).values(
        tenant_id=tenant_id,
        business_id=business_id,
        warehouse_id=warehouse_id,
        variant_id=variant_id,
        batch_id=batch_id,
        qty_base=qty,
        value=value,
    )
    upsert = stmt.on_conflict_do_update(
        index_elements=["warehouse_id", "variant_id", "batch_id"],
        set_={"qty_base": StockBalance.qty_base + qty, "value": StockBalance.value + value},
    ).returning(StockBalance.qty_base)
    new_qty: Decimal = (await session.execute(upsert)).scalar_one()
    return new_qty


async def warehouse_qty(
    session: AsyncSession, warehouse_id: uuid.UUID, variant_id: uuid.UUID, batch_id: uuid.UUID | None
) -> Decimal:
    found = await session.scalar(
        select(StockBalance.qty_base).where(
            StockBalance.warehouse_id == warehouse_id,
            StockBalance.variant_id == variant_id,
            StockBalance.batch_id.is_(None) if batch_id is None else StockBalance.batch_id == batch_id,
        )
    )
    return found or ZERO


@dataclass
class Context:
    """The business-level facts every move of one posting shares."""

    tenant_id: uuid.UUID
    business_id: uuid.UUID
    day: date
    document_id: uuid.UUID | None
    revaluation_account_id: uuid.UUID | None  # stock_cost_adjustment: the other side of rule 27
    touched: set[uuid.UUID] = field(default_factory=set)
    backdated: set[uuid.UUID] = field(default_factory=set)


async def _write(session: AsyncSession, ctx: Context, **values: object) -> StockMove:
    row = StockMove(
        tenant_id=ctx.tenant_id,
        business_id=ctx.business_id,
        move_date=ctx.day,
        seq=await _next_seq(session, ctx.business_id),
        document_id=ctx.document_id,
        **values,
    )
    session.add(row)
    await session.flush()
    return row


async def apply(session: AsyncSession, ctx: Context, spec: Spec) -> Applied:
    """One quantity move through the cost pool: rows written, caches updated, values returned."""
    if spec.qty <= 0:
        raise ValueError("a move quantity is above zero")
    row = await pool(session, ctx.tenant_id, ctx.business_id, spec.variant_id)
    backdated = row.last_move_date is not None and ctx.day < row.last_move_date
    state = state_of(row)
    revaluation = ZERO
    no_cost = False
    if spec.move_type in INBOUND:
        unit = spec.unit_cost if spec.unit_cost is not None else (state.unit_cost() or ZERO)
        result = costing.inbound(state, spec.qty, unit, purchase=spec.purchase, value=spec.value)
        value, revaluation, new_state = result.value, result.revaluation, result.state
        unit_cost = costing.cost(value / spec.qty)
        qty = spec.qty
    elif spec.move_type in OUTBOUND:
        out = costing.outbound(state, spec.qty)
        value, new_state, no_cost = -out.value, out.state, out.no_cost
        unit_cost = out.unit_cost
        qty = -spec.qty
    else:
        raise ValueError(f"{spec.move_type} is not a quantity move")

    move = await _write(
        session,
        ctx,
        warehouse_id=spec.warehouse_id,
        variant_id=spec.variant_id,
        batch_id=spec.batch_id,
        move_type=spec.move_type.value,
        qty_base=qty,
        unit_cost=unit_cost,
        value=value,
        document_line_id=spec.document_line_id,
        counter_account_id=spec.counter_account_id,
    )
    revaluation_move = None
    if revaluation:
        revaluation_move = await _write(
            session,
            ctx,
            warehouse_id=spec.warehouse_id,
            variant_id=spec.variant_id,
            batch_id=spec.batch_id,
            move_type=MoveType.NEGATIVE_REVALUATION.value,
            qty_base=ZERO,
            unit_cost=ZERO,
            value=revaluation,
            document_line_id=spec.document_line_id,
            counter_account_id=ctx.revaluation_account_id,
            adjusts_move_id=move.id,
        )
    _store(row, new_state)
    if row.last_move_date is None or ctx.day > row.last_move_date:
        row.last_move_date = ctx.day
    await add_balance(
        session,
        tenant_id=ctx.tenant_id,
        business_id=ctx.business_id,
        warehouse_id=spec.warehouse_id,
        variant_id=spec.variant_id,
        batch_id=spec.batch_id,
        qty=qty,
        value=value + revaluation,
    )
    await session.flush()
    ctx.touched.add(spec.variant_id)
    if backdated:
        ctx.backdated.add(spec.variant_id)
    return Applied(spec, move, value, revaluation, revaluation_move, no_cost, backdated)


# --- replay (rule 28) --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Replayed:
    state: costing.State
    corrections: list[Correction]
    # item_transfer_out moves whose value changed, and by how much: their target lines must follow
    item_transfer_deltas: list[tuple[StockMove, Decimal]]


async def replay(session: AsyncSession, business_id: uuid.UUID, variant_id: uuid.UUID) -> Replayed:
    """Recomputes the variant from all its moves in date order; returns what differs from what was posted."""
    rows = (
        await session.scalars(
            select(StockMove)
            .where(StockMove.business_id == business_id, StockMove.variant_id == variant_id)
            .order_by(StockMove.move_date, StockMove.seq)
        )
    ).all()
    own_adjust: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    reval: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for r in rows:
        if r.move_type == MoveType.RECOST and r.adjusts_move_id:
            own_adjust[r.adjusts_move_id] += r.value
        elif r.move_type == MoveType.NEGATIVE_REVALUATION and r.adjusts_move_id:
            reval[r.adjusts_move_id] += r.value

    state = costing.State()
    corrections: list[Correction] = []
    transfer_deltas: list[tuple[StockMove, Decimal]] = []
    expected_out: dict[
        uuid.UUID | None, Decimal
    ] = {}  # transfer_out value by document line, for its transfer_in
    for m in rows:
        if m.move_type not in {t.value for t in INBOUND | OUTBOUND}:
            continue
        recorded_own = m.value + own_adjust[m.id]
        recorded_rev = reval[m.id]
        expected_rev = ZERO
        if m.move_type in {t.value for t in OUTBOUND}:
            out = costing.outbound(state, -m.qty_base)
            expected_own = -out.value
            state = out.state
            if m.move_type == MoveType.TRANSFER_OUT:
                expected_out[m.document_line_id] = expected_own
        else:
            if m.move_type == MoveType.TRANSFER_IN and m.document_line_id in expected_out:
                expected_own = -expected_out[m.document_line_id]
            else:
                expected_own = recorded_own  # brought in at a stated cost (or by another item's transfer)
            result = costing.inbound(
                state,
                m.qty_base,
                m.unit_cost,
                purchase=m.move_type == MoveType.RECEIPT,
                value=expected_own,
            )
            expected_rev, state = result.revaluation, result.state
        if expected_own != recorded_own:
            corrections.append(Correction(m, MoveType.RECOST, expected_own - recorded_own))
            if m.move_type == MoveType.ITEM_TRANSFER_OUT:
                transfer_deltas.append((m, expected_own - recorded_own))
        if expected_rev != recorded_rev:
            corrections.append(Correction(m, MoveType.NEGATIVE_REVALUATION, expected_rev - recorded_rev))
    return Replayed(state, corrections, transfer_deltas)


async def write_correction(
    session: AsyncSession, ctx: Context, correction: Correction, revaluation_account_id: uuid.UUID | None
) -> StockMove:
    """A value-only row for a correction, dated on the move it corrects; caches follow."""
    m = correction.move
    row = StockMove(
        tenant_id=ctx.tenant_id,
        business_id=ctx.business_id,
        move_date=m.move_date,
        seq=await _next_seq(session, ctx.business_id),
        document_id=ctx.document_id,
        warehouse_id=m.warehouse_id,
        variant_id=m.variant_id,
        batch_id=m.batch_id,
        move_type=correction.kind.value,
        qty_base=ZERO,
        unit_cost=ZERO,
        value=correction.delta,
        document_line_id=m.document_line_id,
        counter_account_id=(
            m.counter_account_id if correction.kind == MoveType.RECOST else revaluation_account_id
        ),
        adjusts_move_id=m.id,
    )
    session.add(row)
    await add_balance(
        session,
        tenant_id=ctx.tenant_id,
        business_id=ctx.business_id,
        warehouse_id=m.warehouse_id,
        variant_id=m.variant_id,
        batch_id=m.batch_id,
        qty=ZERO,
        value=correction.delta,
    )
    await session.flush()
    return row


async def set_pool(session: AsyncSession, ctx: Context, variant_id: uuid.UUID, state: costing.State) -> None:
    row = await pool(session, ctx.tenant_id, ctx.business_id, variant_id)
    _store(row, state)
    await session.flush()
