"""Acceptance for M0.2: two tenants; neither can see the other's rows through any endpoint."""

from httpx import AsyncClient

from tests.api_helpers import bearer, signup

BRANCH = {"code": "SHJ", "name_en": "Sharjah branch", "name_ar": "فرع الشارقة", "emirate": "sharjah"}
WAREHOUSE = {"code": "W2", "name_en": "Industrial area", "name_ar": "المنطقة الصناعية"}


async def test_two_tenants_never_see_each_others_data(client: AsyncClient) -> None:
    a = await signup(client, "a@alnoor.ae", "Tenant A")
    b = await signup(client, "b@baraka.ae", "Tenant B")

    a_business = (await client.get("/businesses", headers=bearer(a))).json()[0]
    a_branch = (
        await client.post(f"/businesses/{a_business['id']}/branches", json=BRANCH, headers=bearer(a))
    ).json()
    a_warehouse = (
        await client.post(f"/branches/{a_branch['id']}/warehouses", json=WAREHOUSE, headers=bearer(a))
    ).json()
    assert a_warehouse["business_id"] == a_business["id"]

    b_businesses = (await client.get("/businesses", headers=bearer(b))).json()
    assert [x["legal_name_en"] for x in b_businesses] == ["Tenant B LLC"]

    as_b = bearer(b)
    assert (await client.get(f"/businesses/{a_business['id']}", headers=as_b)).status_code == 404
    assert (await client.get(f"/businesses/{a_business['id']}/branches", headers=as_b)).status_code == 404
    assert (await client.get(f"/branches/{a_branch['id']}/warehouses", headers=as_b)).status_code == 404
    attach = await client.post(f"/businesses/{a_business['id']}/branches", json=BRANCH, headers=as_b)
    assert attach.status_code == 404
    attach_wh = await client.post(f"/branches/{a_branch['id']}/warehouses", json=WAREHOUSE, headers=as_b)
    assert attach_wh.status_code == 404


async def test_signup_creates_head_office_and_default_warehouse(client: AsyncClient) -> None:
    tokens = await signup(client, "setup@alnoor.ae")
    business = (await client.get("/businesses", headers=bearer(tokens))).json()[0]

    branches = (await client.get(f"/businesses/{business['id']}/branches", headers=bearer(tokens))).json()
    assert [(b["code"], b["is_head_office"], b["emirate"]) for b in branches] == [("HO", True, "dubai")]
    warehouses = (
        await client.get(f"/branches/{branches[0]['id']}/warehouses", headers=bearer(tokens))
    ).json()
    assert [(w["code"], w["is_default"]) for w in warehouses] == [("MAIN", True)]
    assert business["costing_method"] == "moving_average"
    assert business["negative_stock_allowed"] is True


async def test_duplicate_branch_code_is_a_conflict(client: AsyncClient) -> None:
    tokens = await signup(client, "dupcode@alnoor.ae")
    business = (await client.get("/businesses", headers=bearer(tokens))).json()[0]
    url = f"/businesses/{business['id']}/branches"

    first = await client.post(url, json=BRANCH, headers=bearer(tokens))
    second = await client.post(url, json=BRANCH, headers=bearer(tokens))

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["code"] == "duplicate_code"
    assert len((await client.get(url, headers=bearer(tokens))).json()) == 2, "head office + the first SHJ"


async def test_create_business_validates_trn(client: AsyncClient) -> None:
    tokens = await signup(client, "trn@alnoor.ae")
    body = {"legal_name_en": "Second LLC", "legal_name_ar": "الثانية", "trn": "12345", "vat_registered": True}

    response = await client.post("/businesses", json=body, headers=bearer(tokens))

    assert response.status_code == 422
