"""The units of measure engine (M1.1): exact conversions at entry, Decimal only, rounding to unit decimals."""

from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from app.modules.catalog import uom
from app.modules.catalog.uom import UnitRef

M = UnitRef("m", "length", Decimal(1), 2)
CM = UnitRef("cm", "length", Decimal("0.01"), 1)
YD = UnitRef("yd", "length", Decimal("0.9144"), 2)
KM = UnitRef("km", "length", Decimal(1000), 3)
PCS = UnitRef("pcs", "count", Decimal(1), 0)


def line(qty: str, unit: str, decimals: int, factor: str, base_decimals: int) -> uom.LineQuantity:
    return uom.to_base(
        Decimal(qty),
        uom_entered=unit,
        entered_decimals=decimals,
        factor_to_base=Decimal(factor),
        base_decimals=base_decimals,
    )


# --- the trader's everyday conversions ---------------------------------------------------------------------


def test_a_roll_of_fifty_metres() -> None:
    result = line("2.5", "roll", 1, "50", 2)
    assert result.qty_base == Decimal("125.00")
    assert not result.rounded
    assert uom.from_base(result.qty_base, factor_to_base=Decimal(50), decimals=1) == Decimal("2.5")


def test_cartons_of_twenty_four_pieces() -> None:
    result = line("3", "carton", 0, "24", 0)
    assert (result.qty_entered, result.uom_entered, result.factor_to_base, result.qty_base) == (
        Decimal(3),
        "carton",
        Decimal("24.000000"),
        Decimal(72),
    )


def test_a_yard_in_metres_is_rounded_to_the_metre_decimals_and_flagged() -> None:
    result = line("1", "yd", 2, "0.9144", 2)
    assert result.qty_base == Decimal("0.91")
    assert result.rounded, "the screen must warn that 0.0044 m was rounded away"


def test_no_half_pieces() -> None:
    with pytest.raises(uom.TooManyDecimalsError):
        line("1.5", "pcs", 0, "1", 0)


@pytest.mark.parametrize("factor", ["0", "-24", "NaN", "Infinity", "0.0000001"])
def test_bad_factors_are_rejected(factor: str) -> None:
    with pytest.raises(uom.InvalidFactorError):
        line("1", "carton", 0, factor, 0)


def test_standard_factors_within_a_family_and_never_across() -> None:
    assert uom.standard_factor(KM, M) == Decimal("1000.000000")
    assert uom.standard_factor(CM, M) == Decimal("0.010000")
    assert uom.standard_factor(M, YD) == Decimal("1.093613")  # 1 / 0.9144, at 6 dp
    with pytest.raises(uom.UnitsNotConvertibleError):
        uom.standard_factor(M, PCS)


# --- properties --------------------------------------------------------------------------------------------

decimals_st = st.integers(min_value=0, max_value=6)
factors_st = st.decimals(min_value=Decimal("0.000001"), max_value=Decimal(100000), places=6, allow_nan=False)


@st.composite
def quantities(draw: st.DrawFn, decimals: int) -> Decimal:
    return draw(st.decimals(min_value=0, max_value=Decimal(10**6), places=decimals, allow_nan=False))


@given(data=st.data(), entered_decimals=decimals_st, factor=factors_st)
def test_round_trip_loses_nothing_when_no_rounding_was_needed(
    data: st.DataObject, entered_decimals: int, factor: Decimal
) -> None:
    qty = data.draw(quantities(entered_decimals))
    result = uom.to_base(
        qty, uom_entered="x", entered_decimals=entered_decimals, factor_to_base=factor, base_decimals=6
    )
    assume(not result.rounded)
    back = uom.from_base(result.qty_base, factor_to_base=factor, decimals=entered_decimals)
    assert back == qty


@given(data=st.data(), entered_decimals=decimals_st, base_decimals=decimals_st, factor=factors_st)
def test_base_quantity_never_has_more_than_the_base_unit_decimals(
    data: st.DataObject, entered_decimals: int, base_decimals: int, factor: Decimal
) -> None:
    qty = data.draw(quantities(entered_decimals))
    result = uom.to_base(
        qty,
        uom_entered="x",
        entered_decimals=entered_decimals,
        factor_to_base=factor,
        base_decimals=base_decimals,
    )
    assert uom.places(result.qty_base) <= base_decimals
    assert abs(result.qty_base - qty * result.factor_to_base) <= Decimal(1).scaleb(-base_decimals) / 2
    assert result.rounded == (result.qty_base != qty * result.factor_to_base)


@given(factor=st.decimals(max_value=Decimal(0), allow_nan=False, allow_infinity=False))
def test_a_factor_must_be_greater_than_zero(factor: Decimal) -> None:
    with pytest.raises(uom.InvalidFactorError):
        uom.check_factor(factor)


length_units = st.sampled_from(
    [
        UnitRef("mm", "length", Decimal("0.001"), 0),
        CM,
        M,
        KM,
        UnitRef("in", "length", Decimal("0.0254"), 2),
        UnitRef("ft", "length", Decimal("0.3048"), 2),
        YD,
    ]
)


@given(a=length_units, b=length_units)
def test_standard_factors_are_the_exact_ratio_to_six_decimal_places(a: UnitRef, b: UnitRef) -> None:
    """Conversions go through the family base, never by chaining two rounded factors."""
    exact = a.factor_to_family_base / b.factor_to_family_base  # 28 significant digits
    assert abs(uom.standard_factor(a, b) - exact) <= Decimal("0.0000005")
