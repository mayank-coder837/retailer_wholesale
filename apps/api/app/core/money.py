"""Money rounding (CLAUDE.md rule 2: Decimal only, round only at defined points with the currency's rules).

Two kinds of rounding, both explicit:

1. **Currency precision** — an amount that is settled (a bill total, a receipt, a payment, a VAT amount) is
   in the currency's smallest unit: AED has 2 decimal places (fils). Always applied at those points.
2. **Round-off** — optional, off by default (founder, 15 Oct 2026): a business may round the bill total,
   receipt, payment, line VAT or total VAT further, to a step (0.05, 0.25, 0.50 or 1.00) and a direction. The
   difference is returned as an adjustment so it posts to a round-off account and never disappears.

Quantities are never rounded (M1.1, units engine).
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from enum import StrEnum

CURRENCY_DECIMALS = {"AED": 2, "SAR": 2, "OMR": 3, "KWD": 3, "BHD": 3, "QAR": 2, "USD": 2, "EUR": 2, "INR": 2}
ROUND_OFF_STEPS = ("0.05", "0.25", "0.50", "1.00")


class RoundOffMode(StrEnum):
    OFF = "off"
    NEAREST = "nearest"
    UP = "up"
    DOWN = "down"


class RoundOffPlace(StrEnum):
    """Where round-off may apply; each has its own setting (``rounding.<place>.mode`` / ``.step``)."""

    INVOICE_TOTAL = "invoice_total"
    RECEIPT = "receipt"
    PAYMENT = "payment"
    TAX_LINE = "tax_line"
    TAX_TOTAL = "tax_total"


_ROUNDING = {
    RoundOffMode.NEAREST: ROUND_HALF_UP,
    RoundOffMode.UP: ROUND_CEILING,
    RoundOffMode.DOWN: ROUND_FLOOR,
}


@dataclass(frozen=True)
class Rounded:
    amount: Decimal  # what the document shows and posts
    adjustment: Decimal  # amount - original: posts to the round-off account (0 when nothing changed)


def currency_decimals(currency: str) -> int:
    try:
        return CURRENCY_DECIMALS[currency]
    except KeyError:
        raise ValueError(f"unknown currency {currency}") from None


def to_currency(amount: Decimal, currency: str = "AED") -> Decimal:
    """An amount in the currency's smallest unit (AED: fils, 2 decimal places), half away from zero."""
    if not amount.is_finite():
        raise ValueError(f"not an amount: {amount}")
    return amount.quantize(Decimal(1).scaleb(-currency_decimals(currency)), rounding=ROUND_HALF_UP)


def round_off(amount: Decimal, mode: RoundOffMode, step: Decimal, currency: str = "AED") -> Rounded:
    """Currency precision, then the optional round-off to a multiple of ``step``.

    Up and down follow the number line (up never lowers an amount), so a credit note rounds the same way as an
    invoice of the same size with the opposite sign.
    """
    exact = to_currency(amount, currency)
    if mode is RoundOffMode.OFF:
        return Rounded(exact, Decimal(0).quantize(exact))
    if not step.is_finite() or step <= 0:
        raise ValueError(f"a round-off step must be greater than zero, not {step}")
    rounded = ((exact / step).quantize(Decimal(1), rounding=_ROUNDING[mode]) * step).quantize(exact)
    return Rounded(rounded, rounded - exact)
