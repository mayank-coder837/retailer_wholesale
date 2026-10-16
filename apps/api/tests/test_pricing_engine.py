"""The pricing engine (M1.3, decision A9): each level wins when the ones above are empty; first match wins."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.modules.pricing.engine import (
    Break,
    LastSale,
    ListPrices,
    PriceSource,
    Promotion,
    PromotionKind,
    QuoteInput,
    check_price,
    quote,
)

D = Decimal
EVERYTHING = QuoteInput(
    qty=D(10),
    unit="m",
    factor_to_base=D(1),
    base_price=D("3.0000"),
    min_price=D("2.0000"),
    last_sale=LastSale(D("2.7500"), "m", D(1), "INV-2026-00412", date(2026, 10, 1)),
    customer_list=ListPrices("Al Noor special", [Break(D(0), D("2.6000"))]),
    group_list=ListPrices("Wholesale", [Break(D(0), D("2.8000"))]),
    standard_list=ListPrices("Standard", [Break(D(0), D("2.9500")), Break(D(100), D("2.5000"))]),
    promotions=[Promotion("Eid", PromotionKind.PERCENT, D(10))],
)


@pytest.mark.parametrize(
    ("emptied", "price", "source", "name"),
    [
        ([], "2.7500", PriceSource.LAST_PRICE, "INV-2026-00412"),
        (["last_sale"], "2.6000", PriceSource.CUSTOMER_LIST, "Al Noor special"),
        (["last_sale", "customer_list"], "2.8000", PriceSource.GROUP_LIST, "Wholesale"),
        (["last_sale", "customer_list", "group_list"], "2.9500", PriceSource.QUANTITY_BREAK, "Standard"),
        (
            ["last_sale", "customer_list", "group_list", "standard_list"],
            "2.7000",
            PriceSource.PROMOTION,
            "Eid",
        ),
        (
            ["last_sale", "customer_list", "group_list", "standard_list", "promotions"],
            "3.0000",
            PriceSource.BASE,
            "",
        ),
    ],
)
def test_each_level_wins_when_the_levels_above_are_empty(
    emptied: list[str], price: str, source: PriceSource, name: str
) -> None:
    empty = {f: ([] if f == "promotions" else None) for f in emptied}
    result = quote(replace(EVERYTHING, **empty))
    assert (result.price, result.source, result.source_name) == (D(price), source, name)


def test_nothing_found_leaves_the_price_to_the_salesperson() -> None:
    result = quote(QuoteInput(qty=D(1), unit="pcs", factor_to_base=D(1)))
    assert (result.price, result.source, result.price_source) == (None, PriceSource.NONE, "none")


def test_what_the_line_stores_says_where_the_price_came_from() -> None:
    assert quote(EVERYTHING).price_source == "last_price:INV-2026-00412"
    assert quote(replace(EVERYTHING, last_sale=None)).price_source == "customer_list:Al Noor special"
    base_only = QuoteInput(qty=D(1), unit="m", factor_to_base=D(1), base_price=D(3))
    assert quote(base_only).price_source == "base"


def test_a_last_price_in_another_unit_is_converted_and_flagged() -> None:
    by_the_roll = LastSale(D("120.0000"), "roll", D(50), "INV-2026-00390", date(2026, 9, 20))
    per_metre = quote(replace(EVERYTHING, last_sale=by_the_roll))
    assert (per_metre.price, per_metre.converted) == (D("2.4000"), True)  # 120 / 50 m

    last_in_metres = LastSale(D("2.4500"), "m", D(1), "INV-2026-00391", date(2026, 9, 21))
    per_roll = quote(replace(EVERYTHING, unit="roll", factor_to_base=D(50), last_sale=last_in_metres))
    assert (per_roll.price, per_roll.converted) == (D("122.5000"), True)
    assert quote(EVERYTHING).converted is False


def test_quantity_breaks_pick_the_highest_minimum_not_above_the_quantity() -> None:
    standard_only = replace(EVERYTHING, last_sale=None, customer_list=None, group_list=None)
    assert quote(replace(standard_only, qty=D("99.99"))).price == D("2.9500")
    assert quote(replace(standard_only, qty=D(100))).price == D("2.5000")
    no_break_reached = ListPrices("Standard", [Break(D(50), D("2.7000"))])
    assert quote(replace(standard_only, standard_list=no_break_reached)).source is PriceSource.PROMOTION


def test_promotions_never_stack_and_only_apply_from_their_minimum_quantity() -> None:
    promo_only = replace(EVERYTHING, last_sale=None, customer_list=None, group_list=None, standard_list=None)
    two = [
        Promotion("Big buyer", PromotionKind.AMOUNT, D("0.5000"), min_qty=D(50)),
        Promotion("Eid", PromotionKind.PERCENT, D(10)),
    ]
    assert quote(replace(promo_only, promotions=two)).source_name == "Eid", (
        "first one that applies, never both"
    )
    assert quote(replace(promo_only, qty=D(50), promotions=two)).price == D("2.5000")
    fixed = [Promotion("Clearance", PromotionKind.FIXED_PRICE, D("1.9900"))]
    assert quote(replace(promo_only, base_price=None, promotions=fixed)).price == D("1.9900")
    percent = [Promotion("Eid", PromotionKind.PERCENT, D(10))]
    assert quote(replace(promo_only, base_price=None, promotions=percent)).source is PriceSource.NONE


def test_the_minimum_price_guard_warns_and_says_who_may_go_below() -> None:
    cheap = replace(EVERYTHING, last_sale=LastSale(D("1.5000"), "m", D(1), "INV-1", date(2026, 1, 1)))
    salesperson = quote(cheap)
    manager = quote(replace(cheap, may_override_min=True))
    assert (salesperson.below_min, salesperson.may_go_below_min) == (True, False)
    assert manager.may_go_below_min is True
    assert quote(EVERYTHING).below_min is False
    assert check_price(D("1.99"), D(2), may_override_min=False) is False
    assert check_price(D("1.99"), D(2), may_override_min=True) is True
    assert check_price(D("2.00"), D(2), may_override_min=False) is True
    assert check_price(D("0.01"), None, may_override_min=False) is True


@given(
    price=st.decimals(min_value=D("0.0001"), max_value=D(10**6), places=4, allow_nan=False),
    sold_factor=st.sampled_from([D(1), D(12), D(24), D(50), D("0.9144")]),
    wanted_factor=st.sampled_from([D(1), D(12), D(24), D(50), D("0.9144")]),
)
def test_a_converted_last_price_is_the_same_price_per_base_unit(
    price: Decimal, sold_factor: Decimal, wanted_factor: Decimal
) -> None:
    sale = LastSale(price, "a", sold_factor, "INV", date(2026, 1, 1))
    result = quote(
        QuoteInput(
            qty=D(1),
            unit="a" if sold_factor == wanted_factor else "b",
            factor_to_base=wanted_factor,
            last_sale=sale,
        )
    )
    assert result.price is not None
    per_base_sold = price / sold_factor
    per_base_quoted = result.price / wanted_factor
    # Rounded once, to money precision (4 dp) per requested unit: never more than half a ten-thousandth off.
    assert abs(per_base_quoted - per_base_sold) * wanted_factor <= D("0.00005")
