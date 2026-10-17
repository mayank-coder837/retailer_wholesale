"""Parties API (M1.4): customers and suppliers, codes and TRN, addresses, contacts, per-business terms,
customer groups and their price lists, the credit check, permissions and the credit override audit."""

from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.platform_models import AuditLog
from app.modules.parties import service as parties
from app.modules.parties.schemas import CreditCheckIn, CustomerProfileIn, PartyIn
from tests.api_helpers import bearer
from tests.factories import seed_tenant
from tests.test_pricing_api import ok, quote, shop_with_fabric
from tests.test_roles_api import add_member, login, make_shop

TRN = "100123456700003"


def party(name: str = "Al Noor Trading", **extra: Any) -> dict[str, Any]:
    return {"name_en": name, "name_ar": "النور", "is_customer": True} | extra


async def test_codes_are_numbered_per_role_and_trn_is_unique(client: AsyncClient) -> None:
    shop = await shop_with_fabric(client, "codes@shop.ae")
    h = shop["h"]

    first = await ok(await client.post("/parties", json=party(trn=TRN), headers=h), 201)
    second = await ok(await client.post("/parties", json=party("Gulf Fabrics"), headers=h), 201)
    supplier = await ok(
        await client.post("/parties", json=party("Mill", is_customer=False, is_supplier=True), headers=h), 201
    )
    both = await ok(await client.post("/parties", json=party("Both", is_supplier=True), headers=h), 201)
    same_trn = await client.post("/parties", json=party("Copy", trn=TRN), headers=h)
    same_code = await client.post("/parties", json=party("Copy", code=first["code"]), headers=h)
    bad_trn = await client.post("/parties", json=party(trn="12345"), headers=h)
    no_role = await client.post("/parties", json=party(is_customer=False), headers=h)

    assert [first["code"], second["code"], supplier["code"], both["code"]] == [
        "C0001",
        "C0002",
        "S0001",
        "P0001",
    ]
    assert (same_trn.status_code, same_trn.json()["code"]) == (409, "duplicate_party")
    assert same_code.status_code == 409
    assert bad_trn.status_code == 422
    assert no_role.status_code == 422


async def test_search_finds_by_name_code_trn_and_phone_and_filters_by_role(client: AsyncClient) -> None:
    shop = await shop_with_fabric(client, "search@shop.ae")
    h = shop["h"]
    await ok(await client.post("/parties", json=party(trn=TRN, phone="+971501234567"), headers=h), 201)
    await ok(
        await client.post("/parties", json=party("Mill", is_customer=False, is_supplier=True), headers=h), 201
    )

    async def names(**params: str) -> list[str]:
        return [p["name_en"] for p in await ok(await client.get("/parties", params=params, headers=h))]

    assert await names(q="noor") == ["Al Noor Trading"]
    assert await names(q=TRN) == ["Al Noor Trading"]
    assert await names(q="50123") == ["Al Noor Trading"]
    assert await names(q="C0001") == ["Al Noor Trading"]
    assert await names(role="supplier") == ["Mill"]
    assert await names() == ["Al Noor Trading", "Mill"]


async def test_addresses_need_an_emirate_in_the_uae_and_have_one_default_per_type(
    client: AsyncClient,
) -> None:
    shop = await shop_with_fabric(client, "addr@shop.ae")
    h = shop["h"]
    created = await ok(
        await client.post(
            "/parties",
            json=party(
                addresses=[{"line1": "Deira, shop 4", "emirate": "dubai", "is_default": True}],
                contacts=[{"name": "Rashid", "role": "Buyer", "phone": "+971501111111"}],
            ),
            headers=h,
        ),
        201,
    )
    pid = created["id"]
    no_emirate = await client.post(f"/parties/{pid}/addresses", json={"line1": "Sharjah"}, headers=h)
    abroad = await ok(
        await client.post(
            f"/parties/{pid}/addresses",
            json={"line1": "Muscat", "country": "OM", "is_default": True},
            headers=h,
        ),
        201,
    )
    defaults = [a["line1"] for a in abroad["addresses"] if a["is_default"]]
    contact_id = created["contacts"][0]["id"]
    after = await ok(await client.delete(f"/parties/{pid}/contacts/{contact_id}", headers=h))

    assert no_emirate.status_code == 422
    assert defaults == ["Muscat"], "a new default billing address replaces the old one"
    assert after["contacts"] == []


async def test_terms_are_per_business_and_credit_terms_need_credit_limit(client: AsyncClient) -> None:
    shop = await make_shop(client, "terms@shop.ae")
    h = bearer(shop.owner)
    await add_member(client, shop, "sales@shop.ae", "salesperson", business=shop.business_id)
    sales = bearer(await login(client, "sales@shop.ae"))
    customer = await ok(await client.post("/parties", json=party(), headers=sales), 201)
    supplier_by_sales = await client.post(
        "/parties", json=party("Mill", is_customer=False, is_supplier=True), headers=sales
    )

    pid = customer["id"]
    terms = {
        "business_id": shop.business_id,
        "credit_limit": "10000",
        "credit_days": 30,
        "credit_policy": "block",
    }
    by_sales = await client.put(f"/parties/{pid}/customer-profile", json=terms, headers=sales)
    by_owner = await ok(await client.put(f"/parties/{pid}/customer-profile", json=terms, headers=h))
    again = await ok(await client.put(f"/parties/{pid}/customer-profile", json=terms, headers=h))
    supplier_side = await client.put(
        f"/parties/{pid}/supplier-profile", json={"business_id": shop.business_id}, headers=h
    )

    assert supplier_by_sales.status_code == 403, "a salesperson adds customers, not suppliers"
    assert (by_sales.status_code, by_sales.json()["permission"]) == (403, "credit.limit")
    assert by_owner["customer_profiles"][0]["credit_limit"] == "10000.0000"
    assert len(again["customer_profiles"]) == 1, "one profile per business, updated in place"
    assert supplier_side.status_code == 422, "this party is not a supplier"


