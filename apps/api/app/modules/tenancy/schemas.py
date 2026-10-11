import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.tenancy.models import Emirate

Code = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
Name = Field(min_length=1, max_length=200)


class BusinessIn(BaseModel):
    legal_name_en: str = Name
    legal_name_ar: str = Name
    trade_license_no: str | None = Field(default=None, max_length=50)
    trn: str | None = Field(default=None, pattern=r"^[0-9]{15}$", description="UAE TRN, 15 digits")
    vat_registered: bool = False
    base_currency: str = Field(default="AED", pattern=r"^[A-Z]{3}$")
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    time_zone: str = Field(default="Asia/Dubai", max_length=64)

    @model_validator(mode="after")
    def _trn_when_vat_registered(self) -> "BusinessIn":
        if self.vat_registered and not self.trn:
            raise ValueError("trn is required when vat_registered is true")
        return self


class BusinessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name_en: str
    legal_name_ar: str
    trade_license_no: str | None
    trn: str | None
    vat_registered: bool
    base_currency: str
    fiscal_year_start_month: int
    time_zone: str
    costing_method: str
    negative_stock_allowed: bool


class BranchIn(BaseModel):
    code: str = Code
    name_en: str = Name
    name_ar: str = Name
    address: str | None = Field(default=None, max_length=500)
    emirate: Emirate = Field(description="Decides the VAT 201 Box 1 line for sales this branch makes")
    is_head_office: bool = False


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    code: str
    name_en: str
    name_ar: str
    address: str | None
    emirate: Emirate
    is_head_office: bool


class WarehouseIn(BaseModel):
    code: str = Code
    name_en: str = Name
    name_ar: str = Name
    is_default: bool = False


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID
    code: str
    name_en: str
    name_ar: str
    is_default: bool
