"""Matching a file's columns to a kind's fields (M1.12). Pure, no database.

A mapping is ``{field key: column index}`` (0-based, in the order of the file's header row); a field the
file does not have is ``None``. Columns are named by position because two columns can carry the same title.
"""

from app.core.errors import UnprocessableError
from app.modules.imports.kinds.base import Field, Row
from app.modules.imports.readers import Table
from app.modules.imports.values import normal_header

Mapping = dict[str, int | None]


class MappingError(UnprocessableError):
    code = "mapping_incomplete"
    title = "Match every required field to a column"


def suggest(headers: list[str], fields: tuple[Field, ...]) -> Mapping:
    """The best column for each field: an exact header match first, then a header containing one of its words.

    A column is suggested for one field only; required fields choose first.
    """
    columns = [normal_header(h) for h in headers]
    taken: set[int] = set()
    suggestion: Mapping = dict.fromkeys(f.key for f in fields)
    ordered = sorted(fields, key=lambda f: not f.required)
    for exact in (True, False):
        for f in ordered:
            if suggestion[f.key] is not None:
                continue
            words = {normal_header(w) for w in (*f.names, f.label_en, f.label_ar)}
            for index, column in enumerate(columns):
                if index in taken or not column:
                    continue
                hit = column in words if exact else any(len(w) >= 4 and w in column for w in words)
                if hit:
                    suggestion[f.key] = index
                    taken.add(index)
                    break
    return suggestion


def check_mapping(mapping: Mapping, fields: tuple[Field, ...], width: int) -> Mapping:
    """The mapping with only known fields and real columns; every required field must be matched."""
    known = {f.key for f in fields}
    clean: Mapping = {}
    for key, index in mapping.items():
        if key not in known:
            raise MappingError(f"There is no field '{key}' in this import.", field=key)
        if index is not None and not 0 <= index < width:
            raise MappingError(f"Column {index + 1} is not in the file.", field=key)
        clean[key] = index
    used = [i for i in clean.values() if i is not None]
    if len(used) != len(set(used)):
        raise MappingError("A column can be matched to one field only.")
    missing = [f.key for f in fields if f.required and clean.get(f.key) is None]
    if missing:
        raise MappingError("These required fields have no column: " + ", ".join(missing), missing=missing)
    return clean


def rows_of(table: Table, mapping: Mapping) -> list[Row]:
    return [
        Row(number, {key: cells[index] for key, index in mapping.items() if index is not None})
        for number, cells in zip(table.numbers, table.rows, strict=True)
    ]
