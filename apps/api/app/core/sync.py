"""Sync outbox (ADR 0010).

A trigger captures every write to a tenant table here from Phase 0 on, so turning cloud sync on in M2.1 needs
no backfill.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, String, UniqueConstraint, text
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
