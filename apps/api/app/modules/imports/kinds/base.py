"""What every kind of import shares: its fields, a row reader that collects problems, check / apply."""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.imports.readers import Cell
from app.modules.imports.values import CellError, clean_text, parse_date, parse_decimal

Status = Literal["new", "exists", "error"]
YES_NO = {
    **dict.fromkeys(("yes", "y", "true", "1", "نعم"), True),
    **dict.fromkeys(("no", "n", "false", "0", "لا"), False),
}


@dataclass(frozen=True)
class Field:
    """One thing a file can say about a row. ``names`` are header words (English, Arabic) it is found by."""

    key: str
    label_en: str
    label_ar: str
    names: tuple[str, ...]
    required: bool = False
    example: str = ""
    hint_en: str = ""
    hint_ar: str = ""


@dataclass(frozen=True)
class Option:
    """A choice that applies to the whole file (not a column), e.g. what kind of party the rows are."""

    key: str
    label_en: str
    label_ar: str
    values: tuple[str, ...]
    default: str
    required: bool = False
    kind: Literal["choice", "date"] = "choice"


class ImportProblem(BaseModel):
    field: str | None
    code: str
    message: str


@dataclass(frozen=True)
class Row:
    number: int  # the row number a person sees in Excel
    values: dict[str, Cell]


@dataclass
class RowResult:
    number: int
    status: Status
    label: str
    problems: list[ImportProblem] = field(default_factory=list)
    prepared: Any = None  # what apply() writes; never leaves the server


@dataclass
class Context:
    session: AsyncSession
    tenant_id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID | None
    user_id: uuid.UUID | None
    node_id: uuid.UUID | None
    options: dict[str, str]
    may_override_lock: bool = False
    allows: Callable[[str], bool] = field(default=lambda _permission: True)  # does the importer hold this?
    batch_id: uuid.UUID | None = None


class Kind(Protocol):
    key: str
    label_en: str
    label_ar: str
    description_en: str
    description_ar: str
    fields: tuple[Field, ...]
    options: tuple[Option, ...]
    permissions: tuple[
        str, ...
    ]  # held on top of import.post (an import never lets anyone do more than a screen)
    order: int

    async def check(self, ctx: Context, rows: list[Row]) -> list[RowResult]: ...

    async def apply(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]: ...


class Reader:
    """Reads one row's cells by field, remembering what could not be read."""

    def __init__(self, row: Row, fields: tuple[Field, ...]) -> None:
        self.row = row
        self.labels = {f.key: f.label_en for f in fields}
        self.problems: list[ImportProblem] = []

    def _fail(self, key: str, code: str, message: str) -> None:
        self.problems.append(ImportProblem(field=key, code=code, message=message))

    def text(self, key: str, *, required: bool = False, limit: int | None = None) -> str | None:
        value = clean_text(self.row.values.get(key))
        if value is None:
            if required:
                self._fail(key, "required", f"{self.labels[key]} is empty.")
            return None
        if limit is not None and len(value) > limit:
            self._fail(key, "too_long", f"{self.labels[key]} is longer than {limit} characters.")
            return value[:limit]
        return value

    def decimal(self, key: str, *, required: bool = False, balance: bool = False) -> Decimal | None:
        try:
            value = parse_decimal(self.row.values.get(key), balance=balance)
        except CellError as exc:
            self._fail(key, exc.code, f"{self.labels[key]}: {exc}")
            return None
        if value is None and required:
            self._fail(key, "required", f"{self.labels[key]} is empty.")
        return value

    def date(self, key: str, *, required: bool = False) -> date | None:
        try:
            value = parse_date(self.row.values.get(key))
        except CellError as exc:
            self._fail(key, exc.code, f"{self.labels[key]}: {exc}")
            return None
        if value is None and required:
            self._fail(key, "required", f"{self.labels[key]} is empty.")
        return value

    def choice[T](
        self, key: str, words: Mapping[str, T], *, default: T | None = None, required: bool = False
    ) -> T | None:
        """The cell as one of ``words`` (lower-cased text → value); blank gives ``default``."""
        text = self.text(key)
        if text is None:
            if default is None and required:
                self._fail(key, "required", f"{self.labels[key]} is empty.")
            return default
        found = words.get(text.lower())
        if found is None:
            found = next((v for word, v in words.items() if word in text.lower().split()), None)
        if found is None:
            self._fail(key, "unknown_value", f"'{text}' is not one of: {', '.join(sorted(set(words)))}.")
        return found

    def flag(self, key: str) -> bool:
        """Yes / no cells (yes, y, true, 1, نعم); blank is no."""
        return bool(self.choice(key, YES_NO, default=False))

    def add(self, key: str | None, code: str, message: str) -> None:
        self.problems.append(ImportProblem(field=key, code=code, message=message))

    def result(self, status: Status, label: str, prepared: Any = None) -> RowResult:
        if self.problems:
            return RowResult(self.row.number, "error", label, self.problems)
        return RowResult(self.row.number, status, label, [], prepared)


def from_validation(
    reader: Reader, exc: ValidationError, rename: Callable[[tuple[str | int, ...]], str | None]
) -> None:
    """Turns a schema error into problems on the file's fields (``rename``: schema path → field key)."""
    for error in exc.errors():
        message = str(error["msg"]).removeprefix("Value error, ")
        reader.add(rename(tuple(error["loc"])), "invalid", message)
