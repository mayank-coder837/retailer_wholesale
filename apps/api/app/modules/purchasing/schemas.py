"""Purchasing API shapes (M1.7). Quantities are typed in one of the product's units; prices are per unit
typed and VAT-exclusive; money and quantities are exact Decimal."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from app.modules.documents.models import DocumentStatus

Quantity = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=6)]
Price = Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=6)]


class PurchaseDocType(StrEnum):
    PURCHASE_ORDER = "purchase_order"
    GOODS_RECEIPT = "goods_receipt"
    PURCHASE_BILL = "purchase_bill"
    PURCHASE_RETURN = "purchase_return"


class PurchaseLineIn(BaseModel):
    variant_id: uuid.UUID | None = Field(default=None, description="The item; empty on an expense line")
    account_id: uuid.UUID | None = Field(
        default=None, description="Expense lines and service items on a bill: the account debited"
    )
    unit: str | None = Field(
        default=None, max_length=20, description="One of the item's units (default: base)"
    )
    qty: Quantity = Field(description="Expense lines: 1")
    unit_price: Price | None = Field(
        default=None, description="Per unit typed, VAT-exclusive (default: the line it follows)"
    )
    vat_rate: Decimal | None = Field(
        default=None, description="0 or 5 (%); default: the line it follows, else 0"
    )
    source_line_id: uuid.UUID | None = Field(
        default=None,
        description="Receipt: the order line; bill: the receipt line; return: the bill or receipt line",
    )
    batch_id: uuid.UUID | None = None
    batch_no: str | None = Field(default=None, min_length=1, max_length=60)
    expiry_date: date | None = None
    description: str | None = Field(default=None, max_length=500)


class PurchaseDocIn(BaseModel):
    doc_type: PurchaseDocType
    business_id: uuid.UUID
    branch_id: uuid.UUID
    doc_date: date
    party_id: uuid.UUID = Field(description="The supplier")
    warehouse_id: uuid.UUID | None = Field(
        default=None, description="Receipts and returns; bills with items not received before"
    )
    supplier_ref: str | None = Field(
        default=None, max_length=60, description="Bills: the supplier's bill number"
    )
    due_date: date | None = Field(default=None, description="Bills: default the supplier's payment terms")
    reference: str | None = Field(default=None, max_length=100)
    narration: str | None = Field(default=None, max_length=2000)
    lines: list[PurchaseLineIn] = Field(min_length=1, max_length=500)


class PostPurchaseIn(BaseModel):
    override_lock_reason: str | None = Field(default=None, max_length=500)
    reuse_number: int | None = Field(
        default=None, ge=1, description="Take a number freed by a cancelled document (see released-numbers)"
    )


class PurchaseLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    variant_id: uuid.UUID | None
    sku: str | None
    name_en: str | None
    name_ar: str | None
    account_id: uuid.UUID | None
    unit: str | None
    qty: Decimal
    qty_base: Decimal
    base_unit: str | None
    unit_price: Decimal | None
    net_amount: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    line_total: Decimal
    source_line_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    batch_no: str | None
    expiry_date: date | None
    description: str | None
    cleared_value: Decimal | None = Field(
        description="Bills: GRNI cleared; returns: the cost the goods left at"
    )


class PurchaseDocSummary(BaseModel):
    id: uuid.UUID
    doc_type: PurchaseDocType
    status: DocumentStatus
    number: str | None
    doc_date: date
    business_id: uuid.UUID
    branch_id: uuid.UUID
    party_id: uuid.UUID | None
    warehouse_id: uuid.UUID | None
    supplier_ref: str | None
    due_date: date | None
    reference: str | None
    narration: str | None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    posted_at: datetime | None


class PurchaseDocOut(PurchaseDocSummary):
    lines: list[PurchaseLineOut]
    progress: str | None = Field(
        default=None,
        description="Orders: open, partly_received or received; receipts: open, partly_billed, billed",
    )


class OpenLineOut(BaseModel):
    """What is still to do on a posted line: to receive (an order line) or to bill (a receipt line)."""

    line_id: uuid.UUID
    variant_id: uuid.UUID | None
    sku: str | None
    name_en: str | None
    name_ar: str | None
    unit: str | None
    factor_to_base: Decimal
    qty_open_base: Decimal
    qty_open: Decimal = Field(
        description="In the line's own unit (when it divides exactly, else the base unit)"
    )
    unit_price: Decimal | None
    vat_rate: Decimal
    value_open: Decimal | None = Field(default=None, description="Receipt lines: GRNI still to clear")
