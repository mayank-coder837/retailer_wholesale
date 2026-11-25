"""The sync engine (M2.1a, ADR 0010): what a node has to send, and applying what another node sent.

No network here. A *batch* is a list of ``Entry`` (one captured write each, in the order the sender's
database made them). ``apply`` runs a batch in one transaction on the receiving database, in order, entirely
in SQL (``jsonb_populate_record``) so amounts never pass through floating point. The receiver's own
constraints then re-validate everything: the balance trigger, immutability of posted rows, row-level
security. A batch that breaks one is refused as a whole, and the sender's position does not move
(``SyncRejectedError``).

Who sends what:
* A branch sends its own captured writes (``node_id`` = itself, or none: rows made before the node existed).
* The cloud applies a pushed batch with ``app.node_id`` set to the *sender*, so its own trigger records
  those rows as the sender's: they are relayed to the other branches, and never back to the sender.
* A branch applies what it pulls with ``app.node_id`` set to the row's origin (the cloud for rows with
  none), so nothing it received is pushed again."""

import itertools
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, cast

from sqlalchemy import CursorResult, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import models_registry  # noqa: F401  (every model, so the table list is complete)
from app.core import audit
from app.core.db import apply_context
from app.core.errors import ConflictError, DomainError
from app.core.models import Base
from app.core.sync import SyncConflict, SyncState

# Local bookkeeping, never sent.
LOCAL_ONLY = frozenset({"sync_outbox", "sync_state", "sync_conflict", "outbox_event", "idempotency_key"})

# Fields (ADR 0011 rule 4) where head office wins a clash whatever the time; "*" is every field of the table.
CONTROL_FIELDS: dict[str, tuple[str, ...]] = {
    "price_list": ("*",),
    "price_list_item": ("*",),
    "account": ("*",),
    "account_role": ("*",),
    "unit": ("*",),
    "unit_family": ("*",),
    "product_unit": ("*",),  # conversion factors
    "customer_profile": ("credit_limit", "credit_days", "credit_policy"),
    "party": ("sales_tax_code", "purchase_tax_code"),  # tax code overrides
    "business": ("costing_method", "negative_stock_allowed"),
}
# Master data (ADR 0011) is merged field by field. Everything else (documents and their status, stock
# balances, counters, memberships...) is owned by one place or derived, so its updates are taken whole.
MERGE_TABLES = frozenset(
    {
        "party",
        "party_address",
        "party_contact",
        "customer_group",
        "customer_profile",
        "supplier_profile",
        "product",
        "variant",
        "barcode",
        "category",
        "brand",
        "attribute",
        "attribute_value",
        "custom_field_def",
        "unit_family",
        "unit",
        "product_unit",
        "product_attribute",
        "variant_attribute_value",
        "price_list",
        "price_list_item",
        "promotion",
        "account",
        "account_role",
        "business",
        "branch",
        "warehouse",
        "setting",
    }
)


class SyncRejectedError(ConflictError):
    code = "sync_batch_rejected"
    title = "The receiving database refused this batch"


class UnknownTableError(DomainError):
    code = "sync_unknown_table"
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    title = "The batch names a table this version does not sync"


class OutOfOrderError(ConflictError):
    code = "sync_out_of_order"
    title = "The batch does not continue where the last one stopped"


@dataclass(frozen=True)
class Entry:
    seq: int  # the sender's outbox id
    node_id: uuid.UUID | None
    table: str
    row_id: uuid.UUID
    op: str  # insert | update | delete
    payload: str  # the row as JSON text (never parsed in Python: numbers stay exact)
    changed: str | None = None  # an update: the changed fields' values before the edit, as JSON text
    edited_at: str | None = None  # when the edit really happened (the per-field clock), as a timestamp


def syncable_tables() -> dict[str, list[str]]:
    """Table name → column names, for every table that travels."""
    return {
        name: [c.name for c in table.columns]
        for name, table in Base.metadata.tables.items()
        if name not in LOCAL_ONLY and "tenant_id" in table.columns
    } | {
        # Not tenant rows, but a membership needs its user (and the cloud needs the user to log in).
        name: [c.name for c in Base.metadata.tables[name].columns]
        for name in ("tenant", "user_account")
    }


async def _state(
    session: AsyncSession, tenant_id: uuid.UUID, node_id: uuid.UUID, *, lock: bool
) -> SyncState | None:
    query = select(SyncState).where(SyncState.tenant_id == tenant_id, SyncState.node_id == node_id)
    if lock:
        query = query.with_for_update()
    return (await session.scalars(query)).first()


