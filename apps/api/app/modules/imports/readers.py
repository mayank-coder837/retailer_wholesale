"""Reading an uploaded file into a header row and data rows (M1.12). Pure, no database.

CSV (any encoding Excel on a UAE PC writes) and Excel ``.xlsx`` today; Tally XML joins in M1.12c. A file is
untrusted input: its size, row and column counts are capped, a spreadsheet is read in read-only mode
(nothing is evaluated, formulas arrive as their last calculated value) and its zip container is checked
before it is opened.
"""

import csv
import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from openpyxl import load_workbook

from app.core.errors import UnprocessableError
from app.modules.imports.values import is_blank as _blank

MAX_ROWS = 20_000
MAX_COLUMNS = 200
MAX_BLANK_RUN = 1000  # a sheet can declare a million formatted rows with nothing in them
MAX_UNZIPPED_BYTES = 200 * 1024 * 1024
_ARABIC = re.compile(r"[؀-ۿ]")
_XLSX_MAGIC = b"PK\x03\x04"

Cell = str | int | float | Decimal | bool | date | datetime | None


class UnreadableFileError(UnprocessableError):
    code = "unreadable_file"
    title = "The file cannot be read"


class TooManyRowsError(UnprocessableError):
    code = "too_many_rows"
    title = "The file has too many rows"


@dataclass(frozen=True)
class Note:
    """Something the reader found out about the file; ``code`` is stable, for the screens to translate."""

    code: str
    message: str
    count: int = 0


@dataclass(frozen=True)
class Table:
    """What the file holds. ``numbers[i]`` is the row number a person sees in Excel for ``rows[i]``."""

    headers: list[str]
    rows: list[list[Cell]]
    numbers: list[int]
    canonical: bool = False  # headers are the kind's own field keys (a Tally export): no matching needed
    notes: list[Note] = field(default_factory=list)  # what the reader found out about the file


def read_table(filename: str, data: bytes, kind: str | None = None) -> Table:
    """Reads CSV, ``.xlsx`` or a Tally ``.xml`` export (for the kind chosen) by the file's own bytes."""
    name = filename.lower()
    if name.endswith(".xml"):
        from app.modules.imports import tally  # the reader builds on this module

        return tally.read_tally(data, kind)
    if data.startswith(_XLSX_MAGIC):
        return read_xlsx(data)
    if name.endswith((".xls", ".xlsb", ".xlsm")):
        raise UnreadableFileError(
            "This is an old or macro Excel format. Open it in Excel and save as .xlsx or CSV.",
            filename=filename,
        )
    if name.endswith((".csv", ".txt", ".tsv")):
        return read_csv(data)
    raise UnreadableFileError("Upload a CSV, Excel (.xlsx) or Tally (.xml) file.", filename=filename)


def decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), else Windows-1256 when that gives Arabic, else Windows-1252."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    arabic = data.decode("cp1256", errors="replace")
    if _ARABIC.search(arabic):
        return arabic
    return data.decode("cp1252", errors="replace")


def _delimiter(text: str) -> str:
    first = next((line for line in text.splitlines() if line.strip()), "")
    counts = {d: first.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def read_csv(data: bytes) -> Table:
    text = decode(data)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(text))
    try:
        return _table((i, list(row)) for i, row in enumerate(reader, start=1))
    except csv.Error as exc:
        raise UnreadableFileError("The CSV file is not valid.", reason=str(exc)) from exc


def _check_zip(data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(info.file_size for info in archive.infolist()) > MAX_UNZIPPED_BYTES:
                raise UnreadableFileError("The spreadsheet is too large when opened.")
    except zipfile.BadZipFile as exc:
        raise UnreadableFileError("This is not a valid Excel (.xlsx) file.") from exc


def read_xlsx(data: bytes) -> Table:
    _check_zip(data)
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            sheet = next((s for s in workbook.worksheets if s.sheet_state == "visible"), None)
            if sheet is None:
                raise UnreadableFileError("The workbook has no visible sheet.")
            rows = sheet.iter_rows(values_only=True, max_col=MAX_COLUMNS)
            return _table((i, list(row)) for i, row in enumerate(rows, start=1))
        finally:
            workbook.close()
    except (UnreadableFileError, TooManyRowsError):
        raise
    except Exception as exc:  # openpyxl raises many types for damaged files
        raise UnreadableFileError("This Excel file cannot be read.", reason=type(exc).__name__) from exc


def _cell(value: Any) -> Cell:
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, bool | int | float | Decimal | date | datetime | str) or value is None:
        return value
    return str(value)


def _table(numbered: Iterable[tuple[int, list[Any]]]) -> Table:
    """The first non-blank row is the header; blank rows are skipped; every row is as wide as the header.

    Reads as it goes, so the limits hold before a huge file is held in memory.
    """
    headers: list[str] | None = None
    width = 0
    rows: list[list[Cell]] = []
    numbers: list[int] = []
    blank_run = 0
    for number, raw in numbered:
        if headers is None:
            header_row = raw[:MAX_COLUMNS]
            width = max((i + 1 for i, c in enumerate(header_row) if not _blank(c)), default=0)
            if width:
                headers = [
                    str(header_row[i]).strip() if not _blank(header_row[i]) else f"Column {i + 1}"
                    for i in range(width)
                ]
                blank_run = 0
                continue
        else:
            cells = [_cell(c) for c in raw[:width]]
            cells += [None] * (width - len(cells))
            if not all(_blank(c) for c in cells):
                if len(rows) >= MAX_ROWS:
                    raise TooManyRowsError(
                        f"A file can have at most {MAX_ROWS} rows; split it.", max_rows=MAX_ROWS
                    )
                rows.append(cells)
                numbers.append(number)
                blank_run = 0
                continue
        blank_run += 1
        if blank_run > MAX_BLANK_RUN:
            break
    if headers is None:
        raise UnreadableFileError("The file is empty.")
    return Table(headers, rows, numbers)
