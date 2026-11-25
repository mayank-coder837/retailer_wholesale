"""A permission held in one business must never open another business of the same tenant.

Each endpoint that is given a business or a record is checked against *that* business (the request's, or the
record's own), not just "the member holds this permission somewhere". Plus the response headers and the
request-size cap added with the security pass.
"""

from httpx import AsyncClient

from tests.api_helpers import bearer
from tests.test_accounting_api import Books, books
from tests.test_attachments import upload
from tests.test_pricing_api import ok
from tests.test_roles_api import add_member, login


async def second_business_accountant(client: AsyncClient, b: Books, email: str) -> tuple[dict[str, str], str]:
    """An accountant of a second business of the same tenant only. Returns (headers, second business id)."""
    second = await ok(
        await client.post(
            "/businesses", json={"legal_name_en": "Second LLC", "legal_name_ar": "ثانية"}, headers=b.h
        ),
        201,
    )
    await add_member(client, b.shop, email, "accountant", business=second["id"])
    return bearer(await login(client, email)), str(second["id"])


async def test_party_books_and_bills_of_another_business_are_refused(client: AsyncClient) -> None:
    b = await books(client, "xb1@shop.ae")
    await b.invoice("100", "INV-1")
    other, second = await second_business_accountant(client, b, "xb1-acct@shop.ae")
    mine = {"business_id": b.shop.business_id}

    assert (await client.get(f"/parties/{b.customer}/ledger", params=mine, headers=other)).status_code == 403
    assert (
        await client.get("/bills", params={**mine, "party_id": b.customer}, headers=other)
    ).status_code == 403
    # Their own business is fine.
    ours = {"business_id": second}
    assert (await client.get(f"/parties/{b.customer}/ledger", params=ours, headers=other)).status_code == 200
    # And the owner still sees this one.
    assert (await client.get(f"/parties/{b.customer}/ledger", params=mine, headers=b.h)).status_code == 200


async def test_stock_of_another_business_is_refused(client: AsyncClient) -> None:
    b = await books(client, "xb2@shop.ae")
    other, second = await second_business_accountant(client, b, "xb2-acct@shop.ae")
    variant = "00000000-0000-0000-0000-000000000001"
    mine = {"business_id": b.shop.business_id}

    assert (await client.get("/stock/balances", params=mine, headers=other)).status_code == 403
    assert (
        await client.get(f"/stock/variants/{variant}/ledger", params=mine, headers=other)
    ).status_code == 403
    assert (
        await client.get("/stock/batches", params={**mine, "variant_id": variant}, headers=other)
    ).status_code == 403
    assert (
        await client.get("/stock/balances", params={"business_id": second}, headers=other)
    ).status_code == 200
    assert (await client.get("/stock/balances", params=mine, headers=b.h)).status_code == 200


async def test_files_on_a_document_of_another_business_are_refused(client: AsyncClient) -> None:
    b = await books(client, "xb3@shop.ae")
    journal = await b.invoice("100", "INV-1")
    other, _second = await second_business_accountant(client, b, "xb3-acct@shop.ae")
    other_tokens = await login(client, "xb3-acct@shop.ae")

    made = await upload(client, b.shop.owner, journal["id"], entity_type="journal")
    assert made.status_code == 201, made.text
    attachment = made.json()["id"]
    listing = {"entity_type": "journal", "entity_id": journal["id"]}

    assert (await client.get("/attachments", params=listing, headers=other)).status_code == 403
    assert (await client.get(f"/attachments/{attachment}/content", headers=other)).status_code == 403
    assert (await client.delete(f"/attachments/{attachment}", headers=other)).status_code == 403
    assert (await upload(client, other_tokens, journal["id"], entity_type="journal")).status_code == 403
    assert (await client.get("/attachments", params=listing, headers=b.h)).status_code == 200
    assert (await client.get(f"/attachments/{attachment}/content", headers=b.h)).status_code == 200


async def test_every_response_carries_the_protective_headers(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


async def test_a_request_that_declares_a_huge_body_is_refused_before_it_is_read(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", content=b"{}", headers={"content-length": str(500 * 1024 * 1024)}
    )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert response.headers["x-content-type-options"] == "nosniff"
