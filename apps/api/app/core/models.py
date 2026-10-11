"""Declarative base and the columns every tenant table carries (CLAUDE.md conventions).

Audit columns are filled by the database, not by Python: ``created_by`` / ``origin_node_id`` default from the
request's ``app.user_id`` / ``app.node_id`` settings, and a ``touch_row`` trigger maintains ``updated_at`` /
``updated_by`` (see ``app/core/rls.py``).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.core.ids import new_id

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

CURRENT_USER_ID = text("nullif(current_setting('app.user_id', true), '')::uuid")
CURRENT_NODE_ID = text("nullif(current_setting('app.node_id', true), '')::uuid")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AuditColumns:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(server_default=CURRENT_USER_ID)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(server_default=CURRENT_USER_ID)
    origin_node_id: Mapped[uuid.UUID | None] = mapped_column(server_default=CURRENT_NODE_ID)


class TenantScoped(AuditColumns):
    """``id`` + ``tenant_id`` + audit columns. Tables using it call ``tenant_table()`` in their migration."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805 - SQLAlchemy declared_attr signature
        return mapped_column(ForeignKey("tenant.id"), index=True)
