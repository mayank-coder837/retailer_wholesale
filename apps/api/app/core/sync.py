"""Sync outbox (ADR 0010).

A trigger captures every write to a tenant table here from Phase 0 on, so turning cloud sync on in M2.1 needs
no backfill.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped


class SyncOutbox(Base):
    __tablename__ = "sync_outbox"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # No foreign key: outbox rows must outlive the rows (even the tenant) whose deletion they record.
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    node_id: Mapped[uuid.UUID | None]
    table_name: Mapped[str] = mapped_column(String(63))
    row_id: Mapped[uuid.UUID]
    op: Mapped[str] = mapped_column(String(10))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The writing transaction: a batch never splits one, so the receiver can check the whole of it (M2.1a).
    txid: Mapped[int] = mapped_column(BigInteger, server_default=text("txid_current()"))
    # For an update: each changed column's value before the edit (M2.1c: tells "only they changed it" from
    # "both of us did"), and when the edit really happened (the per-field clock; kept as the row travels).
    changed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class SyncState(TenantScoped, Base):
    """Where sync stands for one node of one tenant. Never synced itself: each database keeps its own.

    On the cloud there is a row per branch node: ``pushed_seq`` is the last outbox row of that node applied.
    On a branch there is a row for the branch itself: ``pushed_seq`` is the last of its own outbox rows
    the cloud acknowledged, and ``pulled_seq`` the last cloud outbox row it applied.
    """

    __tablename__ = "sync_state"
    __audit__ = False  # bookkeeping: auditing it would write an audit row on every push, forever
    __table_args__ = (UniqueConstraint("tenant_id", "node_id"),)

    node_id: Mapped[uuid.UUID]
    pushed_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    pulled_seq: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))


class SyncConflict(TenantScoped, Base):
    """Two nodes changed the same field of a master record while apart (ADR 0011). One is kept, the other is
    recorded here in full, so nothing is silently lost; an owner can put the lost value back. Never synced:
    each database logs the clashes it decided itself."""

    __tablename__ = "sync_conflict"
    __audit__ = False  # it is itself the record of what happened
    __table_args__ = (
        CheckConstraint("reason IN ('later_edit', 'head_office')", name="reason"),
        Index("ix_sync_conflict_open", "tenant_id", "created_at"),
    )

    table_name: Mapped[str] = mapped_column(String(63))
    row_id: Mapped[uuid.UUID]
    field: Mapped[str] = mapped_column(String(63))
    kept_value: Mapped[Any | None] = mapped_column(JSONB)
    lost_value: Mapped[Any | None] = mapped_column(JSONB)
    kept_node_id: Mapped[uuid.UUID | None]
    lost_node_id: Mapped[uuid.UUID | None]
    kept_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(20))  # later_edit | head_office
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None]
