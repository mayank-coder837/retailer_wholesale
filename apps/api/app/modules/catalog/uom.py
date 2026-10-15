"""The units of measure engine (M1.1, CLAUDE.md rules 2 and 6). Pure functions: no database, Decimal only.

Conversion happens once, at entry: a document line stores what was typed (``qty_entered`` in
``uom_entered``), the factor used and the result in the product's base unit. Stock and costing only ever
use the base quantity, and old lines keep their meaning even if a product's factor changes later.

Factors always point from a unit to the base (1 roll = 50 m → 50; 1 cm = 0.01 m → 0.01), so the common factors
are exact, and they are kept at 6 decimal places (``NUMERIC(19,6)``).
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

FACTOR_PLACES = 6
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


@dataclass(frozen=True)
class UnitRef:
    """What the engine needs to know about a unit."""

    code: str
    family: str
    factor_to_family_base: Decimal
    decimals: int


@dataclass(frozen=True)
class LineQuantity:
    """The four quantity fields every document line stores (CLAUDE.md rule 6)."""

    qty_entered: Decimal
    uom_entered: str
    factor_to_base: Decimal
    qty_base: Decimal
    rounded: bool  # True when rounding to the base unit's decimals changed the value: screens warn


def _quantum(decimals: int) -> Decimal:
    if not 0 <= decimals <= MAX_DECIMALS:
        raise UomError(f"decimals must be between 0 and {MAX_DECIMALS}, not {decimals}")
    return Decimal(1).scaleb(-decimals)


def places(value: Decimal) -> int:
    """Decimal places actually used: 2.50 → 1, 3 → 0, 0.125 → 3."""
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def check_factor(factor: Decimal) -> Decimal:
    """A factor must be positive and fit 6 decimal places exactly (nothing silently cut off)."""
    if not factor.is_finite() or factor <= 0:
        raise InvalidFactorError(f"a unit factor must be greater than zero, not {factor}")
    if places(factor) > FACTOR_PLACES:
        raise InvalidFactorError(f"a unit factor has at most {FACTOR_PLACES} decimal places: {factor}")
    return factor.quantize(_quantum(FACTOR_PLACES))


def check_quantity(qty: Decimal, decimals: int, unit: str) -> Decimal:
    """A quantity may use at most its unit's decimal places (no 1.5 pieces)."""
    if not qty.is_finite():
        raise UomError(f"not a quantity: {qty}")
    if places(qty) > decimals:
        raise TooManyDecimalsError(f"{unit} allows {decimals} decimal places; {qty} has more")
    return qty


def standard_factor(from_unit: UnitRef, to_unit: UnitRef) -> Decimal:
    """Factor between two units of one family, through the family's base, at 6 decimal places."""
    if from_unit.family != to_unit.family:
        raise UnitsNotConvertibleError(
            f"{from_unit.code} ({from_unit.family}) and {to_unit.code} ({to_unit.family}) are different "
            "kinds of unit; set a product-specific factor instead (e.g. 1 roll = 50 m)"
        )
    ratio = check_factor(from_unit.factor_to_family_base) / check_factor(to_unit.factor_to_family_base)
    return ratio.quantize(_quantum(FACTOR_PLACES), rounding=ROUND_HALF_UP)


def to_base(
    qty_entered: Decimal,
    *,
    uom_entered: str,
    entered_decimals: int,
    factor_to_base: Decimal,
    base_decimals: int,
) -> LineQuantity:
    """Converts a quantity typed in any unit to the product's base unit, once, at entry."""
    qty = check_quantity(qty_entered, entered_decimals, uom_entered)
    factor = check_factor(factor_to_base)
    exact = qty * factor
    qty_base = exact.quantize(_quantum(base_decimals), rounding=ROUND_HALF_UP)
    return LineQuantity(
        qty_entered=qty,
        uom_entered=uom_entered,
        factor_to_base=factor,
        qty_base=qty_base,
        rounded=qty_base != exact,
    )


def from_base(qty_base: Decimal, *, factor_to_base: Decimal, decimals: int) -> Decimal:
    """A base quantity shown in another unit (stock of 125 m = 2.5 rolls), rounded to that unit's decimals."""
    factor = check_factor(factor_to_base)
    return (qty_base / factor).quantize(_quantum(decimals), rounding=ROUND_HALF_UP)
