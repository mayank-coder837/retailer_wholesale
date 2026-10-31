"""Pricing API shapes (M1.3). Money is exact Decimal (NUMERIC(19,4)), sent and received as text."""

import datetime as dt
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.pricing.engine import PriceSource, PromotionKind
from app.modules.pricing.models import PromotionScope
from app.modules.tax.codes import PriceMode

Money = Field(ge=0, max_digits=19, decimal_places=4)
Name = Field(min_length=1, max_length=100)


class UnitPricesIn(BaseModel):
    sale_price: Decimal | None = Field(default=None, ge=0, max_digits=19, decimal_places=4)
    min_price: Decimal | None = Field(default=None, ge=0, max_digits=19, decimal_places=4)


class PriceListIn(BaseModel):
    business_id: uuid.UUID
    name_en: str = Name
    name_ar: str = Name
    currency: str = Field(default="AED", pattern=r"^[A-Z]{3}$")
    is_standard: bool = False
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def _range(self) -> "PriceListIn":
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to is before valid_from")
        return self


class PriceListPatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    is_standard: bool | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    archived: bool | None = None


class PriceListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    name_en: str
    name_ar: str
    currency: str
    is_standard: bool
    valid_from: date | None
    valid_to: date | None
    archived_at: datetime | None


class PriceListItemIn(BaseModel):
    variant_id: uuid.UUID
    unit: str = Field(max_length=20)
    min_qty: Decimal = Field(default=Decimal(0), ge=0, max_digits=19, decimal_places=6)
    price: Decimal = Money


class PriceListItemOut(BaseModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    unit: str
    min_qty: Decimal
    price: Decimal


class PromotionIn(BaseModel):
    business_id: uuid.UUID
    name_en: str = Name
    name_ar: str = Name
    kind: PromotionKind
    value: Decimal = Money
    scope: PromotionScope
    scope_id: uuid.UUID
    min_qty: Decimal = Field(default=Decimal(0), ge=0, max_digits=19, decimal_places=6)
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="after")
    def _check(self) -> "PromotionIn":
        if self.kind is PromotionKind.PERCENT and self.value > 100:
            raise ValueError("a percent promotion is at most 100")
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to is before valid_from")
        return self


class PromotionPatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    valid_from: date | None = None
    valid_to: date | None = None
    archived: bool | None = None


class PromotionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    name_en: str
    name_ar: str
    kind: PromotionKind
    value: Decimal
    scope: PromotionScope
    scope_id: uuid.UUID
    min_qty: Decimal
    valid_from: date | None
    valid_to: date | None
    archived_at: datetime | None


class QuoteIn(BaseModel):
    business_id: uuid.UUID
    variant_id: uuid.UUID
    unit: str = Field(max_length=20)
    qty: Decimal = Field(gt=0, max_digits=19, decimal_places=6)
    # dt.date: a field named "date" would hide the type inside this class
    date: dt.date | None = Field(default=None, description="Defaults to today (Asia/Dubai)")
    party_id: uuid.UUID | None = Field(
        default=None, description="The customer: their price list (step 2) and group's list (step 3)"
    )


class QuoteOut(BaseModel):
    price: Decimal | None
    price_source: str  # stored on the document line, e.g. "price_list:Wholesale"
    source: PriceSource
    source_name: str
    converted: bool
    min_price: Decimal | None
    below_min: bool
    may_go_below_min: bool
    price_mode: PriceMode = Field(
        default=PriceMode.VAT_EXCLUSIVE,
        description="The mode the price is in: a last price in its invoice's; lists, promotions and base "
        "prices exclude VAT (R9). A screen in another mode does not use it as it is.",
    )
