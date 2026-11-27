"""Reading a bank statement file and matching it to the books. Pure: no database.

Money in the bank is positive (it is a debit in the books). A match needs the same amount and a date within a
few days; a shared reference ranks first, then the nearer date; each side is used once.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.errors import UnprocessableError
from app.modules.imports.readers import Table
from app.modules.imports.values import CellError, clean_text, normal_header, parse_date, parse_decimal

WINDOW_DAYS = 5
ZERO = Decimal("0.0000")

_DATE = {
    "date",
    "transaction date",
    "txn date",
    "value date",
    "posting date",
    "booking date",
    "التاريخ",
    "تاريخ",
}
_TEXT = {
    "description",
    "details",
    "narration",
    "particulars",
    "transaction details",
    "memo",
    "البيان",
    "التفاصيل",
}
_REF = {"reference", "ref", "ref no", "cheque no", "cheque number", "chq no", "transaction id", "المرجع"}
_DEBIT = {"debit", "withdrawal", "withdrawals", "paid out", "money out", "dr", "مدين", "سحب"}
_CREDIT = {"credit", "deposit", "deposits", "paid in", "money in", "cr", "دائن", "ايداع"}
_AMOUNT = {"amount", "net amount", "المبلغ"}


class BadStatementError(UnprocessableError):
    code = "bank_statement_unreadable"
    title = "The statement could not be read"


@dataclass(frozen=True)
class ParsedLine:
    line_no: int
    line_date: date
    description: str | None
    reference: str | None
    amount: Decimal  # in the bank positive, out negative


def _column(headers: list[str], names: set[str]) -> int | None:
    for i, header in enumerate(headers):
        if normal_header(header) in names:
            return i
    return None


def parse_statement(table: Table) -> list[ParsedLine]:
    """Date, description, reference and either one signed amount or separate debit/credit (withdrawal/deposit)
    columns. From the bank's side: a *credit* or *deposit* is money in. Rows with no amount are skipped."""
    headers = [str(h) for h in table.headers]
    date_col = _column(headers, _DATE)
    amount_col = _column(headers, _AMOUNT)
    debit_col = _column(headers, _DEBIT)
    credit_col = _column(headers, _CREDIT)
    if date_col is None or (amount_col is None and debit_col is None and credit_col is None):
        raise BadStatementError(
            "The file needs a Date and an Amount column (or Debit and Credit, Withdrawal and Deposit).",
            headers=headers,
        )
    text_col, ref_col = _column(headers, _TEXT), _column(headers, _REF)
    out: list[ParsedLine] = []
    for row, number in zip(table.rows, table.numbers, strict=True):
        try:
            day = parse_date(row[date_col])
            if amount_col is not None:
                amount = parse_decimal(row[amount_col])
            else:
                paid_out = parse_decimal(row[debit_col]) if debit_col is not None else None
                paid_in = parse_decimal(row[credit_col]) if credit_col is not None else None
                amount = (paid_in or ZERO) - (paid_out or ZERO) if (paid_in or paid_out) else None
        except CellError as error:
            raise BadStatementError(f"Row {number}: {error}", row=number, problem=error.code) from error
        if amount is None or amount == 0:
            continue
        if day is None:
            raise BadStatementError(f"Row {number}: the date is empty.", row=number, problem="date_missing")
        out.append(
            ParsedLine(
                line_no=len(out) + 1,
                line_date=day,
                description=clean_text(row[text_col]) if text_col is not None else None,
                reference=(clean_text(row[ref_col]) or None) if ref_col is not None else None,
                amount=amount.quantize(ZERO),
            )
        )
    if not out:
        raise BadStatementError("The statement has no lines with an amount.")
    return out


@dataclass(frozen=True)
class StatementSide:
    id: uuid.UUID
    day: date
    amount: Decimal
    text: str  # reference and description, to look for a shared reference in


@dataclass(frozen=True)
class BookSide:
    id: uuid.UUID
    day: date
    amount: Decimal
    text: str  # the document number and narration


def _shares_reference(reference: str | None, book_text: str) -> bool:
    ref = re.sub(r"\W+", "", (reference or "").lower())
    return len(ref) >= 3 and ref in re.sub(r"\W+", "", book_text.lower())


def auto_match(
    statement: list[tuple[StatementSide, str | None]],
    book: list[BookSide],
    *,
    window_days: int = WINDOW_DAYS,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """(statement line id, book line id) pairs. Candidates share the exact amount and fall within the window;
    the best are taken first (shared reference, then the nearest date), each line once."""
    candidates: list[tuple[int, int, int, uuid.UUID, uuid.UUID]] = []
    for index, (line, reference) in enumerate(statement):
        for number, entry in enumerate(book):
            gap = abs((line.day - entry.day).days)
            if entry.amount == line.amount and gap <= window_days:
                shared = _shares_reference(reference, entry.text)
                candidates.append((0 if shared else 1, gap, index * 100_000 + number, line.id, entry.id))
    candidates.sort(key=lambda c: c[:3])
    used_statement: set[uuid.UUID] = set()
    used_book: set[uuid.UUID] = set()
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    for *_rank, line_id, book_id in candidates:
        if line_id in used_statement or book_id in used_book:
            continue
        used_statement.add(line_id)
        used_book.add(book_id)
        pairs.append((line_id, book_id))
    return pairs
