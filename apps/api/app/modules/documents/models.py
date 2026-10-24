"""The shared document header (domain-model.md → Documents, ADR 0009).

Every document and voucher is one ``document`` row with a ``doc_type``. M1.5 adds the columns vouchers need;
sales and purchasing add theirs (price mode, due date, totals…) in their milestones.

A posted document is permanent (CLAUDE.md rule 3): a trigger (migration 0009) refuses to delete it or change
anything but its move to ``cancelled`` / ``reversed`` and the columns that record that move.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped
from app.core.numbering import PREFIXES


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    POSTED = "posted"
    CANCELLED = "cancelled"
    REVERSED = "reversed"


def _in(column: str, values: type[StrEnum] | tuple[str, ...]) -> str:
    items = [v.value for v in values] if isinstance(values, type) else list(values)
    return f"{column} IN ({', '.join(repr(v) for v in items)})"


def _tenant_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", column], [f"{table}.tenant_id", f"{table}.id"])


class Document(TenantScoped, Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "business_id", "id"),
        _tenant_fk("business_id", "business"),
        # The branch must belong to the document's business.
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "branch_id"], ["branch.tenant_id", "branch.business_id", "branch.id"]
        ),
        _tenant_fk("series_id", "numbering_series"),
        _tenant_fk("party_id", "party"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "account_id"],
            ["account.tenant_id", "account.business_id", "account.id"],
        ),
        _tenant_fk("reversal_of_id", "document"),
        _tenant_fk("reissue_of_id", "document"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "warehouse_id"],
            ["warehouse.tenant_id", "warehouse.business_id", "warehouse.id"],
        ),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "to_warehouse_id"],
            ["warehouse.tenant_id", "warehouse.business_id", "warehouse.id"],
        ),
        CheckConstraint(_in("doc_type", tuple(PREFIXES)), name="doc_type"),
        CheckConstraint(_in("status", DocumentStatus), name="status"),
        CheckConstraint("(status = 'draft') = (number IS NULL)", name="numbered_when_posted"),
        CheckConstraint("(number IS NULL) = (series_id IS NULL AND number_seq IS NULL)", name="number_parts"),
        CheckConstraint("status = 'draft' OR posted_at IS NOT NULL", name="posted_at"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("total >= 0", name="total_not_negative"),
        # A cancelled document keeps its number; the replacement that reuses it is the only other holder.
        Index(
            "uq_document_number",
            "series_id",
            "number_seq",
            unique=True,
            postgresql_where=text("status <> 'cancelled'"),
        ),
        Index("ix_document_list", "business_id", "doc_type", "doc_date"),
        Index("ix_document_party", "party_id", "doc_date"),
    )

    doc_type: Mapped[str] = mapped_column(String(30))
    business_id: Mapped[uuid.UUID]
    branch_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(String(20), server_default=DocumentStatus.DRAFT.value)
    series_id: Mapped[uuid.UUID | None]
    number_seq: Mapped[int | None] = mapped_column(BigInteger)
    number: Mapped[str | None] = mapped_column(String(40))
    doc_date: Mapped[date] = mapped_column(Date)
    reference: Mapped[str | None] = mapped_column(String(100))
    narration: Mapped[str | None] = mapped_column(Text)
    party_id: Mapped[uuid.UUID | None]
    account_id: Mapped[uuid.UUID | None]  # a voucher's cash or bank account; a cash sale's
    currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    total: Mapped[Decimal] = mapped_column(Numeric(19, 4), server_default=text("0"))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    posted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    reversal_of_id: Mapped[uuid.UUID | None]
    reversed_by_document_id: Mapped[uuid.UUID | None]
    reissue_of_id: Mapped[uuid.UUID | None]
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warehouse_id: Mapped[uuid.UUID | None]  # inventory documents: the warehouse (transfers: from)
    to_warehouse_id: Mapped[uuid.UUID | None]  # warehouse transfers: to
    reason: Mapped[str | None] = mapped_column(String(200))  # stock adjustments: damaged, count, found…
    due_date: Mapped[date | None] = mapped_column(Date)  # bills: when the payable falls due
    supplier_ref: Mapped[str | None] = mapped_column(String(60))  # the supplier's own bill number
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))  # net of VAT
    tax_total: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))  # VAT: the sum of the lines' VAT (A3)
    valid_until: Mapped[date | None] = mapped_column(Date)  # quotations and proformas


class LineDirection(StrEnum):
    IN = "in"
    OUT = "out"


class DocumentLine(TenantScoped, Base):
    """An item line of a document (ADR 0009).

    Quantities keep their unit (CLAUDE.md rule 6): converted once, at entry, by the UOM engine. Sales and
    purchasing add prices and tax in their milestones. Lines of a posted document never change (0010).
    """

    __tablename__ = "document_line"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("document_id", "line_no"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "document_id"],
            ["document.tenant_id", "document.business_id", "document.id"],
        ),
        _tenant_fk("variant_id", "variant"),
        _tenant_fk("unit_id", "unit"),
        _tenant_fk("batch_id", "batch"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "warehouse_id"],
            ["warehouse.tenant_id", "warehouse.business_id", "warehouse.id"],
        ),
        CheckConstraint(
            "qty_entered > 0 AND qty_base > 0 AND factor_to_base > 0", name="positive_quantities"
        ),
        CheckConstraint("unit_cost IS NULL OR unit_cost >= 0", name="cost_not_negative"),
        CheckConstraint(_in("direction", LineDirection), name="direction"),
        ForeignKeyConstraint(
            ["tenant_id", "business_id", "account_id"],
            ["account.tenant_id", "account.business_id", "account.id"],
        ),
        _tenant_fk("source_line_id", "document_line"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="price_not_negative"),
        CheckConstraint("variant_id IS NOT NULL OR account_id IS NOT NULL", name="item_or_account"),
        CheckConstraint("(variant_id IS NULL) = (unit_id IS NULL)", name="item_has_unit"),
        CheckConstraint("vat_rate IS NULL OR (vat_rate >= 0 AND vat_rate < 100)", name="vat_rate_range"),
        Index(
            "ix_document_line_source", "source_line_id", postgresql_where=text("source_line_id IS NOT NULL")
        ),
    )

    document_id: Mapped[uuid.UUID]
    business_id: Mapped[uuid.UUID]
    line_no: Mapped[int] = mapped_column(Integer)
    variant_id: Mapped[uuid.UUID | None]  # empty on expense lines (a bill's rent, freight…)
    description: Mapped[str | None] = mapped_column(String(500))
    qty_entered: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    unit_id: Mapped[uuid.UUID | None]
    factor_to_base: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    qty_base: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))  # per base unit; adjustments in
    direction: Mapped[str] = mapped_column(String(3), server_default=LineDirection.IN.value)
    warehouse_id: Mapped[uuid.UUID | None]
    batch_id: Mapped[uuid.UUID | None]
    batch_no: Mapped[str | None] = mapped_column(String(60))  # a new batch, created at post
    expiry_date: Mapped[date | None] = mapped_column(Date)
    # Purchasing and sales (M1.7, M1.8): prices per unit entered, VAT per line (A3).
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    vat_rate: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    account_id: Mapped[uuid.UUID | None]  # expense and service lines: the account debited
    source_line_id: Mapped[uuid.UUID | None]  # the order / receipt / bill line this line follows
    price_source: Mapped[str | None] = mapped_column(String(80))  # sales: where the price came from (A9)
    below_min: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))  # let through below min
    cleared_value: Mapped[Decimal | None] = mapped_column(
        Numeric(19, 4)
    )  # set at post: GRNI cleared, cost returned
