"""Bank reconciliation shapes (P2.3). Money is exact Decimal; in the bank is positive, out is negative."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class StatementLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    statement_id: uuid.UUID
    line_no: int
    line_date: dt.date
    description: str | None
    reference: str | None
    amount: Decimal
    matched_line_id: uuid.UUID | None
    matched_at: dt.datetime | None


class StatementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    account_id: uuid.UUID
    filename: str
    date_from: dt.date
    date_to: dt.date
    opening_balance: Decimal | None
    closing_balance: Decimal | None


class ImportedOut(BaseModel):
    statement: StatementOut
    lines: int = Field(description="Lines read from the file and kept")
    duplicates_skipped: int = Field(
        description="Lines already imported from an earlier, overlapping statement"
    )
    auto_matched: int = Field(description="Lines matched to the books straight away")


class StatementDetailOut(StatementOut):
    lines: list[StatementLineOut]


class BookLineOut(BaseModel):
    line_id: uuid.UUID
    entry_date: dt.date
    amount: Decimal = Field(description="Money in the bank account positive, out negative")
    narration: str | None
    number: str | None
    doc_type: str | None


class MatchIn(BaseModel):
    book_line_id: uuid.UUID


class AutoMatchOut(BaseModel):
    matched: int
    unmatched_statement_lines: int


class ReconciliationOut(BaseModel):
    """Statement against books: the two adjusted balances agree when everything is explained."""

    business_id: uuid.UUID
    account_id: uuid.UUID
    as_of: dt.date
    statement_closing: Decimal | None = Field(
        description="The latest statement's closing balance, if it has one"
    )
    book_balance: Decimal
    unmatched_statement_total: Decimal = Field(description="In the bank, not yet in the books (signed)")
    unmatched_book_total: Decimal = Field(description="In the books, not yet in the bank (signed)")
    adjusted_bank: Decimal | None = Field(
        description="Statement closing + what is in the books but not the bank"
    )
    adjusted_book: Decimal = Field(description="Book balance + what is in the bank but not the books")
    difference: Decimal | None
    reconciled: bool
