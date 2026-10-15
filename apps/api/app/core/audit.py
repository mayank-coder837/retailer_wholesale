"""Generic audit log (CLAUDE.md rule 8).

Every ORM create/update/delete of a tenant model is recorded automatically by a flush hook, with the actor
from the session's ``AuditContext``. Business actions that are not a plain row change (post, cancel, reverse)
call ``record()``. Rows are append-only (see migration 0004).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, cast

from sqlalchemy import event, insert, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstanceState, Session, UOWTransaction

from app.core.ids import new_id
from app.core.models import TenantScoped
from app.core.platform_models import AuditLog

AUDIT_KEY = "audit_context"
# Bookkeeping columns: maintained by the database, never interesting as "changes".
IGNORED = frozenset(
    {"created_at", "created_by", "updated_at", "updated_by", "origin_node_id", "id", "tenant_id"}
)


@dataclass(frozen=True, slots=True)
class AuditContext:
    actor_user_id: uuid.UUID | None
    client: str | None = None
    request_id: str | None = None
    node_id: uuid.UUID | None = None
    via_ai: bool = False
    on_behalf_of_user_id: uuid.UUID | None = None
    ai_trace_id: uuid.UUID | None = None


def set_context(session: AsyncSession | Session, context: AuditContext) -> None:
    target = session.sync_session if isinstance(session, AsyncSession) else session
    target.info[AUDIT_KEY] = context


def jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [jsonable(v) for v in value]
    return value


def _state(obj: TenantScoped) -> InstanceState[Any]:
    return cast("InstanceState[Any]", inspect(obj))


def _row_dict(obj: TenantScoped) -> dict[str, Any]:
    return {
        attr.key: jsonable(getattr(obj, attr.key))
        for attr in _state(obj).mapper.column_attrs
        if attr.key not in IGNORED and attr.key in obj.__dict__
    }


def _changes(obj: TenantScoped) -> dict[str, list[Any]]:
    state = _state(obj)
    out: dict[str, list[Any]] = {}
    for attr in state.mapper.column_attrs:
        if attr.key in IGNORED:
            continue
        history = state.attrs[attr.key].history
        if history.has_changes():
            old = history.deleted[0] if history.deleted else None
            new = history.added[0] if history.added else None
            if old != new:
                out[attr.key] = [jsonable(old), jsonable(new)]
    return out


def _entry(context: AuditContext | None, obj: TenantScoped, action: str, **data: Any) -> dict[str, Any]:
    c = context or AuditContext(actor_user_id=None)
    return {
        "tenant_id": obj.tenant_id,
        "actor_user_id": c.actor_user_id,
        "via_ai": c.via_ai,
        "on_behalf_of_user_id": c.on_behalf_of_user_id,
        "ai_trace_id": c.ai_trace_id,
        "client": c.client,
        "request_id": c.request_id,
        "node_id": c.node_id,
        "action": action,
        "entity_type": _state(obj).mapper.local_table.description,
        "entity_id": obj.id,
        **data,
    }


def _audited(obj: object) -> bool:
    """Tenant models are audited unless they opt out with ``__audit__ = False`` (pure counters)."""
    return isinstance(obj, TenantScoped) and getattr(type(obj), "__audit__", True)


def _collect(session: Session) -> list[dict[str, Any]]:
    context: AuditContext | None = session.info.get(AUDIT_KEY)
    entries: list[dict[str, Any]] = []
    for obj in session.new:
        if _audited(obj):
            if obj.id is None:  # the column default runs during the flush, after this hook
                obj.id = new_id()
            row = _row_dict(obj)
            entries.append(_entry(context, obj, "create", changes={k: [None, v] for k, v in row.items()}))
    for obj in session.dirty:
        if _audited(obj) and session.is_modified(obj, include_collections=False):
            changes = _changes(obj)
            if changes:
                entries.append(_entry(context, obj, "update", changes=changes))
    for obj in session.deleted:
        if _audited(obj):
            entries.append(_entry(context, obj, "delete", snapshot=_row_dict(obj)))
    return entries


@event.listens_for(Session, "before_flush")
def _capture(session: Session, _flush_context: UOWTransaction, _instances: Any) -> None:
    """Changes are read before the flush (history is lost afterwards) and written after it.

    Replaced, not appended: entries of a flush that failed (a unique clash in a savepoint) are dropped.
    """
    session.info["audit_pending"] = _collect(session)


@event.listens_for(Session, "after_flush")
def _write(session: Session, _flush_context: UOWTransaction) -> None:
    pending: list[dict[str, Any]] = session.info.pop("audit_pending", [])
    if pending:
        session.connection().execute(insert(AuditLog), pending)


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop("audit_pending", None)


async def record(
    session: AsyncSession,
    obj: TenantScoped,
    action: str,
    *,
    reason: str | None = None,
    changes: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> None:
    """Explicit entry for business actions (post, cancel, reverse, apply) and grouped changes."""
    context: AuditContext | None = session.sync_session.info.get(AUDIT_KEY)
    entry = _entry(
        context,
        obj,
        action,
        reason=reason,
        changes=jsonable(changes) if changes else None,
        snapshot=snapshot if snapshot is not None else (None if changes else _row_dict(obj)),
    )
    await session.execute(insert(AuditLog), [entry])
