"""Catalog API (M1.2): products, units of a product, variants, barcodes, categories, brands, custom fields."""

from typing import Any

import pytest
from httpx import AsyncClient

from app.modules.catalog.products import ean13_check_digit
from tests.api_helpers import bearer, signup
from tests.test_roles_api import add_member, login, make_shop


async def ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


async def textile_shop(client: AsyncClient, email: str = "fabric@shop.ae") -> dict[str, Any]:
    owner = await signup(client, email)
    business_id = (await client.get("/businesses", headers=bearer(owner))).json()[0]["id"]
    await ok(
        await client.post(
            f"/businesses/{business_id}/industry-packs", json={"code": "textile"}, headers=bearer(owner)
        )
    )
    attributes = {a["code"]: a for a in await ok(await client.get("/attributes", headers=bearer(owner)))}
    for value_en, value_ar in (("Red", "أحمر"), ("Blue", "أزرق")):
        await ok(
            await client.post(
                f"/attributes/{attributes['color']['id']}/values",
                json={"value_en": value_en, "value_ar": value_ar},
                headers=bearer(owner),
            ),
            201,
        )
    return owner


FABRIC = {
    "sku": "FAB-001",
    "name_en": "Cotton poplin",
    "name_ar": "بوبلين قطن",
    "base_unit": "m",
    "units": [{"unit": "roll", "factor_to_base": "50", "is_purchase_default": True}],
    "attributes": [
        {"attribute": "color", "values": ["Red", "Blue"]},
        {"attribute": "width", "values": ["44 in", "58 in"]},
    ],
    "custom_fields": {"gsm": "120.5", "design_no": "D-778"},
}


async def test_acceptance_a_textile_fabric_by_the_roll_with_colour_and_width_and_a_scannable_barcode(
    client: AsyncClient,
) -> None:
    owner = await textile_shop(client)

    fabric = await ok(await client.post("/products", json=FABRIC, headers=bearer(owner)), 201)

    units = {u["unit"]: u for u in fabric["units"]}
    assert units["m"]["is_base"]
    assert units["m"]["factor_to_base"] == "1.000000"
    assert units["roll"]["factor_to_base"] == "50.000000"
    assert units["roll"]["is_purchase_default"], "buy by the roll"
    assert units["m"]["is_sales_default"], "sell by the metre"
    assert len(fabric["variants"]) == 4, "colour x width"
    red_44 = next(
        v
        for v in fabric["variants"]
        if {(x["attribute"], x["value_en"]) for x in v["values"]}
        == {
            ("color", "Red"),
            ("width", "44 in"),
        }
    )
    assert red_44["sku"] == "FAB-001-RED-44IN"
    assert fabric["custom_fields"] == {"gsm": "120.5", "design_no": "D-778"}

    first12 = "629100012345"
    code = first12 + ean13_check_digit(first12)
    await ok(
        await client.post(
            f"/products/{fabric['id']}/barcodes",
            json={"variant_id": red_44["id"], "unit": "roll", "code": code},
            headers=bearer(owner),
        ),
        201,
    )
    scan = await ok(await client.get(f"/barcodes/{code}", headers=bearer(owner)))

    assert scan["variant_id"] == red_44["id"]
    assert (scan["unit"], scan["factor_to_base"], scan["base_unit"]) == ("roll", "50.000000", "m")
    assert {(v["attribute"], v["value_ar"]) for v in scan["values"]} == {
        ("color", "أحمر"),
        ("width", "44 بوصة"),
    }
    found = await ok(await client.get("/products", params={"q": code}, headers=bearer(owner)))
    assert [p["sku"] for p in found] == ["FAB-001"], "searching by barcode finds the product"


async def test_a_product_without_attributes_has_one_default_variant(client: AsyncClient) -> None:
    owner = await signup(client, "plain@shop.ae")
    data = {
        "sku": "PEN-1",
        "name_en": "Pen",
        "name_ar": "قلم",
        "base_unit": "pcs",
        "units": [{"unit": "dozen"}],
    }

    pen = await ok(await client.post("/products", json=data, headers=bearer(owner)), 201)

    assert [v["sku"] for v in pen["variants"]] == ["PEN-1"]
    dozen = next(u for u in pen["units"] if u["unit"] == "dozen")
    assert dozen["factor_to_base"] == "12.000000", "same family: the standard factor is used"


