"""Reading uploaded files and matching their columns (M1.12). No database."""

import io
import zipfile
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from app.modules.imports import mapping, readers
from app.modules.imports.kinds import parties, products
from app.modules.imports.readers import TooManyRowsError, UnreadableFileError, read_table

ARABIC_NAME = "الخليج للأقمشة"


def csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


def xlsx_bytes(rows: list[list[object]], title: str | None = None, hidden_first: bool = False) -> bytes:
    book = Workbook()
    first = book.active
    assert first is not None
    if hidden_first:
        first.title = "Hidden"
        first.sheet_state = "hidden"
        first = book.create_sheet("Data")
    if title:
        first.title = title
    for row in rows:
        first.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_csv_in_utf8_with_a_byte_order_mark() -> None:
    table = read_table("c.csv", b"\xef\xbb\xbf" + csv_bytes(f"Name,TRN\n{ARABIC_NAME},100123456700003\n"))
    assert table.headers == ["Name", "TRN"]
    assert table.rows == [[ARABIC_NAME, "100123456700003"]]
    assert table.numbers == [2]


def test_csv_saved_by_arabic_excel_is_windows_1256() -> None:
    table = read_table("c.csv", csv_bytes(f"Name,TRN\n{ARABIC_NAME},1\n", "cp1256"))
    assert table.rows[0][0] == ARABIC_NAME


def test_csv_in_windows_1252_keeps_its_accents() -> None:
    table = read_table("c.csv", csv_bytes("Name\nCafé Zürich\n", "cp1252"))
    assert table.rows == [["Café Zürich"]]


@pytest.mark.parametrize("delimiter", [",", ";", "\t"])
def test_the_delimiter_is_found(delimiter: str) -> None:
    table = read_table(
        "c.csv", csv_bytes(delimiter.join(["Name", "TRN"]) + "\n" + delimiter.join(["A", "1"]) + "\n")
    )
    assert table.headers == ["Name", "TRN"]
    assert table.rows == [["A", "1"]]


def test_blank_rows_and_title_gaps_are_skipped_and_numbers_stay_true() -> None:
    table = read_table("c.csv", csv_bytes(",,\nName,TRN\nA,1\n,\n\nB,2\n"))
    assert table.headers == ["Name", "TRN"]
    assert table.rows == [["A", "1"], ["B", "2"]]
    assert table.numbers == [3, 6]  # the rows as Excel numbers them


def test_short_rows_are_padded_and_extra_cells_dropped() -> None:
    table = read_table("c.csv", csv_bytes("A,B,C\n1\n1,2,3,4,5\n"))
    assert table.rows == [["1", None, None], ["1", "2", "3"]]


def test_blank_header_cells_get_a_name() -> None:
    table = read_table("c.csv", csv_bytes("Name,,Phone\nA,x,1\n"))
    assert table.headers == ["Name", "Column 2", "Phone"]


def test_excel_cells_keep_their_types() -> None:
    data = xlsx_bytes(
        [
            ["Name", "Amount", "Date", "Code"],
            ["A", 1234.5, datetime(2026, 3, 31), 1001],
            ["B", None, date(2026, 4, 1), "x"],
        ]
    )
    table = read_table("book.xlsx", data)
    assert table.headers == ["Name", "Amount", "Date", "Code"]
    assert table.rows[0] == ["A", 1234.5, datetime(2026, 3, 31), 1001]
    assert table.rows[1][1] is None


def test_the_first_visible_sheet_is_read() -> None:
    table = read_table("book.xlsx", xlsx_bytes([["Name"], ["A"]], hidden_first=True))
    assert table.rows == [["A"]]


def test_a_file_is_recognised_by_its_bytes_not_its_name() -> None:
    table = read_table("customers.csv", xlsx_bytes([["Name"], ["A"]]))
    assert table.rows == [["A"]]


