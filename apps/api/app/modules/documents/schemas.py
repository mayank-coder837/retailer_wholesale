"""Cancel and Reverse requests and answers (M1.7b)."""

import datetime as dt
import uuid
from datetime import date

from pydantic import BaseModel, Field

from app.modules.documents.models import DocumentStatus


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    date: dt.date | None = Field(default=None, description="The reversal's date (default: today)")
    override_lock_reason: str | None = Field(
        default=None,
        max_length=500,
        description="Needed to date it in a locked period (period.override_lock)",
    )


class CorrectedOut(BaseModel):
    """The cancelled or reversed document, and the reversal when there is one."""

    id: uuid.UUID
    doc_type: str
    status: DocumentStatus
    number: str | None
    doc_date: date
    cancel_reason: str | None
    reversal_id: uuid.UUID | None = None
    reversal_doc_type: str | None = None
    reversal_number: str | None = None
    reversal_date: date | None = None


class ReleasedNumberOut(BaseModel):
    number: int
    formatted: str
    fiscal_year: str
    cancelled_document_id: uuid.UUID