@pytest.mark.parametrize(
    ("units", "code"),
    [
        ([{"unit": "kg"}], "invalid_product"),  # another family needs a factor
        ([{"unit": "roll", "factor_to_base": "0"}], "validation_error"),
        ([{"unit": "nope", "factor_to_base": "2"}], "invalid_product"),
        ([{"unit": "m"}], "invalid_product"),  # already the base unit
    ],
)
async def test_bad_product_units_are_refused(client: AsyncClient, units: list[Any], code: str) -> None:
    owner = await signup(client, f"units-{code}-{len(units[0])}@shop.ae")
    data = {"sku": "X-1", "name_en": "X", "name_ar": "س", "base_unit": "m", "units": units}

    response = await client.post("/products", json=data, headers=bearer(owner))

    assert response.status_code == 422, response.text
    assert response.json()["code"] == code


async def test_sku_and_barcodes_are_unique_per_tenant_not_globally(client: AsyncClient) -> None:
    first = await textile_shop(client, "first@shop.ae")
    second = await textile_shop(client, "second@shop.ae")
    a = await ok(await client.post("/products", json=FABRIC, headers=bearer(first)), 201)
    again = await client.post("/products", json=FABRIC, headers=bearer(first))
    b = await ok(await client.post("/products", json=FABRIC, headers=bearer(second)), 201)
    code = "4006381333931"  # a real-world EAN-13

    def add(tokens: dict[str, Any], product: dict[str, Any]) -> Any:
        body = {"variant_id": product["variants"][0]["id"], "unit": "m", "code": code}
        return client.post(f"/products/{product['id']}/barcodes", json=body, headers=bearer(tokens))

    assert again.status_code == 409
    assert again.json()["code"] == "duplicate_sku"
    assert (await add(first, a)).status_code == 201
    taken = await add(first, a)
    assert taken.status_code == 409
    assert taken.json()["code"] == "barcode_taken"
    assert (await add(second, b)).status_code == 201, "another tenant may use the same code"
    assert (await client.get(f"/barcodes/{code}", headers=bearer(second))).json()["product_id"] == b["id"]
    assert (await client.get(f"/products/{a['id']}", headers=bearer(second))).status_code == 404


async def test_barcode_rules(client: AsyncClient) -> None:
    owner = await signup(client, "barcodes@shop.ae")
    data = {
        "sku": "TEA-1",
        "name_en": "Tea",
        "name_ar": "شاي",
        "base_unit": "pcs",
        "units": [{"unit": "carton", "factor_to_base": "24"}],
    }
    tea = await ok(await client.post("/products", json=data, headers=bearer(owner)), 201)
    variant = tea["variants"][0]["id"]
    url = f"/products/{tea['id']}/barcodes"

    bad_digit = await client.post(
        url, json={"variant_id": variant, "unit": "pcs", "code": "4006381333932"}, headers=bearer(owner)
    )
    not_a_unit = await client.post(
        url, json={"variant_id": variant, "unit": "kg", "code": "4006381333931"}, headers=bearer(owner)
    )
    generated = await ok(
        await client.post(url, json={"variant_id": variant, "unit": "carton"}, headers=bearer(owner)), 201
    )
    code128 = await client.post(
        url,
        json={"variant_id": variant, "unit": "pcs", "code": "TEA-PCS-01", "kind": "code128"},
        headers=bearer(owner),
    )

    assert bad_digit.status_code == 422
    assert bad_digit.json()["code"] == "invalid_barcode"
    assert not_a_unit.status_code == 422
    made = generated["barcodes"][0]
    assert made["kind"] == "internal"
    assert made["code"].startswith("2")
    assert made["code"][-1] == ean13_check_digit(made["code"][:12])
    assert code128.status_code == 201
    scan = await ok(await client.get(f"/barcodes/{made['code']}", headers=bearer(owner)))
    assert (scan["unit"], scan["factor_to_base"]) == ("carton", "24.000000")
    removed = await client.delete(f"/products/{tea['id']}/units/carton", headers=bearer(owner))
    assert removed.status_code == 422, "a unit used by a barcode cannot be removed"
    assert (await client.get("/barcodes/0000000000000", headers=bearer(owner))).status_code == 404


