"""Units of measure (M1.1) and the catalog (M1.2): domain-model.md → Units of measure, Catalog; ADR 0013.

Each tenant has its own copy of the standard families and units. A unit stores one factor to its family's base
unit; the base unit itself is flagged (``is_family_base``, factor exactly 1, one per family), which avoids a
circular family ↔ unit foreign key.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped

CODE_PATTERN = "^[a-z][a-z0-9_]*$"


class UnitFamily(TenantScoped, Base):
    __tablename__ = "unit_family"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="code_format"),
    )

    code: Mapped[str] = mapped_column(String(20))
    name_en: Mapped[str] = mapped_column(String(100))
    name_ar: Mapped[str] = mapped_column(String(100))


class Unit(TenantScoped, Base):
    __tablename__ = "unit"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "code"),
        ForeignKeyConstraint(["tenant_id", "family_id"], ["unit_family.tenant_id", "unit_family.id"]),
        CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="code_format"),
        CheckConstraint("decimals BETWEEN 0 AND 6", name="decimals_range"),
        CheckConstraint("factor_to_family_base > 0", name="factor_positive"),
        CheckConstraint("NOT is_family_base OR factor_to_family_base = 1", name="base_factor_one"),
        Index(
            "uq_unit_family_base",
            "tenant_id",
            "family_id",
            unique=True,
            postgresql_where=text("is_family_base"),
        ),
    )

    family_id: Mapped[uuid.UUID]
    code: Mapped[str] = mapped_column(String(20))
    name_en: Mapped[str] = mapped_column(String(100))
    name_ar: Mapped[str] = mapped_column(String(100))
    symbol: Mapped[str] = mapped_column(String(20))
    decimals: Mapped[int] = mapped_column(SmallInteger)
    factor_to_family_base: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    is_family_base: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_standard: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- catalog (M1.2) -----------------------------------------------------------------------------------------


class ProductType(StrEnum):
    STOCK = "stock"  # kept in stock, costed
    SERVICE = "service"  # labour, delivery: never in stock
    NON_STOCK = "non_stock"  # bought and sold but not tracked (consumables)


class CustomFieldEntity(StrEnum):
    PRODUCT = "product"
    VARIANT = "variant"
    PARTY = "party"
    DOCUMENT = "document"


class CustomFieldType(StrEnum):
    TEXT = "text"
    NUMBER = "number"
    DECIMAL = "decimal"
    DATE = "date"
    BOOL = "bool"
    SELECT = "select"
    MULTISELECT = "multiselect"


class BarcodeKind(StrEnum):
    EAN13 = "ean13"
    CODE128 = "code128"
    INTERNAL = "internal"
    SCALE = "scale"


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant_fk(column: str, table: str) -> ForeignKeyConstraint:
    """Composite foreign key: a row can only point at a row of its own tenant (see tenancy/models.py)."""
    return ForeignKeyConstraint(["tenant_id", column], [f"{table}.tenant_id", f"{table}.id"])


class Category(TenantScoped, Base):
    __tablename__ = "category"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        _tenant_fk("parent_id", "category"),
        # Names unique among siblings, top level included (NULL parents count as equal).
        Index(
            "uq_category_sibling_name",
            "tenant_id",
            "parent_id",
            "name_en",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    parent_id: Mapped[uuid.UUID | None]
    name_en: Mapped[str] = mapped_column(String(100))
    name_ar: Mapped[str] = mapped_column(String(100))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Brand(TenantScoped, Base):
    __tablename__ = "brand"
    __table_args__ = (UniqueConstraint("tenant_id", "id"), UniqueConstraint("tenant_id", "name_en"))

    name_en: Mapped[str] = mapped_column(String(100))
    name_ar: Mapped[str] = mapped_column(String(100))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attribute(TenantScoped, Base):
    __tablename__ = "attribute"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="code_format"),
    )

    code: Mapped[str] = mapped_column(String(30))
    name_en: Mapped[str] = mapped_column(String(100))
    name_ar: Mapped[str] = mapped_column(String(100))


class AttributeValue(TenantScoped, Base):
    __tablename__ = "attribute_value"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "attribute_id", "id"),
        UniqueConstraint("attribute_id", "value_en"),
        _tenant_fk("attribute_id", "attribute"),
    )

    attribute_id: Mapped[uuid.UUID]
    value_en: Mapped[str] = mapped_column(String(100))
    value_ar: Mapped[str] = mapped_column(String(100))
    sort: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))


class CustomFieldDef(TenantScoped, Base):
    __tablename__ = "custom_field_def"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_type", "key"),
        CheckConstraint(_in("entity_type", CustomFieldEntity), name="entity_type"),
        CheckConstraint(_in("data_type", CustomFieldType), name="data_type"),
        CheckConstraint(f"key ~ '{CODE_PATTERN}'", name="key_format"),
    )

    entity_type: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(50))
    label_en: Mapped[str] = mapped_column(String(100))
    label_ar: Mapped[str] = mapped_column(String(100))
    data_type: Mapped[str] = mapped_column(String(20))
    # select / multiselect: [{"value": "...", "label_en": "...", "label_ar": "..."}]
    options: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
    required: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Product(TenantScoped, Base):
    __tablename__ = "product"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "sku"),
        CheckConstraint(_in("product_type", ProductType), name="product_type"),
        _tenant_fk("category_id", "category"),
        _tenant_fk("brand_id", "brand"),
        _tenant_fk("base_unit_id", "unit"),
    )

    sku: Mapped[str] = mapped_column(String(50))
    name_en: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2000))
    product_type: Mapped[str] = mapped_column(String(20), server_default=ProductType.STOCK.value)
    category_id: Mapped[uuid.UUID | None]
    brand_id: Mapped[uuid.UUID | None]
    base_unit_id: Mapped[uuid.UUID]
    track_batch: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    track_expiry: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    track_serial: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductUnit(TenantScoped, Base):
    __tablename__ = "product_unit"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("product_id", "unit_id"),
        UniqueConstraint("tenant_id", "product_id", "unit_id"),
        _tenant_fk("product_id", "product"),
        _tenant_fk("unit_id", "unit"),
        CheckConstraint("factor_to_base > 0", name="factor_positive"),
        Index(
            "uq_product_unit_purchase_default",
            "product_id",
            unique=True,
            postgresql_where=text("is_purchase_default"),
        ),
        Index(
            "uq_product_unit_sales_default",
            "product_id",
            unique=True,
            postgresql_where=text("is_sales_default"),
        ),
    )

    product_id: Mapped[uuid.UUID]
    unit_id: Mapped[uuid.UUID]
    factor_to_base: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    is_purchase_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_sales_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class ProductAttribute(TenantScoped, Base):
    __tablename__ = "product_attribute"
    __table_args__ = (
        UniqueConstraint("product_id", "attribute_id"),
        _tenant_fk("product_id", "product"),
        _tenant_fk("attribute_id", "attribute"),
    )

    product_id: Mapped[uuid.UUID]
    attribute_id: Mapped[uuid.UUID]


class Variant(TenantScoped, Base):
    __tablename__ = "variant"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "sku"),
        _tenant_fk("product_id", "product"),
    )

    product_id: Mapped[uuid.UUID]
    sku: Mapped[str] = mapped_column(String(80))
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VariantAttributeValue(TenantScoped, Base):
    __tablename__ = "variant_attribute_value"
    __table_args__ = (
        UniqueConstraint("variant_id", "attribute_id"),
        _tenant_fk("variant_id", "variant"),
        # The value must belong to the attribute it is filed under.
        ForeignKeyConstraint(
            ["tenant_id", "attribute_id", "value_id"],
            ["attribute_value.tenant_id", "attribute_value.attribute_id", "attribute_value.id"],
        ),
    )

    variant_id: Mapped[uuid.UUID]
    attribute_id: Mapped[uuid.UUID]
    value_id: Mapped[uuid.UUID]


class Barcode(TenantScoped, Base):
    __tablename__ = "barcode"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code"),
        CheckConstraint(_in("kind", BarcodeKind), name="kind"),
        _tenant_fk("variant_id", "variant"),
        _tenant_fk("unit_id", "unit"),
    )

    variant_id: Mapped[uuid.UUID]
    unit_id: Mapped[uuid.UUID]
    code: Mapped[str] = mapped_column(String(48))
    kind: Mapped[str] = mapped_column(String(20))
