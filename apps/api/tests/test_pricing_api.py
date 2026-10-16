"""Pricing API (M1.3): unit prices, price lists with quantity breaks, promotions, and the quote end to end."""

from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer, signup
from tests.test_roles_api import add_member, login, make_shop


async def ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


async def shop_with_fabric(client: AsyncClient, email: str = "prices@shop.ae") -> dict[str, Any]:
    owner = await signup(client, email)
    h = bearer(owner)
    business_id = (await client.get("/businesses", headers=h)).json()[0]["id"]
    fabric = await ok(
        await client.post(
            "/products",
            json={
                "sku": "FAB-1",
                "name_en": "Poplin",
                "name_ar": "بوبلين",
                "base_unit": "m",
                "units": [{"unit": "roll", "factor_to_base": "50"}],
            },
            headers=h,
        ),
        201,
    )
    return {
        "owner": owner,
        "h": h,
        "business_id": business_id,
        "fabric": fabric,
        "variant": fabric["variants"][0]["id"],
    }


async def customer_on(
    client: AsyncClient, shop: dict[str, Any], price_list_id: str, code: str = "C-1"
) -> str:
    """A customer whose terms in the shop's business use this price list (pricing step 2)."""
    party = await ok(
        await client.post(
            "/parties",
            json={"code": code, "name_en": "Al Noor", "name_ar": "النور", "is_customer": True},
            headers=shop["h"],
        ),
        201,
    )
    await ok(
        await client.put(
            f"/parties/{party['id']}/customer-profile",
            json={"business_id": shop["business_id"], "price_list_id": price_list_id},
            headers=shop["h"],
        )
    )
    party_id: str = party["id"]
    return party_id