async def test_custom_fields_are_validated_by_type(client: AsyncClient) -> None:
    owner = await signup(client, "fields@shop.ae")
    h = bearer(owner)
    for field in (
        {
            "key": "origin",
            "data_type": "select",
            "options": [{"value": "uae", "label_en": "UAE", "label_ar": "الإمارات"}],
        },
        {"key": "pack_size", "data_type": "number"},
        {"key": "weight", "data_type": "decimal"},
        {"key": "launch", "data_type": "date"},
        {"key": "fragile", "data_type": "bool"},
        {"key": "notes", "data_type": "text", "required": True},
    ):
        body = {"entity_type": "product", "label_en": field["key"], "label_ar": "حقل"} | field
        await ok(await client.post("/custom-fields", json=body, headers=h), 201)
    base = {"sku": "CF-1", "name_en": "Item", "name_ar": "صنف", "base_unit": "pcs"}

    good = await client.post(
        "/products",
        json=base
        | {
            "custom_fields": {
                "origin": "uae",
                "pack_size": 6,
                "weight": "0.125",
                "launch": "2026-10-16",
                "fragile": True,
                "notes": "Keep dry",
            }
        },
        headers=h,
    )
    assert good.status_code == 201, good.text
    assert good.json()["custom_fields"]["weight"] == "0.125", "exact decimal, never a float"

    for bad in (
        {"notes": "x", "origin": "china"},
        {"notes": "x", "pack_size": "6"},
        {"notes": "x", "weight": 0.125},
        {"notes": "x", "launch": "16/10/2026"},
        {"notes": "x", "fragile": "yes"},
        {"origin": "uae"},  # required notes missing
        {"notes": "x", "colour": "red"},  # no such field
    ):
        response = await client.post(
            "/products", json=base | {"sku": "CF-2", "custom_fields": bad}, headers=h
        )
        assert response.status_code == 422, bad
        assert response.json()["code"] == "invalid_custom_field", bad
    no_options = await client.post(
        "/custom-fields",
        json={
            "entity_type": "product",
            "key": "grade",
            "label_en": "G",
            "label_ar": "د",
            "data_type": "select",
        },
        headers=h,
    )
    assert no_options.status_code == 422


async def test_variants_can_be_added_but_never_duplicated(client: AsyncClient) -> None:
    owner = await textile_shop(client, "variants@shop.ae")
    fabric = await ok(
        await client.post(
            "/products", json=FABRIC | {"attributes": [FABRIC["attributes"][0]]}, headers=bearer(owner)
        ),
        201,
    )
    url = f"/products/{fabric['id']}/variants"
    attributes = {a["code"]: a for a in await ok(await client.get("/attributes", headers=bearer(owner)))}
    await ok(
        await client.post(
            f"/attributes/{attributes['color']['id']}/values",
            json={"value_en": "Green", "value_ar": "أخضر"},
            headers=bearer(owner),
        ),
        201,
    )

    green = await client.post(url, json={"values": {"color": "Green"}}, headers=bearer(owner))
    duplicate = await client.post(url, json={"values": {"color": "Red"}}, headers=bearer(owner))
    wrong = await client.post(url, json={"values": {"width": "44 in"}}, headers=bearer(owner))

    assert green.status_code == 201
    assert len(green.json()["variants"]) == 3
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "duplicate_variant"
    assert wrong.status_code == 422


