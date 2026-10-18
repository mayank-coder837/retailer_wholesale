"""Inventory API shapes (M1.6). Quantities and costs are exact Decimal; a quantity is typed in one of the
product's units and converted to the base unit once, at entry (CLAUDE.md rule 6)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from app.modules.documents.models import DocumentStatus, LineDirection

Quantity = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=6)]
UnitCost = Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=6)]


class StockDocType(StrEnum):
    STOCK_ADJUSTMENT = "stock_adjustment"
    STOCK_TRANSFER = "stock_transfer"
    ITEM_TRANSFER = "item_transfer"


class StockLineIn(BaseModel):
    variant_id: uuid.UUID
    unit: str = Field(
        min_length=1, max_length=20, description="One of the product's units, e.g. pcs or carton"
    )
    qty: Quantity
    direction: LineDirection = Field(
        default=LineDirection.OUT,
        description="Adjustments and Item Transfers: in or out; warehouse transfers: ignored",
    )
    unit_cost: UnitCost | None = Field(
        default=None, description="Adjustments in: cost per unit typed (default: the item's current average)"
    )
    batch_id: uuid.UUID | None = None
    batch_no: str | None = Field(
        default=None,
        min_length=1,
        max_length=60,
        description="In lines of batch items: a new or existing batch",
    )
    expiry_date: date | None = None
    description: str | None = Field(default=None, max_length=500)


class StockDocIn(BaseModel):
    doc_type: StockDocType
    business_id: uuid.UUID
    branch_id: uuid.UUID
    doc_date: date
    warehouse_id: uuid.UUID = Field(description="The warehouse (a transfer: where the goods leave)")
    to_warehouse_id: uuid.UUID | None = Field(
        default=None, description="Warehouse transfers: where they arrive"
    )
    reason: str | None = Field(
        default=None, max_length=200, description="Adjustments: damaged, count, found…"
    )
    reference: str | None = Field(default=None, max_length=100)
    narration: str | None = Field(default=None, max_length=2000)
    lines: list[StockLineIn] = Field(min_length=1, max_length=500)


class PostStockIn(BaseModel):
    override_lock_reason: str | None = Field(default=None, max_length=500)


class StockLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    variant_id: uuid.UUID
    sku: str
    name_en: str
    name_ar: str
    unit: str
    qty: Decimal
    qty_base: Decimal
    base_unit: str
    direction: LineDirection
    unit_cost: Decimal | None
    batch_id: uuid.UUID | None
    batch_no: str | None
    expiry_date: date | None
    description: str | None
    value: Decimal | None = Field(description="What the line moved (posted only; signed: + in, - out)")


class StockWarning(BaseModel):
    code: str = Field(description="negative_stock or no_cost")
    variant_id: uuid.UUID
    warehouse_id: uuid.UUID
    qty_after: Decimal | None = None


class StockDocSummary(BaseModel):
    id: uuid.UUID
    doc_type: StockDocType
    status: DocumentStatus
    number: str | None
    doc_date: date
    business_id: uuid.UUID
    branch_id: uuid.UUID
    warehouse_id: uuid.UUID | None
    to_warehouse_id: uuid.UUID | None
    reason: str | None
    reference: str | None
    narration: str | None
    total: Decimal
    posted_at: datetime | None


class StockDocOut(StockDocSummary):
    lines: list[StockLineOut]
    warnings: list[StockWarning] = Field(default_factory=list, description="From the post just made")


class BalanceOut(BaseModel):
    variant_id: uuid.UUID
    sku: str
    name_en: str
    name_ar: str
    base_unit: str
    warehouse_id: uuid.UUID
    batch_id: uuid.UUID | None
    batch_no: str | None
    expiry_date: date | None
    qty: Decimal
    value: Decimal


class ItemLedgerLine(BaseModel):
    move_date: date
    move_type: str
    document_id: uuid.UUID | None
    number: str | None
    warehouse_id: uuid.UUID
    batch_no: str | None
    qty: Decimal
    unit_cost: Decimal
    value: Decimal
    qty_balance: Decimal = Field(description="Running quantity after this move (the warehouse, or all)")
    value_balance: Decimal


class ItemLedgerOut(BaseModel):
    variant_id: uuid.UUID
    opening_qty: Decimal
    opening_value: Decimal
    lines: list[ItemLedgerLine]
    closing_qty: Decimal
    closing_value: Decimal
    avg_cost: Decimal | None


class ValuationOut(BaseModel):
    business_id: uuid.UUID
    stock_value: Decimal = Field(description="Σ stock_move.value: the stock ledger")
    inventory_account: Decimal = Field(description="The inventory account's balance in the journal")
    difference: Decimal = Field(description="Always zero (CLAUDE.md rule 5)")


class BatchOut(BaseModel):
    id: uuid.UUID
    variant_id: uuid.UUID
    batch_no: str
    mfg_date: date | None
    expiry_date: date | None
    qty: Decimal = Field(description="In the warehouse asked for, or in all")
