"""Report shapes (M1.11). Money is exact Decimal, signed as an accountant reads the statement."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class StatementRowOut(BaseModel):
    """One line of a statement: an account, or a calculated line (``account_id`` empty, ``key`` says
    which)."""

    account_id: uuid.UUID | None
    key: str | None = Field(default=None, description="Calculated lines: profit_year, profit_earlier")
    code: str | None
    name_en: str
    name_ar: str
    amount: Decimal = Field(description="As the section reads it: income and liabilities credit-positive")


class SectionOut(BaseModel):
    key: str = Field(
        examples=["sales"],
        description="P&L: sales, cogs, other_income, expenses. Balance sheet: current_assets, fixed_assets, "
        "current_liabilities, non_current_liabilities, equity",
    )
    rows: list[StatementRowOut]
    total: Decimal


class ProfitLossOut(BaseModel):
    business_id: uuid.UUID
    date_from: dt.date
    date_to: dt.date
    sections: list[SectionOut]
    gross_profit: Decimal = Field(description="Sales less cost of goods sold")
    net_profit: Decimal = Field(description="Gross profit plus other income less expenses; negative: a loss")


class BalanceSheetOut(BaseModel):
    business_id: uuid.UUID
    as_of: dt.date
    fiscal_year_start: dt.date
    assets: list[SectionOut]
    liabilities: list[SectionOut]
    equity: SectionOut = Field(description="With the profit not yet closed into equity (open question R10)")
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    balanced: bool = Field(description="Assets = liabilities + equity (checked, not assumed)")


class DayBookLineOut(BaseModel):
    account_id: uuid.UUID
    code: str
    name_en: str
    name_ar: str
    party_id: uuid.UUID | None
    party_name_en: str | None
    party_name_ar: str | None
    description: str | None
    debit: Decimal
    credit: Decimal


class DayBookEntryOut(BaseModel):
    entry_id: uuid.UUID
    entry_date: dt.date
    number: str | None
    narration: str | None
    document_id: uuid.UUID | None
    doc_type: str | None
    lines: list[DayBookLineOut]


class DayBookOut(BaseModel):
    business_id: uuid.UUID
    date_from: dt.date
    date_to: dt.date
    entries: list[DayBookEntryOut]
    total_debit: Decimal
    total_credit: Decimal
    truncated: bool = Field(description="More entries than shown: narrow the dates")