async def test_categories_form_a_tree_without_cycles(client: AsyncClient) -> None:
    owner = await signup(client, "tree@shop.ae")
    h = bearer(owner)
    fabrics = await ok(
        await client.post("/categories", json={"name_en": "Fabrics", "name_ar": "أقمشة"}, headers=h), 201
    )
    cotton = await ok(
        await client.post(
            "/categories", json={"name_en": "Cotton", "name_ar": "قطن", "parent_id": fabrics["id"]}, headers=h
        ),
        201,
    )
    twin = await client.post("/categories", json={"name_en": "Fabrics", "name_ar": "أقمشة"}, headers=h)
    cycle = await client.patch(f"/categories/{fabrics['id']}", json={"parent_id": cotton["id"]}, headers=h)

    assert cotton["path_en"] == "Fabrics / Cotton"
    assert twin.status_code == 409, "names unique among siblings, top level included"
    assert cycle.status_code == 422
    assert cycle.json()["code"] == "category_cycle"
    brand = await ok(
        await client.post("/brands", json={"name_en": "Al Noor", "name_ar": "النور"}, headers=h), 201
    )
    product = await ok(
        await client.post(
            "/products",
            json={
                "sku": "C-1",
                "name_en": "Lawn",
                "name_ar": "لون",
                "base_unit": "m",
                "category_id": cotton["id"],
                "brand_id": brand["id"],
            },
            headers=h,
        ),
        201,
    )
    by_category = await ok(await client.get("/products", params={"category_id": cotton["id"]}, headers=h))
    assert [p["id"] for p in by_category] == [product["id"]]


async def test_base_unit_and_factors_are_fixed_once_created(client: AsyncClient) -> None:
    owner = await signup(client, "fixed@shop.ae")
    data = {
        "sku": "R-1",
        "name_en": "Rope",
        "name_ar": "حبل",
        "base_unit": "m",
        "units": [{"unit": "roll", "factor_to_base": "100"}],
    }
    rope = await ok(await client.post("/products", json=data, headers=bearer(owner)), 201)

    patched = await ok(
        await client.patch(
            f"/products/{rope['id']}",
            json={"name_en": "Nylon rope", "base_unit": "cm"},
            headers=bearer(owner),
        )
    )
    defaults = await ok(
        await client.patch(
            f"/products/{rope['id']}/units/roll", json={"is_sales_default": True}, headers=bearer(owner)
        )
    )

    assert patched["name_en"] == "Nylon rope"
    assert patched["base_unit"] == "m", "the base unit never changes"
    units = {u["unit"]: u for u in defaults["units"]}
    assert units["roll"]["factor_to_base"] == "100.000000"
    assert units["roll"]["is_sales_default"], "one sales default"
    assert not units["m"]["is_sales_default"]
    base_removed = await client.delete(f"/products/{rope['id']}/units/m", headers=bearer(owner))
    assert base_removed.status_code == 422


async def test_a_salesperson_sees_products_but_cannot_create_them(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await add_member(client, shop, "sales@shop.ae", "salesperson")
    sales = await login(client, "sales@shop.ae")
    data = {"sku": "S-1", "name_en": "Soap", "name_ar": "صابون", "base_unit": "pcs"}
    await ok(await client.post("/products", json=data, headers=bearer(shop.owner)), 201)

    listed = await client.get("/products", headers=bearer(sales))
    created = await client.post("/products", json=data | {"sku": "S-2"}, headers=bearer(sales))
    field = await client.post(
        "/custom-fields",
        json={"entity_type": "product", "key": "k", "label_en": "K", "label_ar": "ك", "data_type": "text"},
        headers=bearer(sales),
    )

    assert listed.status_code == 200
    assert [p["sku"] for p in listed.json()] == ["S-1"]
    assert created.status_code == 403
    assert field.status_code == 403


async def test_the_textile_pack_adds_its_attributes_and_fields_once(client: AsyncClient) -> None:
    owner = await signup(client, "pack2@shop.ae")
    business_id = (await client.get("/businesses", headers=bearer(owner))).json()[0]["id"]
    for _ in range(2):
        await ok(
            await client.post(
                f"/businesses/{business_id}/industry-packs", json={"code": "textile"}, headers=bearer(owner)
            )
        )

    attributes = {a["code"]: a for a in await ok(await client.get("/attributes", headers=bearer(owner)))}
    fields = await ok(
        await client.get("/custom-fields", params={"entity_type": "product"}, headers=bearer(owner))
    )

    assert {"color", "width", "fabric", "size"} <= set(attributes)
    assert [v["value_en"] for v in attributes["width"]["values"]] == ["44 in", "58 in", "60 in"]
    assert sorted(f["key"] for f in fields) == ["design_no", "gsm"]
