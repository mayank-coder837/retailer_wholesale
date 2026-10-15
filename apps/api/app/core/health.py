"""Liveness and readiness endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.core.config import Settings, get_settings
from app.core.errors import DatabaseUnavailableError

router = APIRouter(prefix="/health", tags=["health"])


class HealthOut(BaseModel):
    status: str
    product: str
    version: str


class DatabaseHealthOut(BaseModel):
    database: str
    server_version: str


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


@router.get("", operation_id="health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthOut:
    """Liveness: the API process is up. Does not touch the database."""
    return HealthOut(status="ok", product=settings.product.name, version=__version__)


@router.get(
    "/db",
    operation_id="health_db",
    responses={503: {"description": "Database unavailable (problem+json, code `database_unavailable`)"}},
)
async def health_db(engine: Annotated[AsyncEngine, Depends(get_engine)]) -> DatabaseHealthOut:
    """Readiness: the database answers."""
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SHOW server_version"))).scalar_one()
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        raise DatabaseUnavailableError("The database did not answer.") from exc
    return DatabaseHealthOut(database="ok", server_version=str(version))
