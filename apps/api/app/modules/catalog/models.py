"""Units of measure (domain-model.md → Units of measure, ADR 0013).

Each tenant has its own copy of the standard families and units. A unit stores one factor to its family's base
unit; the base unit itself is flagged (``is_family_base``, factor exactly 1, one per family), which avoids a
circular family ↔ unit foreign key.
"""

import uuid
from datetime import datetime
from decimal import Decimal

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
