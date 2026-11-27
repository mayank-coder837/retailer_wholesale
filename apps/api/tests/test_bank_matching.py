"""Reading a bank statement and matching it to the books (P2.3), pure."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.modules.banking import matching
from app.modules.banking.matching import BadStatementError, BookSide, StatementSide
from app.modules.imports.readers import read_table


def parse(csv: str) -> list[matching.ParsedLine]:
    return matching.parse_statement(read_table("statement.csv", csv.encode()))


def test_a_signed_amount_column_is_read_as_is() -> None:
    lines = parse(
        "Date,Description,Reference,Amount\n"
        '2026-11-03,Ali Traders,CHQ 1001,"1,500.50"\n'
        "2026-11-04,Rent,,-2000\n"
    )
    assert [(ln.line_date, ln.amount, ln.reference) for ln in lines] == [
        (date(2026, 11, 3), Decimal("1500.5000"), "CHQ 1001"),
        (date(2026, 11, 4), Decimal("-2000.0000"), None),
    ]


def test_deposit_and_withdrawal_columns_become_one_signed_amount() -> None:
    lines = parse(
        "Transaction Date,Details,Withdrawal,Deposit\n03/11/2026,Supplier,500,\n04/11/2026,Customer,,750.25\n"
    )
    assert [ln.amount for ln in lines] == [Decimal("-500.0000"), Decimal("750.2500")]


def test_a_file_without_a_date_or_amount_is_refused_and_empty_rows_are_skipped() -> None:
    with pytest.raises(BadStatementError):
        parse("Name,Notes\nx,y\n")
    with pytest.raises(BadStatementError):
        parse("Date,Amount\n2026-11-03,\n")
    lines = parse("Date,Amount\n2026-11-03,10\n2026-11-04,\n")
    assert len(lines) == 1


def side(amount: str, day: int, text: str = "") -> tuple[StatementSide, BookSide]:
    key = uuid.uuid4()
    return (
        StatementSide(key, date(2026, 11, day), Decimal(amount), text),
        BookSide(uuid.uuid4(), date(2026, 11, day), Decimal(amount), text),
    )


def test_same_amount_within_the_window_matches_once_each() -> None:
    s1, b1 = side("100", 3)
    s2, b2 = side("100", 4)
    pairs = matching.auto_match([(s1, None), (s2, None)], [b1, b2])
    assert len({p[0] for p in pairs}) == 2
    assert len({p[1] for p in pairs}) == 2


def test_a_shared_reference_beats_a_nearer_date() -> None:
    s = StatementSide(uuid.uuid4(), date(2026, 11, 10), Decimal("250"), "CHQ 7788")
    near = BookSide(uuid.uuid4(), date(2026, 11, 10), Decimal("250"), "RV-1 Cash sale")
    right = BookSide(uuid.uuid4(), date(2026, 11, 13), Decimal("250"), "RV-9 cheque 7788 Ali")
    assert matching.auto_match([(s, "7788")], [near, right]) == [(s.id, right.id)]


def test_different_amounts_or_dates_beyond_the_window_never_match() -> None:
    s = StatementSide(uuid.uuid4(), date(2026, 11, 10), Decimal("100"), "")
    other_amount = BookSide(uuid.uuid4(), date(2026, 11, 10), Decimal("100.01"), "")
    too_late = BookSide(uuid.uuid4(), date(2026, 11, 20), Decimal("100"), "")
    assert matching.auto_match([(s, None)], [other_amount, too_late]) == []
