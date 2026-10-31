"""VAT return API shapes (M1.10b). Money is exact Decimal."""

import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field


class TaxCodeOut(BaseModel):
    """One code of the registry (M1.10d): what entry screens offer on a line."""

    code: str = Field(examples=["SR"])
    side: str = Field(description="sales or purchase")
    rate: Decimal = Field(description="Percent, e.g. 5")
    treatment: str = Field(description="output, recoverable, blocked, reverse_charge or none")
    name_en: str
    name_ar: str


class VatBoxOut(BaseModel):
    box: str = Field(examples=["1b"])
    label_en: str
    label_ar: str
    kind: str = Field(description="amount_vat, amount (no VAT column), total (calculated), manual (Phase 2)")
    amount: Decimal
    vat: Decimal
    adjustment: Decimal = Field(default=Decimal("0.0000"), description="Manual adjustments come with Phase 2")


class VatReturnOut(BaseModel):
    business_id: uuid.UUID
    date_from: dt.date
    date_to: dt.date
    status: str = Field(default="draft", description="Saving and filing (which locks the period) come later")
    boxes: list[VatBoxOut]
    payable: Decimal = Field(description="Box 14: VAT due less VAT recoverable; negative is refundable")


class VatBoxLineOut(BaseModel):
    """One document behind a box figure (the drill-down)."""

    document_id: uuid.UUID
    number: str | None
    doc_type: str
    doc_date: dt.date
    party_id: uuid.UUID | None
    party_name_en: str | None
    party_name_ar: str | None
    amount: Decimal
    vat: Decimal
