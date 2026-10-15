"""Money rounding: currency precision at settlement points; round-off only when a business turns it on."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core import money
from app.core.money import RoundOffMode, RoundOffPlace
from app.core.settings_registry import SETTINGS


def test_amounts_are_in_fils_and_nothing_else_by_default() -> None:
    assert money.to_currency(Decimal("10.3349")) == Decimal("10.33")
    assert money.to_currency(Decimal("10.335")) == Decimal("10.34")  # half away from zero
    assert money.to_currency(Decimal("1.2345"), "KWD") == Decimal("1.235")  # 3 decimal places
    off = money.round_off(Decimal("1234.37"), RoundOffMode.OFF, Decimal("1.00"))
    assert (off.amount, off.adjustment) == (Decimal("1234.37"), Decimal("0.00"))


@pytest.mark.parametrize(
    ("mode", "step", "expected", "adjustment"),
    [
        (RoundOffMode.NEAREST, "1.00", "1234.00", "-0.37"),
        (RoundOffMode.NEAREST, "0.50", "1234.50", "0.13"),
        (RoundOffMode.NEAREST, "0.25", "1234.25", "-0.12"),
        (RoundOffMode.NEAREST, "0.05", "1234.35", "-0.02"),
        (RoundOffMode.UP, "1.00", "1235.00", "0.63"),
        (RoundOffMode.DOWN, "0.50", "1234.00", "-0.37"),
    ],
)
def test_round_off_to_a_step(mode: RoundOffMode, step: str, expected: str, adjustment: str) -> None:
    result = money.round_off(Decimal("1234.37"), mode, Decimal(step))
    assert (result.amount, result.adjustment) == (Decimal(expected), Decimal(adjustment))


def test_every_round_off_place_has_its_own_settings_and_all_are_off_by_default() -> None:
    for place in RoundOffPlace:
        mode = SETTINGS[f"rounding.{place.value}.mode"]
        step = SETTINGS[f"rounding.{place.value}.step"]
        assert mode.default == "off", place
        assert set(mode.choices or ()) == {"off", "nearest", "up", "down"}
        assert step.choices == ("0.05", "0.25", "0.50", "1.00")
    assert {p.value for p in RoundOffPlace} == {
        "invoice_total",
        "receipt",
        "payment",
        "tax_line",
        "tax_total",
    }


amounts = st.decimals(min_value=Decimal(-(10**9)), max_value=Decimal(10**9), places=4, allow_nan=False)
steps = st.sampled_from([Decimal(s) for s in money.ROUND_OFF_STEPS])


@given(amount=amounts, step=steps, mode=st.sampled_from(list(RoundOffMode)))
def test_the_adjustment_always_explains_the_difference(
    amount: Decimal, step: Decimal, mode: RoundOffMode
) -> None:
    exact = money.to_currency(amount)
    result = money.round_off(amount, mode, step)
    assert result.amount - result.adjustment == exact, "nothing disappears: the round-off line carries it"
    if mode is RoundOffMode.OFF:
        assert result.adjustment == 0
        return
    assert result.amount % step == 0, "a multiple of the step"
    if mode is RoundOffMode.UP:
        assert 0 <= result.adjustment < step
    elif mode is RoundOffMode.DOWN:
        assert -step < result.adjustment <= 0
    else:
        assert abs(result.adjustment) <= step / 2
