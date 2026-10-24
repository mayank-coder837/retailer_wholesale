"""Accounting tables (domain-model.md → Accounting; docs/milestones/m1.5-accounting.md).

The journal is the money ledger: ``journal_entry`` + ``journal_line`` rows are only ever inserted (CLAUDE.md
rule 3) and every entry balances (rule 4), enforced by triggers in migration 0009 as well as in code.
Children reference parents through composite ``(tenant_id, business_id, id)`` keys, so a line can only point
at an account of its own business (and never at another tenant's rows: foreign keys skip row-level security).
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
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped


class Nature(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


class AccountType(StrEnum):
    CASH = "cash"
    BANK = "bank"
    RECEIVABLE = "receivable"
    PAYABLE = "payable"
    STOCK = "stock"
    TAX = "tax"
    CURRENT_ASSET = "current_asset"
    FIXED_ASSET = "fixed_asset"
    CURRENT_LIABILITY = "current_liability"
    NON_CURRENT_LIABILITY = "non_current_liability"
    EQUITY = "equity"
    SALES = "sales"
    OTHER_INCOME = "other_income"
    COGS = "cogs"
    EXPENSE = "expense"


# The nature each ledger type belongs to; ``tax`` sits on either side (input VAT asset, output VAT liability).
TYPE_NATURES: dict[AccountType, frozenset[Nature]] = {
    AccountType.CASH: frozenset({Nature.ASSET}),
    AccountType.BANK: frozenset({Nature.ASSET}),
    AccountType.RECEIVABLE: frozenset({Nature.ASSET}),
    AccountType.PAYABLE: frozenset({Nature.LIABILITY}),
    AccountType.STOCK: frozenset({Nature.ASSET}),
    AccountType.TAX: frozenset({Nature.ASSET, Nature.LIABILITY}),
    AccountType.CURRENT_ASSET: frozenset({Nature.ASSET}),
    AccountType.FIXED_ASSET: frozenset({Nature.ASSET}),
    AccountType.CURRENT_LIABILITY: frozenset({Nature.LIABILITY}),
    AccountType.NON_CURRENT_LIABILITY: frozenset({Nature.LIABILITY}),
    AccountType.EQUITY: frozenset({Nature.EQUITY}),
    AccountType.SALES: frozenset({Nature.INCOME}),
    AccountType.OTHER_INCOME: frozenset({Nature.INCOME}),
    AccountType.COGS: frozenset({Nature.EXPENSE}),
    AccountType.EXPENSE: frozenset({Nature.EXPENSE}),
}
CASH_OR_BANK = frozenset({AccountType.CASH, AccountType.BANK})
CONTROL = frozenset({AccountType.RECEIVABLE, AccountType.PAYABLE})  # every line names a party


class Role(StrEnum):
    """Posting rules name roles, never account codes (posting-rules.md)."""

    AR_CONTROL = "ar_control"
    AP_CONTROL = "ap_control"
    INVENTORY = "inventory"
    COGS = "cogs"
    GOODS_DELIVERED_NOT_INVOICED = "goods_delivered_not_invoiced"
    STOCK_COST_ADJUSTMENT = "stock_cost_adjustment"
    SALES = "sales"
    SALES_RETURNS = "sales_returns"
    PURCHASE_PRICE_VARIANCE = "purchase_price_variance"
    GRNI = "grni"
    VAT_OUTPUT = "vat_output"
    VAT_INPUT = "vat_input"
    STOCK_ADJUSTMENT = "stock_adjustment"
    BANK_CHARGES = "bank_charges"
    ROUNDING = "rounding"
    FX_GAIN_LOSS = "fx_gain_loss"
    OPENING_BALANCE_EQUITY = "opening_balance_equity"


class PeriodStatus(StrEnum):
    OPEN = "open"
    LOCKED = "locked"


class YearStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"  # year-end closing: Phase 2


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class EntryKind(StrEnum):
    """Why a journal entry exists. Cancel and Reverse mirror a document's ``posting`` entries only."""

    POSTING = "posting"  # the document's own entry, written when it was posted
    RECOST = "recost"  # rule 28: later stock moves taking new costs after a backdated move
    UNDO = "undo"  # Cancel / Reverse: a mirror of a posting entry, or the stock value it did not cover


class AllocationType(StrEnum):
    """Tally's bill-wise choices for a party line."""

    AGAINST_REF = "against_ref"
    NEW_REF = "new_ref"
    ADVANCE = "advance"
    ON_ACCOUNT = "on_account"


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", column], [f"{table}.tenant_id", f"{table}.id"])


def _business_fk(column: str, table: str) -> ForeignKeyConstraint:
    """Points at a row of the same tenant *and* the same business."""
    return ForeignKeyConstraint(
        ["tenant_id", "business_id", column], [f"{table}.tenant_id", f"{table}.business_id", f"{table}.id"]
    )


class Account(TenantScoped, Base):
    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "business_id", "id"),
        UniqueConstraint("business_id", "code"),
        _tenant_fk("business_id", "business"),
        _business_fk("parent_id", "account"),
        CheckConstraint(_in("nature", Nature), name="nature"),
        CheckConstraint(f"account_type IS NULL OR {_in('account_type', AccountType)}", name="account_type"),
        CheckConstraint("is_group = (account_type IS NULL)", name="ledgers_have_a_type"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
    )

    business_id: Mapped[uuid.UUID]
    code: Mapped[str] = mapped_column(String(20))
    name_en: Mapped[str] = mapped_column(String(200))
    name_ar: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[uuid.UUID | None]
    is_group: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    nature: Mapped[str] = mapped_column(String(20))
    account_type: Mapped[str | None] = mapped_column(String(30))
    allow_manual_posting: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountRole(TenantScoped, Base):
    __tablename__ = "account_role"
    __table_args__ = (
        UniqueConstraint("business_id", "role"),
        _tenant_fk("business_id", "business"),
        _business_fk("account_id", "account"),
        CheckConstraint(_in("role", Role), name="role"),
    )

    business_id: Mapped[uuid.UUID]
    role: Mapped[str] = mapped_column(String(40))
    account_id: Mapped[uuid.UUID]


class FiscalYear(TenantScoped, Base):
    __tablename__ = "fiscal_year"
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "id"),
        UniqueConstraint("business_id", "code"),
        UniqueConstraint("business_id", "start_date"),
        _tenant_fk("business_id", "business"),
        CheckConstraint("start_date < end_date", name="dates"),
        CheckConstraint(_in("status", YearStatus), name="status"),
    )

    business_id: Mapped[uuid.UUID]
    code: Mapped[str] = mapped_column(
        String(9), comment="2026, or 2026-27 when the year starts after January"
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), server_default=YearStatus.OPEN.value)


class FiscalPeriod(TenantScoped, Base):
    __tablename__ = "fiscal_period"
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "id"),
        UniqueConstraint("fiscal_year_id", "period_no"),
        UniqueConstraint("business_id", "start_date"),
        _business_fk("fiscal_year_id", "fiscal_year"),
        CheckConstraint("period_no BETWEEN 1 AND 12", name="period_no"),
        CheckConstraint("start_date <= end_date", name="dates"),
        CheckConstraint(_in("status", PeriodStatus), name="status"),
        CheckConstraint("(status = 'locked') = (locked_at IS NOT NULL)", name="locked_at"),
    )

    business_id: Mapped[uuid.UUID]
    fiscal_year_id: Mapped[uuid.UUID]
    period_no: Mapped[int] = mapped_column(SmallInteger)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), server_default=PeriodStatus.OPEN.value)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))


