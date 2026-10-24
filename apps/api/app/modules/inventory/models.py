"""Inventory tables (domain-model.md → Inventory; docs/milestones/m1.6-inventory.md).

``stock_move`` is the goods ledger: insert-only (migration 0010), and every quantity and value is derived from
it (CLAUDE.md rule 5). ``average_cost`` and ``stock_balance`` are caches updated in the same transaction and
rebuildable from the moves.
"""

import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped


class MoveType(StrEnum):
    RECEIPT = "receipt"
    ISSUE = "issue"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    ITEM_TRANSFER_IN = "item_transfer_in"
    ITEM_TRANSFER_OUT = "item_transfer_out"
    ADJUSTMENT_IN = "adjustment_in"
    ADJUSTMENT_OUT = "adjustment_out"
    RETURN_IN = "return_in"
    RETURN_OUT = "return_out"
    OPENING = "opening"
    NEGATIVE_REVALUATION = "negative_revaluation"  # value only: rule 27
    RECOST = "recost"  # value only: rule 28
    CANCEL = "cancel"  # undoes a move of a cancelled document, on its own date (rule 32)
    REVERSAL = "reversal"  # undoes a move of a reversed document, on the reversal's date (rule 33)


INBOUND = frozenset(
    {
        MoveType.RECEIPT,
        MoveType.TRANSFER_IN,
        MoveType.ITEM_TRANSFER_IN,
        MoveType.ADJUSTMENT_IN,
        MoveType.RETURN_IN,
        MoveType.OPENING,
    }
)
OUTBOUND = frozenset(
    {
        MoveType.ISSUE,
        MoveType.TRANSFER_OUT,
        MoveType.ITEM_TRANSFER_OUT,
        MoveType.ADJUSTMENT_OUT,
        MoveType.RETURN_OUT,
    }
)
VALUE_ONLY = frozenset({MoveType.NEGATIVE_REVALUATION, MoveType.RECOST})
UNDO = frozenset({MoveType.CANCEL, MoveType.REVERSAL})  # copies of an earlier move with the signs flipped


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


def _tenant_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["tenant_id", column], [f"{table}.tenant_id", f"{table}.id"])


def _business_fk(column: str, table: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "business_id", column], [f"{table}.tenant_id", f"{table}.business_id", f"{table}.id"]
    )


class Batch(TenantScoped, Base):
    __tablename__ = "batch"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("variant_id", "batch_no"),
        _tenant_fk("variant_id", "variant"),
        CheckConstraint("mfg_date IS NULL OR expiry_date IS NULL OR mfg_date <= expiry_date", name="dates"),
    )

    variant_id: Mapped[uuid.UUID]
    batch_no: Mapped[str] = mapped_column(String(60))
    mfg_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)


class StockMove(TenantScoped, Base):
    """One movement of goods (or of value only). Insert-only."""

    __tablename__ = "stock_move"
    __audit__ = False  # written only by posting; the posted document carries the audit entry
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("business_id", "seq"),
        _tenant_fk("business_id", "business"),
        _business_fk("warehouse_id", "warehouse"),
        _tenant_fk("variant_id", "variant"),
        _tenant_fk("batch_id", "batch"),
        _business_fk("document_id", "document"),
        _tenant_fk("document_line_id", "document_line"),
        _business_fk("counter_account_id", "account"),
        _tenant_fk("adjusts_move_id", "stock_move"),
        CheckConstraint(_in("move_type", MoveType), name="move_type"),
        CheckConstraint(
            "move_type NOT IN ('negative_revaluation', 'recost') OR qty_base = 0",
            name="value_only_has_no_quantity",
        ),
        CheckConstraint(
            "move_type IN ('negative_revaluation', 'recost', 'cancel', 'reversal') OR qty_base <> 0",
            name="quantity_moves_move_quantity",
        ),
        CheckConstraint(
            "(move_type IN ('cancel', 'reversal')) = (reversal_of_id IS NOT NULL)", name="undo_names_its_move"
        ),
        _tenant_fk("reversal_of_id", "stock_move"),
        Index("uq_stock_move_undone_once", "reversal_of_id", unique=True),
        CheckConstraint(
            "move_type NOT IN ('negative_revaluation', 'recost') OR adjusts_move_id IS NOT NULL",
            name="value_only_adjusts_a_move",
        ),
        Index("ix_stock_move_variant", "business_id", "variant_id", "move_date", "seq"),
        Index("ix_stock_move_document", "document_id"),
    )

    business_id: Mapped[uuid.UUID]
    warehouse_id: Mapped[uuid.UUID]
    variant_id: Mapped[uuid.UUID]
    batch_id: Mapped[uuid.UUID | None]
    move_date: Mapped[date] = mapped_column(Date)
    seq: Mapped[int] = mapped_column(BigInteger)
    move_type: Mapped[str] = mapped_column(String(30))
    qty_base: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    value: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    document_id: Mapped[uuid.UUID | None]
    document_line_id: Mapped[uuid.UUID | None]
    counter_account_id: Mapped[uuid.UUID | None]  # where the other side of the value posted (recost uses it)
    adjusts_move_id: Mapped[uuid.UUID | None]  # value-only moves: the quantity move they belong to
    reversal_of_id: Mapped[uuid.UUID | None]  # cancel / reversal rows: the move they undo


class AverageCost(TenantScoped, Base):
    """The cost pool of one variant in one business (cache of its moves)."""

    __tablename__ = "average_cost"
    __audit__ = False
    __table_args__ = (
        UniqueConstraint("business_id", "variant_id"),
        _tenant_fk("business_id", "business"),
        _tenant_fk("variant_id", "variant"),
    )

    business_id: Mapped[uuid.UUID]
    variant_id: Mapped[uuid.UUID]
    qty_base: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    value: Mapped[Decimal] = mapped_column(Numeric(19, 4))
    avg_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    last_purchase_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    last_move_date: Mapped[date | None] = mapped_column(Date)


class StockBalance(TenantScoped, Base):
    """Quantity and value per warehouse, variant and batch (cache of the moves)."""

    __tablename__ = "stock_balance"
    __audit__ = False
    __table_args__ = (
        _tenant_fk("business_id", "business"),
        _business_fk("warehouse_id", "warehouse"),
        _tenant_fk("variant_id", "variant"),
        _tenant_fk("batch_id", "batch"),
        Index(
            "uq_stock_balance",
            "warehouse_id",
            "variant_id",
            "batch_id",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_stock_balance_variant", "business_id", "variant_id"),
    )

    business_id: Mapped[uuid.UUID]
    warehouse_id: Mapped[uuid.UUID]
    variant_id: Mapped[uuid.UUID]
    batch_id: Mapped[uuid.UUID | None]
    qty_base: Mapped[Decimal] = mapped_column(Numeric(19, 6))
    value: Mapped[Decimal] = mapped_column(Numeric(19, 4))
