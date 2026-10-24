"""Cancel and Reverse for every posted document (posting-rules.md rules 32 and 33; decision A7).

Neither changes a posted row (CLAUDE.md rule 3). Both write new rows that take the document's effect out of
the books, the stock ledger and bill-wise outstanding:

- **Cancel** (its period open): a mirror of its entries and a ``cancel`` row per stock move, dated on its own
  date; its number is freed for the next document of the series; status ``cancelled``; its full contents kept
  in the audit log.
- **Reverse**: a reversal document with its own number (a debit note for a purchase bill, else one of the same
  type), dated on the chosen day, holding the mirror entries and ``reversal`` stock rows; status ``reversed``.

A document that later documents follow (an order received, a receipt billed) needs those undone first.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import object_mapper

from app.core import audit, numbering
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.numbering import NotNumberOwnerError
from app.modules.accounting import service as accounting
from app.modules.accounting.models import EntryKind
from app.modules.documents.models import Document, DocumentLine, DocumentStatus
from app.modules.documents.schemas import CorrectedOut, LinkedDocOut, ReleasedNumberOut
from app.modules.inventory import service as inventory
from app.modules.inventory.models import MoveType
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Business

# The reversal of these is a document of another type; every other type is reversed by one of its own type.
REVERSED_BY = {"purchase_bill": "debit_note", "sales_invoice": "credit_note"}
# Commitments only: nothing in the books or the stock to take back, so they are cancelled, never reversed.
NO_EFFECT = frozenset({"quotation", "sales_order", "proforma_invoice", "purchase_order"})


class DocumentNotFoundError(NotFoundError):
    code = "document_not_found"
    title = "Document not found"


class NotPostedError(ConflictError):
    code = "document_not_posted"
    title = "Only a posted document can be cancelled or reversed"


class IsReversalError(ConflictError):
    code = "document_is_a_reversal"
    title = "A reversal cannot itself be cancelled or reversed"


class HasDependantsError(ConflictError):
    code = "document_has_dependants"
    title = "Later documents follow this one: cancel or reverse them first"


class CancelPeriodLockedError(ConflictError):
    code = "cancel_period_locked"
    title = "This document's period is locked: reverse it instead"


class CancelOnlyError(ConflictError):
    code = "cancel_only"
    title = "This document has nothing in the books to reverse: cancel it instead"


class InvalidReversalError(UnprocessableError):
    code = "invalid_reversal"
    title = "This reversal cannot be made"


async def _locked(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = (
        await session.scalars(
            select(Document)
            .where(Document.id == doc_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if doc is None:
        raise DocumentNotFoundError(document_id=str(doc_id))
    return doc


async def get_document(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = await session.get(Document, doc_id)
    if doc is None:
        raise DocumentNotFoundError(document_id=str(doc_id))
    return doc


async def _check(session: AsyncSession, doc: Document) -> None:
    if doc.status != DocumentStatus.POSTED:
        raise NotPostedError(document_status=doc.status)
    if doc.reversal_of_id is not None:
        raise IsReversalError()
    following = DocumentLine.source_line_id.in_(
        select(DocumentLine.id).where(DocumentLine.document_id == doc.id)
    )
    numbers = (
        await session.scalars(
            select(Document.number)
            .join(DocumentLine, DocumentLine.document_id == Document.id)
            .where(following, Document.status == DocumentStatus.POSTED.value)
            .distinct()
            .order_by(Document.number)
        )
    ).all()
    if numbers:
        raise HasDependantsError(
            f"Undo {', '.join(n or '' for n in numbers)} first.", documents=list(numbers)
        )


def _row(obj: object) -> dict[str, Any]:
    columns = object_mapper(obj).column_attrs
    return {c.key: audit.jsonable(getattr(obj, c.key)) for c in columns}


async def _snapshot(session: AsyncSession, doc: Document) -> dict[str, Any]:
    """Everything the cancelled version said: header, lines and the entry it posted."""
    lines = (
        await session.scalars(
            select(DocumentLine).where(DocumentLine.document_id == doc.id).order_by(DocumentLine.line_no)
        )
    ).all()
    journal = await accounting.posting_lines(session, doc.id)
    return {"document": _row(doc), "lines": [_row(x) for x in lines], "journal": [_row(x) for x in journal]}


async def _undo(
    session: AsyncSession,
    business: Business,
    doc: Document,
    *,
    kind: MoveType,
    day: date,
    period_id: uuid.UUID,
    number: str | None,
    source_document_id: uuid.UUID,
    narration: str,
) -> None:
    """The rows that take ``doc``'s effect out: stock first (it serialises the business), then the books."""
    ctx = None
    stock_lines = []
    if await inventory.has_moves(session, doc.id):
        ctx = await inventory.begin_posting(session, business, day, source_document_id)
        stock_lines = await inventory.undo_moves(session, ctx, doc.id, kind)
    await accounting.undo_journal(
        session,
        business=business,
        document_id=doc.id,
        branch_id=doc.branch_id,
        day=day,
        period_id=period_id,
        number=number,
        narration=narration,
        source_document_id=source_document_id,
    )
    if stock_lines:
        await accounting.write_journal(
            session,
            business=business,
            branch_id=doc.branch_id,
            day=day,
            period_id=period_id,
            number=number,
            narration=narration,
            source_document_id=source_document_id,
            lines=stock_lines,
            kind=EntryKind.UNDO,
        )
    if ctx is not None:
        await inventory.finish_posting(
            session,
            ctx,
            business,
            branch_id=doc.branch_id,
            document_id=source_document_id,
            number=number or "",
            narration=f"Recost after {narration[0].lower()}{narration[1:]}",
        )