async def quote(client: AsyncClient, shop: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body = {
        "business_id": shop["business_id"],
        "variant_id": shop["variant"],
        "unit": "m",
        "qty": "10",
    } | extra
    result: dict[str, Any] = await ok(await client.post("/pricing/quote", json=body, headers=shop["h"]))
    return result


async def test_quote_walks_the_levels_base_promotion_break_customer_list(client: AsyncClient) -> None:
    shop = await shop_with_fabric(client)
    h, fabric = shop["h"], shop["fabric"]

    nothing = await quote(client, shop)
    assert (nothing["price"], nothing["price_source"]) == (None, "none")

    await ok(
        await client.patch(
            f"/products/{fabric['id']}/units/m/prices",
            json={"sale_price": "3.0000", "min_price": "2.0000"},
            headers=h,
        )
    )
    base = await quote(client, shop)
    assert (base["price"], base["price_source"], base["min_price"]) == ("3.0000", "base", "2.0000")

    await ok(
        await client.post(
            "/promotions",
            json={
                "business_id": shop["business_id"],
                "name_en": "Eid",
                "name_ar": "العيد",
                "kind": "percent",
                "value": "10",
                "scope": "product",
                "scope_id": fabric["id"],
            },
            headers=h,
        ),
        201,
    )
    promo = await quote(client, shop)
    assert (promo["price"], promo["price_source"]) == ("2.7000", "promotion:Eid")

    standard = await ok(
        await client.post(
            "/price-lists",
            json={
                "business_id": shop["business_id"],
                "name_en": "Standard",
                "name_ar": "القياسية",
                "is_standard": True,
            },
            headers=h,
        ),
        201,
    )
    for min_qty, price in (("0", "2.9000"), ("100", "2.5000")):
        await ok(
            await client.put(
                f"/price-lists/{standard['id']}/items",
                json={"variant_id": shop["variant"], "unit": "m", "min_qty": min_qty, "price": price},
                headers=h,
            )
        )
    assert (await quote(client, shop))["price_source"] == "quantity_break:Standard"
    assert (await quote(client, shop, qty="150"))["price"] == "2.5000"

    special = await ok(
        await client.post(
            "/price-lists",
            json={"business_id": shop["business_id"], "name_en": "Al Noor special", "name_ar": "خاص"},
            headers=h,
        ),
        201,
    )
    await ok(
        await client.put(
            f"/price-lists/{special['id']}/items",
            json={"variant_id": shop["variant"], "unit": "m", "price": "1.5000"},
            headers=h,
        )
    )
    customer = await quote(client, shop, party_id=await customer_on(client, shop, special["id"]))
    assert (customer["price"], customer["price_source"]) == ("1.5000", "customer_list:Al Noor special")
    assert customer["below_min"] is True, "1.50 is below the 2.00 minimum"
    assert customer["may_go_below_min"] is True, "the owner may override"


async def test_prices_are_per_unit_and_a_list_price_needs_one_of_the_products_units(
    client: AsyncClient,
) -> None:
    shop = await shop_with_fabric(client, "units-prices@shop.ae")
    h, fabric = shop["h"], shop["fabric"]
    await ok(
        await client.patch(
            f"/products/{fabric['id']}/units/roll/prices", json={"sale_price": "140.0000"}, headers=h
        )
    )

    by_roll = await quote(client, shop, unit="roll", qty="2")
    by_metre = await quote(client, shop)
    plist = await ok(
        await client.post(
            "/price-lists",
            json={"business_id": shop["business_id"], "name_en": "L", "name_ar": "ق"},
            headers=h,
        ),
        201,
    )
    wrong_unit = await client.put(
        f"/price-lists/{plist['id']}/items",
        json={"variant_id": shop["variant"], "unit": "kg", "price": "1"},
        headers=h,
    )
    not_a_unit = await client.post(
        "/pricing/quote",
        json={"business_id": shop["business_id"], "variant_id": shop["variant"], "unit": "kg", "qty": "1"},
        headers=h,
    )

    assert (by_roll["price"], by_roll["price_source"]) == ("140.0000", "base")
    assert by_metre["price"] is None, "the roll's price is not silently divided into metres"
    assert wrong_unit.status_code == 422
    assert not_a_unit.status_code == 422
    assert wrong_unit.json()["code"] == "invalid_pricing"


async def test_bad_prices_and_lists_are_refused(client: AsyncClient) -> None:
    shop = await shop_with_fabric(client, "bad@shop.ae")
    h, fabric = shop["h"], shop["fabric"]

    min_above_sale = await client.patch(
        f"/products/{fabric['id']}/units/m/prices", json={"sale_price": "2", "min_price": "3"}, headers=h
    )
    negative = await client.patch(
        f"/products/{fabric['id']}/units/m/prices", json={"sale_price": "-1"}, headers=h
    )
    first = {"business_id": shop["business_id"], "name_en": "Std", "name_ar": "ق", "is_standard": True}
    await ok(await client.post("/price-lists", json=first, headers=h), 201)
    second_standard = await client.post("/price-lists", json=first | {"name_en": "Std2"}, headers=h)
    backwards = await client.post(
        "/price-lists",
        json=first
        | {"name_en": "X", "is_standard": False, "valid_from": "2026-10-10", "valid_to": "2026-10-01"},
        headers=h,
    )
    too_much = await client.post(
        "/promotions",
        json={
            "business_id": shop["business_id"],
            "name_en": "Free",
            "name_ar": "مجاني",
            "kind": "percent",
            "value": "150",
            "scope": "product",
            "scope_id": fabric["id"],
        },
        headers=h,
    )

    assert min_above_sale.status_code == 422
    assert negative.status_code == 422
    assert second_standard.status_code == 409, "one standard list per business"
    assert backwards.status_code == 422
    assert too_much.status_code == 422


async def test_expired_promotions_and_lists_are_ignored(client: AsyncClient) -> None:
    shop = await shop_with_fabric(client, "dates@shop.ae")
    h, fabric = shop["h"], shop["fabric"]
    await ok(
        await client.patch(
            f"/products/{fabric['id']}/units/m/prices", json={"sale_price": "3.0000"}, headers=h
        )
    )
    await ok(
        await client.post(
            "/promotions",
            json={
                "business_id": shop["business_id"],
                "name_en": "Summer",
                "name_ar": "الصيف",
                "kind": "fixed_price",
                "value": "1.0000",
                "scope": "variant",
                "scope_id": shop["variant"],
                "valid_from": "2026-06-01",
                "valid_to": "2026-08-31",
            },
            headers=h,
        ),
        201,
    )

    during = await quote(client, shop, date="2026-07-15")
    after = await quote(client, shop, date="2026-10-16")

    assert (during["price"], during["price_source"]) == ("1.0000", "promotion:Summer")
    assert after["price_source"] == "base"


async def test_a_salesperson_may_quote_but_not_change_prices_or_go_below_the_minimum(
    client: AsyncClient,
) -> None:
    shop = await make_shop(client)
    owner = bearer(shop.owner)
    business_id = (await client.get("/businesses", headers=owner)).json()[0]["id"]
    product = await ok(
        await client.post(
            "/products",
            json={"sku": "S-1", "name_en": "Soap", "name_ar": "صابون", "base_unit": "pcs"},
            headers=owner,
        ),
        201,
    )
    await ok(
        await client.patch(
            f"/products/{product['id']}/units/pcs/prices",
            json={"sale_price": "5", "min_price": "4"},
            headers=owner,
        )
    )
    await add_member(client, shop, "sales@shop.ae", "salesperson")
    sales = bearer(await login(client, "sales@shop.ae"))

    quoted = await client.post(
        "/pricing/quote",
        json={
            "business_id": business_id,
            "variant_id": product["variants"][0]["id"],
            "unit": "pcs",
            "qty": "1",
        },
        headers=sales,
    )
    edit = await client.patch(
        f"/products/{product['id']}/units/pcs/prices", json={"sale_price": "1"}, headers=sales
    )
    new_list = await client.post(
        "/price-lists", json={"business_id": business_id, "name_en": "Mine", "name_ar": "لي"}, headers=sales
    )

    assert quoted.status_code == 200
    assert quoted.json()["price"] == "5.0000"
    assert quoted.json()["may_go_below_min"] is False, "a salesperson cannot sell below the minimum"
    assert edit.status_code == 403
    assert new_list.status_code == 403


async def test_price_lists_belong_to_one_business_and_one_tenant(client: AsyncClient) -> None:
    first = await shop_with_fabric(client, "one@shop.ae")
    second = await shop_with_fabric(client, "two@shop.ae")
    plist = await ok(
        await client.post(
            "/price-lists",
            json={"business_id": first["business_id"], "name_en": "Secret", "name_ar": "سري"},
            headers=first["h"],
        ),
        201,
    )

    seen = await client.get("/price-lists", headers=second["h"])
    party = await ok(
        await client.post(
            "/parties",
            json={"name_en": "Buyer", "name_ar": "مشتري", "is_customer": True},
            headers=second["h"],
        ),
        201,
    )
    used = await client.put(
        f"/parties/{party['id']}/customer-profile",
        json={"business_id": second["business_id"], "price_list_id": plist["id"]},
        headers=second["h"],
    )

    assert seen.json() == []
    assert used.status_code == 404, "another tenant's list does not exist for this tenant"
