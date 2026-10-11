"""Outbox and worker: delivered at least once, retried with backoff, run under the event's tenant."""

from collections.abc import Iterator

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core import jobs
from app.core.db import apply_context
from app.core.platform_models import OutboxEvent
from app.modules.tenancy.models import Business
from tests.factories import TenantSeed, seed_tenant

pytestmark = pytest.mark.usefixtures("clean_db")

calls: list[str] = []


@pytest.fixture(autouse=True)
def _handlers() -> Iterator[None]:
    calls.clear()
    yield
    for event_type in ("test.ok", "test.flaky", "test.boom", "test.count"):
        jobs.unregister(event_type)


async def _seed_with_event(
    sm: async_sessionmaker[AsyncSession], event_type: str, *, commit: bool = True
) -> TenantSeed:
    async with sm() as s:
        await s.begin()
        seed = await seed_tenant(s)
        await jobs.publish(s, tenant_id=seed.tenant_id, event_type=event_type, payload={"n": 1})
        await (s.commit() if commit else s.rollback())
    return seed


async def _event(owner_engine: AsyncEngine) -> OutboxEvent:
    async with AsyncSession(owner_engine) as s:
        return (await s.scalars(select(OutboxEvent))).one()


async def test_event_published_in_a_rolled_back_transaction_never_exists(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    await _seed_with_event(app_sessionmaker, "test.ok", commit=False)

    async with owner_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM outbox_event"))).scalar_one() == 0


async def test_handler_runs_once_and_event_is_marked_dispatched(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    @jobs.handler("test.ok")
    async def ok(_session: AsyncSession, event: OutboxEvent) -> None:
        calls.append(f"ok:{event.payload['n']}")

    await _seed_with_event(app_sessionmaker, "test.ok")
    worker = jobs.Worker(app_sessionmaker)

    assert await worker.run_once() == 1
    assert await worker.run_once() == 0
    assert calls == ["ok:1"]
    assert (await _event(owner_engine)).dispatched_at is not None


async def test_failure_is_retried_with_backoff(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    @jobs.handler("test.flaky")
    async def flaky(_session: AsyncSession, _event: OutboxEvent) -> None:
        calls.append("try")
        if len(calls) == 1:
            raise RuntimeError("courier API down")

    await _seed_with_event(app_sessionmaker, "test.flaky")
    worker = jobs.Worker(app_sessionmaker)

    assert await worker.run_once() == 0
    failed = await _event(owner_engine)
    assert (failed.attempts, failed.last_error) == (1, "RuntimeError: courier API down")
    assert await worker.run_once() == 0, "not due yet: backoff"

    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE outbox_event SET available_at = now()"))
    assert await worker.run_once() == 1
    assert calls == ["try", "try"], "at least once: the handler ran again"


async def test_a_dead_workers_claim_expires_and_the_event_is_reclaimed(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    await _seed_with_event(app_sessionmaker, "test.ok")
    crashed = jobs.Worker(app_sessionmaker, lock_seconds=300)

    assert len(await crashed.claim()) == 1
    assert await jobs.Worker(app_sessionmaker).claim() == [], "still locked by the crashed worker"
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE outbox_event SET locked_until = now() - interval '1 second'"))
    assert len(await jobs.Worker(app_sessionmaker).claim()) == 1


async def test_event_dies_after_max_attempts(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    @jobs.handler("test.boom")
    async def boom(_session: AsyncSession, _event: OutboxEvent) -> None:
        raise ValueError("bad payload")

    await _seed_with_event(app_sessionmaker, "test.boom")
    async with owner_engine.begin() as conn:
        await conn.execute(text("UPDATE outbox_event SET attempts = :n"), {"n": jobs.MAX_ATTEMPTS - 1})

    await jobs.Worker(app_sessionmaker).run_once()

    event = await _event(owner_engine)
    assert event.dead_at is not None
    assert event.attempts == jobs.MAX_ATTEMPTS


async def test_unknown_event_type_is_recorded_as_a_failure(
    app_sessionmaker: async_sessionmaker[AsyncSession], owner_engine: AsyncEngine
) -> None:
    await _seed_with_event(app_sessionmaker, "test.nobody")

    await jobs.Worker(app_sessionmaker).run_once()

    assert "no handler" in ((await _event(owner_engine)).last_error or "")


async def test_handlers_run_inside_the_events_tenant(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seen: list[int] = []

    @jobs.handler("test.count")
    async def count(session: AsyncSession, _event: OutboxEvent) -> None:
        seen.append((await session.execute(select(func.count()).select_from(Business))).scalar_one())

    async with app_sessionmaker() as s, s.begin():
        await seed_tenant(s, "Other")
    await _seed_with_event(app_sessionmaker, "test.count")

    assert await jobs.Worker(app_sessionmaker).run_once() == 1
    assert seen == [1], "row-level security applies inside handlers"


async def test_backoff_grows_and_is_capped() -> None:
    assert [jobs.backoff(n).total_seconds() for n in (1, 2, 3)] == [5, 10, 20]
    assert jobs.backoff(30).total_seconds() == 3600


async def test_pending_events_lists_only_live_ones(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed_with_event(app_sessionmaker, "test.ok")
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        assert [e.event_type for e in await jobs.pending_events(s)] == ["test.ok"]
