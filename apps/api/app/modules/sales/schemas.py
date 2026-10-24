"""Sales API shapes (M1.8). Quantities are typed in one of the product's units; prices are per unit typed and
VAT-exclusive (price modes arrive with tax, M1.10); money and quantities are exact Decimal."""

import datetime as dt
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from app.modules.documents.models import DocumentStatus

Quantity = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=6)]
Price = Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=6)]


class SalesDocType(StrEnum):
    QUOTATION = "quotation"
    PROFORMA_INVOICE = "proforma_invoice"
    SALES_ORDER = "sales_order"
    DELIVERY_NOTE = "delivery_note"
    SALES_INVOICE = "sales_invoice"
    SALES_RETURN = "sales_return"


class SalesLineIn(BaseModel):
    variant_id: uuid.UUID = Field(description="The item")
    unit: str | None = Field(
        default=None, max_length=20, description="One of the item's units (default: base)"
    )
    qty: Quantity
    unit_price: Price | None = Field(
        default=None,
        description="Per unit typed, VAT-exclusive (default: the order line it follows); none on deliveries",
    )
    vat_rate: Decimal | None = Field(
        default=None, description="0 or 5 (%); default: the line it follows, else 5"
    )
    price_source: str | None = Field(
        default=None,
        max_length=80,
        description="What the pricing engine said, e.g. last_price:INV-2026-00412",
    )
    source_line_id: uuid.UUID | None = Field(
        default=None,
        description="Order: the quotation line; delivery: the order line; invoice: the delivery or order "
        "line; return: the invoice line",
    )
    batch_id: uuid.UUID | None = Field(default=None, description="Goods out of a batch-tracked item")
    description: str | None = Field(default=None, max_length=500)


class SalesDocIn(BaseModel):
    doc_type: SalesDocType
    business_id: uuid.UUID
    branch_id: uuid.UUID
    doc_date: date
    party_id: uuid.UUID = Field(description="The customer")
    warehouse_id: uuid.UUID | None = Field(
        default=None, description="Delivery notes and returns; invoices with goods not delivered before"
    )
    cash_account_id: uuid.UUID | None = Field(
        default=None, description="Invoices: a cash sale, paid into this cash or bank account (no receivable)"
    )
    valid_until: date | None = Field(default=None, description="Quotations and proformas")
    due_date: date | None = Field(
        default=None, description="Credit invoices: default the customer's credit days"
    )
    reference: str | None = Field(default=None, max_length=100, description="The customer's order number")
    narration: str | None = Field(default=None, max_length=2000)
    lines: list[SalesLineIn] = Field(min_length=1, max_length=500)


class PostSalesIn(BaseModel):
    override_lock_reason: str | None = Field(default=None, max_length=500)
    credit_override_reason: str | None = Field(
        default=None, max_length=500, description="Needed to post past a credit block (credit.override)"
    )
    reuse_number: int | None = Field(
        default=None, ge=1, description="Take a number freed by a cancelled document (see released-numbers)"
    )


class SalesLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    variant_id: uuid.UUID | None
    sku: str | None
    name_en: str | None
    name_ar: str | None
    unit: str | None
    qty: Decimal
    qty_base: Decimal
    base_unit: str | None
    unit_price: Decimal | None
    price_source: str | None
    below_min: bool
    net_amount: Decimal
    vat_rate: Decimal
    vat_amount: Decimal
    line_total: Decimal
    source_line_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    description: str | None
    cost: Decimal | None = Field(
        description="Delivery notes and invoices: the cost the goods left at; returns: what they came back at"
    )


class SalesDocSummary(BaseModel):
    id: uuid.UUID
    doc_type: SalesDocType
    status: DocumentStatus
    number: str | None
    doc_date: date
    business_id: uuid.UUID
    branch_id: uuid.UUID
    party_id: uuid.UUID | None
    warehouse_id: uuid.UUID | None
    cash_account_id: uuid.UUID | None
    valid_until: date | None
    due_date: date | None
    reference: str | None
    narration: str | None
    subtotal: Decimal
    tax_total: Decimal
    round_off: Decimal
    total: Decimal
    posted_at: datetime | None


class SalesWarningOut(BaseModel):
    code: str = Field(description="credit_warning: the customer is over the limit or overdue (policy warn)")
    reasons: list[str] = []


class SalesDocOut(SalesDocSummary):
    lines: list[SalesLineOut]
    progress: str | None = Field(
        default=None,
        description="Quotations: open, partly_ordered, ordered; orders: open, partly_delivered, delivered; "
        "delivery notes: not_invoiced, partly_invoiced, invoiced",
    )
    warnings: list[SalesWarningOut] = Field(default=[], description="From the post just made (not stored)")


class OpenSalesLineOut(BaseModel):
    """What is still to do on a posted line: to order, deliver, invoice or return."""

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
    unit_price: Decimal | None = Field(description="Delivery lines: the price of the order line they follow")
    vat_rate: Decimal


class SalesHistoryLineOut(BaseModel):
    """One item invoiced to the customer (the history panel, A9)."""

    doc_date: dt.date
    document_id: uuid.UUID
    number: str
    variant_id: uuid.UUID
    sku: str
    name_en: str
    name_ar: str
    qty: Decimal
    unit: str
    unit_price: Decimal | None
