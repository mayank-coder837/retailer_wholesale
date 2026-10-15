"""The units of measure engine (M1.1): exact conversions at entry, Decimal only, never rounded."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.modules.catalog import uom
from app.modules.catalog.uom import UnitRef

M = UnitRef("m", "length", Decimal(1), 2)
CM = UnitRef("cm", "length", Decimal("0.01"), 1)
YD = UnitRef("yd", "length", Decimal("0.9144"), 2)
KM = UnitRef("km", "length", Decimal(1000), 3)
PCS = UnitRef("pcs", "count", Decimal(1), 0)


def line(qty: str, unit: str, decimals: int, factor: str) -> uom.LineQuantity:
    return uom.to_base(
        Decimal(qty), uom_entered=unit, entered_decimals=decimals, factor_to_base=Decimal(factor)
    )


# --- the trader's everyday conversions ---------------------------------------------------------------------


def test_a_roll_of_fifty_metres() -> None:
    result = line("2.5", "roll", 1, "50")
    assert result.qty_base == Decimal("125")
    assert uom.from_base(result.qty_base, factor_to_base=Decimal(50)) == Decimal("2.5")


def test_cartons_of_twenty_four_pieces() -> None:
    result = line("3", "carton", 0, "24")
    assert (result.qty_entered, result.uom_entered, result.factor_to_base, result.qty_base) == (
        Decimal(3),
        "carton",
        Decimal("24.000000"),
        Decimal(72),
    )


def test_a_yard_is_exactly_0_9144_metres_never_rounded() -> None:
    assert line("1", "yd", 2, "0.9144").qty_base == Decimal("0.9144")
    assert line("12.75", "yd", 2, "0.9144").qty_base == Decimal("11.6586")  # exact, 4 decimal places


def test_a_conversion_that_would_need_rounding_is_refused() -> None:
    with pytest.raises(uom.InexactConversionError):
        line("0.001", "yd", 3, "0.9144")  # 0.0009144 m needs 7 decimal places
    with pytest.raises(uom.InexactConversionError):
        uom.from_base(Decimal(1), factor_to_base=Decimal("0.9144"), unit="yd")  # 1 m = 1.0936132… yd


def test_no_half_pieces() -> None:
    with pytest.raises(uom.TooManyDecimalsError):
        line("1.5", "pcs", 0, "1")


@pytest.mark.parametrize("factor", ["0", "-24", "NaN", "Infinity", "0.0000001"])
def test_bad_factors_are_rejected(factor: str) -> None:
    with pytest.raises(uom.InvalidFactorError):
        line("1", "carton", 0, factor)


def test_standard_factors_are_exact_or_refused() -> None:
    assert uom.standard_factor(KM, M) == Decimal("1000.000000")
    assert uom.standard_factor(CM, M) == Decimal("0.010000")
    assert uom.standard_factor(YD, M) == Decimal("0.914400")
    with pytest.raises(uom.InexactConversionError):
        uom.standard_factor(M, YD)  # 1 / 0.9144 has no exact 6-place value
    with pytest.raises(uom.UnitsNotConvertibleError):
        uom.standard_factor(M, PCS)


# --- properties --------------------------------------------------------------------------------------------

decimals_st = st.integers(min_value=0, max_value=6)
factors_st = st.decimals(min_value=Decimal("0.000001"), max_value=Decimal(100000), places=6, allow_nan=False)


@st.composite
def quantities(draw: st.DrawFn, decimals: int) -> Decimal:
    return draw(st.decimals(min_value=0, max_value=Decimal(10**6), places=decimals, allow_nan=False))


@given(data=st.data(), entered_decimals=decimals_st, factor=factors_st)
def test_the_base_quantity_is_exact_or_the_conversion_is_refused(
    data: st.DataObject, entered_decimals: int, factor: Decimal
) -> None:
    qty = data.draw(quantities(entered_decimals))
    try:
        result = uom.to_base(qty, uom_entered="x", entered_decimals=entered_decimals, factor_to_base=factor)
    except uom.InexactConversionError:
        assert uom.places(qty * factor) > uom.PLACES, "refused only when 6 decimal places cannot hold it"
        return
    assert result.qty_base == qty * factor, "never rounded"
    assert uom.places(result.qty_base) <= uom.PLACES


@given(data=st.data(), entered_decimals=decimals_st, factor=factors_st)
def test_round_trip_always_gives_back_what_was_typed(
    data: st.DataObject, entered_decimals: int, factor: Decimal
) -> None:
    qty = data.draw(quantities(entered_decimals))
    try:
        result = uom.to_base(qty, uom_entered="x", entered_decimals=entered_decimals, factor_to_base=factor)
    except uom.InexactConversionError:
        return
    assert uom.from_base(result.qty_base, factor_to_base=factor) == qty


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
def test_standard_factors_are_the_exact_ratio_or_refused(a: UnitRef, b: UnitRef) -> None:
    exact = a.factor_to_family_base / b.factor_to_family_base  # 28 significant digits
    try:
        assert uom.standard_factor(a, b) == exact
    except uom.InexactConversionError:
        assert uom.places(exact) > uom.PLACES