async def state(session: AsyncSession, tenant_id: uuid.UUID, node_id: uuid.UUID) -> SyncState | None:
    """Where this node's sync stands, or nothing yet."""
    return await _state(session, tenant_id, node_id, lock=False)


async def _save(session: AsyncSession, tenant_id: uuid.UUID, node_id: uuid.UUID, **positions: int) -> None:
    """Moves the saved position forward (never back). The row is made on first use: on the cloud the tenant
    row only exists once the first batch is in, so it cannot be made before."""
    state = await _state(session, tenant_id, node_id, lock=True)
    if state is None:
        state = SyncState(tenant_id=tenant_id, node_id=node_id, pushed_seq=0, pulled_seq=0)
        session.add(state)
    for name, value in positions.items():
        setattr(state, name, max(getattr(state, name) or 0, value))
    await session.flush()


def _entries(
    rows: Sequence[tuple[int, uuid.UUID | None, str, uuid.UUID, str, str, str | None, str | None]],
) -> list[Entry]:
    return [Entry(*r) for r in rows]


async def pending(
    session: AsyncSession, tenant_id: uuid.UUID, node_id: uuid.UUID, *, limit: int = 500
) -> list[Entry]:
    """What this node still has to push: its own writes after the last one the cloud acknowledged."""
    state = await _state(session, tenant_id, node_id, lock=False)
    after = state.pushed_seq if state else 0
    # Only transactions that have all finished (a later commit never lands behind the cursor), and whole ones:
    # the batch is cut after the last complete transaction within the limit.
    own = "tenant_id = :t AND id > :after AND (node_id = :n OR node_id IS NULL) AND table_name <> ALL(:local)"
    rows = await session.execute(
        text(
            f"WITH todo AS (SELECT id, txid FROM sync_outbox WHERE {own} "  # noqa: S608 - `own` is a fixed fragment
            "AND txid < txid_snapshot_xmin(txid_current_snapshot()) ORDER BY id LIMIT :limit) "
            "SELECT id, node_id, table_name, row_id, op, payload::text, changed::text, edited_at::text "
            f"FROM sync_outbox WHERE {own} AND txid <= (SELECT max(txid) FROM todo) "
            "AND txid < txid_snapshot_xmin(txid_current_snapshot()) ORDER BY id"
        ),
        {
            "t": tenant_id,
            "after": after,
            "n": node_id,
            "local": sorted(LOCAL_ONLY),
            "limit": limit,
        },
    )
    entries = _entries([tuple(r) for r in rows])
    return await _with_users(session, entries)


async def mark_pushed(session: AsyncSession, tenant_id: uuid.UUID, node_id: uuid.UUID, seq: int) -> None:
    """The cloud acknowledged everything up to ``seq``."""
    await _save(session, tenant_id, node_id, pushed_seq=seq)


async def pull(
    session: AsyncSession, tenant_id: uuid.UUID, requester: uuid.UUID, after: int, *, limit: int = 500
) -> tuple[list[Entry], int]:
    """Other nodes' writes after ``after``. The cursor moves past rows that are the requester's own too."""
    rows = [
        tuple(r)
        for r in await session.execute(
            text(
                "SELECT id, node_id, table_name, row_id, op, payload::text, changed::text, edited_at::text "
                "FROM sync_outbox WHERE tenant_id = :t AND id > :after AND table_name <> ALL(:local) "
                "ORDER BY id LIMIT :limit"
            ),
            {"t": tenant_id, "after": after, "local": sorted(LOCAL_ONLY), "limit": limit},
        )
    ]
    cursor = int(rows[-1][0]) if rows else after
    mine = [r for r in rows if r[1] != requester]
    return await _with_users(session, _entries(mine)), cursor


async def _with_users(session: AsyncSession, entries: list[Entry]) -> list[Entry]:
    """Puts each member's user account just before the membership that needs it (same sequence number)."""
    members = [e for e in entries if e.table == "membership" and e.op != "delete"]
    if not members:
        return entries
    wanted = {m.row_id: json.loads(m.payload)["user_id"] for m in members}
    accounts = {
        str(r[0]): r[1]
        for r in await session.execute(
            text("SELECT id, to_jsonb(u)::text FROM user_account u WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": sorted(set(wanted.values()))},
        )
    }
    out: list[Entry] = []
    for entry in entries:
        user = wanted.get(entry.row_id) if entry in members else None
        if user is not None and user in accounts:
            out.append(Entry(entry.seq, None, "user_account", uuid.UUID(user), "update", accounts[user]))
        out.append(entry)
    return out


