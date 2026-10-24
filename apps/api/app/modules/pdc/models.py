"""The PDC register (M1.9; decision A2; posting-rules.md rules 15 to 22).

A post-dated cheque is a memo until the bank clears it: nothing here touches the ledger. Clearance posts a
receipt
or payment voucher (``voucher_id``); a bounce may post a bank-charge expense voucher (``charge_voucher_id``).
"""

import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped


class PdcDirection(StrEnum):
    RECEIVED = "received"  # from a customer
    ISSUED = "issued"  # to a supplier


class PdcStatus(StrEnum):
    IN_HAND = "in_hand"
    DEPOSITED = "deposited"
    ISSUED = "issued"
    CLEARED = "cleared"
    BOUNCED = "bounced"
    RETURNED = "returned"
    CANCELLED = "cancelled"


RECEIVED_STATUSES = (
    PdcStatus.IN_HAND,
    PdcStatus.DEPOSITED,
    PdcStatus.CLEARED,
    PdcStatus.BOUNCED,
    PdcStatus.RETURNED,
)
ISSUED_STATUSES = (PdcStatus.ISSUED, PdcStatus.CLEARED, PdcStatus.BOUNCED, PdcStatus.CANCELLED)


def _in(column: str, values: type[StrEnum] | tuple[StrEnum, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", column], [f"{table}.tenant_id", f"{table}.id"])


def _business_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "business_id", column], [f"{table}.tenant_id", f"{table}.business_id", f"{table}.id"]
    )


class Pdc(TenantScoped, Base):
    __tablename__ = "pdc"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        _tenant_fk("business_id", "business"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "branch_id"], ["branch.tenant_id", "branch.business_id", "branch.id"]
        ),
        _tenant_fk("party_id", "party"),
        _business_fk("deposit_account_id", "account"),
        _business_fk("voucher_id", "document"),
        _business_fk("charge_voucher_id", "document"),
        CheckConstraint(_in("direction", PdcDirection), name="direction"),
        CheckConstraint(_in("status", PdcStatus), name="status"),
        CheckConstraint(
            f"(direction = 'received' AND {_in('status', RECEIVED_STATUSES)}) "
            f"OR (direction = 'issued' AND {_in('status', ISSUED_STATUSES)})",
            name="status_of_direction",
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint(
            "(status = 'cleared') = (voucher_id IS NOT NULL AND cleared_on IS NOT NULL)",
            name="cleared_has_voucher",
        ),
        CheckConstraint(
            "status <> 'deposited' OR (deposited_on IS NOT NULL AND deposit_account_id IS NOT NULL)",
            name="deposited_where",
        ),
        CheckConstraint(
            "direction <> 'issued' OR deposit_account_id IS NOT NULL", name="issued_from_an_account"
        ),
        CheckConstraint("(status = 'bounced') = (bounced_on IS NOT NULL)", name="bounced_on"),
        Index("ix_pdc_register", "business_id", "status", "cheque_date"),
        Index("ix_pdc_party", "party_id", "status"),
    )

    business_id: Mapped[uuid.UUID]
    branch_id: Mapped[uuid.UUID]
    direction: Mapped[str] = mapped_column(String(10))
    party_id: Mapped[uuid.UUID]
    cheque_no: Mapped[str] = mapped_column(String(30))
    bank_name: Mapped[str | None] = mapped_column(String(100))  # the drawer's bank
    cheque_date: Mapped[date] = mapped_column(Date)  # maturity: when it may be presented
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    status: Mapped[str] = mapped_column(String(10))
    deposit_account_id: Mapped[uuid.UUID | None]  # our bank: deposited into (received) or drawn on (issued)
    deposited_on: Mapped[date | None] = mapped_column(Date)
    cleared_on: Mapped[date | None] = mapped_column(Date)
    bounced_on: Mapped[date | None] = mapped_column(Date)
    bounce_reason: Mapped[str | None] = mapped_column(Text)
    voucher_id: Mapped[uuid.UUID | None]  # the receipt / payment its clearance posted
    charge_voucher_id: Mapped[uuid.UUID | None]  # a bank charge on a bounce
    notes: Mapped[str | None] = mapped_column(Text)


class PdcIntendedBill(TenantScoped, Base):
    """An open bill the cheque is meant for; becomes a bill-wise allocation on clearance (as far as still
    open)."""

    __tablename__ = "pdc_intended_bill"
    __table_args__ = (
        _tenant_fk("pdc_id", "pdc"),
        _tenant_fk("bill_id", "bill"),
        UniqueConstraint("pdc_id", "bill_id"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    pdc_id: Mapped[uuid.UUID]
    bill_id: Mapped[uuid.UUID]
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))