async def test_quote_uses_the_customers_list_then_the_groups_list(client: AsyncClient) -> None:
    shop = await shop_with_fabric(client, "groups@shop.ae")
    h, b = shop["h"], shop["business_id"]

    async def price_list(name: str, price: str) -> str:
        plist = await ok(
            await client.post(
                "/price-lists", json={"business_id": b, "name_en": name, "name_ar": "ق"}, headers=h
            ),
            201,
        )
        await ok(
            await client.put(
                f"/price-lists/{plist['id']}/items",
                json={"variant_id": shop["variant"], "unit": "m", "price": price},
                headers=h,
            )
        )
        list_id: str = plist["id"]
        return list_id

    wholesale = await price_list("Wholesale", "2.2000")
    special = await price_list("Special", "2.0000")
    group = await ok(
        await client.post(
            "/customer-groups",
            json={
                "business_id": b,
                "name_en": "Wholesalers",
                "name_ar": "تجار الجملة",
                "price_list_id": wholesale,
            },
            headers=h,
        ),
        201,
    )
    pid = (await ok(await client.post("/parties", json=party(), headers=h), 201))["id"]

    no_terms = await quote(client, shop, party_id=pid)
    await ok(
        await client.put(
            f"/parties/{pid}/customer-profile",
            json={"business_id": b, "customer_group_id": group["id"]},
            headers=h,
        )
    )
    by_group = await quote(client, shop, party_id=pid)
    await ok(
        await client.put(
            f"/parties/{pid}/customer-profile",
            json={"business_id": b, "customer_group_id": group["id"], "price_list_id": special},
            headers=h,
        )
    )
    by_customer = await quote(client, shop, party_id=pid)

    assert no_terms["price_source"] == "none"
    assert (by_group["price"], by_group["price_source"]) == ("2.2000", "group_list:Wholesale")
    assert (by_customer["price"], by_customer["price_source"]) == ("2.0000", "customer_list:Special")


async def test_credit_check_blocks_over_the_limit_and_only_some_may_override(client: AsyncClient) -> None:
    shop = await make_shop(client, "credit@shop.ae")
    h = bearer(shop.owner)
    await add_member(client, shop, "seller@shop.ae", "salesperson", business=shop.business_id)
    seller = bearer(await login(client, "seller@shop.ae"))
    pid = (await ok(await client.post("/parties", json=party(), headers=h), 201))["id"]
    await ok(
        await client.put(
            f"/parties/{pid}/customer-profile",
            json={"business_id": shop.business_id, "credit_limit": "5000", "credit_policy": "block"},
            headers=h,
        )
    )

    async def check(amount: str, headers: dict[str, str]) -> dict[str, Any]:
        body = {"business_id": shop.business_id, "amount": amount}
        result: dict[str, Any] = await ok(
            await client.post(f"/parties/{pid}/credit-check", json=body, headers=headers)
        )
        return result

    fine = await check("5000", seller)
    blocked = await check("5000.01", seller)
    owner_view = await check("5000.01", h)

    assert (fine["decision"], fine["available"]) == ("ok", "0.0000")
    assert (blocked["decision"], blocked["reasons"], blocked["may_override"]) == (
        "block",
        ["over_limit"],
        False,
    )
    assert owner_view["may_override"] is True
    assert (blocked["ledger_balance"], blocked["pdc_in_hand"]) == ("0.0000", "0"), (
        "nothing posted yet (ledger, M1.5); PDCs are a plug-in until M1.9"
    )


@pytest.mark.usefixtures("clean_db")
async def test_a_credit_override_is_audited(app_sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with app_sessionmaker() as session, session.begin():
        seed = await seed_tenant(session)
        created = await parties.create_party(
            session, seed.tenant_id, PartyIn(name_en="Late payer", name_ar="متأخر", is_customer=True)
        )
        terms = CustomerProfileIn(
            business_id=seed.business_id, credit_limit=Decimal(100), credit_policy="block"
        )
        await parties.put_customer_profile(session, created.id, terms)
        check = await parties.credit_check(
            session,
            created.id,
            CreditCheckIn(business_id=seed.business_id, amount=Decimal(500)),
            may_override=True,
        )
        await parties.record_credit_override(
            session,
            created.id,
            seed.business_id,
            document="SI-0001",
            check=check,
            reason="Owner approved by phone",
        )
        entry = (await session.scalars(select(AuditLog).where(AuditLog.action == "override"))).one()

    assert check.decision == "block"
    assert entry.reason == "Owner approved by phone"
    assert (entry.changes["override"], entry.changes["document"], entry.changes["exposure"]) == (
        "credit",
        "SI-0001",
        "500.0000",
    )
