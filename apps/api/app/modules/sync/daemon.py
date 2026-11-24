"""Starts cloud sync on a branch's main PC when a cloud address and account are configured (M2.1b)."""

import asyncio
import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import NodeRole, Settings
from app.core.db import apply_context
from app.modules.sync.client import CloudSync
from app.modules.tenancy import service as tenancy

log = logging.getLogger(__name__)


def _account(settings: Settings) -> tuple[str, str]:
    if settings.sync_email is None or settings.sync_password is None:
        raise RuntimeError("cloud sync needs sync_email and sync_password")
    return settings.sync_email, settings.sync_password.get_secret_value()


async def _tenant_and_node(
    http: httpx.AsyncClient, sessions: async_sessionmaker[AsyncSession], settings: Settings
) -> tuple[uuid.UUID, uuid.UUID]:
    """The cloud account says which tenant this is; the local database says which branch node we are."""
    email, password = _account(settings)
    response = await http.post(
        "/auth/login",
        json={"email": email, "password": password, "client": "desktop"},
    )
    response.raise_for_status()
    tenant_id = uuid.UUID(response.json()["tenant_id"])
    async with sessions() as session, session.begin():
        await apply_context(session, tenant_id=tenant_id)
        node = await tenancy.get_main_node(session)
    if node is None:
        raise LookupError("this PC has no branch node for that account yet")
    return tenant_id, node.id


async def _run(settings: Settings, sessions: async_sessionmaker[AsyncSession]) -> None:
    if settings.cloud_url is None:
        raise RuntimeError("cloud sync needs cloud_url")
    email, password = _account(settings)
    async with httpx.AsyncClient(base_url=settings.cloud_url, timeout=30.0) as http:
        while True:  # until it can find out who it is (offline at start-up only delays)
            try:
                tenant_id, node_id = await _tenant_and_node(http, sessions, settings)
                break
            except (httpx.HTTPError, LookupError) as error:
                log.info("sync not started yet: %s", error)
                await asyncio.sleep(settings.sync_interval_s)
        sync = CloudSync(http, sessions, tenant_id=tenant_id, node_id=node_id, email=email, password=password)
        await sync.run(settings.sync_interval_s)


async def start(
    settings: Settings, sessions: async_sessionmaker[AsyncSession]
) -> "asyncio.Task[None] | None":
    """A background task, or nothing when this installation is not set up to sync (or is the cloud itself)."""
    if settings.node_role is NodeRole.CLOUD or not (
        settings.cloud_url and settings.sync_email and settings.sync_password
    ):
        return None
    return asyncio.create_task(_run(settings, sessions), name="smartbiz-sync")
