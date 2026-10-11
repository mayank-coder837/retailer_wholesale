import uuid
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.ids import new_id
from app.core.security import Principal, create_access_token
from tests.api_helpers import bearer, signup, signup_body
from tests.factories import PASSWORD


async def test_signup_creates_account_and_logs_in(client: AsyncClient) -> None:
    tokens = await signup(client, "owner@alnoor.ae")

    me = await client.get("/me", headers=bearer(tokens))

    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "owner@alnoor.ae"
    assert body["tenant"]["name"] == "Al Noor Trading"
    assert body["node_id"] is not None
    assert [m["tenant_name"] for m in body["memberships"]] == ["Al Noor Trading"]


async def test_web_signup_sets_httponly_cookie_and_keeps_token_out_of_body(client: AsyncClient) -> None:
    response = await client.post("/auth/signup", json=signup_body("web@alnoor.ae"))

    assert response.status_code == 201
    assert response.json()["refresh_token"] is None
    cookie = response.headers["set-cookie"]
    assert "smartbiz_refresh=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/auth" in cookie
    assert "SameSite=strict" in cookie


async def test_web_refresh_uses_the_cookie(client: AsyncClient) -> None:
    await client.post("/auth/signup", json=signup_body("cookie@alnoor.ae"))

    response = await client.post("/auth/refresh", json={})

    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_signup_rejects_duplicate_email(client: AsyncClient) -> None:
    await signup(client, "dup@alnoor.ae")

    response = await client.post("/auth/signup", json=signup_body("DUP@alnoor.ae"))

    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


async def test_signup_requires_trn_when_vat_registered(client: AsyncClient) -> None:
    body = signup_body("vat@alnoor.ae")
    body["business"]["vat_registered"] = True

    response = await client.post("/auth/signup", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_signup_requires_a_valid_emirate(client: AsyncClient) -> None:
    body = signup_body("emirate@alnoor.ae")
    body["head_office"]["emirate"] = "doha"

    response = await client.post("/auth/signup", json=body)

    assert response.status_code == 422


async def test_login_and_wrong_password(client: AsyncClient) -> None:
    await signup(client, "login@alnoor.ae")

    ok = await client.post("/auth/login", json={"email": "login@alnoor.ae", "password": PASSWORD})
    wrong = await client.post(
        "/auth/login", json={"email": "login@alnoor.ae", "password": "not the password"}
    )
    unknown = await client.post("/auth/login", json={"email": "nobody@alnoor.ae", "password": PASSWORD})

    assert ok.status_code == 200
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "invalid_credentials"
    assert unknown.json()["code"] == "invalid_credentials", "same answer for unknown emails"


async def test_five_failed_logins_lock_the_account(client: AsyncClient, settings: Settings) -> None:
    await signup(client, "lock@alnoor.ae")
    for _ in range(settings.login_max_failures):
        await client.post("/auth/login", json={"email": "lock@alnoor.ae", "password": "wrong password"})

    response = await client.post("/auth/login", json={"email": "lock@alnoor.ae", "password": PASSWORD})

    assert response.status_code == 423
    assert response.json()["code"] == "account_locked"


async def test_refresh_rotates_and_reuse_ends_the_session(client: AsyncClient) -> None:
    first = await signup(client, "rotate@alnoor.ae")

    second = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    second_token = second.json()["refresh_token"]
    assert second_token != first["refresh_token"]

    replay = await client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    assert replay.json()["code"] == "refresh_token_reused"

    after = await client.post("/auth/refresh", json={"refresh_token": second_token})
    assert after.status_code == 401, "reuse of an old token revokes the whole family, including the newest"
    assert after.json()["code"] == "invalid_refresh_token"


async def test_logout_revokes_the_refresh_token(client: AsyncClient) -> None:
    tokens = await signup(client, "logout@alnoor.ae")

    out = await client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    again = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert out.status_code == 204
    assert again.status_code == 401


async def test_unknown_refresh_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/auth/refresh", json={"refresh_token": "not-a-token"})

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_refresh_token"


async def test_user_in_two_tenants_chooses_and_switches(
    client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    first = await signup(client, "multi@alnoor.ae", "First Co")
    other = await signup(client, "other@alnoor.ae", "Second Co")
    me = (await client.get("/me", headers=bearer(first))).json()
    async with owner_engine.begin() as conn:  # invitations arrive in M0.3; add the membership directly
        await conn.execute(
            text("INSERT INTO membership (id, tenant_id, user_id) VALUES (:id, :t, :u)"),
            {"id": new_id(), "t": other["tenant_id"], "u": me["user"]["id"]},
        )

    ambiguous = await client.post("/auth/login", json={"email": "multi@alnoor.ae", "password": PASSWORD})
    assert ambiguous.status_code == 409
    assert ambiguous.json()["code"] == "tenant_selection_required"
    assert {m["tenant_name"] for m in ambiguous.json()["memberships"]} == {"First Co", "Second Co"}

    chosen = await client.post(
        "/auth/login",
        json={
            "email": "multi@alnoor.ae",
            "password": PASSWORD,
            "tenant_id": other["tenant_id"],
            "client": "desktop",
        },
    )
    assert chosen.status_code == 200
    assert chosen.json()["tenant_id"] == other["tenant_id"]

    switched = await client.post(
        "/auth/switch-tenant",
        json={"tenant_id": first["tenant_id"], "client": "desktop"},
        headers=bearer(chosen.json()),
    )
    assert switched.status_code == 200
    assert (await client.get("/me", headers=bearer(switched.json()))).json()["tenant"]["name"] == "First Co"
    old_session = await client.post("/auth/refresh", json={"refresh_token": chosen.json()["refresh_token"]})
    assert old_session.status_code == 401, "switching ends the previous session's refresh tokens"


async def test_cannot_switch_into_a_tenant_without_membership(client: AsyncClient) -> None:
    mine = await signup(client, "mine@alnoor.ae", "Mine")
    theirs = await signup(client, "theirs@alnoor.ae", "Theirs")

    response = await client.post(
        "/auth/switch-tenant", json={"tenant_id": theirs["tenant_id"]}, headers=bearer(mine)
    )

    assert response.status_code == 401


async def test_protected_routes_reject_missing_forged_and_expired_tokens(
    client: AsyncClient, settings: Settings
) -> None:
    tokens = await signup(client, "tokens@alnoor.ae")
    principal = Principal(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), None, uuid.uuid4())
    forged = create_access_token(principal, settings.model_copy(update={"jwt_secret": "x" * 48}))
    expired = create_access_token(principal, settings, now=datetime.now(UTC) - timedelta(hours=1))

    assert (await client.get("/me")).status_code == 401
    assert (await client.get("/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401
    assert (await client.get("/me", headers={"Authorization": f"Bearer {expired}"})).status_code == 401
    assert (await client.get("/me", headers=bearer(tokens))).status_code == 200


async def test_passwords_are_stored_as_argon2id(client: AsyncClient, owner_engine: AsyncEngine) -> None:
    await signup(client, "hash@alnoor.ae")
    async with owner_engine.connect() as conn:
        stored = (
            await conn.execute(text("SELECT password_hash FROM user_account WHERE email = 'hash@alnoor.ae'"))
        ).scalar_one()
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored
