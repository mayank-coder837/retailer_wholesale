"""Parties API shapes (M1.4). Names on documents are in English and Arabic; money is exact Decimal."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.modules.parties.credit import CreditDecision, CreditPolicy, CreditReason
from app.modules.parties.models import AddressType, PartyKind
from app.modules.tax.fields import PurchaseTaxCode, SalesTaxCode
from app.modules.tenancy.models import Emirate

Trn = Field(default=None, pattern=r"^[0-9]{15}$", description="UAE tax registration number, 15 digits")
Phone = Field(default=None, max_length=30)


class AddressIn(BaseModel):
    type: AddressType = AddressType.BILLING
    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    emirate: Emirate | None = None
    country: str = Field(default="AE", pattern=r"^[A-Z]{2}$")
    is_default: bool = False

    @model_validator(mode="after")
    def _uae_emirate(self) -> "AddressIn":
        if self.country == "AE" and self.emirate is None:
            raise ValueError("a UAE address needs its emirate")
        return self


class AddressOut(AddressIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=100)
    phone: str | None = Phone
    email: EmailStr | None = None


class ContactOut(ContactIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class PartyIn(BaseModel):
    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=30,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
        description="Omit to number it: C0001 customers, S0001 suppliers, P0001 both",
    )
    name_en: str = Field(min_length=1, max_length=200)
    name_ar: str = Field(min_length=1, max_length=200)
    kind: PartyKind = PartyKind.COMPANY
    trn: str | None = Trn
    is_customer: bool = False
    is_supplier: bool = False
    phone: str | None = Phone
    whatsapp: str | None = Phone
    email: EmailStr | None = None
    default_currency: str = Field(default="AED", pattern=r"^[A-Z]{3}$")
    sales_tax_code: SalesTaxCode | None = Field(
        default=None,
        description="Overrides the product's code on sales to this customer, e.g. ZR for exports",
    )
    purchase_tax_code: PurchaseTaxCode | None = Field(
        default=None, description="Overrides the product's code on purchases, e.g. RC-S for overseas services"
    )
    addresses: list[AddressIn] = Field(default_factory=list)
    contacts: list[ContactIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _role(self) -> "PartyIn":
        if not (self.is_customer or self.is_supplier):
            raise ValueError("a party is a customer, a supplier, or both")
        return self


class PartyPatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    name_ar: str | None = Field(default=None, min_length=1, max_length=200)
    kind: PartyKind | None = None
    trn: str | None = Trn
    is_customer: bool | None = None
    is_supplier: bool | None = None
    phone: str | None = Phone
    whatsapp: str | None = Phone
    email: EmailStr | None = None
    sales_tax_code: SalesTaxCode | None = Field(default=None, description="Send null to remove the override")
    purchase_tax_code: PurchaseTaxCode | None = Field(default=None, description="Send null to remove it")
    archived: bool | None = None


class CustomerProfileIn(BaseModel):
    business_id: uuid.UUID
    customer_group_id: uuid.UUID | None = None
    price_list_id: uuid.UUID | None = None
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=19, decimal_places=4)
    credit_days: int | None = Field(default=None, ge=0, le=3650)
    credit_policy: CreditPolicy = CreditPolicy.WARN
    salesperson_user_id: uuid.UUID | None = None


class CustomerProfileOut(CustomerProfileIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class SupplierProfileIn(BaseModel):
    business_id: uuid.UUID
    payment_terms_days: int | None = Field(default=None, ge=0, le=3650)


class SupplierProfileOut(SupplierProfileIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class PartySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name_en: str
    name_ar: str
    kind: PartyKind
    trn: str | None
    is_customer: bool
    is_supplier: bool
    phone: str | None
    archived_at: datetime | None


class PartyOut(PartySummary):
    whatsapp: str | None
    email: str | None
    default_currency: str
    sales_tax_code: str | None
    purchase_tax_code: str | None
    addresses: list[AddressOut]
    contacts: list[ContactOut]
    customer_profiles: list[CustomerProfileOut]
    supplier_profiles: list[SupplierProfileOut]


class CustomerGroupIn(BaseModel):
    business_id: uuid.UUID
    name_en: str = Field(min_length=1, max_length=100)
    name_ar: str = Field(min_length=1, max_length=100)
    price_list_id: uuid.UUID | None = None


class CustomerGroupPatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    price_list_id: uuid.UUID | None = None
    archived: bool | None = None


class CustomerGroupOut(CustomerGroupIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    archived_at: datetime | None


class CreditCheckIn(BaseModel):
    business_id: uuid.UUID
    amount: Decimal = Field(ge=0, max_digits=19, decimal_places=4, description="This document's total")


class CreditCheckOut(BaseModel):
    decision: CreditDecision
    reasons: list[CreditReason]
    credit_limit: Decimal | None
    credit_days: int | None
    policy: CreditPolicy
    ledger_balance: Decimal
    open_orders: Decimal
    exposure: Decimal
    available: Decimal | None
    pdc_in_hand: Decimal
    exposure_after_pdc: Decimal
    may_override: bool
