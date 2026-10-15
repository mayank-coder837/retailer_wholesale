"""`make seed`: a demo tenant you can log in with, created through the API, safe to run twice."""

import pytest
from httpx import AsyncClient

from app.core import config
from app.core.config import Settings
from app.scripts import seed


@pytest.fixture
def seed_settings(settings: Settings, clean_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(seed, "get_settings", lambda: settings)


async def test_seed_creates_a_demo_tenant_you_can_log_in_with(
    seed_settings: None, client: AsyncClient
) -> None:
    first = await seed.seed()
    second = await seed.seed()

    assert "created" in first
    assert "already there" in second
    login = await client.post("/auth/login", json={"email": seed.DEMO_EMAIL, "password": seed.DEMO_PASSWORD})
    assert login.status_code == 200, login.text
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    businesses = (await client.get("/businesses", headers=auth)).json()
    assert [b["legal_name_en"] for b in businesses] == ["Al Noor General Trading LLC"], (
        "one business, not two"
    )
    packs = (await client.get(f"/businesses/{businesses[0]['id']}/industry-packs", headers=auth)).json()
    assert [p["pack_code"] for p in packs] == [seed.DEMO_PACK]
