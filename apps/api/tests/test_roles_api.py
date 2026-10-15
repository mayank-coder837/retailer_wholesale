"""M0.3 acceptance: permissions on every endpoint, scoped to business and branch."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.permissions import ALL_PERMISSIONS
from app.modules.identity.default_roles import default_roles
from tests.api_helpers import bearer, signup
from tests.factories import PASSWORD

BRANCH_B = {"code": "B", "name_en": "Branch B", "name_ar": "الفرع ب", "emirate": "sharjah"}


@dataclass
class Shop:
    owner: dict[str, Any]
    business_id: str
    branch_a: str
    branch_b: str
    roles: dict[str, str]  # system_key -> role id


async def make_shop(client: AsyncClient, email: str = "owner@shop.ae") -> Shop:
    owner = await signup(client, email, "The Shop")
    h = bearer(owner)
    business = (await client.get("/businesses", headers=h)).json()[0]
    branch_a = (await client.get(f"/businesses/{business['id']}/branches", headers=h)).json()[0]
    branch_b = (await client.post(f"/businesses/{business['id']}/branches", json=BRANCH_B, headers=h)).json()
    roles = {r["system_key"]: r["id"] for r in (await client.get("/roles", headers=h)).json()}
    return Shop(owner, business["id"], branch_a["id"], branch_b["id"], roles)


async def add_member(
    client: AsyncClient,
    shop: Shop,
    email: str,
    role: str,
    *,
    business: str | None = None,
    branch: str | None = None,
) -> dict[str, Any]:
    body = {
        "email": email,
        "full_name": email.split("@")[0],
        "password": PASSWORD,
        "assignments": [{"role_id": shop.roles[role], "business_id": business, "branch_id": branch}],
    }
    response = await client.post("/memberships", json=body, headers=bearer(shop.owner))
    assert response.status_code == 201, response.text
    member: dict[str, Any] = response.json()
    return member


async def login(client: AsyncClient, email: str) -> dict[str, Any]:
    response = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD, "client": "desktop"}
    )
    assert response.status_code == 200, response.text
    tokens: dict[str, Any] = response.json()
    return tokens


# (method, path builder, body, permission) — the matrix asserts each endpoint is gated by exactly this code.
Endpoint = tuple[str, Callable[[Shop], str], dict[str, Any] | None, str]
ENDPOINTS: list[Endpoint] = [
    ("GET", lambda s: "/businesses", None, "business.view"),
    (
        "POST",
        lambda s: "/businesses",
        {"legal_name_en": "New LLC", "legal_name_ar": "جديدة"},
        "business.create",
    ),
    ("GET", lambda s: f"/businesses/{s.business_id}/branches", None, "branch.view"),
    (
        "POST",
        lambda s: f"/businesses/{s.business_id}/branches",
        {"code": "N", "name_en": "N", "name_ar": "ن", "emirate": "ajman"},
        "branch.create",
    ),
    ("GET", lambda s: f"/branches/{s.branch_a}/warehouses", None, "warehouse.view"),
    (
        "POST",
        lambda s: f"/branches/{s.branch_a}/warehouses",
        {"code": "X", "name_en": "X", "name_ar": "س"},
        "warehouse.create",
    ),
    ("GET", lambda s: "/roles", None, "role.view"),
    ("GET", lambda s: "/permissions", None, "role.view"),
    ("POST", lambda s: "/roles", {"name_en": "Custom", "name_ar": "مخصص", "permissions": []}, "role.create"),
    ("GET", lambda s: "/memberships", None, "membership.view"),
]


@pytest.mark.parametrize("role", [r.system_key for r in default_roles()])
async def test_permission_matrix_for_every_default_role(client: AsyncClient, role: str) -> None:
    shop = await make_shop(client)
    email = f"{role}@shop.ae"
    if role != "owner":
        await add_member(client, shop, email, role)
    tokens = shop.owner if role == "owner" else await login(client, email)
    held = next(r.permissions for r in default_roles() if r.system_key == role)

    for method, path, body, permission in ENDPOINTS:
        response = await client.request(method, path(shop), json=body, headers=bearer(tokens))
        if permission in held:
            assert response.status_code in (200, 201), f"{role} {method} {path(shop)}: {response.text}"
        else:
            assert response.status_code == 403, f"{role} {method} {path(shop)} should be denied"
            assert response.json() == response.json() | {
                "code": "permission_denied",
                "permission": permission,
            }


async def test_branch_a_salesperson_is_confined_to_branch_a(client: AsyncClient) -> None:
    """Roadmap acceptance: a Salesperson scoped to Branch A cannot read Branch B."""
    shop = await make_shop(client)
    await add_member(
        client, shop, "sales@shop.ae", "salesperson", business=shop.business_id, branch=shop.branch_a
    )
    h = bearer(await login(client, "sales@shop.ae"))

    branches = (await client.get(f"/businesses/{shop.business_id}/branches", headers=h)).json()
    assert [b["id"] for b in branches] == [shop.branch_a]
    assert (await client.get(f"/branches/{shop.branch_a}/warehouses", headers=h)).status_code == 200
    denied = await client.get(f"/branches/{shop.branch_b}/warehouses", headers=h)
    assert denied.status_code == 403
    assert denied.json()["code"] == "permission_denied"
    assert (await client.get(f"/businesses/{shop.business_id}", headers=h)).status_code == 200, (
        "sees own business"
    )
    assert (await client.get("/businesses", headers=h)).json()[0]["id"] == shop.business_id


async def test_business_scoped_manager_cannot_create_businesses(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await add_member(client, shop, "mgr@shop.ae", "manager", business=shop.business_id)
    h = bearer(await login(client, "mgr@shop.ae"))

    new_branch = {"code": "M", "name_en": "M", "name_ar": "م", "emirate": "fujairah"}
    created = await client.post(f"/businesses/{shop.business_id}/branches", json=new_branch, headers=h)
    business = await client.post("/businesses", json={"legal_name_en": "X", "legal_name_ar": "س"}, headers=h)

    assert created.status_code == 201
    assert business.status_code == 403, "creating a business needs a tenant-wide grant"


async def test_signup_seeds_six_roles_and_makes_the_user_owner(client: AsyncClient) -> None:
    shop = await make_shop(client)

    members = (await client.get("/memberships", headers=bearer(shop.owner))).json()
    roles = (await client.get("/roles", headers=bearer(shop.owner))).json()

    assert [r["system_key"] for r in roles] == [
        "owner",
        "manager",
        "salesperson",
        "warehouse",
        "accountant",
        "cashier",
    ], "default roles come first, in seed order"
    assert len(shop.roles) == 6
    assert [(a["role_name_en"], a["business_id"]) for a in members[0]["assignments"]] == [("Owner", None)]


async def test_permissions_list_the_whole_registry_in_logical_order(client: AsyncClient) -> None:
    shop = await make_shop(client)

    codes = [p["code"] for p in (await client.get("/permissions", headers=bearer(shop.owner))).json()]

    assert set(codes) == ALL_PERMISSIONS
    assert codes[:3] == ["tenant.view", "tenant.edit", "business.view"]
    assert codes[-3:] == ["price.override_min", "credit.override", "period.override_lock"]


async def test_custom_role_lifecycle(client: AsyncClient) -> None:
    shop = await make_shop(client)
    h = bearer(shop.owner)

    created = await client.post(
        "/roles",
        json={"name_en": "Driver", "name_ar": "سائق", "permissions": ["delivery_note.view"]},
        headers=h,
    )
    assert created.status_code == 201
    role_id = created.json()["id"]
    patched = await client.patch(
        f"/roles/{role_id}", json={"permissions": ["delivery_note.view", "delivery_note.edit"]}, headers=h
    )
    assert patched.json()["permissions"] == ["delivery_note.edit", "delivery_note.view"]
    taken = await client.post(
        "/roles", json={"name_en": "Driver", "name_ar": "x", "permissions": []}, headers=h
    )
    assert taken.json()["code"] == "role_name_taken"
    assert (await client.delete(f"/roles/{role_id}", headers=h)).status_code == 204


async def test_role_guards(client: AsyncClient) -> None:
    shop = await make_shop(client)
    h = bearer(shop.owner)

    unknown = await client.post(
        "/roles", json={"name_en": "X", "name_ar": "س", "permissions": ["fly.away"]}, headers=h
    )
    owner_edit = await client.patch(f"/roles/{shop.roles['owner']}", json={"permissions": []}, headers=h)
    system_delete = await client.delete(f"/roles/{shop.roles['cashier']}", headers=h)
    await add_member(client, shop, "c@shop.ae", "cashier")

    assert unknown.status_code == 422
    assert unknown.json()["code"] == "unknown_permission"
    assert owner_edit.json()["code"] == "owner_role_locked"
    assert system_delete.json()["code"] == "system_role"


async def test_custom_role_in_use_cannot_be_deleted(client: AsyncClient) -> None:
    shop = await make_shop(client)
    h = bearer(shop.owner)
    role = (
        await client.post("/roles", json={"name_en": "Temp", "name_ar": "مؤقت", "permissions": []}, headers=h)
    ).json()
    shop.roles["temp"] = role["id"]
    await add_member(client, shop, "temp@shop.ae", "temp")

    response = await client.delete(f"/roles/{role['id']}", headers=h)

    assert response.json()["code"] == "role_in_use"


async def test_manager_cannot_grant_owner_or_demote_the_owner(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await add_member(client, shop, "mgr@shop.ae", "manager")
    h = bearer(await login(client, "mgr@shop.ae"))
    owner_membership = (await client.get("/memberships", headers=h)).json()
    owner_mid = next(m["membership_id"] for m in owner_membership if m["email"] == "owner@shop.ae")

    grant = await client.post(
        "/memberships",
        json={
            "email": "x@shop.ae",
            "full_name": "X",
            "password": PASSWORD,
            "assignments": [{"role_id": shop.roles["owner"]}],
        },
        headers=h,
    )
    demote = await client.put(
        f"/memberships/{owner_mid}/roles",
        json={"assignments": [{"role_id": shop.roles["cashier"]}]},
        headers=h,
    )

    assert grant.status_code == 403
    assert demote.status_code == 403


async def test_the_last_owner_cannot_be_removed(client: AsyncClient) -> None:
    shop = await make_shop(client)
    h = bearer(shop.owner)
    me = (await client.get("/me", headers=h)).json()

    response = await client.put(
        f"/memberships/{me['membership_id']}/roles",
        json={"assignments": [{"role_id": shop.roles["manager"]}]},
        headers=h,
    )

    assert response.status_code == 409
    assert response.json()["code"] == "last_owner"


async def test_branch_must_belong_to_the_business(client: AsyncClient) -> None:
    shop = await make_shop(client)
    other = await client.post(
        "/businesses",
        json={"legal_name_en": "Other LLC", "legal_name_ar": "أخرى"},
        headers=bearer(shop.owner),
    )
    body = {
        "email": "s@shop.ae",
        "full_name": "S",
        "password": PASSWORD,
        "assignments": [
            {
                "role_id": shop.roles["salesperson"],
                "business_id": other.json()["id"],
                "branch_id": shop.branch_a,
            }
        ],
    }

    response = await client.post("/memberships", json=body, headers=bearer(shop.owner))

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_scope"


async def test_existing_user_joins_with_their_own_password(client: AsyncClient) -> None:
    shop = await make_shop(client)
    await signup(client, "trader@elsewhere.ae", "Elsewhere")
    body = {
        "email": "trader@elsewhere.ae",
        "full_name": "Ignored",
        "password": "a different password",
        "assignments": [{"role_id": shop.roles["accountant"]}],
    }

    added = await client.post("/memberships", json=body, headers=bearer(shop.owner))
    again = await client.post("/memberships", json=body, headers=bearer(shop.owner))
    login_ok = await client.post(
        "/auth/login",
        json={"email": "trader@elsewhere.ae", "password": PASSWORD, "tenant_id": shop.owner["tenant_id"]},
    )

    assert added.status_code == 201
    assert again.json()["code"] == "already_member"
    assert login_ok.status_code == 200


async def test_suspended_member_is_refused(client: AsyncClient, owner_engine: AsyncEngine) -> None:
    shop = await make_shop(client)
    member = await add_member(client, shop, "gone@shop.ae", "cashier")
    tokens = await login(client, "gone@shop.ae")
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE membership SET status = 'suspended' WHERE id = :m"), {"m": member["membership_id"]}
        )

    response = await client.get("/businesses", headers=bearer(tokens))

    assert response.status_code == 403
    assert response.json()["code"] == "membership_inactive"
