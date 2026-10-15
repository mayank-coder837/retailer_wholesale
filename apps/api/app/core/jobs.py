"""Transactional outbox and the in-process worker (ADR 0012).

- ``publish()`` adds an event inside the business transaction: it exists only if the business change commits.
- ``@handler("event.type")`` registers an async function ``(session, event) -> None``.
- ``Worker.run_once()`` claims due events across tenants (a SECURITY DEFINER function, ``SKIP LOCKED``), then
  runs each in its own transaction scoped to the event's tenant and marks it dispatched in that transaction.
  A failure is retried with exponential backoff; after ``MAX_ATTEMPTS`` the event is dead. A worker that dies
  mid-batch leaves claims that expire after ``lock_seconds``, so delivery is at least once.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import AuditContext, set_context
from app.core.db import apply_context
from app.core.ids import new_id
from app.core.platform_models import OutboxEvent

log = logging.getLogger(__name__)

Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}
MAX_ATTEMPTS = 10


def handler(event_type: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        if event_type in _HANDLERS and _HANDLERS[event_type] is not fn:
            raise ValueError(f"handler already registered for {event_type}")
        _HANDLERS[event_type] = fn
        return fn

    return register


def unregister(event_type: str) -> None:
    _HANDLERS.pop(event_type, None)


def backoff(attempts: int) -> timedelta:
    """5 s, 10 s, 20 s … capped at one hour."""
    return timedelta(seconds=min(5 * 2 ** max(attempts - 1, 0), 3600))


async def publish(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any] | None = None,
    aggregate_type: str | None = None,
    aggregate_id: uuid.UUID | None = None,
) -> uuid.UUID:
    event = OutboxEvent(
        id=new_id(),
        tenant_id=tenant_id,
        event_type=event_type,
        payload=payload or {},
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
    )
    session.add(event)
    await session.flush()
    return event.id


class Worker:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        batch_size: int = 20,
        lock_seconds: int = 300,
        poll_seconds: float = 1.0,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.batch_size = batch_size
        self.lock_seconds = lock_seconds
        self.poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None

    async def claim(self) -> list[tuple[uuid.UUID, uuid.UUID]]:
        async with self.sessionmaker() as session, session.begin():
            rows = await session.execute(
                text("SELECT id, tenant_id FROM jobs_claim_batch(:n, :lock)"),
                {"n": self.batch_size, "lock": self.lock_seconds},
            )
            return [(r.id, r.tenant_id) for r in rows]

    async def run_once(self) -> int:
        """Processes one batch; returns how many events were handled successfully."""
        done = 0
        for event_id, tenant_id in await self.claim():
            if await self._run(event_id, tenant_id):
                done += 1
        return done

    async def _run(self, event_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        try:
            async with self.sessionmaker() as session, session.begin():
                await apply_context(session, tenant_id=tenant_id)
                set_context(session, AuditContext(actor_user_id=None, client="worker"))
                event = await session.get(OutboxEvent, event_id, with_for_update=True)
                if event is None or event.dispatched_at is not None or event.dead_at is not None:
                    return False
                fn = _HANDLERS.get(event.event_type)
                if fn is None:
                    raise LookupError(f"no handler for {event.event_type}")
                await fn(session, event)
                event.dispatched_at = datetime.now(UTC)
                event.locked_until = None
            return True
        except Exception as exc:  # any handler failure: record it and retry later
            log.warning("job %s failed: %s", event_id, exc)
            await self._fail(event_id, tenant_id, exc)
            return False

    async def _fail(self, event_id: uuid.UUID, tenant_id: uuid.UUID, exc: Exception) -> None:
        now = datetime.now(UTC)
        async with self.sessionmaker() as session, session.begin():
            await apply_context(session, tenant_id=tenant_id)
            event = await session.get(OutboxEvent, event_id, with_for_update=True)
            if event is None:
                return
            event.attempts += 1
            event.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            event.locked_until = None
            if event.attempts >= MAX_ATTEMPTS:
                event.dead_at = now
            else:
                event.available_at = now + backoff(event.attempts)

    async def _loop(self) -> None:
        while True:
            try:
                handled = await self.run_once()
            except Exception:  # the database may be briefly unavailable; keep the worker alive
                log.exception("job worker batch failed")
                handled = 0
            if not handled:
                await asyncio.sleep(self.poll_seconds)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="smartbiz-jobs")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


async def pending_events(session: AsyncSession) -> list[OutboxEvent]:
    """Undispatched, live events of the current tenant (for diagnostics and tests)."""
    stmt = select(OutboxEvent).where(OutboxEvent.dispatched_at.is_(None), OutboxEvent.dead_at.is_(None))
    return list((await session.scalars(stmt)).all())