async def cancel(
    session: AsyncSession, doc_id: uuid.UUID, *, user_id: uuid.UUID | None, reason: str
) -> Document:
    """Rule 32. Refused when the period is locked (then reverse)."""
    doc = await _locked(session, doc_id)
    await _check(session, doc)
    business = await tenancy.get_business(session, doc.business_id)
    snapshot = await _snapshot(session, doc)
    if doc.doc_type not in NO_EFFECT:
        if not await accounting.is_open(session, business, doc.doc_date):
            raise CancelPeriodLockedError(date=doc.doc_date.isoformat())
        _year, period = await accounting.posting_period(session, business, doc.doc_date)
        await _undo(
            session,
            business,
            doc,
            kind=MoveType.CANCEL,
            day=doc.doc_date,
            period_id=period.id,
            number=doc.number,
            source_document_id=doc.id,
            narration=f"Cancel of {doc.number}",
        )
    assert doc.series_id is not None
    assert doc.number_seq is not None
    await numbering.release(
        session,
        tenant_id=doc.tenant_id,
        series_id=doc.series_id,
        number=doc.number_seq,
        cancelled_document_id=doc.id,
    )
    doc.status = DocumentStatus.CANCELLED.value
    doc.cancelled_at = datetime.now(UTC)
    doc.cancelled_by = user_id
    doc.cancel_reason = reason
    await session.flush()
    await audit.record(session, doc, "cancel", reason=reason, snapshot=snapshot)
    return doc


