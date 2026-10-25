"""Typed settings (M0.4): every key, its type, default and the scopes it may be set at.

The effective value resolves user → branch → business → tenant → default. Industry packs set business-scope
defaults through the same keys.
"""

from dataclasses import dataclass
from typing import Any

from app.core.money import ROUND_OFF_STEPS, RoundOffMode, RoundOffPlace

SCOPES = ("tenant", "business", "branch", "user")
PRICE_MODES = ("vat_exclusive", "vat_inclusive", "no_vat")
ROUND_OFF_PLACES = {
    RoundOffPlace.INVOICE_TOTAL: "bill total",
    RoundOffPlace.RECEIPT: "payment receipt",
    RoundOffPlace.PAYMENT: "payment voucher",
    RoundOffPlace.TAX_LINE: "VAT of each line",
    RoundOffPlace.TAX_TOTAL: "total VAT",
}


@dataclass(frozen=True)
class SettingDef:
    type: type
    default: Any
    scopes: tuple[str, ...]
    choices: tuple[Any, ...] | None = None
    description: str = ""

    def validate(self, value: Any) -> Any:
        if self.type is bool and not isinstance(value, bool):
            raise ValueError("expected true or false")
        if self.type is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise ValueError("expected a whole number")
        if self.type is str and not isinstance(value, str):
            raise ValueError("expected text")
        if self.choices is not None and value not in self.choices:
            raise ValueError(f"expected one of {', '.join(map(str, self.choices))}")
        return value


SETTINGS: dict[str, SettingDef] = {
    "ui.language": SettingDef(str, "en", ("tenant", "user"), ("en", "ar"), "Interface language"),
    "sales.default_price_mode": SettingDef(
        str, "vat_exclusive", ("tenant", "business", "branch", "user"), PRICE_MODES, "Price entry mode (A10)"
    ),
    "vat.simplified_for_unregistered": SettingDef(
        bool,
        True,
        ("tenant", "business", "branch"),
        description="Invoices to customers without a TRN print as simplified tax invoices (M1.10, R7)",
    ),
    "documents.offer_released_numbers": SettingDef(
        bool, True, ("tenant", "business", "branch"), description="Offer cancelled numbers for reuse (A7)"
    ),
    "inventory.negative_stock_warning": SettingDef(
        bool,
        True,
        ("tenant", "business", "branch"),
        description="Warn when a sale takes stock below zero (A6)",
    ),
    "inventory.track_batch_default": SettingDef(
        bool, False, ("tenant", "business"), description="New products track batches"
    ),
    "inventory.track_expiry_default": SettingDef(
        bool, False, ("tenant", "business"), description="New products track expiry dates"
    ),
    "inventory.track_serial_default": SettingDef(
        bool, False, ("tenant", "business"), description="New products track serial / IMEI numbers"
    ),
    "catalog.default_sales_unit": SettingDef(
        str, "pcs", ("tenant", "business"), description="Unit suggested for new products"
    ),
    # Optional round-off, off by default (founder, 15 Oct 2026). Amounts are always in fils (2 dp); these
    # round further, and the difference posts to the round-off account. VAT round-off: open question R4.
    **{
        f"rounding.{place.value}.{part}": definition
        for place, label in ROUND_OFF_PLACES.items()
        for part, definition in (
            (
                "mode",
                SettingDef(
                    str,
                    RoundOffMode.OFF.value,
                    ("tenant", "business"),
                    tuple(m.value for m in RoundOffMode),
                    f"Round-off of the {label}: off, nearest, up or down",
                ),
            ),
            (
                "step",
                SettingDef(
                    str,
                    "1.00",
                    ("tenant", "business"),
                    ROUND_OFF_STEPS,
                    f"Round-off step of the {label} (AED)",
                ),
            ),
        )
    },
}