def _upsert_sql(table: str, columns: list[str], *, update: bool) -> str:
    cols = ", ".join(f'"{c}"' for c in columns)
    sets = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in columns if c != "id")
    return (
        f'INSERT INTO "{table}" ({cols}) SELECT {cols} FROM jsonb_populate_record(NULL::"{table}", '  # noqa: S608
        f"CAST(:p AS jsonb)) ON CONFLICT (id) DO " + (f"UPDATE SET {sets}" if update else "NOTHING")
    )


async def _apply_entry(
    session: AsyncSession,
    tables: dict[str, list[str]],
    entry: Entry,
    tenant_id: uuid.UUID,
    origin: uuid.UUID | None,
) -> None:
    columns = tables.get(entry.table)
    if columns is None:
        raise UnknownTableError(table=entry.table)
    if entry.op == "delete" and entry.table == "user_account":
        return  # accounts are never deleted by sync
    if entry.op == "delete":
        delete = f'DELETE FROM "{entry.table}" WHERE id = :id'  # noqa: S608 - a name checked against the models
        await session.execute(text(delete), {"id": entry.row_id})
    elif entry.op == "update" and entry.changed is not None and entry.table in MERGE_TABLES:
        # Master data (ADR 0011): field by field, so two people changing different fields both keep their
        # edit, and a clash on the same field is decided and logged. All in SQL: amounts stay exact.
        await session.execute(
            text(
                "SELECT sync_merge_update(:t, :table, :row, CAST(:new AS jsonb), CAST(:old AS jsonb), :o, "
                "CAST(:c AS jsonb), CAST(CAST(:at AS text) AS timestamptz))"
            ),
            {
                "t": tenant_id,
                "table": entry.table,
                "row": entry.row_id,
                "new": entry.payload,
                "old": entry.changed,
                "at": entry.edited_at,
                "o": origin,
                "c": json.dumps(list(CONTROL_FIELDS.get(entry.table, ()))),
            },
        )
    else:
        # A user account is a global row shared by every tenant: a batch may add one (a member's account
        # reaches the cloud with their membership) but never change one that exists, or one tenant could take
        # over another's.
        update = entry.op == "update" and entry.table != "user_account"
        await session.execute(text(_upsert_sql(entry.table, columns, update=update)), {"p": entry.payload})


async def apply(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    entries: Sequence[Entry],
    *,
    sender: uuid.UUID,
    fallback_origin: uuid.UUID | None = None,
    track: bool = True,
    cursor: int | None = None,
) -> int:
    """Applies a batch in order and returns the last sequence applied.

    ``track`` (the cloud receiving a push): the batch continues at the sender's last acknowledged sequence;
    entries at or before it are skipped, so a repeated batch changes nothing. ``fallback_origin`` is the node
    recorded for entries with none (a branch applying the cloud's own rows). For a pull (``track=False``) the
    caller passes the new ``cursor``, saved as the branch's ``pulled_seq`` in the same transaction."""
    tables = syncable_tables()
    state = await _state(session, tenant_id, sender, lock=True)
    last = (state.pushed_seq if track else state.pulled_seq) if state else 0
    fresh = [e for e in entries if (e.seq > last or not track)]
    if track and fresh and any(b.seq < a.seq for a, b in itertools.pairwise(fresh)):
        raise OutOfOrderError()
    try:
        async with session.begin_nested():
            # Rows that arrive by sync keep the time of their edit (see touch_row); only while applying.
            await session.execute(text("SELECT set_config('app.sync_apply', 'on', true)"))
            for entry in fresh:
                # A pushed entry is the sender's, whatever node it names: a branch must not pass its edits
                # off as head office's (or another branch's). Pulled entries keep their own origin.
                origin = sender if track else (entry.node_id or fallback_origin)
                await apply_context(session, tenant_id=tenant_id, node_id=origin)
                await _apply_entry(session, tables, entry, tenant_id, origin)
            await session.execute(text("SELECT set_config('app.sync_apply', '', true)"))
            await session.flush()
            await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # the balance check, now
            await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    except (IntegrityError, DBAPIError) as error:
        raise SyncRejectedError(reason=str(error.orig)[:300]) from error
    done = fresh[-1].seq if fresh else last
    if track:
        await _save(session, tenant_id, sender, pushed_seq=done)
    else:
        await _save(session, tenant_id, sender, pulled_seq=cursor if cursor is not None else done)
    return done


