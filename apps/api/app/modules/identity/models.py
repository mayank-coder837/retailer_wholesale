"""Users, memberships and refresh tokens (domain-model.md → Identity, ADR 0007).

``user_account`` and ``refresh_token`` are global tables (no tenant data, no RLS): a user exists before
choosing a tenant, and a refresh token is looked up by its hash before any tenant is known. Membership is
tenant data.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.core.models import Base, TenantScoped


class MembershipStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class ClientKind(StrEnum):
    WEB = "web"
    DESKTOP = "desktop"


class UserAccount(Base):
    __tablename__ = "user_account"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    locale: Mapped[str] = mapped_column(String(5), server_default="en")
    failed_logins: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Membership(TenantScoped, Base):
    __tablename__ = "membership"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "user_id"),
        CheckConstraint(
            "status IN ('invited', 'active', 'suspended')",
            name="status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), server_default=MembershipStatus.ACTIVE.value)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))


class RefreshToken(Base):
    """One row per issued refresh token. Tokens of one login form a family (rotation + reuse detection)."""

    __tablename__ = "refresh_token"
    __table_args__ = (CheckConstraint("client IN ('web', 'desktop')", name="client"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # The tenant this login session is in. Not named tenant_id: this is a global table, not tenant data.
    session_tenant_id: Mapped[uuid.UUID]
    membership_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    client: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
