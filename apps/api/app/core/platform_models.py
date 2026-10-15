"""Platform tables (M0.4): audit, numbering, jobs, idempotency, attachments, settings, industry packs.

All are tenant tables (row-level security). See docs/milestones/m0.4-platform-services.md.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.core.models import Base, TenantScoped

NIL_UUID = "'00000000-0000-0000-0000-000000000000'::uuid"


class AuditLog(Base):
    """Append-only record of who changed what (CLAUDE.md rule 8). No updated_* columns: rows never change."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id", "at"),
        CheckConstraint(
            "action IN ('create', 'update', 'delete', 'post', 'cancel', 'reverse', 'login', 'apply')",
            name="action",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    actor_user_id: Mapped[uuid.UUID | None]
    via_ai: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    on_behalf_of_user_id: Mapped[uuid.UUID | None]
    ai_trace_id: Mapped[uuid.UUID | None]
    client: Mapped[str | None] = mapped_column(String(20))
    request_id: Mapped[str | None] = mapped_column(String(64))
    node_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(String(10))
    entity_type: Mapped[str] = mapped_column(String(63))
    entity_id: Mapped[uuid.UUID]
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(String(500))


class NumberingSeries(TenantScoped, Base):
    """One gap-free series per branch, document type and fiscal year, owned by one node (ADR 0010)."""

    __tablename__ = "numbering_series"
    __audit__ = False  # next_number moves on every post; the documents themselves are audited
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("branch_id", "doc_type", "fiscal_year"),
        ForeignKeyConstraint(["tenant_id", "branch_id"], ["branch.tenant_id", "branch.id"]),
        ForeignKeyConstraint(["tenant_id", "owner_node_id"], ["node.tenant_id", "node.id"]),
        CheckConstraint("next_number >= 1", name="next_number"),
        CheckConstraint("padding BETWEEN 1 AND 10", name="padding"),
    )

    branch_id: Mapped[uuid.UUID]
    doc_type: Mapped[str] = mapped_column(String(30))
    fiscal_year: Mapped[str] = mapped_column(String(9), comment="Label, e.g. 2026 or 2026-27")
    prefix: Mapped[str] = mapped_column(String(10))
    padding: Mapped[int] = mapped_column(SmallInteger, server_default=text("5"))
    next_number: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    owner_node_id: Mapped[uuid.UUID]


class ReleasedNumber(TenantScoped, Base):
    """A number freed by Cancel (decision A7): offered to the next document, or shown as CANCELLED."""

    __tablename__ = "released_number"
    __table_args__ = (
        UniqueConstraint("series_id", "number"),
        ForeignKeyConstraint(
            ["tenant_id", "series_id"], ["numbering_series.tenant_id", "numbering_series.id"]
        ),
    )

    series_id: Mapped[uuid.UUID] = mapped_column(index=True)
    number: Mapped[int] = mapped_column(BigInteger)
    cancelled_document_id: Mapped[uuid.UUID]
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    reused_by_document_id: Mapped[uuid.UUID | None]
    reused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxEvent(Base):
    """A job to run after the business transaction commits (ADR 0012). Local only: not synced."""

    __tablename__ = "outbox_event"
    __table_args__ = (
        Index(
            "ix_outbox_event_pending",
            "available_at",
            postgresql_where=text("dispatched_at IS NULL AND dead_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    aggregate_type: Mapped[str | None] = mapped_column(String(63))
    aggregate_id: Mapped[uuid.UUID | None]
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(String(2000))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(Base):
    """Stored result of a write, replayed when the same key is sent again (CLAUDE.md rule 9). Not synced."""

    __tablename__ = "idempotency_key"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    tenant_id: Mapped[uuid.UUID] = mapped_column(index=True)
    user_id: Mapped[uuid.UUID]
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[str | None]
    response_headers: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now() + interval '24 hours'")
    )


class Attachment(TenantScoped, Base):
    __tablename__ = "attachment"
    __table_args__ = (Index("ix_attachment_entity", "entity_type", "entity_id"),)

    entity_type: Mapped[str] = mapped_column(String(63))
    entity_id: Mapped[uuid.UUID]
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)


class Setting(TenantScoped, Base):
    __tablename__ = "setting"
    __table_args__ = (
        CheckConstraint("scope IN ('tenant', 'business', 'branch', 'user')", name="scope"),
        CheckConstraint("(scope = 'tenant') = (scope_id IS NULL)", name="scope_id"),
        Index(
            "uq_setting_scope_key",
            "tenant_id",
            "scope",
            text(f"coalesce(scope_id, {NIL_UUID})"),
            "key",
            unique=True,
        ),
    )

    scope: Mapped[str] = mapped_column(String(10))
    scope_id: Mapped[uuid.UUID | None]
    key: Mapped[str] = mapped_column(String(100))
    value: Mapped[Any] = mapped_column(JSONB)


class IndustryPackApplication(TenantScoped, Base):
    __tablename__ = "industry_pack_application"
    __table_args__ = (
        UniqueConstraint("business_id", "pack_code"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["business.tenant_id", "business.id"]),
    )

    business_id: Mapped[uuid.UUID]
    pack_code: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer)
    applied_sections: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    pending_sections: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
