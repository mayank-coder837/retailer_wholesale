"""M0.4 acceptance: a replayed write returns the identical response (CLAUDE.md rule 9)."""

import asyncio
from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer, signup

NEW_BUSINESS = {"legal_name_en": "Second LLC", "legal_name_ar": "الثانية"}


def keyed(tokens: dict[str, Any], key: str) -> dict[str, str]:
    return bearer(tokens) | {"Idempotency-Key": key}


async def business_count(client: AsyncClient, tokens: dict[str, Any]) -> int:
    return len((await client.get("/businesses", headers=bearer(tokens))).json())


async def test_retry_with_the_same_key_replays_the_first_response(client: AsyncClient) -> None:
    owner = await signup(client, "idem@shop.ae")

    first = await client.post("/businesses", json=NEW_BUSINESS, headers=keyed(owner, "create-second-llc"))
    retry = await client.post("/businesses", json=NEW_BUSINESS, headers=keyed(owner, "create-second-llc"))

    assert first.status_code == retry.status_code == 201
    assert retry.content == first.content, "identical body, including the same id"
    assert retry.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    assert await business_count(client, owner) == 2, "signup's business + exactly one more"


async def test_concurrent_duplicates_create_one_record(client: AsyncClient) -> None:
    owner = await signup(client, "race@shop.ae")

    responses = await asyncio.gather(
        *(
            client.post("/businesses", json=NEW_BUSINESS, headers=keyed(owner, "double-click"))
            for _ in range(5)
        )
    )

    assert {r.status_code for r in responses} == {201}
    assert len({r.json()["id"] for r in responses}) == 1
    assert sum(r.headers.get("idempotent-replayed") == "true" for r in responses) == 4
    assert await business_count(client, owner) == 2


async def test_same_key_for_a_different_request_is_refused(client: AsyncClient) -> None:
    owner = await signup(client, "reuse@shop.ae")
    await client.post("/businesses", json=NEW_BUSINESS, headers=keyed(owner, "k1"))

    other = await client.post(
        "/businesses", json={"legal_name_en": "Third", "legal_name_ar": "الثالثة"}, headers=keyed(owner, "k1")
    )

    assert other.status_code == 409
    assert other.json()["code"] == "idempotency_key_reused"
    assert await business_count(client, owner) == 2


async def test_a_failed_request_is_rolled_back_and_its_error_replayed(client: AsyncClient) -> None:
    owner = await signup(client, "fail@shop.ae")
    missing = "01a00000-0000-7000-8000-000000000000"
    branch = {"code": "X", "name_en": "X", "name_ar": "س", "emirate": "dubai"}

    first = await client.post(f"/businesses/{missing}/branches", json=branch, headers=keyed(owner, "k-404"))
    retry = await client.post(f"/businesses/{missing}/branches", json=branch, headers=keyed(owner, "k-404"))

    assert first.status_code == retry.status_code == 404
    assert retry.json() == first.json()
    assert retry.headers["idempotent-replayed"] == "true"


async def test_keys_are_per_user(client: AsyncClient) -> None:
    a = await signup(client, "a@shop.ae", "A")
    b = await signup(client, "b@shop.ae", "B")

    ra = await client.post("/businesses", json=NEW_BUSINESS, headers=keyed(a, "same-key"))
    rb = await client.post("/businesses", json=NEW_BUSINESS, headers=keyed(b, "same-key"))

    assert ra.status_code == rb.status_code == 201
    assert ra.json()["id"] != rb.json()["id"]


async def test_without_a_key_each_request_runs(client: AsyncClient) -> None:
    owner = await signup(client, "nokey@shop.ae")

    await client.post("/businesses", json=NEW_BUSINESS, headers=bearer(owner))
    await client.post("/businesses", json=NEW_BUSINESS, headers=bearer(owner))

    assert await business_count(client, owner) == 3


async def test_malformed_key_is_rejected(client: AsyncClient) -> None:
    owner = await signup(client, "bad@shop.ae")

    response = await client.post("/businesses", json=NEW_BUSINESS, headers=keyed(owner, "has spaces"))

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_idempotency_key"


async def test_unauthenticated_write_with_a_key_still_gets_401(client: AsyncClient) -> None:
    response = await client.post("/businesses", json=NEW_BUSINESS, headers={"Idempotency-Key": "k"})

    assert response.status_code == 401
