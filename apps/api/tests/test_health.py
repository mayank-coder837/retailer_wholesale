import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app import __version__
from app.core.config import DEFAULT_PRODUCT_CONFIG, Environment, Settings
from app.main import create_app


def product_name() -> str:
    name: str = json.loads(DEFAULT_PRODUCT_CONFIG.read_text(encoding="utf-8"))["name"]
    return name


async def test_health_reports_product_from_config(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "product": product_name(), "version": __version__}


async def test_health_db_ok_when_database_answers(client: AsyncClient) -> None:
    """Needs the dev database (`make up`); CI provides a postgres:17 service."""
    response = await client.get("/health/db")

    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["server_version"].startswith("17")


@pytest.fixture
async def unreachable_db_client() -> AsyncIterator[AsyncClient]:
    settings = Settings(
        environment=Environment.TEST,
        database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none",
        database_connect_timeout_s=1.0,
    )
    application = create_app(settings)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as c,
    ):
        yield c


async def test_health_db_returns_problem_when_database_is_down(unreachable_db_client: AsyncClient) -> None:
    response = await unreachable_db_client.get("/health/db")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "database_unavailable"
    assert body["status"] == 503
    assert body["instance"] == "/health/db"


async def test_unknown_route_is_a_problem_response(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