@pytest.mark.parametrize("name", ["old.xls", "macros.xlsm", "doc.pdf", "notes.docx", "image.png"])
def test_other_formats_are_refused_with_a_way_forward(name: str) -> None:
    with pytest.raises(UnreadableFileError):
        read_table(name, b"not a spreadsheet")


def test_a_damaged_workbook_is_refused() -> None:
    with pytest.raises(UnreadableFileError):
        read_table("book.xlsx", b"PK\x03\x04 this is not a real zip")


def test_a_workbook_that_unzips_to_too_much_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readers, "MAX_UNZIPPED_BYTES", 100)
    with pytest.raises(UnreadableFileError):
        read_table("book.xlsx", xlsx_bytes([["Name"], ["A"]]))


def test_a_zip_without_a_workbook_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    with pytest.raises(UnreadableFileError):
        read_table("book.xlsx", buffer.getvalue())


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(UnreadableFileError):
        read_table("c.csv", b"")
    with pytest.raises(UnreadableFileError):
        read_table("c.csv", b",,\n\n")


def test_too_many_rows_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readers, "MAX_ROWS", 3)
    read_table("c.csv", csv_bytes("A\n1\n2\n3\n"))
    with pytest.raises(TooManyRowsError):
        read_table("c.csv", csv_bytes("A\n1\n2\n3\n4\n"))


def test_a_sheet_with_a_million_formatted_but_empty_rows_is_read_quickly() -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Name"])
    sheet.append(["A"])
    sheet["B1000000"] = "stray cell far below"  # stops the file being read after a long empty run
    out = io.BytesIO()
    book.save(out)
    assert read_table("book.xlsx", out.getvalue()).rows == [["A"]]


def test_wide_files_are_cut_at_the_column_limit() -> None:
    wide = ",".join(f"c{i}" for i in range(300))
    assert len(read_table("c.csv", csv_bytes(wide + "\n" + wide + "\n")).headers) == readers.MAX_COLUMNS


# --- matching columns --------------------------------------------------------------------------------------


def test_english_headers_are_matched_to_party_fields() -> None:
    headers = ["Party Name", "Type", "TRN", "Mobile", "E-mail", "Address", "Credit Days", "Notes"]
    found = mapping.suggest(headers, parties.FIELDS)
    assert found["name_en"] == 0
    assert found["type"] == 1
    assert found["trn"] == 2
    assert found["phone"] == 3
    assert found["email"] == 4
    assert found["address"] == 5
    assert found["credit_days"] == 6
    assert found["name_ar"] is None
    assert found["city"] is None


def test_arabic_headers_are_matched_to_party_fields() -> None:
    headers = ["اسم العميل", "الرقم الضريبي", "الهاتف", "العنوان", "الإمارة"]
    found = mapping.suggest(headers, parties.FIELDS)
    assert (found["name_en"], found["trn"], found["phone"], found["address"], found["emirate"]) == (
        0,
        1,
        2,
        3,
        4,
    )


def test_a_column_is_suggested_for_one_field_only() -> None:
    found = mapping.suggest(["Name", "Item Code"], products.FIELDS)
    assert found["name_en"] == 0
    assert found["sku"] == 1
    assert len({i for i in found.values() if i is not None}) == len(
        [i for i in found.values() if i is not None]
    )


def test_the_mapping_must_name_real_columns_for_known_fields() -> None:
    fields = parties.FIELDS
    assert mapping.check_mapping({"name_en": 0, "trn": None}, fields, 2) == {"name_en": 0, "trn": None}
    with pytest.raises(mapping.MappingError):
        mapping.check_mapping({"name_en": 0, "nonsense": 1}, fields, 2)
    with pytest.raises(mapping.MappingError):
        mapping.check_mapping({"name_en": 5}, fields, 2)
    with pytest.raises(mapping.MappingError):
        mapping.check_mapping({"name_en": 0, "trn": 0}, fields, 2)
    with pytest.raises(mapping.MappingError) as caught:
        mapping.check_mapping({"trn": 1}, fields, 2)
    assert caught.value.extra["missing"] == ["name_en"]
