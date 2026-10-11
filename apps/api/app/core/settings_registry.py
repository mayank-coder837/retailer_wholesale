"""Typed settings (M0.4): every key, its type, default and the scopes it may be set at.

The effective value resolves user → branch → business → tenant → default. Industry packs set business-scope
defaults through the same keys.
"""

from dataclasses import dataclass
from typing import Any

SCOPES = ("tenant", "business", "branch", "user")
PRICE_MODES = ("vat_exclusive", "vat_inclusive", "no_vat")


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
}
