"""Units of measure API shapes. Names that appear on documents come in English and Arabic (rule 10)."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.catalog.models import CODE_PATTERN

Name = Field(min_length=1, max_length=100)


class UnitIn(BaseModel):
    family: str = Field(pattern=CODE_PATTERN, max_length=20, description="Family code: count, length, …")
    code: str = Field(pattern=CODE_PATTERN, max_length=20)
    name_en: str = Name
    name_ar: str = Name
    symbol: str = Field(min_length=1, max_length=20)
    decimals: int = Field(
        ge=0,
        le=6,
        description="Decimal places a quantity typed in this unit may have; conversions stay exact",
    )
    factor_to_family_base: Decimal = Field(
        gt=0, max_digits=19, decimal_places=6, description="How many family base units one of this unit is"
    )


class UnitPatch(BaseModel):
    """Factor and family are fixed once a unit exists: lines converted with it must keep their meaning."""

    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    symbol: str | None = Field(default=None, min_length=1, max_length=20)
    decimals: int | None = Field(default=None, ge=0, le=6)
    archived: bool | None = None


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    family: str
    code: str
    name_en: str
    name_ar: str
    symbol: str
    decimals: int
    factor_to_family_base: Decimal
    is_family_base: bool
    is_standard: bool
    archived_at: datetime | None


class ConversionOut(BaseModel):
    qty: Decimal
    from_unit: str
    to_unit: str
    factor: Decimal
    result: Decimal  # exact: a conversion that would need rounding is refused (uom_inexact)