async def checksum(session: AsyncSession, business_id: uuid.UUID) -> dict[str, dict[str, list[str]]]:
    """Per month: how many journal lines / stock moves there are and what they add up to. Two databases that
    hold the same books give the same answer; the first month that differs says where to look (ADR 0010)."""
    journal = await session.execute(
        text(
            "SELECT to_char(entry_date, 'YYYY-MM'), count(*), sum(debit), sum(credit) FROM journal_line "
            "WHERE business_id = :b GROUP BY 1 ORDER BY 1"
        ),
        {"b": business_id},
    )
    stock = await session.execute(
        text(
            "SELECT to_char(move_date, 'YYYY-MM'), count(*), sum(qty_base), sum(value) FROM stock_move "
            "WHERE business_id = :b GROUP BY 1 ORDER BY 1"
        ),
        {"b": business_id},
    )
    return {
        "journal": {r[0]: [str(r[1]), str(r[2]), str(r[3])] for r in journal},
        "stock": {r[0]: [str(r[1]), str(r[2]), str(r[3])] for r in stock},
    }


def differing_periods(a: dict[str, dict[str, list[str]]], b: dict[str, dict[str, list[str]]]) -> list[str]:
    """The months (``journal:2026-11``) where two checksums disagree."""
    out: list[str] = []
    for kind in ("journal", "stock"):
        for period in sorted(set(a.get(kind, {})) | set(b.get(kind, {}))):
            if a.get(kind, {}).get(period) != b.get(kind, {}).get(period):
                out.append(f"{kind}:{period}")
    return out


class ConflictNotFoundError(DomainError):
    code = "sync_conflict_not_found"
    status = HTTPStatus.NOT_FOUND
    title = "No such conflict"


class ConflictStaleError(ConflictError):
    code = "sync_conflict_stale"
    title = "The record has changed since this conflict, so the old value was not put back"


class ConflictResolvedError(ConflictError):
    code = "sync_conflict_resolved"
    title = "This conflict was already put right"


async def list_conflicts(
    session: AsyncSession, *, open_only: bool = True, limit: int = 200
) -> list[SyncConflict]:
    query = select(SyncConflict).order_by(SyncConflict.created_at.desc()).limit(limit)
    if open_only:
        query = query.where(SyncConflict.resolved_at.is_(None))
    return list(await session.scalars(query))


async def restore_conflict(
    session: AsyncSession, conflict_id: uuid.UUID, *, user_id: uuid.UUID
) -> SyncConflict:
    """Puts the losing value back as a fresh edit made here and now: it travels like any edit, and being the
    latest it wins at every other node too. Written in SQL so amounts and dates keep their exact type."""
    conflict = await session.get(SyncConflict, conflict_id, with_for_update=True)
    if conflict is None:
        raise ConflictNotFoundError(conflict_id=str(conflict_id))
    if conflict.resolved_at is not None:
        raise ConflictResolvedError()
    columns = syncable_tables().get(conflict.table_name)
    if columns is None or conflict.field not in columns:  # the log is trusted, but the names go into SQL
        raise UnknownTableError(table=conflict.table_name)
    update = (
        f'UPDATE "{conflict.table_name}" t SET "{conflict.field}" = r."{conflict.field}" '  # noqa: S608
        f'FROM jsonb_populate_record(NULL::"{conflict.table_name}", jsonb_build_object(CAST(:f AS text), '
        "CAST(:v AS jsonb))) r WHERE t.id = :id "
        "AND to_jsonb(t) -> CAST(:f AS text) IS NOT DISTINCT FROM CAST(:kept AS jsonb)"
    )
    done = await session.execute(
        text(update),
        {
            "f": conflict.field,
            "v": json.dumps(conflict.lost_value),
            "kept": json.dumps(conflict.kept_value),
            "id": conflict.row_id,
        },
    )
    if (
        cast(CursorResult[Any], done).rowcount != 1
    ):  # the record is gone, or the field was changed again since: do not overwrite that
        raise ConflictStaleError(table=conflict.table_name, field=conflict.field)
    await audit.record(
        session,
        conflict,
        "update",
        reason="losing value put back",
        changes={
            "table": conflict.table_name,
            "row_id": str(conflict.row_id),
            conflict.field: [conflict.kept_value, conflict.lost_value],
        },
    )
    conflict.resolved_at = datetime.now(UTC)
    conflict.resolved_by = user_id
    await session.flush()
    return conflict
