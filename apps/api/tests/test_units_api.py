"""Units of measure API (M1.1): standard units per tenant, custom units, permissions, packs, isolation."""

from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer, signup
from tests.test_roles_api import add_member, login, make_shop


async def units(client: AsyncClient, tokens: dict[str, Any], **params: Any) -> dict[str, dict[str, Any]]:
    response = await client.get("/units", headers=bearer(tokens), params=params)
    assert response.status_code == 200, response.text
    return {u["code"]: u for u in response.json()}


ROLL = {
    "family": "count",
    "code": "than",
    "name_en": "Than (bolt)",
    "name_ar": "ثان",
    "symbol": "than",
    "decimals": 0,
    "factor_to_family_base": "1",
}


async def test_every_new_tenant_gets_the_standard_units(client: AsyncClient) -> None:
    owner = await signup(client, "units@shop.ae")

    found = await units(client, owner)

    assert {"pcs", "dozen", "carton", "roll", "m", "cm", "yd", "kg", "g", "l", "m2", "hr"} <= set(found)
    assert found["m"]["is_family_base"]
    assert found["m"]["factor_to_family_base"] == "1.000000"
    assert found["yd"]["factor_to_family_base"] == "0.914400"
    assert found["kg"]["name_ar"] == "كيلوغرام"
    assert found["pcs"]["decimals"] == 0
    bases = [u["code"] for u in found.values() if u["is_family_base"]]
    assert sorted(bases) == ["hr", "kg", "l", "m", "m2", "pcs"], "exactly one base unit per family"


async def test_conversions_within_a_family(client: AsyncClient) -> None:
    owner = await signup(client, "convert@shop.ae")

    def convert(qty: str, src: str, dst: str) -> Any:
        return client.get(
            "/units/convert", params={"qty": qty, "from": src, "to": dst}, headers=bearer(owner)
        )

    cm = await convert("250", "cm", "m")
    yards = await convert("1", "yd", "m")
    across = await convert("1", "m", "kg")
    half_piece = await convert("1.5", "pcs", "dozen")

    assert cm.json() | {} == {
        "qty": "250",
        "from_unit": "cm",
        "to_unit": "m",
        "factor": "0.010000",
        "result": "2.50",
        "rounded": False,
    }
    assert (yards.json()["result"], yards.json()["rounded"]) == ("0.91", True)
    assert across.status_code == 422
    assert across.json()["code"] == "uom_not_convertible"
    assert half_piece.status_code == 422
    assert half_piece.json()["code"] == "uom_too_many_decimals"


async def test_owner_adds_renames_and_archives_a_unit_but_cannot_change_its_factor(
    client: AsyncClient,
) -> None:
    owner = await signup(client, "custom@shop.ae")

    created = await client.post("/units", json=ROLL, headers=bearer(owner))
    duplicate = await client.post("/units", json=ROLL, headers=bearer(owner))
    unknown_family = await client.post(
        "/units", json=ROLL | {"code": "x", "family": "energy"}, headers=bearer(owner)
    )
    zero = await client.post(
        "/units", json=ROLL | {"code": "y", "factor_to_family_base": "0"}, headers=bearer(owner)
    )
    unit_id = created.json()["id"]
    renamed = await client.patch(f"/units/{unit_id}", json={"name_en": "Bolt"}, headers=bearer(owner))
    factor = await client.patch(
        f"/units/{unit_id}", json={"factor_to_family_base": "5"}, headers=bearer(owner)
    )
    archived = await client.patch(f"/units/{unit_id}", json={"archived": True}, headers=bearer(owner))

    assert created.status_code == 201, created.text
    assert created.json()["is_standard"] is False
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate_unit"
    assert unknown_family.status_code == 422
    assert unknown_family.json()["code"] == "unknown_unit_family"
    assert zero.status_code == 422
    assert renamed.json()["name_en"] == "Bolt"
    assert factor.status_code == 200
    assert factor.json()["factor_to_family_base"] == "1.000000", "the factor is fixed once a unit exists"
    assert archived.json()["archived_at"] is not None
    assert "than" not in await units(client, owner)
    assert "than" in await units(client, owner, include_archived=True)
    audit = await client.get("/audit", params={"entity_type": "unit"}, headers=bearer(owner))
    assert {"create", "update"} <= {e["action"] for e in audit.json()}


async def test_a_salesperson_may_view_units_but_not_add_them(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await add_member(client, shop, "sales@shop.ae", "salesperson")
    sales = await login(client, "sales@shop.ae")

    listed = await client.get("/units", headers=bearer(sales))
    created = await client.post("/units", json=ROLL, headers=bearer(sales))

    assert listed.status_code == 200
    assert created.status_code == 403


async def test_tenants_never_see_each_others_units(client: AsyncClient) -> None:
    first = await signup(client, "first@shop.ae", "First Co")
    second = await signup(client, "second@shop.ae", "Second Co")
    created = await client.post("/units", json=ROLL, headers=bearer(first))

    seen_by_second = await client.get(f"/units/{created.json()['id']}", headers=bearer(second))

    assert seen_by_second.status_code == 404
    assert "than" not in await units(client, second)
    assert (await client.post("/units", json=ROLL, headers=bearer(second))).status_code == 201, (
        "codes are unique per tenant, not globally"
    )


async def test_a_pack_adds_its_units_once_and_leaves_the_tenants_own_alone(client: AsyncClient) -> None:
    owner = await signup(client, "textile@shop.ae")
    business_id = (await client.get("/businesses", headers=bearer(owner))).json()[0]["id"]
    before = await units(client, owner)
    await client.patch(f"/units/{before['yd']['id']}", json={"name_en": "Gaz"}, headers=bearer(owner))

    for _ in range(2):
        applied = await client.post(
            f"/businesses/{business_id}/industry-packs", json={"code": "textile"}, headers=bearer(owner)
        )
        assert applied.status_code == 200, applied.text

    after = await units(client, owner)
    assert after["than"]["name_ar"] == "ثان", "the textile pack's own unit was added"
    assert after["yd"]["name_en"] == "Gaz", "a unit the tenant already had keeps the tenant's name"
    assert len(after) == len(before) + 1
