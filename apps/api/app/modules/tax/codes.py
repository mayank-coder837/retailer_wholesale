"""Tax codes and line VAT, pure (M1.10; decisions A3 and A10; posting-rules.md rules 1, 8, 29 to 31).

The codes are a fixed registry per country, read from ``seeds/tax_codes/<country>.json``: businesses cannot
edit
rates. A line stores its code and the rate it was charged at, so a later rate change never rewrites history.
"""

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from functools import cache
from pathlib import Path

SEEDS = Path(__file__).resolve().parents[3] / "seeds" / "tax_codes"
FILS = Decimal("0.01")
ZERO = Decimal(0)


class Side(StrEnum):
    SALES = "sales"
    PURCHASE = "purchase"


class Treatment(StrEnum):
    OUTPUT = "output"  # VAT charged to the customer: Cr VAT output
    RECOVERABLE = "recoverable"  # VAT paid to the supplier and claimed back: Dr VAT input
    BLOCKED = "blocked"  # VAT paid and not recoverable: part of the expense (rule 8)
    REVERSE_CHARGE = (
        "reverse_charge"  # self-assessed: Dr VAT input / Cr VAT output, supplier owed the net (29, 30)
    )
    NONE = "none"  # zero rated, exempt, out of scope: no VAT line (rule 31)


class PriceMode(StrEnum):
    VAT_EXCLUSIVE = "vat_exclusive"
    VAT_INCLUSIVE = "vat_inclusive"
    NO_VAT = "no_vat"


class NoVatReason(StrEnum):
    ZERO_RATED = "zero_rated"
    EXEMPT = "exempt"
    OUT_OF_SCOPE = "out_of_scope"


NO_VAT_CODE = {NoVatReason.ZERO_RATED: "ZR", NoVatReason.EXEMPT: "EX", NoVatReason.OUT_OF_SCOPE: "OS"}
STANDARD = "SR"
NOT_REGISTERED = "OS"  # a business that is not VAT-registered charges no VAT


@dataclass(frozen=True, slots=True)
class TaxCode:
    code: str
    side: Side
    rate: Decimal
    treatment: Treatment
    name_en: str
    name_ar: str
    net_box: str | None  # VAT 201 box of the net amount (Box 1 splits by the branch's emirate)
    vat_box: str | None  # box of the VAT due
    recovered_box: str | None = None  # reverse charge: box of the VAT recovered (10)


@cache
def registry(country: str = "AE") -> dict[tuple[Side, str], TaxCode]:
    data = json.loads((SEEDS / f"{country.lower()}.json").read_text(encoding="utf-8"))
    return {
        (Side(c["side"]), c["code"]): TaxCode(
            code=c["code"],
            side=Side(c["side"]),
            rate=Decimal(c["rate"]),
            treatment=Treatment(c["treatment"]),
            name_en=c["name_en"],
            name_ar=c["name_ar"],
            net_box=c["net_box"],
            vat_box=c["vat_box"],
            recovered_box=c.get("recovered_box"),
        )
        for c in data["codes"]
    }


def get(side: Side, code: str, country: str = "AE") -> TaxCode | None:
    return registry(country).get((side, code))


def codes(side: Side, country: str = "AE") -> list[TaxCode]:
    return [c for (s, _), c in registry(country).items() if s is side]


def fils(amount: Decimal) -> Decimal:
    return amount.quantize(FILS, rounding=ROUND_HALF_UP)


def split(amount: Decimal, rate: Decimal, mode: PriceMode) -> tuple[Decimal, Decimal]:
    """A line's net and VAT from the amount typed (already in fils), VAT half-up to the fils (A3).

    Exclusive: the amount is the net. Inclusive (open question R6): VAT = amount * rate / (100 + rate), net =
    the
    rest, so net + VAT is exactly what was typed. No VAT: all net."""
    if mode is PriceMode.NO_VAT or rate == 0:
        return amount, ZERO
    if mode is PriceMode.VAT_INCLUSIVE:
        vat = fils(amount * rate / (100 + rate))
        return amount - vat, vat
    return amount, fils(amount * rate / 100)


def owed(net: Decimal, vat: Decimal, treatment: Treatment) -> Decimal:
    """What the line adds to the bill: the VAT is not paid to a supplier under the reverse charge."""
    return net if treatment is Treatment.REVERSE_CHARGE else net + vat


def legacy_code(side: Side, vat_rate: Decimal) -> str:
    """A line sent with only a VAT rate (clients before M1.10d): 5 is standard, 0 is zero rated on sales and
    out
    of scope on purchases."""
    if vat_rate:
        return STANDARD
    return "ZR" if side is Side.SALES else "OS"


def resolve(
    side: Side,
    *,
    registered: bool,
    mode: PriceMode,
    no_vat_reason: NoVatReason | None,
    typed: str | None,
    followed: str | None,
    party: str | None,
    product: str | None,
) -> str:
    """The code a line takes: a business that is not registered and the no-VAT mode decide it outright; then
    the
    code typed, the code of the line it follows, the party's override, the product's, and standard."""
    if side is Side.SALES and not registered:
        return NOT_REGISTERED
    if mode is PriceMode.NO_VAT:
        assert no_vat_reason is not None
        return NO_VAT_CODE[no_vat_reason]
    return typed or followed or party or product or STANDARD
