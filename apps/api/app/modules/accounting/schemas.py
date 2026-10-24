"""Accounting API shapes (M1.5). Money is exact Decimal; names shown on documents are English and Arabic."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.accounting.models import AccountType, AllocationType, Direction, Nature, PeriodStatus, Role
from app.modules.accounting.posting import VoucherType
from app.modules.documents.models import DocumentStatus

Money = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=4)]
Code = Annotated[str, Field(min_length=1, max_length=20, pattern=r"^[0-9A-Za-z][0-9A-Za-z.-]*$")]


# --- chart of accounts -------------------------------------------------------------------------------------


class AccountIn(BaseModel):
    business_id: uuid.UUID
    code: Code
    name_en: str = Field(min_length=1, max_length=200)
    name_ar: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID = Field(description="The group it sits in; its nature comes from the group")
    is_group: bool = False
    account_type: AccountType | None = Field(
        default=None, description="Required for ledgers, none for groups"
    )
    allow_manual_posting: bool = True

    @model_validator(mode="after")
    def _type(self) -> "AccountIn":
        if self.is_group == (self.account_type is not None):
            raise ValueError("a ledger has an account type; a group has none")
        return self


class AccountPatch(BaseModel):
    code: Code | None = None
    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    name_ar: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None
    account_type: AccountType | None = None
    allow_manual_posting: bool | None = None
    archived: bool | None = None


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    code: str
    name_en: str
    name_ar: str
    parent_id: uuid.UUID | None
    is_group: bool
    nature: Nature
    account_type: AccountType | None
    allow_manual_posting: bool
    is_system: bool
    archived_at: datetime | None
    roles: list[Role] = Field(default_factory=list)
    balance: Decimal = Field(default=Decimal(0), description="Debit positive, credit negative; groups: total")


class AccountRoleIn(BaseModel):
    business_id: uuid.UUID
    account_id: uuid.UUID


class AccountRoleOut(BaseModel):
    role: Role
    account_id: uuid.UUID


# --- fiscal years ------------------------------------------------------------------------------------------


class FiscalYearIn(BaseModel):
    business_id: uuid.UUID
    day: date = Field(description="Any day of the year to create; months follow the business's start month")


class FiscalPeriodOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_no: int
    start_date: date
    end_date: date
    status: PeriodStatus
    locked_at: datetime | None
    locked_by: uuid.UUID | None


class FiscalYearOut(BaseModel):
    id: uuid.UUID
    business_id: uuid.UUID
    code: str
    start_date: date
    end_date: date
    status: str
    periods: list[FiscalPeriodOut]


# --- vouchers ----------------------------------------------------------------------------------------------


class AllocationIn(BaseModel):
    type: AllocationType
    amount: Money
    bill_id: uuid.UUID | None = Field(default=None, description="The bill settled (against_ref)")
    reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Name of a new bill (new_ref; advance: optional)",
    )
    due_date: date | None = None

    @model_validator(mode="after")
    def _shape(self) -> "AllocationIn":
        if (self.type is AllocationType.AGAINST_REF) != (self.bill_id is not None):
            raise ValueError("against_ref names the bill it settles; other types name none")
        if self.type is AllocationType.NEW_REF and self.reference is None:
            raise ValueError("a new reference needs a name")
        if self.type not in (AllocationType.NEW_REF, AllocationType.ADVANCE) and (
            self.reference or self.due_date
        ):
            raise ValueError("only a new reference or an advance has a name and a due date")
        return self


class AllocationOut(AllocationIn):
    pass


class VoucherLineIn(BaseModel):
    account_id: uuid.UUID | None = Field(
        default=None,
        description="Omit with a party on a receipt (receivable) or payment (payable); credit note "
        "default: sales returns; debit note default: purchase price variance",
    )
    party_id: uuid.UUID | None = None
    amount: Money | None = Field(
        default=None, description="Receipt, payment, expense, credit and debit notes"
    )
    debit: Money | None = Field(default=None, description="Journal and contra")
    credit: Money | None = Field(default=None, description="Journal and contra")
    vat_rate: Decimal = Field(default=Decimal(0), description="0 or 5 (%), expense and credit / debit notes")
    description: str | None = Field(default=None, max_length=500)
    allocations: list[AllocationIn] = Field(default_factory=list, max_length=100)


class VoucherIn(BaseModel):
    doc_type: VoucherType
    business_id: uuid.UUID
    branch_id: uuid.UUID
    doc_date: date
    reference: str | None = Field(default=None, max_length=100)
    narration: str | None = Field(default=None, max_length=2000)
    account_id: uuid.UUID | None = Field(
        default=None, description="Receipt, payment, expense: the cash or bank account money moves through"
    )
    party_id: uuid.UUID | None = Field(
        default=None, description="Credit note: the customer; debit note: supplier"
    )
    allocations: list[AllocationIn] = Field(
        default_factory=list, max_length=100, description="Credit / debit note: bill-wise for the party"
    )
    lines: list[VoucherLineIn] = Field(min_length=1, max_length=200)


class PostIn(BaseModel):
    override_lock_reason: str | None = Field(
        default=None, max_length=500, description="Needed to post into a locked period (period.override_lock)"
    )
    reuse_number: int | None = Field(
        default=None, ge=1, description="Take a number freed by a cancelled document (see released-numbers)"
    )


class VoucherLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    account_id: uuid.UUID
    party_id: uuid.UUID | None
    debit: Decimal
    credit: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    description: str | None
    allocations: list[AllocationOut]


class JournalLineOut(BaseModel):
    line_no: int
    account_id: uuid.UUID
    account_code: str
    account_name_en: str
    account_name_ar: str
    party_id: uuid.UUID | None
    debit: Decimal
    credit: Decimal
    description: str | None


class VoucherSummary(BaseModel):
    id: uuid.UUID
    doc_type: VoucherType
    status: DocumentStatus
    number: str | None
    doc_date: date
    business_id: uuid.UUID
    branch_id: uuid.UUID
    reference: str | None
    narration: str | None
    account_id: uuid.UUID | None
    party_id: uuid.UUID | None
    total: Decimal
    posted_at: datetime | None


class VoucherOut(VoucherSummary):
    vat_total: Decimal
    allocations: list[AllocationOut]
    lines: list[VoucherLineOut]
    journal: list[JournalLineOut] = Field(description="What it posted (empty while a draft)")


# --- bills, ledgers, trial balance -------------------------------------------------------------------------


class BillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    party_id: uuid.UUID
    account_id: uuid.UUID
    reference: str
    bill_date: date
    due_date: date | None
    direction: Direction
    amount: Decimal
    amount_open: Decimal
    is_advance: bool
    document_id: uuid.UUID | None


class AgeingBuckets(BaseModel):
    """Open bills by days past their due date (the bill date when there is none)."""

    not_due: Decimal = Decimal(0)
    days_1_30: Decimal = Decimal(0)
    days_31_60: Decimal = Decimal(0)
    days_61_90: Decimal = Decimal(0)
    over_90: Decimal = Decimal(0)
    advances: Decimal = Decimal(0)  # paid ahead, not yet allocated to a bill
    total: Decimal = Decimal(0)  # the buckets less the advances
    pdc: Decimal = Decimal(0)  # cheques not yet cleared: received (receivables) or issued (payables)
    after_pdc: Decimal = Decimal(0)  # the total once they clear


class AgeingRowOut(AgeingBuckets):
    party_id: uuid.UUID
    code: str
    name_en: str
    name_ar: str


class AgeingOut(BaseModel):
    as_of: date
    rows: list[AgeingRowOut]
    totals: AgeingBuckets


class LedgerLineOut(BaseModel):
    entry_date: date
    document_id: uuid.UUID | None
    doc_type: str | None
    number: str | None
    narration: str | None
    account_id: uuid.UUID
    party_id: uuid.UUID | None
    description: str | None
    debit: Decimal
    credit: Decimal
    balance: Decimal = Field(description="Running balance after this line, debit positive")


class LedgerOut(BaseModel):
    opening: Decimal
    debit_total: Decimal
    credit_total: Decimal
    closing: Decimal
    lines: list[LedgerLineOut]


class PartyLedgerOut(LedgerOut):
    open_bills: list[BillOut]
    on_account: Decimal = Field(description="Balance not tied to any bill (debit positive)")
    pdc_received_pending: Decimal = Field(
        default=Decimal(0), description="Cheques received from the party, not yet cleared (A2)"
    )
    pdc_issued_pending: Decimal = Field(
        default=Decimal(0), description="Cheques issued to the party, not cleared"
    )
    outstanding_after_pdc: Decimal = Field(
        default=Decimal(0), description="The closing balance once those cheques clear (debit positive)"
    )


class TrialBalanceRowOut(BaseModel):
    account_id: uuid.UUID
    code: str
    name_en: str
    name_ar: str
    nature: Nature
    debit: Decimal
    credit: Decimal


class TrialBalanceOut(BaseModel):
    business_id: uuid.UUID
    as_of: date
    rows: list[TrialBalanceRowOut]
    total_debit: Decimal
    total_credit: Decimal
