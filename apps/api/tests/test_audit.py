"""Audit log (CLAUDE.md rule 8): who, when, what, old → new; append-only."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_helpers import bearer, signup
from tests.test_roles_api import add_member, login, make_shop

BRANCH = {"code": "AJM", "name_en": "Ajman", "name_ar": "عجمان", "emirate": "ajman"}


async def audit(client: AsyncClient, tokens: dict[str, str], **params: str) -> list[dict[str, object]]:
    response = await client.get("/audit", params=params, headers=bearer(tokens))
    assert response.status_code == 200, response.text
    entries: list[dict[str, object]] = response.json()
    return entries


async def test_create_is_audited_with_actor_client_and_request(client: AsyncClient) -> None:
    owner = await signup(client, "audit@shop.ae")
    me = (await client.get("/me", headers=bearer(owner))).json()
    business = (await client.get("/businesses", headers=bearer(owner))).json()[0]

    created = await client.post(
        f"/businesses/{business['id']}/branches",
        json=BRANCH,
        headers=bearer(owner) | {"X-Request-ID": "req-42"},
    )
    entries = await audit(client, owner, entity_type="branch", entity_id=created.json()["id"])

    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "create"
    assert entry["actor_user_id"] == me["user"]["id"]
    assert (entry["client"], entry["request_id"], entry["via_ai"]) == ("desktop", "req-42", False)
    changes = entry["changes"]
    assert isinstance(changes, dict)
    assert changes["code"] == [None, "AJM"]
    assert changes["emirate"] == [None, "ajman"]
    assert not {"updated_at", "created_by", "tenant_id"} & changes.keys(), "bookkeeping columns are left out"
    assert created.headers["x-request-id"] == "req-42"


async def test_role_changes_record_old_and_new_values(client: AsyncClient) -> None:
    shop = await make_shop(client)
    h = bearer(shop.owner)
    role = (
        await client.post(
            "/roles",
            json={"name_en": "Driver", "name_ar": "سائق", "permissions": ["delivery_note.view"]},
            headers=h,
        )
    ).json()

    await client.patch(f"/roles/{role['id']}", json={"name_en": "Van driver"}, headers=h)
    await client.patch(
        f"/roles/{role['id']}", json={"permissions": ["delivery_note.view", "delivery_note.post"]}, headers=h
    )
    await client.delete(f"/roles/{role['id']}", headers=h)
    entries = await audit(client, shop.owner, entity_type="role", entity_id=role["id"])

    by_action = [(e["action"], e["changes"], e["snapshot"]) for e in reversed(entries)]
    assert by_action[0][0] == "create"
    assert ("update", {"name_en": ["Driver", "Van driver"]}, None) in by_action
    assert (
        "update",
        {"permissions": [["delivery_note.view"], ["delivery_note.post", "delivery_note.view"]]},
        None,
    ) in by_action
    deleted = by_action[-1]
    assert deleted[0] == "delete"
    assert isinstance(deleted[2], dict)
    assert deleted[2]["name_en"] == "Van driver", "the deleted row is kept in full"


async def test_removed_role_assignments_are_audited(client: AsyncClient) -> None:
    shop = await make_shop(client)
    member = await add_member(client, shop, "c@shop.ae", "cashier")

    await client.put(
        f"/memberships/{member['membership_id']}/roles",
        json={"assignments": [{"role_id": shop.roles["accountant"]}]},
        headers=bearer(shop.owner),
    )
    entries = await audit(client, shop.owner, entity_type="role_assignment")

    actions = sorted(str(e["action"]) for e in entries)
    assert actions.count("delete") == 1
    assert actions.count("create") >= 2


async def test_audit_needs_permission(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await add_member(client, shop, "s@shop.ae", "salesperson")
    await add_member(client, shop, "a@shop.ae", "accountant")

    salesperson = await client.get("/audit", headers=bearer(await login(client, "s@shop.ae")))
    accountant = await client.get("/audit", headers=bearer(await login(client, "a@shop.ae")))

    assert salesperson.status_code == 403
    assert accountant.status_code == 200


async def test_audit_rows_cannot_be_changed_even_by_the_owner(
    client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    await signup(client, "immutable@shop.ae")

    with pytest.raises(DBAPIError, match="append-only"):
        async with owner_engine.begin() as conn:
            await conn.execute(text("UPDATE audit_log SET action = 'delete'"))
    with pytest.raises(DBAPIError, match="append-only"):
        async with owner_engine.begin() as conn:
            await conn.execute(text("DELETE FROM audit_log"))