class JournalEntry(TenantScoped, Base):
    """Only posted entries exist (drafts are documents). Insert-only."""

    __tablename__ = "journal_entry"
    __audit__ = False  # written only by posting; the posted document carries the audit entry
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "id", "business_id", "entry_date"),
        _tenant_fk("business_id", "business"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "branch_id"], ["branch.tenant_id", "branch.business_id", "branch.id"]
        ),
        _business_fk("fiscal_period_id", "fiscal_period"),
        _business_fk("source_document_id", "document"),
        _tenant_fk("reversal_of_id", "journal_entry"),
        CheckConstraint(_in("kind", EntryKind), name="kind"),
        CheckConstraint("reversal_of_id IS NULL OR kind = 'undo'", name="mirror_is_undo"),
        Index("ix_journal_entry_source", "source_document_id"),
        Index("uq_journal_entry_mirrored_once", "reversal_of_id", unique=True),
    )

    business_id: Mapped[uuid.UUID]
    branch_id: Mapped[uuid.UUID]
    entry_date: Mapped[date] = mapped_column(Date)
    fiscal_period_id: Mapped[uuid.UUID]
    number: Mapped[str | None] = mapped_column(String(40))
    narration: Mapped[str | None] = mapped_column(Text)
    source_document_id: Mapped[uuid.UUID | None]
    reversal_of_id: Mapped[uuid.UUID | None]  # undo entries: the posting entry they mirror
    kind: Mapped[str] = mapped_column(String(10), server_default=EntryKind.POSTING.value)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class JournalLine(TenantScoped, Base):
    """Base-currency amounts; exactly one of debit / credit is above zero. Insert-only."""

    __tablename__ = "journal_line"
    __audit__ = False
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("entry_id", "line_no"),
        # Same business and date as the entry: ledgers read lines without joining entries.
        ForeignKeyConstraint(
            ["tenant_id", "entry_id", "business_id", "entry_date"],
            [
                "journal_entry.tenant_id",
                "journal_entry.id",
                "journal_entry.business_id",
                "journal_entry.entry_date",
            ],
        ),
        _business_fk("account_id", "account"),
        _tenant_fk("party_id", "party"),
        CheckConstraint("debit >= 0 AND credit >= 0", name="not_negative"),
        CheckConstraint("(debit > 0) <> (credit > 0)", name="one_side"),
        Index("ix_journal_line_account", "account_id", "entry_date"),
        Index(
            "ix_journal_line_party", "party_id", "entry_date", postgresql_where=text("party_id IS NOT NULL")
        ),
    )

    entry_id: Mapped[uuid.UUID]
    business_id: Mapped[uuid.UUID]
    entry_date: Mapped[date] = mapped_column(Date)
    line_no: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[uuid.UUID]
    party_id: Mapped[uuid.UUID | None]
    debit: Mapped[Decimal] = mapped_column(Numeric(19, 4), server_default=text("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(19, 4), server_default=text("0"))
    description: Mapped[str | None] = mapped_column(String(500))


class Bill(TenantScoped, Base):
    """An open item of a customer or supplier, settled bill-wise. Only ``amount_open`` ever changes."""

    __tablename__ = "bill"
    __audit__ = False  # amount_open is a cache of the allocations; the documents are audited
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        _tenant_fk("business_id", "business"),
        _tenant_fk("party_id", "party"),
        _business_fk("account_id", "account"),
        _business_fk("document_id", "document"),
        _tenant_fk("journal_line_id", "journal_line"),
        CheckConstraint(_in("direction", Direction), name="direction"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("amount_open >= 0 AND amount_open <= amount", name="amount_open_range"),
        CheckConstraint("due_date IS NULL OR due_date >= bill_date", name="due_after_bill"),
        Index("ix_bill_party", "business_id", "party_id", "account_id"),
        Index("ix_bill_open", "party_id", "bill_date", postgresql_where=text("amount_open > 0")),
    )

    business_id: Mapped[uuid.UUID]
    party_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    reference: Mapped[str] = mapped_column(String(100))
    bill_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    direction: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    amount_open: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    is_advance: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    document_id: Mapped[uuid.UUID | None]
    journal_line_id: Mapped[uuid.UUID]


class BillAllocation(TenantScoped, Base):
    """A posted line settling part of a bill. Insert-only. A cancelled or reversed document's allocations are
    undone by negative copies (``reversal_of_id``), never by changing them."""

    __tablename__ = "bill_allocation"
    __audit__ = False
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        _tenant_fk("bill_id", "bill"),
        _tenant_fk("journal_line_id", "journal_line"),
        _tenant_fk("reversal_of_id", "bill_allocation"),
        CheckConstraint("amount <> 0", name="amount_not_zero"),
        CheckConstraint("(amount < 0) = (reversal_of_id IS NOT NULL)", name="negative_only_to_undo"),
        Index("ix_bill_allocation_bill", "bill_id"),
        Index("uq_bill_allocation_undone_once", "reversal_of_id", unique=True),
    )

    bill_id: Mapped[uuid.UUID]
    journal_line_id: Mapped[uuid.UUID]
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    reversal_of_id: Mapped[uuid.UUID | None]  # the allocation this negative copy undoes


class VoucherLine(TenantScoped, Base):
    """A line as the user entered it. Posting turns the voucher into journal lines (``posting.py``)."""

    __tablename__ = "voucher_line"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("document_id", "line_no"),
        _business_fk("document_id", "document"),
        _business_fk("account_id", "account"),
        _tenant_fk("party_id", "party"),
        CheckConstraint("debit >= 0 AND credit >= 0", name="not_negative"),
        CheckConstraint("(debit > 0) <> (credit > 0)", name="one_side"),
        CheckConstraint("vat_rate >= 0 AND vat_rate < 100", name="vat_rate_range"),
        CheckConstraint("vat_amount >= 0", name="vat_not_negative"),
    )

    document_id: Mapped[uuid.UUID]
    business_id: Mapped[uuid.UUID]
    line_no: Mapped[int] = mapped_column(Integer)
    account_id: Mapped[uuid.UUID]
    party_id: Mapped[uuid.UUID | None]
    debit: Mapped[Decimal] = mapped_column(Numeric(19, 4), server_default=text("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(19, 4), server_default=text("0"))
    vat_rate: Mapped[Decimal] = mapped_column(Numeric(19, 6), server_default=text("0"))
    vat_amount: Mapped[Decimal] = mapped_column(Numeric(19, 4), server_default=text("0"))
    description: Mapped[str | None] = mapped_column(String(500))


class VoucherAllocation(TenantScoped, Base):
    """A draft's bill-wise choice for a party line (null ``voucher_line_id``: the header party)."""

    __tablename__ = "voucher_allocation"
    __table_args__ = (
        _business_fk("document_id", "document"),
        _tenant_fk("voucher_line_id", "voucher_line"),
        _tenant_fk("bill_id", "bill"),
        CheckConstraint(_in("allocation_type", AllocationType), name="allocation_type"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "(allocation_type = 'against_ref') = (bill_id IS NOT NULL)", name="bill_for_against_ref"
        ),
        CheckConstraint(
            "allocation_type IN ('new_ref', 'advance') OR (reference IS NULL AND due_date IS NULL)",
            name="reference_for_new_bills",
        ),
    )

    document_id: Mapped[uuid.UUID]
    business_id: Mapped[uuid.UUID]
    voucher_line_id: Mapped[uuid.UUID | None]
    allocation_type: Mapped[str] = mapped_column(String(20))
    bill_id: Mapped[uuid.UUID | None]
    reference: Mapped[str | None] = mapped_column(String(100))
    due_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))
