"""Sync over HTTP (M2.1b): a branch PC and the cloud's API converge; offline only delays; a refusal halts.

The first upload of a branch's data (enrolment) is done with the engine; after that the branch's own pusher
talks to the cloud API, signing in with the owner's account, which reached the cloud with the data.
"""

import uuid

import httpx
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import apply_context
from app.modules.sync import service
from app.modules.sync.client import CloudSync
from tests.factories import PASSWORD
from tests.test_pricing_api import ok
from tests.test_sync_engine import books_summary, made_something, send_all

Sessions = async_sessionmaker[AsyncSession]


def pusher(
    http: httpx.AsyncClient, sm: Sessions, tenant: uuid.UUID, node: uuid.UUID, email: str
) -> CloudSync:
    return CloudSync(http, sm, tenant_id=tenant, node_id=node, email=email, password=PASSWORD)


async def count(sm: Sessions, tenant: uuid.UUID, sql: str) -> int:
    async with sm() as s, s.begin():
        await apply_context(s, tenant_id=tenant)
        return int(await s.scalar(text(sql)) or 0)


async def test_a_branch_and_the_cloud_converge_in_both_directions(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    b, tenant, node = await made_something(client, "tr1@shop.ae")
    await send_all(app_sessionmaker, cloud, tenant, node)  # the first upload
    await b.invoice("300", "INV-2")  # more work at the branch, offline so far

    sync = pusher(cloud_http, app_sessionmaker, tenant, node, "tr1@shop.ae")
    first = await sync.cycle()
    assert (first.offline, first.halted) == (False, None)
    assert first.pushed > 0
    assert await books_summary(app_sessionmaker, tenant) == await books_summary(cloud, tenant)

    # Something done in the browser, against the cloud:
    token = (
        await ok(await cloud_http.post("/auth/login", json={"email": "tr1@shop.ae", "password": PASSWORD}))
    )["access_token"]
    party = {"name_en": "Cloud Co", "name_ar": "سحابة", "is_customer": True}
    await ok(await cloud_http.post("/parties", json=party, headers={"Authorization": f"Bearer {token}"}), 201)
    second = await sync.cycle()
    assert second.pulled > 0
    assert second.pushed == 0, "what came from the cloud is not sent back"
    assert await count(app_sessionmaker, tenant, "SELECT count(*) FROM party WHERE name_en = 'Cloud Co'") == 1
    assert (await sync.cycle()).pulled == 0
    assert await count(cloud, tenant, "SELECT count(*) FROM party") == await count(
        app_sessionmaker, tenant, "SELECT count(*) FROM party"
    )


async def test_offline_only_delays(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    b, tenant, node = await made_something(client, "tr2@shop.ae")
    await send_all(app_sessionmaker, cloud, tenant, node)
    await b.invoice("125", "INV-3")

    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no internet")

    dead = httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://cloud")
    result = await pusher(dead, app_sessionmaker, tenant, node, "tr2@shop.ae").cycle()
    assert (result.offline, result.pushed) == (True, 0)
    assert await books_summary(app_sessionmaker, tenant) != await books_summary(cloud, tenant)

    back = await pusher(cloud_http, app_sessionmaker, tenant, node, "tr2@shop.ae").cycle()
    assert (back.offline, back.pushed > 0) == (False, True)
    assert await books_summary(app_sessionmaker, tenant) == await books_summary(cloud, tenant)


async def test_a_batch_the_cloud_refuses_halts_this_pc_and_skips_nothing(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    b, tenant, node = await made_something(client, "tr3@shop.ae")
    await send_all(app_sessionmaker, cloud, tenant, node)
    bad = (
        f'{{"id": "{uuid.uuid4()}", "tenant_id": "{tenant}", "trn": "not-a-trn", '
        '"legal_name_en": "x", "base_currency": "AED"}'
    )
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=tenant, node_id=node)
        await s.execute(
            text(
                "INSERT INTO sync_outbox (tenant_id, node_id, table_name, row_id, op, payload) "
                "VALUES (:t, :n, 'business', :r, 'insert', CAST(:p AS jsonb))"
            ),
            {"t": tenant, "n": node, "r": uuid.uuid4(), "p": bad},
        )
    await b.invoice("50", "INV-9")  # a good change queued behind the bad one

    sync = pusher(cloud_http, app_sessionmaker, tenant, node, "tr3@shop.ae")
    result = await sync.cycle()
    assert result.halted == "sync_batch_rejected"
    assert await count(cloud, tenant, "SELECT count(*) FROM business") == 1  # the bad row did not land
    again = await sync.cycle()
    assert (again.halted, again.pushed) == ("sync_batch_rejected", 0)  # and nothing is skipped past it


async def test_only_a_signed_in_account_with_the_sync_permission_may_use_it(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    b, tenant, node = await made_something(client, "tr4@shop.ae")
    await send_all(app_sessionmaker, cloud, tenant, node)
    assert (await cloud_http.get("/sync/pull", params={"node_id": str(node)})).status_code == 401
    # A salesperson on the cloud (made there, as a branch owner would) holds neither sync.view nor sync.post.
    token = (
        await ok(await cloud_http.post("/auth/login", json={"email": "tr4@shop.ae", "password": PASSWORD}))
    )["access_token"]
    owner = {"Authorization": f"Bearer {token}"}
    roles = {r["system_key"]: r["id"] for r in await ok(await cloud_http.get("/roles", headers=owner))}
    body = {
        "email": "sales4@shop.ae",
        "full_name": "Sales",
        "password": PASSWORD,
        "assignments": [{"role_id": roles["salesperson"], "business_id": None, "branch_id": None}],
    }
    await ok(await cloud_http.post("/memberships", json=body, headers=owner), 201)
    login = await ok(
        await cloud_http.post("/auth/login", json={"email": "sales4@shop.ae", "password": PASSWORD})
    )
    sales = {"Authorization": f"Bearer {login['access_token']}"}
    assert (
        await cloud_http.get("/sync/pull", params={"node_id": str(node)}, headers=sales)
    ).status_code == 403
    push = {"node_id": str(node), "entries": []}
    assert (await cloud_http.post("/sync/push", json=push, headers=sales)).status_code == 403
    assert (
        await cloud_http.get("/sync/pull", params={"node_id": str(node)}, headers=owner)
    ).status_code == 200
    _ = b


async def test_per_month_checksums_agree_once_synced_and_name_the_month_that_does_not(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions, cloud_http: AsyncClient
) -> None:
    b, tenant, node = await made_something(client, "tr5@shop.ae")
    await send_all(app_sessionmaker, cloud, tenant, node)
    await b.invoice("75", "INV-4")  # not on the cloud yet
    business = uuid.UUID(b.shop.business_id)

    async def both() -> tuple[dict[str, dict[str, list[str]]], dict[str, dict[str, list[str]]]]:
        async with app_sessionmaker() as s, s.begin():
            await apply_context(s, tenant_id=tenant)
            here = await service.checksum(s, business)
        token = (
            await ok(
                await cloud_http.post("/auth/login", json={"email": "tr5@shop.ae", "password": PASSWORD})
            )
        )["access_token"]
        there = await ok(
            await cloud_http.get(
                "/sync/checksum",
                params={"business_id": str(business)},
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        return here, there

    here, there = await both()
    assert here["journal"]
    assert service.differing_periods(here, there) != []  # the branch is ahead: the month shows
    await pusher(cloud_http, app_sessionmaker, tenant, node, "tr5@shop.ae").cycle()
    here, there = await both()
    assert service.differing_periods(here, there) == []
