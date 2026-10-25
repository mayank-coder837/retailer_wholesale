"""Purchasing API shapes (M1.7, VAT M1.10). Quantities are typed in one of the product's units; prices are
per unit typed, in the document's price mode; money and quantities are exact Decimal."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.modules.documents.models import DocumentStatus
from app.modules.tax.codes import NoVatReason, PriceMode
from app.modules.tax.fields import PurchaseTaxCode

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
        default=None,
        description="Per unit typed, in the document's price mode (default: the line it follows)",
    )
    tax_code: PurchaseTaxCode | None = Field(
        default=None,
        description="SR, SR-B, ZR, EX, OS, RC-S or RC-G; default: the line it follows, the supplier's, the "
        "item's, else SR",
    )
    vat_rate: Decimal | None = Field(
        default=None, description="Deprecated (before tax codes): 5 means SR, 0 means OS"
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
    price_mode: PriceMode = Field(
        default=PriceMode.VAT_EXCLUSIVE,
        description="How prices are typed: VAT-exclusive, VAT-inclusive, or no VAT (with the reason)",
    )
    no_vat_reason: NoVatReason | None = Field(
        default=None, description="With no_vat: zero_rated (ZR), exempt (EX) or out_of_scope (OS)"
    )
    lines: list[PurchaseLineIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _reason(self) -> "PurchaseDocIn":
        if (self.price_mode is PriceMode.NO_VAT) != (self.no_vat_reason is not None):
            raise ValueError("no_vat needs its reason, and only no_vat has one")
        return self


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
    tax_code: str | None
    vat_rate: Decimal
    vat_amount: Decimal = Field(
        description="Reverse charge: the VAT self-assessed (not owed to the supplier)"
    )
    line_total: Decimal = Field(description="What the line adds to the bill")
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
    price_mode: PriceMode | None
    no_vat_reason: NoVatReason | None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal = Field(description="What the supplier is owed (reverse-charge VAT is not)")
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
    price_mode: PriceMode | None = Field(default=None, description="The mode that price was typed in")
    tax_code: str | None = None
    vat_rate: Decimal
    value_open: Decimal | None = Field(default=None, description="Receipt lines: GRNI still to clear")