async def reverse(
    session: AsyncSession,
    doc_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    reason: str,
    day: date | None,
    may_override_lock: bool,
    override_reason: str | None,
) -> Document:
    """Rule 33: a reversal document dated ``day`` (default today, in the business's time zone)."""
    doc = await _locked(session, doc_id)
    if doc.doc_type in NO_EFFECT:
        raise CancelOnlyError()
    await _check(session, doc)
    business = await tenancy.get_business(session, doc.business_id)
    day = day or datetime.now(ZoneInfo(business.time_zone)).date()
    if day < doc.doc_date:
        raise InvalidReversalError("A reversal cannot be dated before the document.", field="date")
    year, period = await accounting.posting_period(
        session,
        business,
        day,
        may_override=may_override_lock,
        override_reason=override_reason,
        document=f"reversal of {doc.number}",
    )
    if node_id is None:
        raise NotNumberOwnerError()
    reversal = Document(
        tenant_id=doc.tenant_id,
        doc_type=REVERSED_BY.get(doc.doc_type, doc.doc_type),
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        doc_date=day,
        reference=doc.number,
        narration=f"Reversal of {doc.number}: {reason}",
        party_id=doc.party_id,
        account_id=doc.account_id,
        currency=doc.currency,
        total=doc.total,
        subtotal=doc.subtotal,
        tax_total=doc.tax_total,
        warehouse_id=doc.warehouse_id,
        to_warehouse_id=doc.to_warehouse_id,
        supplier_ref=doc.supplier_ref,
        reversal_of_id=doc.id,
    )
    session.add(reversal)
    await session.flush()
    number = await numbering.allocate(
        session,
        tenant_id=doc.tenant_id,
        branch_id=doc.branch_id,
        doc_type=reversal.doc_type,
        fiscal_year=year.code,
        node_id=node_id,
        document_id=reversal.id,
    )
    await _undo(
        session,
        business,
        doc,
        kind=MoveType.REVERSAL,
        day=day,
        period_id=period.id,
        number=number.formatted,
        source_document_id=reversal.id,
        narration=f"Reversal of {doc.number}",
    )
    now = datetime.now(UTC)
    reversal.status = DocumentStatus.POSTED.value
    reversal.series_id = number.series_id
    reversal.number_seq = number.number
    reversal.number = number.formatted
    reversal.posted_at = now
    reversal.posted_by = user_id
    doc.status = DocumentStatus.REVERSED.value
    doc.reversed_by_document_id = reversal.id
    await session.flush()
    await audit.record(session, reversal, "post", reason=reason, changes={"number": [None, number.formatted]})
    await audit.record(
        session,
        doc,
        "reverse",
        reason=reason,
        changes={
            "status": [DocumentStatus.POSTED.value, doc.status],
            "reversed_by": [None, number.formatted],
        },
    )
    return doc


async def _linked(session: AsyncSession, doc_id: uuid.UUID | None) -> Document | None:
    return await session.get(Document, doc_id) if doc_id else None


def _link(doc: Document | None) -> LinkedDocOut | None:
    if doc is None:
        return None
    return LinkedDocOut(id=doc.id, doc_type=doc.doc_type, number=doc.number, doc_date=doc.doc_date)


async def corrected_out(session: AsyncSession, doc: Document) -> CorrectedOut:
    reversal = await _linked(session, doc.reversed_by_document_id)
    return CorrectedOut(
        id=doc.id,
        doc_type=doc.doc_type,
        status=DocumentStatus(doc.status),
        number=doc.number,
        doc_date=doc.doc_date,
        cancel_reason=doc.cancel_reason,
        cancelled_at=doc.cancelled_at,
        reversal_id=reversal.id if reversal else None,
        reversal_doc_type=reversal.doc_type if reversal else None,
        reversal_number=reversal.number if reversal else None,
        reversal_date=reversal.doc_date if reversal else None,
        reversal_of=_link(await _linked(session, doc.reversal_of_id)),
        reissue_of=_link(await _linked(session, doc.reissue_of_id)),
    )


async def released_numbers(
    session: AsyncSession, branch_id: uuid.UUID, doc_type: str
) -> list[ReleasedNumberOut]:
    return [
        ReleasedNumberOut(
            number=o.number,
            formatted=o.formatted,
            fiscal_year=o.fiscal_year,
            cancelled_document_id=o.cancelled_document_id,
        )
        for o in await numbering.offered_for(session, branch_id, doc_type)
    ]
