"""The trace of what the assistant did (M1.13): one row per tool call, from the request to the confirmation.

Everything the tools write is audited ``via_ai`` through the normal audit log; this table adds the words the person
typed, the arguments, the result, and for a commit-tier tool who confirmed it.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped


class ActionStatus(StrEnum):
    DONE = "done"  # a read or draft tool that ran
    PENDING = "pending"  # a commit tool waiting for the person to confirm
    CONFIRMED = "confirmed"  # a commit tool the person confirmed, and it ran
    REJECTED = "rejected"  # the person said no
    DENIED = "denied"  # the person may not do this
    FAILED = "failed"  # the tool refused or could not finish; nothing was written


class AiAction(TenantScoped, Base):
    __tablename__ = "ai_action"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "business_id"], ["business.tenant_id", "business.id"]),
        CheckConstraint("risk IN ('read', 'draft', 'commit')", name="risk"),
        CheckConstraint(
            "status IN ('done', 'pending', 'confirmed', 'rejected', 'denied', 'failed')", name="status"
        ),
        Index("ix_ai_action_business", "business_id", "created_at"),
        Index("ix_ai_action_trace", "trace_id"),
    )

    business_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]  # the person the assistant acted for
    trace_id: Mapped[uuid.UUID]  # shared by every step of one request
    tool: Mapped[str] = mapped_column(String(60))
    risk: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10))
    prompt: Mapped[str | None] = mapped_column(Text)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(60))
    decided_by: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
