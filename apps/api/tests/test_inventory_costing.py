"""Moving-average costing, pure (ADR 0006; posting-rules.md rules 23 to 27)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.modules.inventory.costing import State, inbound, outbound, split

D = Decimal


def test_inbound_moves_the_average_and_outbound_leaves_at_it() -> None:
    first = inbound(State(), D(10), D(15))
    second = inbound(first.state, D(10), D(18))
    assert (second.state.qty, second.state.value, second.state.avg) == (D(20), D(330), D("16.500000"))
    out = outbound(second.state, D(4))
    assert (out.value, out.unit_cost) == (D("66.00"), D("16.500000"))
    assert out.state.value == D(264)
    assert out.state.avg == D("16.500000")  # an issue never moves the average


def test_issuing_everything_takes_the_whole_value() -> None:
    """Three units worth 10.00 have an average of 3.333333; the last one must take what is left."""
    state = inbound(State(), D(3), D(0), value=D("10.00")).state
    a = outbound(state, D(1))
    b = outbound(a.state, D(2))
    assert (a.value, b.value) == (D("3.33"), D("6.67"))
    assert (b.state.qty, b.state.value) == (D(0), D(0))


def test_rule_27_negative_stock_is_revalued_at_the_incoming_cost() -> None:
    """10 pcs sold with none in stock at the last purchase cost 60; then 20 arrive at 65."""
    sold = outbound(State(last_purchase=D(60)), D(10))
    assert (sold.value, sold.negative, sold.no_cost) == (D(600), True, False)
    assert (sold.state.qty, sold.state.value) == (D(-10), D(-600))
    arrived = inbound(sold.state, D(20), D(65), purchase=True)
    assert arrived.value == D(1300)
    assert arrived.revaluation == D(-50)  # Dr stock cost adjustment 50 / Cr inventory 50
    assert (arrived.state.qty, arrived.state.value, arrived.state.avg) == (D(10), D(650), D("65.000000"))


def test_cheaper_stock_arriving_revalues_the_other_way() -> None:
    sold = outbound(State(last_purchase=D(60)), D(10))
    arrived = inbound(sold.state, D(10), D(55))
    assert arrived.revaluation == D(50)  # Dr inventory 50 / Cr stock cost adjustment 50
    assert (arrived.state.qty, arrived.state.value) == (D(0), D(0))


def test_partly_covered_negative_stock_keeps_the_rest_at_its_old_cost() -> None:
    sold = outbound(State(last_purchase=D(60)), D(10))
    arrived = inbound(sold.state, D(4), D(65))
    assert arrived.revaluation == D(-20)
    assert (arrived.state.qty, arrived.state.value) == (D(-6), D(-360))


def test_an_issue_with_nothing_to_cost_it_at_goes_out_at_zero_with_a_warning() -> None:
    out = outbound(State(), D(5))
    assert (out.value, out.no_cost, out.negative) == (D(0), True, True)
    later = inbound(out.state, D(5), D(12))
    assert later.revaluation == D(-60)  # the five units are now known to cost 12 each
    assert (later.state.qty, later.state.value) == (D(0), D(0))


def test_split_keeps_every_fils() -> None:
    assert split(D("100.00"), [D(1), D(1), D(1)]) == [D("33.33"), D("33.33"), D("33.34")]
    with pytest.raises(ValueError, match="above zero"):
        split(D(1), [D(0)])


quantities = st.decimals(min_value=D("0.001"), max_value=D(500), places=3)
costs = st.decimals(min_value=D(0), max_value=D(999), places=2)


@given(st.lists(st.tuples(st.booleans(), quantities, costs), min_size=1, max_size=30))
def test_the_pool_value_is_what_came_in_minus_what_went_out_plus_revaluations(
    moves: list[tuple[bool, Decimal, Decimal]],
) -> None:
    state = State(last_purchase=D(10))
    total = D(0)
    for is_in, qty, unit in moves:
        if is_in:
            r = inbound(state, qty, unit)
            total += r.value + r.revaluation
            state = r.state
        else:
            o = outbound(state, qty)
            total -= o.value
            state = o.state
        assert state.value == total
        assert state.value == state.value.quantize(D("0.01"))  # always whole fils
        if state.qty == 0:
            assert state.value == 0
        if state.qty > 0:
            assert state.value >= 0
