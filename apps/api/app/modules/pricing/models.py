"""Price lists, their items (with quantity breaks) and promotions (domain-model.md → Pricing, M1.3).

Business-scoped: a tenant with two businesses keeps their prices apart. Money is NUMERIC(19,4), quantities
NUMERIC(19,6). Customer and group assignments arrive with parties (M1.4).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped


class PromotionScope(StrEnum):
    VARIANT = "variant"
    PRODUCT = "product"
    CATEGORY = "category"
    BRAND = "brand"


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", column], [f"{table}.tenant_id", f"{table}.id"])


class PriceList(TenantScoped, Base):
    __tablename__ = "price_list"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("business_id", "name_en"),
        _tenant_fk("business_id", "business"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from", name="valid_range"
        ),
        Index("uq_price_list_standard", "business_id", unique=True, postgresql_where=text("is_standard")),
    )

    business_id: Mapped[uuid.UUID]
    name_en: Mapped[str] = mapped_column(String(100))
    name_ar: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    is_standard: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceListItem(TenantScoped, Base):
    __tablename__ = "price_list_item"
    __table_args__ = (
        UniqueConstraint("price_list_id", "variant_id", "unit_id", "min_qty"),
        _tenant_fk("price_list_id", "price_list"),
        _tenant_fk("variant_id", "variant"),
        _tenant_fk("unit_id", "unit"),
        CheckConstraint("min_qty >= 0", name="min_qty_not_negative"),
        CheckConstraint("price >= 0", name="price_not_negative"),
    )

    price_list_id: Mapped[uuid.UUID]
    variant_id: Mapped[uuid.UUID]
    unit_id: Mapped[uuid.UUID]
    min_qty: Mapped[Decimal] = mapped_column(Numeric(19, 6), server_default=text("0"))
    price: Mapped[Decimal] = mapped_column(Numeric(19, 4))


class Promotion(TenantScoped, Base):
    __tablename__ = "promotion"
    __table_args__ = (
        _tenant_fk("business_id", "business"),
        CheckConstraint("kind IN ('percent', 'amount', 'fixed_price')", name="kind"),
        CheckConstraint(_in("scope", PromotionScope), name="scope"),
        CheckConstraint("value >= 0", name="value_not_negative"),
        CheckConstraint("kind <> 'percent' OR value <= 100", name="percent_at_most_100"),
        CheckConstraint("min_qty >= 0", name="min_qty_not_negative"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from", name="valid_range"
        ),
    )

    business_id: Mapped[uuid.UUID]
    name_en: Mapped[str] = mapped_column(String(100))
    name_ar: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20))
    value: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    scope: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[
        uuid.UUID
    ]  # a variant, product, category or brand of the same tenant (checked by the service)
    min_qty: Mapped[Decimal] = mapped_column(Numeric(19, 6), server_default=text("0"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
