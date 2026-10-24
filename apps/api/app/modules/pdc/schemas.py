"""PDC register API shapes (M1.9). Money is exact Decimal."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

from app.modules.pdc.models import PdcDirection, PdcStatus

Money = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=2)]


class IntendedBillIn(BaseModel):
    bill_id: uuid.UUID
    amount: Money


class PdcIn(BaseModel):
    business_id: uuid.UUID
    branch_id: uuid.UUID
    direction: PdcDirection
    party_id: uuid.UUID = Field(description="Received: the customer; issued: the supplier")
    cheque_no: str = Field(min_length=1, max_length=30)
    bank_name: str | None = Field(default=None, max_length=100, description="The drawer's bank")
    cheque_date: dt.date = Field(description="Maturity: when it may be presented")
    amount: Money
    deposit_account_id: uuid.UUID | None = Field(
        default=None, description="Issued: the bank account it is drawn on (required)"
    )
    intended_bills: list[IntendedBillIn] = Field(
        default_factory=list, max_length=100, description="The open bills it is meant for"
    )
    notes: str | None = Field(default=None, max_length=1000)


class DepositIn(BaseModel):
    bank_account_id: uuid.UUID
    date: dt.date


class ClearIn(BaseModel):
    date: dt.date = Field(description="The clearance date: the receipt or payment is dated on it")
    bank_account_id: uuid.UUID | None = Field(
        default=None, description="Received and not deposited: the bank account it cleared into"
    )
    override_lock_reason: str | None = Field(default=None, max_length=500)


class BounceIn(BaseModel):
    date: dt.date
    reason: str = Field(min_length=1, max_length=500)
    charge: Money | None = Field(
        default=None, description="A bank charge for the bounce: posts an expense voucher (pdc.post)"
    )


class IntendedBillOut(BaseModel):
    bill_id: uuid.UUID
    reference: str
    amount: Decimal
    amount_open: Decimal = Field(description="What is still open on the bill now")


class PdcOut(BaseModel):
    id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID
    direction: PdcDirection
    party_id: uuid.UUID
    party_name_en: str
    party_name_ar: str
    cheque_no: str
    bank_name: str | None
    cheque_date: dt.date
    amount: Decimal
    currency: str
    status: PdcStatus
    deposit_account_id: uuid.UUID | None
    deposited_on: dt.date | None
    cleared_on: dt.date | None
    bounced_on: dt.date | None
    bounce_reason: str | None
    voucher_id: uuid.UUID | None
    voucher_number: str | None
    charge_voucher_id: uuid.UUID | None
    notes: str | None
    intended_bills: list[IntendedBillOut]
