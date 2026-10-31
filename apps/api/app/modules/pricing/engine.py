"""The pricing engine (M1.3, decision A9). A pure function: no database, Decimal only.

Order — the first level that has a price wins, promotions never stack:

1. last price sold to this customer for this variant (same unit, or converted from another unit and flagged)
2. the customer's price list
3. the customer group's price list
4. a quantity break on the business's standard price list
5. a promotion (percent off, amount off or a fixed price, on the base price)
6. the base price of the unit (``product_unit.sale_price``)

Then the minimum-price guard: a price below ``product_unit.min_price`` is flagged, and posting it needs
``price.override_min``. Prices are VAT-exclusive (VAT modes arrive with tax, M1.10).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

PRICE_PLACES = Decimal("0.0001")  # NUMERIC(19,4)


class PriceSource(StrEnum):
    LAST_PRICE = "last_price"
    CUSTOMER_LIST = "customer_list"
    GROUP_LIST = "group_list"
    QUANTITY_BREAK = "quantity_break"
    PROMOTION = "promotion"
    BASE = "base"
    NONE = "none"  # nothing found: the salesperson types the price


class PromotionKind(StrEnum):
    PERCENT = "percent"
    AMOUNT = "amount"
    FIXED_PRICE = "fixed_price"


@dataclass(frozen=True)
class Break:
    """One price-list price for this variant and unit, from ``min_qty`` upwards."""

    min_qty: Decimal
    price: Decimal


@dataclass(frozen=True)
class ListPrices:
    name: str
    breaks: Sequence[Break]  # already filtered to the variant, the unit and lists valid on the date


@dataclass(frozen=True)
class LastSale:
    price: Decimal  # unit price on the last posted invoice line
    unit: str
    factor_to_base: Decimal  # of that line's unit
    document_number: str
    document_date: date
    price_mode: str = "vat_exclusive"  # the mode that invoice's prices were typed in (M1.10d)


@dataclass(frozen=True)
class Promotion:
    name: str
    kind: PromotionKind
    value: Decimal
    min_qty: Decimal = Decimal(0)


@dataclass(frozen=True)
class QuoteInput:
    qty: Decimal
    unit: str
    factor_to_base: Decimal  # of the requested unit
    base_price: Decimal | None = None  # product_unit.sale_price of the requested unit
    min_price: Decimal | None = None  # product_unit.min_price of the requested unit
    last_sale: LastSale | None = None
    customer_list: ListPrices | None = None
    group_list: ListPrices | None = None
    standard_list: ListPrices | None = None
    promotions: Sequence[Promotion] = ()  # active on the date and matching the item, newest first
    may_override_min: bool = False


@dataclass(frozen=True)
class Quote:
    price: Decimal | None
    source: PriceSource
    source_name: str  # invoice number, list or promotion name; "" for base and none
    converted: bool  # a last price from another unit, converted by the unit factors
    min_price: Decimal | None
    below_min: bool
    may_go_below_min: bool

    @property
    def price_source(self) -> str:
        """What the document line stores, e.g. ``last_price:INV-2026-00412`` or ``base``."""
        return f"{self.source.value}:{self.source_name}" if self.source_name else self.source.value


def _money(value: Decimal) -> Decimal:
    return value.quantize(PRICE_PLACES, rounding=ROUND_HALF_UP)


def _best_break(prices: ListPrices | None, qty: Decimal) -> Decimal | None:
    """Inside a list, the break with the highest ``min_qty`` not above the quantity."""
    if prices is None:
        return None
    eligible = [b for b in prices.breaks if b.min_qty <= qty]
    return max(eligible, key=lambda b: b.min_qty).price if eligible else None


def _last_price(sale: LastSale, unit: str, factor_to_base: Decimal) -> tuple[Decimal, bool]:
    if sale.unit == unit:
        return sale.price, False
    # Price per base unit, then per requested unit: 120 per roll of 50 m → 2.40 per m.
    return _money(sale.price / sale.factor_to_base * factor_to_base), True


def _promoted(promotion: Promotion, base_price: Decimal | None) -> Decimal | None:
    if promotion.kind is PromotionKind.FIXED_PRICE:
        return promotion.value
    if base_price is None:
        return None  # a discount needs a price to discount
    if promotion.kind is PromotionKind.PERCENT:
        return _money(base_price * (Decimal(100) - promotion.value) / Decimal(100))
    return max(base_price - promotion.value, Decimal(0))


def _found(data: QuoteInput) -> tuple[Decimal | None, PriceSource, str, bool]:
    if data.last_sale is not None:
        last, converted = _last_price(data.last_sale, data.unit, data.factor_to_base)
        return last, PriceSource.LAST_PRICE, data.last_sale.document_number, converted
    for source, prices in (
        (PriceSource.CUSTOMER_LIST, data.customer_list),
        (PriceSource.GROUP_LIST, data.group_list),
        (PriceSource.QUANTITY_BREAK, data.standard_list),
    ):
        listed = _best_break(prices, data.qty)
        if listed is not None and prices is not None:
            return listed, source, prices.name, False
    for promotion in data.promotions:
        promoted = _promoted(promotion, data.base_price) if promotion.min_qty <= data.qty else None
        if promoted is not None:
            return promoted, PriceSource.PROMOTION, promotion.name, False
    if data.base_price is not None:
        return data.base_price, PriceSource.BASE, "", False
    return None, PriceSource.NONE, "", False


def quote(data: QuoteInput) -> Quote:
    price, source, name, converted = _found(data)
    below = price is not None and data.min_price is not None and price < data.min_price
    return Quote(
        price=price,
        source=source,
        source_name=name,
        converted=converted,
        min_price=data.min_price,
        below_min=below,
        may_go_below_min=data.may_override_min,
    )


def check_price(price: Decimal, min_price: Decimal | None, may_override_min: bool) -> bool:
    """For a typed price: True when it may be used (at or above the minimum, or overridden)."""
    return min_price is None or price >= min_price or may_override_min
