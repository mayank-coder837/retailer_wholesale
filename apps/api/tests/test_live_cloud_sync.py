"""Live sync against the real cloud database (Supabase), not collected unless asked for.

    SMARTBIZ_LIVE_CLOUD_DATABASE_URL=<the cloud URL> uv run pytest tests/test_live_cloud_sync.py

It makes one throwaway demo tenant on the local test database, sends it to the cloud, runs the branch's real
pusher against the cloud's API (in process, on the cloud database), makes a change "in the browser" and checks
that both sides end with the same books and checksums. It never truncates anything on the cloud: the demo
tenant
has a unique e-mail and stays there as test data (posted rows are permanent by design).
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import NodeRole, Settings
from app.core.db import apply_context, create_engine, create_sessionmaker
from app.main import create_app
from app.modules.sync import service
from tests.factories import PASSWORD
from tests.test_pricing_api import ok
from tests.test_sync_engine import books_summary, made_something, send_all
from tests.test_sync_transport import count, pusher

LIVE_URL = os.environ.get("SMARTBIZ_LIVE_CLOUD_DATABASE_URL")

pytestmark = pytest.mark.skipif(LIVE_URL is None, reason="set SMARTBIZ_LIVE_CLOUD_DATABASE_URL to run")

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
async def live_settings(settings: Settings) -> Settings:
    assert LIVE_URL is not None
    return settings.model_copy(update={"database_url": LIVE_URL, "node_role": NodeRole.CLOUD})


@pytest.fixture
async def live_cloud(live_settings: Settings) -> AsyncIterator[Sessions]:
    engine = create_engine(live_settings)
    yield create_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def live_http(live_settings: Settings, live_cloud: Sessions) -> AsyncIterator[AsyncClient]:
    app: FastAPI = create_app(live_settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://cloud", timeout=60.0) as http,
    ):
        yield http


async def test_a_branch_and_the_real_cloud_converge(
    client: AsyncClient, app_sessionmaker: Sessions, live_cloud: Sessions, live_http: AsyncClient
) -> None:
    email = f"live-{uuid.uuid4().hex[:8]}@shop.ae"
    b, tenant, node = await made_something(client, email)

    assert await send_all(app_sessionmaker, live_cloud, tenant, node) > 20  # the first upload
    await b.invoice("300", "INV-2")  # more work while "offline"

    async with live_cloud() as c, c.begin():  # the cloud's own node, as a real deployment has
        await apply_context(c, tenant_id=tenant)
        await c.execute(
            text("INSERT INTO node (id, tenant_id, kind, name) VALUES (:i, :t, 'cloud', 'Cloud')"),
            {"i": uuid.uuid4(), "t": tenant},
        )
    sync = pusher(live_http, app_sessionmaker, tenant, node, email)
    first = await sync.cycle()
    assert (first.offline, first.halted) == (False, None)
    assert first.pushed > 0
    here, there = await books_summary(app_sessionmaker, tenant), await books_summary(live_cloud, tenant)
    assert here == there
    assert there["debit"] == there["credit"] > 0

    # Something done in the browser, against the cloud API:
    token = (await ok(await live_http.post("/auth/login", json={"email": email, "password": PASSWORD})))[
        "access_token"
    ]
    party = {"name_en": "Cloud Co", "name_ar": "سحابة", "is_customer": True}
    await ok(await live_http.post("/parties", json=party, headers={"Authorization": f"Bearer {token}"}), 201)
    second = await sync.cycle()
    assert second.pulled > 0
    assert second.pushed == 0, "what came from the cloud is not sent back"
    assert await count(app_sessionmaker, tenant, "SELECT count(*) FROM party WHERE name_en = 'Cloud Co'") == 1

    # Per-month checksums agree between the branch and the cloud.
    business = uuid.UUID(b.shop.business_id)
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=tenant)
        here_sums = await service.checksum(s, business)
    there_sums = await ok(
        await live_http.get(
            "/sync/checksum",
            params={"business_id": str(business)},
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    assert here_sums["journal"]
    assert service.differing_periods(here_sums, there_sums) == []
