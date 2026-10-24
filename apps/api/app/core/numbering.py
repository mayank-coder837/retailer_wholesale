"""Gap-free document numbering (decisions A7, A12; ADR 0010).

``allocate`` must run inside the posting transaction: it locks the series row, so concurrent posts queue and a
rolled-back post returns its number. Only the series' owner node allocates, so local and cloud never clash.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.ids import new_id
from app.core.platform_models import NumberingSeries, ReleasedNumber

PREFIXES: dict[str, str] = {
    "quotation": "QT",
    "sales_order": "SO",
    "delivery_note": "DN",
    "proforma_invoice": "PI",
    "sales_invoice": "INV",
    "sales_return": "SR",
    "purchase_order": "PO",
    "goods_receipt": "GRN",
    "purchase_bill": "PB",
    "purchase_return": "PR",
    "receipt": "RV",
    "payment": "PV",
    "contra": "CV",
    "journal": "JV",
    "expense": "EV",
    "credit_note": "CN",
    "debit_note": "DBN",
    "stock_adjustment": "SA",
    "stock_transfer": "ST",
    "item_transfer": "IT",
}


class NotNumberOwnerError(ConflictError):
    code = "not_number_owner"
    title = "Only the branch's main PC can number this document"


class NumberNotAvailableError(ConflictError):
    code = "number_not_available"
    title = "That number is not free to reuse"


class UnknownDocumentTypeError(ConflictError):
    code = "unknown_document_type"
    title = "This document type has no numbering series"


@dataclass(frozen=True, slots=True)
class Allocated:
    series_id: uuid.UUID
    number: int
    formatted: str
    reused: bool
    reissue_of: uuid.UUID | None = None  # reused: the cancelled document that held the number


def format_number(series: NumberingSeries, number: int) -> str:
    return f"{series.prefix}-{series.fiscal_year}-{number:0{series.padding}d}"


async def _lock_series(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    branch_id: uuid.UUID,
    doc_type: str,
    fiscal_year: str,
    node_id: uuid.UUID,
) -> NumberingSeries:
    if doc_type not in PREFIXES:
        raise UnknownDocumentTypeError(doc_type=doc_type)
    await session.execute(
        insert(NumberingSeries)
        .values(
            id=new_id(),
            tenant_id=tenant_id,
            branch_id=branch_id,
            doc_type=doc_type,
            fiscal_year=fiscal_year,
            prefix=PREFIXES[doc_type],
            owner_node_id=node_id,
        )
        .on_conflict_do_nothing(index_elements=["branch_id", "doc_type", "fiscal_year"])
    )
    series = (
        await session.scalars(
            select(NumberingSeries)
            .where(
                NumberingSeries.branch_id == branch_id,
                NumberingSeries.doc_type == doc_type,
                NumberingSeries.fiscal_year == fiscal_year,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).one()
    if series.owner_node_id != node_id:
        raise NotNumberOwnerError()
    return series


async def allocate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    branch_id: uuid.UUID,
    doc_type: str,
    fiscal_year: str,
    node_id: uuid.UUID,
    document_id: uuid.UUID,
    reuse: int | None = None,
) -> Allocated:
    """Next number of the series, or a released number the user chose to reuse (``reuse``)."""
    series = await _lock_series(
        session,
        tenant_id=tenant_id,
        branch_id=branch_id,
        doc_type=doc_type,
        fiscal_year=fiscal_year,
        node_id=node_id,
    )
    if reuse is not None:
        released = (
            await session.scalars(
                select(ReleasedNumber)
                .where(
                    ReleasedNumber.series_id == series.id,
                    ReleasedNumber.number == reuse,
                    ReleasedNumber.reused_by_document_id.is_(None),
                )
                .with_for_update()
            )
        ).first()
        if released is None:
            raise NumberNotAvailableError(number=reuse)
        released.reused_by_document_id = document_id
        released.reused_at = datetime.now(UTC)
        await session.flush()
        return Allocated(
            series.id,
            reuse,
            format_number(series, reuse),
            reused=True,
            reissue_of=released.cancelled_document_id,
        )

    number = series.next_number
    series.next_number = number + 1
    await session.flush()
    return Allocated(series.id, number, format_number(series, number), reused=False)


async def release(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    series_id: uuid.UUID,
    number: int,
    cancelled_document_id: uuid.UUID,
) -> ReleasedNumber:
    """Frees a cancelled document's number (decision A7). Unreused, it stays visible as CANCELLED: no gap."""
    row = ReleasedNumber(
        id=new_id(),
        tenant_id=tenant_id,
        series_id=series_id,
        number=number,
        cancelled_document_id=cancelled_document_id,
    )
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True, slots=True)
class Offered:
    number: int
    formatted: str
    fiscal_year: str
    cancelled_document_id: uuid.UUID


async def offered_for(session: AsyncSession, branch_id: uuid.UUID, doc_type: str) -> list[Offered]:
    """Freed numbers of a branch's series of one document type (every fiscal year), oldest first."""
    stmt = (
        select(NumberingSeries, ReleasedNumber)
        .join(ReleasedNumber, ReleasedNumber.series_id == NumberingSeries.id)
        .where(
            NumberingSeries.branch_id == branch_id,
            NumberingSeries.doc_type == doc_type,
            ReleasedNumber.reused_by_document_id.is_(None),
        )
        .order_by(NumberingSeries.fiscal_year, ReleasedNumber.number)
    )
    return [
        Offered(r.number, format_number(s, r.number), s.fiscal_year, r.cancelled_document_id)
        for s, r in (await session.execute(stmt)).all()
    ]


async def offered_numbers(session: AsyncSession, series_id: uuid.UUID) -> list[int]:
    """Freed numbers the next document in the series may take, oldest first."""
    stmt = (
        select(ReleasedNumber.number)
        .where(ReleasedNumber.series_id == series_id, ReleasedNumber.reused_by_document_id.is_(None))
        .order_by(ReleasedNumber.number)
    )
    return list((await session.scalars(stmt)).all())
