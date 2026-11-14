"""Reading the values of a spreadsheet cell, exactly (M1.12).

Pure functions, no database. Numbers never pass through ``float`` arithmetic: a number typed in Excel
arrives as a float, and its shortest text form (``repr``) is what the person saw, so that is what is
read. Dates are read **day first** (31/03/2026) like the UAE writes them; month first is never guessed.
"""

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_ARABIC_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬",  # Arabic-Indic and Persian digits, Arabic decimal and thousands marks
    "01234567890123456789.,",
)
_SPACES = re.compile(r"[\s   ']+")
_NUMBER = re.compile(r"^(\d+(\.\d*)?|\.\d+)$")  # unsigned: the sign was taken off before
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+(\.\d*)?$")
_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3}){2,}(,\d*)?$")
_DR_CR = re.compile(r"^(?P<lead>(dr|cr)\.?\s*)?(?P<body>.*?)(?P<trail>\s*(dr|cr)\.?)?$", re.IGNORECASE)
_CURRENCY = re.compile(r"(aed|د\.إ|درهم)", re.IGNORECASE)

MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ),
        start=1,
    )
    for name in names
}
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T].*)?$")
_DAY_FIRST = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")
_NAMED_MONTH = re.compile(r"^(\d{1,2})[\s\-/.]([A-Za-z]{3,9})[\s\-/.,]+(\d{2}|\d{4})$")
YEARS = range(1990, 2101)


class CellError(ValueError):
    """A cell that cannot be read; ``code`` is stable, the message is for the person fixing the file."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def clean_text(value: Any) -> str | None:
    """Trimmed text; ``None`` for a blank cell. A number in a text column (SKU 1001) keeps its digits."""
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float | int | Decimal):
        number = _exact(value)
        return format(number.normalize() if number == number.to_integral() else number, "f")
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    text = unicodedata.normalize("NFC", str(value))
    text = "".join(ch for ch in text if ch >= " " or ch in "\t\n")
    return " ".join(text.split()) or None


def _exact(value: float | int | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # repr is the shortest text that reads back to the same float: what Excel showed.
        return Decimal(repr(value))
    return Decimal(value)


def parse_decimal(value: Any, *, balance: bool = False) -> Decimal | None:
    """An exact number, or ``None`` for a blank cell.

    Reads ``1,234.50``, ``1.234,50``, ``1 234,5``, ``(500.00)``, ``500-``, ``AED 12``, Arabic-Indic digits
    and, with ``balance=True``, a Tally-style ``Dr`` / ``Cr`` mark (debit positive, credit negative).
    """
    if is_blank(value):
        return None
    if isinstance(value, bool):
        raise CellError("not_a_number", "A yes/no cell is not a number.")
    if isinstance(value, float | int | Decimal):
        number = _exact(value)
        if not number.is_finite():
            raise CellError("not_a_number", "This is not a number.")
        return number
    text = _CURRENCY.sub("", str(value).translate(_ARABIC_DIGITS))
    text = _SPACES.sub("", text)
    sign = 1
    if balance:
        marks = _DR_CR.match(text)
        if marks and (marks.group("lead") or marks.group("trail")):
            mark = (marks.group("lead") or marks.group("trail") or "").strip(" .").lower()
            sign = -1 if mark == "cr" else 1
            text = marks.group("body")
    if text.startswith("(") and text.endswith(")"):
        sign, text = -sign, text[1:-1]
    if text.endswith("-"):
        sign, text = -sign, text[:-1]
    if text.startswith("+"):
        text = text[1:]
    elif text.startswith("-"):
        sign, text = -sign, text[1:]
    text = _separators(text)
    if not _NUMBER.match(text):
        raise CellError("not_a_number", f"'{value}' is not a number.")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - the pattern already excludes these
        raise CellError("not_a_number", f"'{value}' is not a number.") from exc
    return number * sign


def _separators(text: str) -> str:
    """Drops thousands marks and leaves a ``.`` decimal point."""
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):  # 1.234,50
            return text.replace(".", "").replace(",", ".")
        return text.replace(",", "")  # 1,234.50
    if "," in text:
        if _THOUSANDS_COMMA.match(text):  # 1,234 or 12,345,678
            return text.replace(",", "")
        return text.replace(",", ".") if text.count(",") == 1 else text  # 1,5 is a decimal comma
    if _THOUSANDS_DOT.match(text):  # 1.234.567
        return text.replace(".", "")
    return text


def parse_date(value: Any) -> date | None:
    """A date, or ``None`` for a blank cell. Day first; ISO and named months also read."""
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        return _in_range(value.date())
    if isinstance(value, date):
        return _in_range(value)
    if isinstance(value, bool | float | int | Decimal):
        raise CellError("not_a_date", f"'{value}' is not a date; type it as 31/03/2026.")
    text = str(value).translate(_ARABIC_DIGITS).strip()
    parts: tuple[int, int, int] | None = None
    if match := _ISO.match(text):
        parts = (int(match[1]), int(match[2]), int(match[3]))
    elif match := _DAY_FIRST.match(text):
        parts = (_year(match[3]), int(match[2]), int(match[1]))
    elif (match := _NAMED_MONTH.match(text)) and match[2].lower() in MONTHS:
        parts = (_year(match[3]), MONTHS[match[2].lower()], int(match[1]))
    if parts is None:
        raise CellError("not_a_date", f"'{value}' is not a date; use 31/03/2026 or 2026-03-31.")
    try:
        return _in_range(date(*parts))
    except ValueError as exc:
        raise CellError("not_a_date", f"'{value}' is not a real date.") from exc


def _year(text: str) -> int:
    if len(text) == 4:
        return int(text)
    return 2000 + int(text) if int(text) < 70 else 1900 + int(text)


def _in_range(day: date) -> date:
    if day.year not in YEARS:
        raise CellError(
            "date_out_of_range", f"{day.isoformat()} is outside the years {YEARS[0]} to {YEARS[-1]}."
        )
    return day


_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_MARKS = re.compile(r"[ؐ-ًؚ-ٰٟـ]")
_NOT_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normal_header(text: str) -> str:
    """A column title as comparable text: lower case, no accents or Arabic marks, single spaces."""
    decomposed = unicodedata.normalize("NFKD", str(text))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = _MARKS.sub("", plain).translate(_ALEF).lower().replace("_", " ")
    return _NOT_WORD.sub(" ", plain).strip()
