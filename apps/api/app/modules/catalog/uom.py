"""The units of measure engine (M1.1, CLAUDE.md rules 2 and 6). Pure functions: no database, Decimal only.

Conversion happens once, at entry: a document line stores what was typed (``qty_entered`` in
``uom_entered``), the factor used and the result in the product's base unit. Stock and costing only ever
use the base quantity, and old lines keep their meaning even if a product's factor changes later.

**Conversions are exact, never rounded** (founder, 15 Oct 2026): 1 yd is stored as 0.9144 m, not 0.91 m. A
unit's ``decimals`` only limits what may be typed in that unit (no 1.5 pieces); the converted quantity keeps
every digit up to the 6 decimal places quantities are stored with (``NUMERIC(19,6)``). A conversion that
cannot be represented exactly in 6 decimal places is refused, never rounded.

Factors always point from a unit to the base (1 roll = 50 m → 50; 1 cm = 0.01 m → 0.01), so the common factors
are exact.
"""

from dataclasses import dataclass
from decimal import Decimal

PLACES = 6  # quantities, rates and factors: NUMERIC(19,6)
MAX_DECIMALS = 6


class UomError(ValueError):
    """A quantity or factor that cannot be converted; ``code`` is the stable error code."""

    code = "uom_invalid"


class TooManyDecimalsError(UomError):
    code = "uom_too_many_decimals"


class InvalidFactorError(UomError):
    code = "uom_invalid_factor"


class UnitsNotConvertibleError(UomError):
    code = "uom_not_convertible"


class InexactConversionError(UomError):
    """The exact result needs more than 6 decimal places: refused rather than rounded."""

    code = "uom_inexact"


@dataclass(frozen=True)
class UnitRef:
    """What the engine needs to know about a unit."""

    code: str
    family: str
    factor_to_family_base: Decimal
    decimals: int


@dataclass(frozen=True)
class LineQuantity:
    """The four quantity fields every document line stores (CLAUDE.md rule 6). ``qty_base`` is exact."""

    qty_entered: Decimal
    uom_entered: str
    factor_to_base: Decimal
    qty_base: Decimal


def places(value: Decimal) -> int:
    """Decimal places actually used: 2.50 → 1, 3 → 0, 0.125 → 3."""
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def _exact(value: Decimal, what: str) -> Decimal:
    """``value`` at 6 decimal places, only if that changes nothing."""
    if places(value) > PLACES:
        raise InexactConversionError(
            f"{what} is {value.normalize()}, which needs more than {PLACES} decimal places; "
            "it would have to be rounded, so it is refused"
        )
    return value.quantize(Decimal(1).scaleb(-PLACES))


def check_factor(factor: Decimal) -> Decimal:
    """A factor must be positive and fit 6 decimal places exactly (nothing silently cut off)."""
    if not factor.is_finite() or factor <= 0:
        raise InvalidFactorError(f"a unit factor must be greater than zero, not {factor}")
    if places(factor) > PLACES:
        raise InvalidFactorError(f"a unit factor has at most {PLACES} decimal places: {factor}")
    return factor.quantize(Decimal(1).scaleb(-PLACES))


def check_quantity(qty: Decimal, decimals: int, unit: str) -> Decimal:
    """A typed quantity may use at most its unit's decimal places (no 1.5 pieces)."""
    if not 0 <= decimals <= MAX_DECIMALS:
        raise UomError(f"decimals must be between 0 and {MAX_DECIMALS}, not {decimals}")
    if not qty.is_finite():
        raise UomError(f"not a quantity: {qty}")
    if places(qty) > decimals:
        raise TooManyDecimalsError(f"{unit} allows {decimals} decimal places; {qty} has more")
    return qty


def standard_factor(from_unit: UnitRef, to_unit: UnitRef) -> Decimal:
    """Exact factor between two units of one family, through the family's base.

    Refused when the ratio has no exact 6-place value (metres → yards is 1.0936132983…): choose the unit with
    the smaller factor as the base instead (yards → metres is exactly 0.9144).
    """
    if from_unit.family != to_unit.family:
        raise UnitsNotConvertibleError(
            f"{from_unit.code} ({from_unit.family}) and {to_unit.code} ({to_unit.family}) are different "
            "kinds of unit; set a product-specific factor instead (e.g. 1 roll = 50 m)"
        )
    ratio = check_factor(from_unit.factor_to_family_base) / check_factor(to_unit.factor_to_family_base)
    return _exact(ratio, f"1 {from_unit.code} in {to_unit.code}")


def to_base(
    qty_entered: Decimal,
    *,
    uom_entered: str,
    entered_decimals: int,
    factor_to_base: Decimal,
) -> LineQuantity:
    """Converts a quantity typed in any unit to the product's base unit, once, at entry, exactly."""
    qty = check_quantity(qty_entered, entered_decimals, uom_entered)
    factor = check_factor(factor_to_base)
    qty_base = _exact(qty * factor, f"{qty} {uom_entered} in the base unit")
    return LineQuantity(qty_entered=qty, uom_entered=uom_entered, factor_to_base=factor, qty_base=qty_base)


def from_base(qty_base: Decimal, *, factor_to_base: Decimal, unit: str = "the unit") -> Decimal:
    """A base quantity expressed in another unit (125 m of stock = 2.5 rolls), exactly or not at all."""
    factor = check_factor(factor_to_base)
    return _exact(qty_base / factor, f"{qty_base.normalize()} in {unit}")
