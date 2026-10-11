from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Environment, Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(environment=Environment.TEST)


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
