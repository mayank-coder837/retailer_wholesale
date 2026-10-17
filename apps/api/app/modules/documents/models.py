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
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
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
    account_id: Mapped[uuid.UUID | None]  # a voucher's cash or bank account
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
