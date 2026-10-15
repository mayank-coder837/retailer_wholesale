"""Sync outbox (ADR 0010).

A trigger captures every write to a tenant table here from Phase 0 on, so turning cloud sync on in M2.1 needs
no backfill.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


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
