"""Moving-average costing, pure (ADR 0006; posting-rules.md rules 23 to 27).

One cost pool per business and variant, across all warehouses. The state is the pool's quantity and value;
values are in fils (2 places), unit costs to 6 places. Nothing here reads the database: every rule is tested
with numbers.

- In (``inbound``): at a stated unit cost. Into negative stock, the units issued below zero are revalued at
  the incoming cost and the difference is returned as ``revaluation`` (rule 27).
- Out (``outbound``): at the current average; the whole quantity takes the whole value, so no fils are left.
  With no stock, at the last average, else the last purchase cost, else zero (``no_cost`` warning).
"""

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
FILS = Decimal("0.01")
COST_PLACES = Decimal("0.000001")


def fils(amount: Decimal) -> Decimal:
    return amount.quantize(FILS, rounding=ROUND_HALF_UP)


def cost(amount: Decimal) -> Decimal:
    return amount.quantize(COST_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class State:
    qty: Decimal = ZERO
    value: Decimal = ZERO
    avg: Decimal | None = None  # the last known average (kept while the pool is at or below zero)
    last_purchase: Decimal | None = None

    def unit_cost(self) -> Decimal | None:
        if self.qty > 0:
            return cost(self.value / self.qty)
        return self.avg if self.avg is not None else self.last_purchase


@dataclass(frozen=True, slots=True)
class Inbound:
    value: Decimal  # the move's own value: quantity times the stated cost
    revaluation: Decimal  # change to the pool's value for negative stock covered (rule 27); 0 when none
    state: State


@dataclass(frozen=True, slots=True)
class Outbound:
    value: Decimal  # positive: what leaves
    unit_cost: Decimal
    state: State
    no_cost: bool  # nothing to cost it at: issued at zero
    negative: bool  # the pool is below zero afterwards


def inbound(
    state: State, qty: Decimal, unit_cost: Decimal, *, purchase: bool = False, value: Decimal | None = None
) -> Inbound:
    """``qty`` (> 0) arrives at ``unit_cost``; ``value`` overrides quantity times cost (transfers keep it)."""
    if qty <= 0:
        raise ValueError("an inbound quantity is above zero")
    own = fils(qty * unit_cost) if value is None else value
    last_purchase = unit_cost if purchase else state.last_purchase
    revaluation = ZERO
    if state.qty < 0:
        covered = min(qty, -state.qty)
        left_at = state.value / state.qty  # the cost the negative units left at (≥ 0)
        incoming = own / qty
        revaluation = -fils(covered * (incoming - left_at))
    new_qty = state.qty + qty
    new_value = state.value + own + revaluation
    if new_qty == 0:
        new_value = ZERO  # exactly cleared: nothing may be left over
    avg = (
        cost(new_value / new_qty)
        if new_qty > 0
        else (state.avg if state.avg is not None else cost(own / qty))
    )
    return Inbound(own, revaluation, State(new_qty, new_value, avg, last_purchase))


def outbound(state: State, qty: Decimal) -> Outbound:
    """``qty`` (> 0) leaves at the pool's cost."""
    if qty <= 0:
        raise ValueError("an outbound quantity is above zero")
    unit = state.unit_cost()
    no_cost = unit is None
    unit = unit if unit is not None else ZERO
    if state.qty > 0 and qty <= state.qty:
        value = state.value if qty == state.qty else fils(qty * state.value / state.qty)
    elif state.qty > 0:
        value = state.value + fils((qty - state.qty) * unit)  # all that is in stock, the rest below zero
    else:
        value = fils(qty * unit)
    new_qty = state.qty - qty
    new_value = ZERO if new_qty == 0 else state.value - value
    new = replace(state, qty=new_qty, value=new_value, avg=unit if not no_cost else state.avg)
    return Outbound(value, unit, new, no_cost, new_qty < 0)


def split(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """``total`` in fils shared in proportion to ``weights``; the last share takes the rounding remainder."""
    if not weights or any(w <= 0 for w in weights):
        raise ValueError("weights are above zero")
    whole = sum(weights, ZERO)
    shares = [fils(total * w / whole) for w in weights[:-1]]
    return [*shares, total - sum(shares, ZERO)]
