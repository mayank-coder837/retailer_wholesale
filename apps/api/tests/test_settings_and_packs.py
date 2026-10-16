from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.platform import packs
from tests.api_helpers import bearer, signup
from tests.test_roles_api import add_member, login, make_shop


async def effective(client: AsyncClient, tokens: dict[str, Any], **params: str) -> dict[str, Any]:
    rows = (await client.get("/settings", params=params, headers=bearer(tokens))).json()
    return {r["key"]: (r["value"], r["source"]) for r in rows}


async def test_settings_resolve_user_over_branch_over_business_over_tenant(client: AsyncClient) -> None:
    shop = await make_shop(client)
    h = bearer(shop.owner)
    key = "sales.default_price_mode"

    assert (await effective(client, shop.owner))[key] == ("vat_exclusive", "default")
    await client.put(f"/settings/{key}", json={"scope": "tenant", "value": "vat_inclusive"}, headers=h)
    await client.put(
        f"/settings/{key}",
        json={"scope": "business", "scope_id": shop.business_id, "value": "no_vat"},
        headers=h,
    )
    await client.put(
        f"/settings/{key}",
        json={"scope": "branch", "scope_id": shop.branch_b, "value": "vat_exclusive"},
        headers=h,
    )

    assert (await effective(client, shop.owner))[key] == ("vat_inclusive", "tenant")
    assert (await effective(client, shop.owner, business_id=shop.business_id))[key] == ("no_vat", "business")
    assert (await effective(client, shop.owner, branch_id=shop.branch_a))[key] == ("no_vat", "business")
    assert (await effective(client, shop.owner, branch_id=shop.branch_b))[key] == ("vat_exclusive", "branch")

    await client.put(f"/settings/{key}", json={"scope": "user", "value": "vat_inclusive"}, headers=h)
    assert (await effective(client, shop.owner, branch_id=shop.branch_b))[key] == ("vat_inclusive", "user")


async def test_invalid_settings_are_refused(client: AsyncClient) -> None:
    owner = await signup(client, "settings@shop.ae")
    h = bearer(owner)

    bad_value = await client.put("/settings/ui.language", json={"scope": "tenant", "value": "fr"}, headers=h)
    bad_scope = await client.put(
        "/settings/ui.language",
        json={"scope": "business", "scope_id": owner["tenant_id"], "value": "ar"},
        headers=h,
    )
    unknown = await client.put("/settings/does.not.exist", json={"scope": "tenant", "value": 1}, headers=h)

    assert bad_value.json()["code"] == "invalid_setting"
    assert bad_scope.status_code in (404, 422)
    assert unknown.status_code == 404


async def test_staff_change_only_their_own_preferences(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await add_member(client, shop, "sales@shop.ae", "salesperson")
    sales = await login(client, "sales@shop.ae")

    own = await client.put(
        "/settings/ui.language", json={"scope": "user", "value": "ar"}, headers=bearer(sales)
    )
    tenant = await client.put(
        "/settings/ui.language", json={"scope": "tenant", "value": "ar"}, headers=bearer(sales)
    )

    assert own.status_code == 200
    assert own.json()["value"] == "ar"
    assert tenant.status_code == 403
    assert (await effective(client, shop.owner))["ui.language"] == ("en", "default"), "owner unaffected"


def test_all_five_packs_load_and_use_exact_decimals() -> None:
    loaded = packs.load_packs()

    assert set(loaded) == {"general_trading", "textile", "hardware", "electronics", "grocery"}
    yard = next(u for u in loaded["textile"].sections.units if u.code == "yd")
    assert yard.factor_to_family_base == Decimal("0.9144")
    assert loaded["grocery"].sections.settings["inventory.track_expiry_default"] is True


async def test_applying_a_pack_is_idempotent(client: AsyncClient) -> None:
    owner = await signup(client, "pack@shop.ae")
    business_id = (await client.get("/businesses", headers=bearer(owner))).json()[0]["id"]
    url = f"/businesses/{business_id}/industry-packs"

    first = await client.post(url, json={"code": "textile"}, headers=bearer(owner))
    second = await client.post(url, json={"code": "textile"}, headers=bearer(owner))

    assert first.json() == {
        "pack_code": "textile",
        "version": 1,
        # units apply from M1.1, attributes and custom fields from M1.2: nothing is pending any more
        "applied_sections": ["attributes", "custom_fields", "settings", "units"],
        "pending_sections": [],
        "changed": True,
    }
    assert second.json()["changed"] is False
    values = await effective(client, owner, business_id=business_id)
    assert values["catalog.default_sales_unit"] == ("m", "business")
    assert values["inventory.track_batch_default"] == (True, "business")
    audit = await client.get(
        "/audit", params={"entity_type": "industry_pack_application"}, headers=bearer(owner)
    )
    assert [e["action"] for e in audit.json()].count("apply") == 1, "the second apply changed nothing"


@pytest.fixture
def units_applier() -> Iterator[list[str]]:
    received: list[str] = []

    async def apply_units(_s: AsyncSession, _t: Any, _b: Any, units: Any) -> None:
        received.extend(u.code for u in units)

    real = packs._APPLIERS.get("units")  # the catalog module's applier (M1.1)
    packs.register_applier("units", apply_units)
    yield received
    if real is not None:
        packs.register_applier("units", real)
    else:
        packs._APPLIERS.pop("units", None)


async def test_pending_sections_apply_once_their_module_exists(
    client: AsyncClient, units_applier: list[str]
) -> None:
    packs._APPLIERS.pop("units", None)
    owner = await signup(client, "later@shop.ae")
    business_id = (await client.get("/businesses", headers=bearer(owner))).json()[0]["id"]
    url = f"/businesses/{business_id}/industry-packs"
    await client.post(url, json={"code": "grocery"}, headers=bearer(owner))

    async def fake_units(_s: AsyncSession, _t: Any, _b: Any, units: Any) -> None:
        units_applier.extend(u.code for u in units)

    packs.register_applier("units", fake_units)  # as if M1.1 had arrived
    later = (await client.post(url, json={"code": "grocery"}, headers=bearer(owner))).json()

    assert later["changed"] is True
    assert "units" in later["applied_sections"]
    assert "units" not in later["pending_sections"]
    assert units_applier == ["pcs", "pack", "carton", "kg", "g", "l", "ml"]


async def test_packs_need_business_edit(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await add_member(client, shop, "wh@shop.ae", "warehouse")
    wh = await login(client, "wh@shop.ae")

    listed = await client.get("/industry-packs", headers=bearer(wh))
    applied = await client.post(
        f"/businesses/{shop.business_id}/industry-packs", json={"code": "hardware"}, headers=bearer(wh)
    )

    assert len(listed.json()) == 5
    assert applied.status_code == 403


async def test_pack_catalogue_is_public_for_the_signup_wizard(client: AsyncClient) -> None:
    response = await client.get("/industry-packs")

    assert response.status_code == 200
    assert {p["code"] for p in response.json()} >= {"textile", "grocery"}
    assert all(p["name_ar"] and p["description_ar"] for p in response.json()), "bilingual (rule 10)"
