"""Bank statements and what they were matched to (P2.3).

A match lives on the statement line, never on the journal line: posted ledger rows are never updated
(CLAUDE.md rule 3).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped


class BankStatement(TenantScoped, Base):
    __tablename__ = "bank_statement"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "account_id"],
            ["account.tenant_id", "account.business_id", "account.id"],
        ),
        CheckConstraint("date_from <= date_to", name="dates"),
        Index("ix_bank_statement_account", "account_id", "date_to"),
    )

    business_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]  # the bank account in the chart of accounts
    filename: Mapped[str] = mapped_column(String(255))
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    opening_balance: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    closing_balance: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))


class BankStatementLine(TenantScoped, Base):
    __tablename__ = "bank_statement_line"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "statement_id"], ["bank_statement.tenant_id", "bank_statement.id"]
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "account_id"],
            ["account.tenant_id", "account.business_id", "account.id"],
        ),
        ForeignKeyConstraint(["tenant_id", "matched_line_id"], ["journal_line.tenant_id", "journal_line.id"]),
        UniqueConstraint("statement_id", "line_no"),
        CheckConstraint("amount <> 0", name="amount_not_zero"),
        # One book line is matched to one statement line.
        Index(
            "uq_bank_statement_line_matched",
            "matched_line_id",
            unique=True,
            postgresql_where=text("matched_line_id IS NOT NULL"),
        ),
        Index("ix_bank_statement_line_account", "account_id", "line_date"),
    )

    statement_id: Mapped[uuid.UUID]
    business_id: Mapped[uuid.UUID]
    account_id: Mapped[uuid.UUID]
    line_no: Mapped[int]
    line_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(Numeric(19, 4))  # money in the bank positive, out negative
    matched_line_id: Mapped[uuid.UUID | None]
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matched_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
