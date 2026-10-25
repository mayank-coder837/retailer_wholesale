"""Parties (domain-model.md → Parties, ADR 0005): one party table with customer and supplier roles, contacts,
addresses, and per-business customer and supplier profiles.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped
from app.modules.parties.credit import CreditPolicy


class PartyKind(StrEnum):
    COMPANY = "company"
    INDIVIDUAL = "individual"


class AddressType(StrEnum):
    BILLING = "billing"
    SHIPPING = "shipping"


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", column], [f"{table}.tenant_id", f"{table}.id"])


class Party(TenantScoped, Base):
    __tablename__ = "party"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(_in("kind", PartyKind), name="kind"),
        CheckConstraint("trn IS NULL OR trn ~ '^[0-9]{15}$'", name="trn_format"),
        CheckConstraint("is_customer OR is_supplier", name="has_a_role"),
        CheckConstraint("default_currency ~ '^[A-Z]{3}$'", name="currency_format"),
        Index("uq_party_trn", "tenant_id", "trn", unique=True, postgresql_where=text("trn IS NOT NULL")),
    )

    code: Mapped[str] = mapped_column(String(30))
    name_en: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), server_default=PartyKind.COMPANY.value)
    trn: Mapped[str | None] = mapped_column(String(15))
    is_customer: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_supplier: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    phone: Mapped[str | None] = mapped_column(String(30))
    whatsapp: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(200))
    default_currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    # Tax code overrides for this party's lines (M1.10), e.g. an export customer ZR, an overseas service
    # supplier RC-S.
    sales_tax_code: Mapped[str | None] = mapped_column(String(8))
    purchase_tax_code: Mapped[str | None] = mapped_column(String(8))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PartyAddress(TenantScoped, Base):
    __tablename__ = "party_address"
    __table_args__ = (
        _tenant_fk("party_id", "party"),
        CheckConstraint(_in("type", AddressType), name="type"),
        CheckConstraint("country ~ '^[A-Z]{2}$'", name="country_format"),
        CheckConstraint("country <> 'AE' OR emirate IS NOT NULL", name="uae_needs_emirate"),
        Index(
            "uq_party_address_default",
            "party_id",
            "type",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    party_id: Mapped[uuid.UUID]
    type: Mapped[str] = mapped_column(String(20))
    line1: Mapped[str] = mapped_column(String(200))
    line2: Mapped[str | None] = mapped_column(String(200))
    city: Mapped[str | None] = mapped_column(String(100))
    emirate: Mapped[str | None] = mapped_column(String(30))
    country: Mapped[str] = mapped_column(String(2), server_default="AE")
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class PartyContact(TenantScoped, Base):
    __tablename__ = "party_contact"
    __table_args__ = (_tenant_fk("party_id", "party"),)

    party_id: Mapped[uuid.UUID]
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(200))


class CustomerGroup(TenantScoped, Base):
    __tablename__ = "customer_group"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("business_id", "name_en"),
        _tenant_fk("business_id", "business"),
        _tenant_fk("price_list_id", "price_list"),
    )

    business_id: Mapped[uuid.UUID]
    name_en: Mapped[str] = mapped_column(String(100))
    name_ar: Mapped[str] = mapped_column(String(100))
    price_list_id: Mapped[uuid.UUID | None]  # pricing step 3
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerProfile(TenantScoped, Base):
    __tablename__ = "customer_profile"
    __table_args__ = (
        UniqueConstraint("party_id", "business_id"),
        _tenant_fk("party_id", "party"),
        _tenant_fk("business_id", "business"),
        _tenant_fk("customer_group_id", "customer_group"),
        _tenant_fk("price_list_id", "price_list"),
        CheckConstraint(_in("credit_policy", CreditPolicy), name="credit_policy"),
        CheckConstraint("credit_limit IS NULL OR credit_limit >= 0", name="credit_limit_not_negative"),
        CheckConstraint("credit_days IS NULL OR credit_days >= 0", name="credit_days_not_negative"),
    )

    party_id: Mapped[uuid.UUID]
    business_id: Mapped[uuid.UUID]
    customer_group_id: Mapped[uuid.UUID | None]
    price_list_id: Mapped[uuid.UUID | None]  # pricing step 2
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    credit_days: Mapped[int | None] = mapped_column(Integer)
    credit_policy: Mapped[str] = mapped_column(String(10), server_default=CreditPolicy.WARN.value)
    salesperson_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))


class SupplierProfile(TenantScoped, Base):
    __tablename__ = "supplier_profile"
    __table_args__ = (
        UniqueConstraint("party_id", "business_id"),
        _tenant_fk("party_id", "party"),
        _tenant_fk("business_id", "business"),
        CheckConstraint("payment_terms_days IS NULL OR payment_terms_days >= 0", name="terms_not_negative"),
    )

    party_id: Mapped[uuid.UUID]
    business_id: Mapped[uuid.UUID]
    payment_terms_days: Mapped[int | None] = mapped_column(Integer)
