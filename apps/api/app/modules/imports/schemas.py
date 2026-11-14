"""Import API shapes (M1.12)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.imports.kinds.base import ImportProblem
from app.modules.imports.mapping import Mapping


class ImportFieldOut(BaseModel):
    key: str
    label_en: str
    label_ar: str
    required: bool
    example: str
    hint_en: str
    hint_ar: str


class ImportOptionOut(BaseModel):
    key: str
    label_en: str
    label_ar: str
    values: list[str]
    default: str
    required: bool
    kind: Literal["choice", "date"]


class ImportKindOut(BaseModel):
    key: str
    label_en: str
    label_ar: str
    description_en: str
    description_ar: str
    order: int
    fields: list[ImportFieldOut]
    options: list[ImportOptionOut]
    permissions: list[str]


class ImportBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    business_id: uuid.UUID
    kind: str
    status: str
    filename: str
    size: int
    sha256: str
    headers: list[str]
    row_count: int
    mapping: dict[str, Any]
    options: dict[str, Any]
    counts: dict[str, Any]
    result: dict[str, Any]
    created_at: datetime
    committed_at: datetime | None


class ImportUploadOut(ImportBatchOut):
    suggested_mapping: Mapping
    sample: list[list[str]] = Field(description="The first rows of the file as text, to recognise columns")
    duplicate_of: uuid.UUID | None = Field(
        default=None, description="This exact file was already imported (that batch)"
    )


class ImportCheckIn(BaseModel):
    mapping: Mapping = Field(description="Field key → 0-based column of the file (null: the file has none)")
    options: dict[str, str] = Field(default_factory=dict)


class ImportRowOut(BaseModel):
    number: int = Field(description="The row number in the file, as Excel shows it")
    status: Literal["new", "exists", "error"]
    label: str
    problems: list[ImportProblem]


class ImportCheckOut(BaseModel):
    batch_id: uuid.UUID
    total: int
    new: int
    exists: int
    errors: int
    rows: list[ImportRowOut] = Field(description="Rows with problems first, then the first rows without")
    truncated: bool = Field(description="More rows exist than are listed here; the counts cover all of them")
