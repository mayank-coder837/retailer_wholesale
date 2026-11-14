"""Reading spreadsheet cells exactly (M1.12): numbers, dates, text, header words."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.modules.imports.values import CellError, clean_text, normal_header, parse_date, parse_decimal

D = Decimal


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("1234.5", D("1234.5")),
        ("1,234.50", D("1234.50")),
        ("1.234,50", D("1234.50")),
        ("1 234,5", D("1234.5")),
        ("1 234,5", D("1234.5")),
        ("1,234", D("1234")),  # a comma and exactly three digits is a thousands mark
        ("1,5", D("1.5")),  # a lone comma before one or two digits is a decimal comma
        ("1.234", D("1.234")),  # one dot is a decimal point (quantities have three decimals)
        ("1.234.567", D("1234567")),
        ("12,345,678.9", D("12345678.9")),
        ("(500.00)", D("-500.00")),
        ("500-", D("-500")),
        ("-3", D("-3")),
        ("+3", D("3")),
        ("AED 12", D("12")),
        ("12 aed", D("12")),
        ("١٢٣٫٥", D("123.5")),  # Arabic-Indic digits and decimal mark
        ("١٬٢٣٤٫٥٠", D("1234.50")),
        (1234.5, D("1234.5")),
        (0.1, D("0.1")),  # the shortest text of the float, not its binary expansion
        (7, D("7")),
        (D("2.50"), D("2.50")),
        ("0", D("0")),
        (".5", D("0.5")),
    ],
)
def test_numbers_are_read_exactly(cell: object, expected: Decimal) -> None:
    assert parse_decimal(cell) == expected


@pytest.mark.parametrize("cell", [None, "", "   ", "\t"])
def test_a_blank_cell_is_none(cell: object) -> None:
    assert parse_decimal(cell) is None
    assert parse_date(cell) is None
    assert clean_text(cell) is None


@pytest.mark.parametrize(
    "cell", ["abc", "1e5", "nan", "inf", "-inf", "1.2.3", "12-34", "--5", True, float("nan")]
)
def test_text_that_is_not_a_number_is_refused(cell: object) -> None:
    with pytest.raises(CellError) as caught:
        parse_decimal(cell)
    assert caught.value.code == "not_a_number"


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("1,000.00 Dr", D("1000.00")),
        ("1,000.00 Cr", D("-1000.00")),
        ("1000 DR", D("1000")),
        ("500Cr", D("-500")),
        ("Cr 50", D("-50")),
        ("Dr. 75", D("75")),
        ("1000", D("1000")),
        ("(40) Dr", D("-40")),
    ],
)
def test_tally_debit_and_credit_marks(cell: str, expected: Decimal) -> None:
    assert parse_decimal(cell, balance=True) == expected


def test_a_dr_cr_mark_is_refused_where_none_is_expected() -> None:
    with pytest.raises(CellError):
        parse_decimal("100 Dr")


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("31/03/2026", date(2026, 3, 31)),
        ("31-03-2026", date(2026, 3, 31)),
        ("31.03.2026", date(2026, 3, 31)),
        ("31/03/26", date(2026, 3, 31)),
        ("31/12/98", date(1998, 12, 31)),  # two digits: 00-69 are 20xx, 70-99 are 19xx
        ("01/01/69", date(2069, 1, 1)),
        ("01/01/95", date(1995, 1, 1)),
        ("1/2/2026", date(2026, 2, 1)),  # day first, never month first
        ("2026-03-31", date(2026, 3, 31)),
        ("2026-03-31 00:00:00", date(2026, 3, 31)),
        ("2026-03-31T09:30:00", date(2026, 3, 31)),
        ("31 Mar 2026", date(2026, 3, 31)),
        ("31-March-2026", date(2026, 3, 31)),
        ("1 sept 2026", date(2026, 9, 1)),
        ("٣١/٠٣/٢٠٢٦", date(2026, 3, 31)),
        (datetime(2026, 3, 31, 0, 0), date(2026, 3, 31)),
        (date(2026, 3, 31), date(2026, 3, 31)),
    ],
)
def test_dates_are_read_day_first(cell: object, expected: date) -> None:
    assert parse_date(cell) == expected


@pytest.mark.parametrize(
    "cell",
    [
        "03/31/2026",
        "2026-02-30",
        "31/02/2026",
        "yesterday",
        45000,
        45000.5,
        "1850-01-01",
        "31/03/1850",
        "01/01/70",
        True,
    ],
)
def test_dates_that_cannot_be_read_are_refused_not_guessed(cell: object) -> None:
    with pytest.raises(CellError):
        parse_date(cell)


def test_text_is_trimmed_and_numbers_in_text_columns_keep_their_digits() -> None:
    assert clean_text("  Gulf   Fabrics \n LLC ") == "Gulf Fabrics LLC"
    assert clean_text(1001.0) == "1001"  # a SKU typed as a number
    assert clean_text(12.5) == "12.5"
    assert clean_text(D("3.50")) == "3.50"
    assert clean_text(date(2026, 3, 31)) == "2026-03-31"
    assert clean_text("a\x00b\x07") == "ab"


def test_formula_looking_text_is_kept_as_text() -> None:
    assert clean_text('=HYPERLINK("http://x")') == '=HYPERLINK("http://x")'
    assert clean_text("@SUM(A1)") == "@SUM(A1)"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Party_Name", "party name"),
        ("  TRN. ", "trn"),
        ("Credit-Limit (AED)", "credit limit aed"),
        ("اسم العميل", "اسم العميل"),
        ("الإمارة", "الاماره"),
        ("عَمِيل", "عميل"),
        ("Café", "cafe"),
    ],
)
def test_header_words_compare_without_punctuation_case_or_marks(title: str, expected: str) -> None:
    assert normal_header(title) == expected


def _plain(d: Decimal) -> str:
    return f"{d:f}"


def _thousands(d: Decimal) -> str:
    return f"{d:,.4f}"


def _european(d: Decimal) -> str:
    return f"{d:,.4f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def _arabic(d: Decimal) -> str:
    return f"{d:,.4f}".translate(str.maketrans("0123456789,.", "٠١٢٣٤٥٦٧٨٩٬٫"))


def _brackets(d: Decimal) -> str:
    return f"({_thousands(abs(d))})" if d < 0 else _thousands(d)


def _spaced(d: Decimal) -> str:
    return _thousands(d).replace(",", " ")


@given(
    st.integers(min_value=-(10**15), max_value=10**15).map(lambda n: Decimal(n).scaleb(-4)),
    st.sampled_from([_plain, _thousands, _european, _arabic, _brackets, _spaced]),
)
def test_any_amount_written_in_any_style_reads_back_exactly(amount: Decimal, style: object) -> None:
    text = style(amount)  # type: ignore[operator]
    assert parse_decimal(text) == amount


@given(st.integers(min_value=-(10**12), max_value=10**12))
def test_a_float_typed_in_excel_reads_as_the_number_shown(cents: int) -> None:
    shown = Decimal(cents).scaleb(-2)
    assert parse_decimal(float(shown)) == shown
