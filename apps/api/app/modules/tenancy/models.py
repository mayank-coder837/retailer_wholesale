"""Tenant → business → branch → warehouse, plus sync nodes (domain-model.md → Tenancy).

Child tables reference parents through composite ``(tenant_id, parent_id)`` foreign keys: PostgreSQL checks
foreign keys without row-level security, so a plain ``business_id`` FK would let one tenant attach rows to
another tenant's business by guessing its id.
"""

import uuid
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.core.models import AuditColumns, Base, TenantScoped


class Emirate(StrEnum):
    ABU_DHABI = "abu_dhabi"
    DUBAI = "dubai"
    SHARJAH = "sharjah"
    AJMAN = "ajman"
    UMM_AL_QUWAIN = "umm_al_quwain"
    RAS_AL_KHAIMAH = "ras_al_khaimah"
    FUJAIRAH = "fujairah"


class TenantStatus(StrEnum):
    TRIAL = "trial"
    ACTIVE = "active"
    SUSPENDED = "suspended"


class NodeKind(StrEnum):
    BRANCH_MAIN_PC = "branch_main_pc"
    CLOUD = "cloud"


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class Tenant(AuditColumns, Base):
    __tablename__ = "tenant"
    __table_args__ = (CheckConstraint(_in("status", TenantStatus), name="status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), server_default=TenantStatus.TRIAL.value)
    default_locale: Mapped[str] = mapped_column(String(5), server_default="en")


class Business(TenantScoped, Base):
    __tablename__ = "business"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        CheckConstraint("trn IS NULL OR trn ~ '^[0-9]{15}$'", name="trn_format"),
        CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("fiscal_year_start_month BETWEEN 1 AND 12", name="fiscal_month"),
        CheckConstraint("costing_method = 'moving_average'", name="costing_method"),
    )

    legal_name_en: Mapped[str] = mapped_column(String(200))
    legal_name_ar: Mapped[str] = mapped_column(String(200))
    trade_license_no: Mapped[str | None] = mapped_column(String(50))
    trn: Mapped[str | None] = mapped_column(String(15))
    vat_registered: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    base_currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    fiscal_year_start_month: Mapped[int] = mapped_column(SmallInteger, server_default=text("1"))
    time_zone: Mapped[str] = mapped_column(String(64), server_default="Asia/Dubai")
    costing_method: Mapped[str] = mapped_column(String(20), server_default="moving_average")
    negative_stock_allowed: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Branch(TenantScoped, Base):
    __tablename__ = "branch"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("business_id", "code"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["business.tenant_id", "business.id"]),
        CheckConstraint(_in("emirate", Emirate), name="emirate"),
    )

    business_id: Mapped[uuid.UUID]
    code: Mapped[str] = mapped_column(String(20))
    name_en: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(500))
    emirate: Mapped[str] = mapped_column(String(20))
    is_head_office: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Warehouse(TenantScoped, Base):
    __tablename__ = "warehouse"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("business_id", "code"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["business.tenant_id", "business.id"]),
        ForeignKeyConstraint(["tenant_id", "branch_id"], ["branch.tenant_id", "branch.id"]),
    )

    business_id: Mapped[uuid.UUID]
    branch_id: Mapped[uuid.UUID]
    code: Mapped[str] = mapped_column(String(20))
    name_en: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Node(TenantScoped, Base):
    """A database that owns numbering series and syncs: a branch's main PC, or the cloud (ADR 0010)."""

    __tablename__ = "node"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "branch_id"], ["branch.tenant_id", "branch.id"]),
        CheckConstraint(_in("kind", NodeKind), name="kind"),
        CheckConstraint("(kind = 'cloud') = (branch_id IS NULL)", name="branch_for_main_pc"),
    )

    kind: Mapped[str] = mapped_column(String(20))
    branch_id: Mapped[uuid.UUID | None]
    name: Mapped[str] = mapped_column(String(200))
    last_push_seq: Mapped[int | None] = mapped_column(BigInteger)
    last_pull_cursor: Mapped[str | None] = mapped_column(String(100))
