"""Inventory documents: stock adjustment, warehouse transfer, Item Transfer (M1.6; posting rules 23 to 28).

Drafts are ``document`` + ``document_line`` rows. Posting validates again, checks the period, takes the next
gap-free number, creates new batches, applies every move through the cost pools (``stock.py``), recosts later
moves when the document is backdated, and writes one journal entry for what changed in value — all in the
request's transaction, so a failure leaves nothing behind, the number included.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, numbering
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.numbering import NotNumberOwnerError
from app.modules.accounting import service as accounting
from app.modules.accounting.models import Role
from app.modules.catalog import products as catalog
from app.modules.catalog.models import ProductType
from app.modules.documents.models import Document, DocumentLine, DocumentStatus, LineDirection
from app.modules.inventory import costing, stock
from app.modules.inventory.models import OUTBOUND, Batch, MoveType, StockMove
from app.modules.inventory.posting import ValueJournal, batch_by_no, ensure_batch, recost
from app.modules.inventory.schemas import (
    StockDocIn,
    StockDocOut,
    StockDocSummary,
    StockDocType,
    StockLineIn,
    StockLineOut,
    StockWarning,
)
from app.modules.platform import settings as settings_service
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Business

ZERO = Decimal(0)
STOCK_TYPES = frozenset(t.value for t in StockDocType)


class StockDocNotFoundError(NotFoundError):
    code = "stock_document_not_found"
    title = "There is no such stock document"


class InvalidStockDocError(UnprocessableError):
    code = "invalid_stock_document"
    title = "The stock document is not valid"


class StockDocNotDraftError(ConflictError):
    code = "stock_document_not_draft"
    title = "Only a draft can change"


class NegativeStockError(ConflictError):
    code = "negative_stock"
    title = "This would take stock below zero, which this business does not allow"


def _item(row: DocumentLine) -> uuid.UUID:
    """Inventory document lines always name an item (only bills have expense lines without one)."""
    assert row.variant_id is not None
    return row.variant_id


def _invalid(message: str, **where: object) -> InvalidStockDocError:
    return InvalidStockDocError(message, **where)


@dataclass(frozen=True, slots=True)
class CheckedLine:
    raw: StockLineIn
    item: catalog.StockItem
    unit: catalog.StockUnit
    qty_base: Decimal
    batch_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class Checked:
    business: Business
    lines: list[CheckedLine]


async def _check(session: AsyncSession, data: StockDocIn) -> Checked:
    business = await tenancy.get_business(session, data.business_id)
    branch = await tenancy.get_branch(session, data.branch_id)
    if branch.business_id != business.id:
        raise _invalid("The branch belongs to another business.", field="branch_id")
    warehouses = {
        w.id: w
        for b in await tenancy.list_branches(session, business.id)
        for w in await tenancy.list_warehouses(session, b.id)
    }
    source = warehouses.get(data.warehouse_id)
    if source is None or source.branch_id != branch.id:
        raise _invalid("The warehouse belongs to another branch.", field="warehouse_id")
    if data.doc_type is StockDocType.STOCK_TRANSFER:
        if data.to_warehouse_id is None or data.to_warehouse_id not in warehouses:
            raise _invalid("Choose the warehouse the goods go to.", field="to_warehouse_id")
        if data.to_warehouse_id == data.warehouse_id:
            raise _invalid("A transfer goes to another warehouse.", field="to_warehouse_id")
    elif data.to_warehouse_id is not None:
        raise _invalid("Only a warehouse transfer has a second warehouse.", field="to_warehouse_id")

    lines: list[CheckedLine] = []
    for i, raw in enumerate(data.lines):
        where = f"lines[{i}]"
        item = await catalog.stock_item(session, raw.variant_id)
        if item.product_type != ProductType.STOCK:
            raise _invalid(f"{item.sku} is not kept in stock.", field=f"{where}.variant_id")
        unit, quantity = item.quantity(raw.qty, raw.unit)
        direction = raw.direction if data.doc_type is not StockDocType.STOCK_TRANSFER else LineDirection.OUT
        if raw.unit_cost is not None and not (
            data.doc_type is StockDocType.STOCK_ADJUSTMENT and direction is LineDirection.IN
        ):
            raise _invalid("Only goods coming in on an adjustment have a cost.", field=f"{where}.unit_cost")
        batch_id = await _check_batch(session, item, raw, direction, where)
        lines.append(
            CheckedLine(
                raw.model_copy(update={"direction": direction}), item, unit, quantity.qty_base, batch_id
            )
        )

    if data.doc_type is StockDocType.ITEM_TRANSFER:
        outs = {c.item.variant_id for c in lines if c.raw.direction is LineDirection.OUT}
        ins = {c.item.variant_id for c in lines if c.raw.direction is LineDirection.IN}
        if not outs or not ins:
            raise _invalid("An Item Transfer moves stock out of one item and into another.", field="lines")
        if outs & ins:
            raise _invalid("An item cannot be both the source and the target.", field="lines")
    return Checked(business, lines)


async def _check_batch(
    session: AsyncSession, item: catalog.StockItem, raw: StockLineIn, direction: LineDirection, where: str
) -> uuid.UUID | None:
    if not item.track_batch:
        if raw.batch_id or raw.batch_no or raw.expiry_date:
            raise _invalid(f"{item.sku} is not tracked by batch.", field=f"{where}.batch_id")
        return None
    if raw.batch_id is not None:
        batch = await session.get(Batch, raw.batch_id)
        if batch is None or batch.variant_id != item.variant_id:
            raise _invalid("There is no such batch of this item.", field=f"{where}.batch_id")
        return batch.id
    if direction is LineDirection.OUT or not raw.batch_no:
        raise _invalid(f"Choose the batch of {item.sku}.", field=f"{where}.batch_id")
    existing = await batch_by_no(session, item.variant_id, raw.batch_no)
    if existing is not None:
        return existing.id
    if item.track_expiry and raw.expiry_date is None:
        raise _invalid(f"A new batch of {item.sku} needs its expiry date.", field=f"{where}.expiry_date")
    return None  # created when the document is posted


# --- drafts ------------------------------------------------------------------------------------------------


async def _write_lines(session: AsyncSession, doc: Document, checked: Checked) -> None:
    for n, c in enumerate(checked.lines, start=1):
        session.add(
            DocumentLine(
                tenant_id=doc.tenant_id,
                document_id=doc.id,
                business_id=doc.business_id,
                line_no=n,
                variant_id=c.item.variant_id,
                description=c.raw.description,
                qty_entered=c.raw.qty,
                unit_id=c.unit.unit_id,
                factor_to_base=c.unit.factor_to_base,
                qty_base=c.qty_base,
                unit_cost=c.raw.unit_cost,
                direction=c.raw.direction.value,
                warehouse_id=doc.warehouse_id,
                batch_id=c.batch_id,
                batch_no=c.raw.batch_no if c.batch_id is None else None,
                expiry_date=c.raw.expiry_date if c.batch_id is None else None,
            )
        )
    await session.flush()


async def create_draft(session: AsyncSession, tenant_id: uuid.UUID, data: StockDocIn) -> Document:
    checked = await _check(session, data)
    doc = Document(
        tenant_id=tenant_id,
        doc_type=data.doc_type.value,
        business_id=data.business_id,
        branch_id=data.branch_id,
        doc_date=data.doc_date,
        reference=data.reference,
        narration=data.narration,
        currency=checked.business.base_currency,
        warehouse_id=data.warehouse_id,
        to_warehouse_id=data.to_warehouse_id,
        reason=data.reason,
    )
    session.add(doc)
    await session.flush()
    await _write_lines(session, doc, checked)
    return doc


async def get_document(session: AsyncSession, doc_id: uuid.UUID, *, lock: bool = False) -> Document:
    stmt = select(Document).where(Document.id == doc_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    doc = (await session.scalars(stmt)).first()
    if doc is None or doc.doc_type not in STOCK_TYPES:
        raise StockDocNotFoundError()
    return doc


async def _locked_draft(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = await get_document(session, doc_id, lock=True)
    if doc.status != DocumentStatus.DRAFT:
        raise StockDocNotDraftError(current=doc.status)
    return doc


async def replace_draft(session: AsyncSession, doc_id: uuid.UUID, data: StockDocIn) -> Document:
    doc = await _locked_draft(session, doc_id)
    if data.doc_type.value != doc.doc_type or data.business_id != doc.business_id:
        raise _invalid("A draft keeps its document type and business.", field="doc_type")
    checked = await _check(session, data)
    await session.execute(delete(DocumentLine).where(DocumentLine.document_id == doc.id))
    doc.branch_id = data.branch_id
    doc.doc_date = data.doc_date
    doc.reference = data.reference
    doc.narration = data.narration
    doc.warehouse_id = data.warehouse_id
    doc.to_warehouse_id = data.to_warehouse_id
    doc.reason = data.reason
    await session.flush()
    await _write_lines(session, doc, checked)
    return doc


async def delete_draft(session: AsyncSession, doc_id: uuid.UUID) -> None:
    doc = await _locked_draft(session, doc_id)
    await session.execute(delete(DocumentLine).where(DocumentLine.document_id == doc.id))
    await session.delete(doc)
    await session.flush()


async def _lines(session: AsyncSession, doc: Document) -> list[DocumentLine]:
    return list(
        (
            await session.scalars(
                select(DocumentLine).where(DocumentLine.document_id == doc.id).order_by(DocumentLine.line_no)
            )
        ).all()
    )


async def _stored_input(session: AsyncSession, doc: Document) -> StockDocIn:
    lines: list[StockLineIn] = []
    for row in await _lines(session, doc):
        item = await catalog.stock_item(session, _item(row))
        unit = next(code for code, u in item.units.items() if u.unit_id == row.unit_id)
        lines.append(
            StockLineIn(
                variant_id=_item(row),
                unit=unit,
                qty=row.qty_entered,
                direction=LineDirection(row.direction),
                unit_cost=row.unit_cost,
                batch_id=row.batch_id,
                batch_no=row.batch_no,
                expiry_date=row.expiry_date,
                description=row.description,
            )
        )
    assert doc.warehouse_id is not None
    return StockDocIn(
        doc_type=StockDocType(doc.doc_type),
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        doc_date=doc.doc_date,
        warehouse_id=doc.warehouse_id,
        to_warehouse_id=doc.to_warehouse_id,
        reason=doc.reason,
        reference=doc.reference,
        narration=doc.narration,
        lines=lines,
    )


# --- posting -----------------------------------------------------------------------------------------------


async def post(
    session: AsyncSession,
    doc_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    may_override_lock: bool,
    override_reason: str | None,
    reuse_number: int | None = None,
) -> tuple[Document, list[StockWarning]]:
    doc = await _locked_draft(session, doc_id)
    data = await _stored_input(session, doc)
    checked = await _check(session, data)
    business = checked.business
    year, period = await accounting.posting_period(
        session,
        business,
        doc.doc_date,
        may_override=may_override_lock,
        override_reason=override_reason,
        document=f"{doc.doc_type} {doc.id}",
    )
    if node_id is None:
        raise NotNumberOwnerError()
    number = await numbering.allocate(
        session,
        tenant_id=doc.tenant_id,
        branch_id=doc.branch_id,
        doc_type=doc.doc_type,
        fiscal_year=year.code,
        node_id=node_id,
        document_id=doc.id,
        reuse=reuse_number,
    )
    await stock.lock_business(session, business.id)
    roles = await accounting.role_accounts(session, business.id)
    for role in (Role.INVENTORY, Role.STOCK_ADJUSTMENT, Role.STOCK_COST_ADJUSTMENT):
        if role not in roles:
            raise _invalid(f"Give the role {role.value} an account first.", role=role.value)
    journal = ValueJournal(roles[Role.INVENTORY])
    ctx = stock.Context(doc.tenant_id, business.id, doc.doc_date, doc.id, roles[Role.STOCK_COST_ADJUSTMENT])
    settings = await settings_service.effective(session, business_id=business.id, branch_id=doc.branch_id)
    warn = bool(settings["inventory.negative_stock_warning"].value)
    warnings: list[StockWarning] = []
    rows = await _lines(session, doc)
    assert doc.warehouse_id is not None
    warehouse = doc.warehouse_id

    async def run(spec: stock.Spec) -> stock.Applied:
        applied = await stock.apply(session, ctx, spec)
        journal.add(applied.value, spec.counter_account_id)
        journal.add(applied.revaluation, roles[Role.STOCK_COST_ADJUSTMENT])
        if spec.move_type in OUTBOUND:
            left = await stock.warehouse_qty(session, spec.warehouse_id, spec.variant_id, spec.batch_id)
            if left < 0 and not business.negative_stock_allowed:
                raise NegativeStockError(variant_id=str(spec.variant_id), qty_after=str(left))
            if left < 0 and warn:
                warnings.append(
                    StockWarning(
                        code="negative_stock",
                        variant_id=spec.variant_id,
                        warehouse_id=spec.warehouse_id,
                        qty_after=left,
                    )
                )
            if applied.no_cost:
                warnings.append(
                    StockWarning(code="no_cost", variant_id=spec.variant_id, warehouse_id=spec.warehouse_id)
                )
        return applied

    total = ZERO
    kind = StockDocType(doc.doc_type)
    if kind is StockDocType.STOCK_ADJUSTMENT:
        for row in rows:
            batch_id = await ensure_batch(session, doc.tenant_id, row)
            if row.direction == LineDirection.IN:
                value = costing.fils(row.qty_entered * row.unit_cost) if row.unit_cost is not None else None
                spec = stock.Spec(
                    MoveType.ADJUSTMENT_IN,
                    warehouse,
                    _item(row),
                    row.qty_base,
                    batch_id,
                    value=value,
                    counter_account_id=roles[Role.STOCK_ADJUSTMENT],
                    document_line_id=row.id,
                )
            else:
                spec = stock.Spec(
                    MoveType.ADJUSTMENT_OUT,
                    warehouse,
                    _item(row),
                    row.qty_base,
                    batch_id,
                    counter_account_id=roles[Role.STOCK_ADJUSTMENT],
                    document_line_id=row.id,
                )
            total += abs((await run(spec)).value)
    elif kind is StockDocType.STOCK_TRANSFER:
        assert doc.to_warehouse_id is not None
        for row in rows:
            out = await run(
                stock.Spec(
                    MoveType.TRANSFER_OUT,
                    warehouse,
                    _item(row),
                    row.qty_base,
                    row.batch_id,
                    document_line_id=row.id,
                )
            )
            await run(
                stock.Spec(
                    MoveType.TRANSFER_IN,
                    doc.to_warehouse_id,
                    _item(row),
                    row.qty_base,
                    row.batch_id,
                    value=-out.value,
                    document_line_id=row.id,
                )
            )
            total += -out.value
    else:
        moved = ZERO
        for row in (r for r in rows if r.direction == LineDirection.OUT):
            out = await run(
                stock.Spec(
                    MoveType.ITEM_TRANSFER_OUT,
                    warehouse,
                    _item(row),
                    row.qty_base,
                    row.batch_id,
                    document_line_id=row.id,
                )
            )
            moved += -out.value
        targets = [r for r in rows if r.direction == LineDirection.IN]
        for row, share in zip(targets, costing.split(moved, [r.qty_base for r in targets]), strict=True):
            batch_id = await ensure_batch(session, doc.tenant_id, row)
            await run(
                stock.Spec(
                    MoveType.ITEM_TRANSFER_IN,
                    warehouse,
                    _item(row),
                    row.qty_base,
                    batch_id,
                    value=share,
                    document_line_id=row.id,
                )
            )
        total = moved

    lines = journal.lines()
    if lines:
        await accounting.write_journal(
            session,
            business=business,
            branch_id=doc.branch_id,
            day=doc.doc_date,
            period_id=period.id,
            number=number.formatted,
            narration=doc.narration or doc.reason,
            source_document_id=doc.id,
            lines=lines,
        )
    if ctx.backdated:
        await recost(session, ctx, business, doc.branch_id, doc.id, number.formatted, roles)

    doc.status = DocumentStatus.POSTED.value
    doc.series_id = number.series_id
    doc.number_seq = number.number
    doc.number = number.formatted
    doc.reissue_of_id = number.reissue_of
    doc.total = total
    doc.posted_at = datetime.now(UTC)
    doc.posted_by = user_id
    await session.flush()
    await audit.record(
        session, doc, "post", changes={"number": [None, number.formatted], "total": [None, str(total)]}
    )
    return doc, warnings


# --- reading -----------------------------------------------------------------------------------------------


def summary(doc: Document) -> StockDocSummary:
    return StockDocSummary(
        id=doc.id,
        doc_type=StockDocType(doc.doc_type),
        status=DocumentStatus(doc.status),
        number=doc.number,
        doc_date=doc.doc_date,
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        warehouse_id=doc.warehouse_id,
        to_warehouse_id=doc.to_warehouse_id,
        reason=doc.reason,
        reference=doc.reference,
        narration=doc.narration,
        total=doc.total,
        posted_at=doc.posted_at,
    )


async def get_stock_document(
    session: AsyncSession, doc_id: uuid.UUID, warnings: list[StockWarning] | None = None
) -> StockDocOut:
    doc = await get_document(session, doc_id)
    moved: dict[uuid.UUID, Decimal] = {}
    for line_id, move_type, value in await session.execute(
        select(StockMove.document_line_id, StockMove.move_type, StockMove.value).where(
            StockMove.document_id == doc.id
        )
    ):
        if line_id is not None and move_type != MoveType.TRANSFER_IN.value:
            moved[line_id] = moved.get(line_id, ZERO) + value
    batches: dict[uuid.UUID, str] = {}
    out: list[StockLineOut] = []
    for row in await _lines(session, doc):
        item = await catalog.stock_item(session, _item(row))
        unit = next(code for code, u in item.units.items() if u.unit_id == row.unit_id)
        if row.batch_id is not None and row.batch_id not in batches:
            batch = await session.get(Batch, row.batch_id)
            batches[row.batch_id] = batch.batch_no if batch else ""
        out.append(
            StockLineOut(
                id=row.id,
                line_no=row.line_no,
                variant_id=_item(row),
                sku=item.sku,
                name_en=item.name_en,
                name_ar=item.name_ar,
                unit=unit,
                qty=row.qty_entered,
                qty_base=row.qty_base,
                base_unit=item.base_unit,
                direction=LineDirection(row.direction),
                unit_cost=row.unit_cost,
                batch_id=row.batch_id,
                batch_no=batches.get(row.batch_id) if row.batch_id else row.batch_no,
                expiry_date=row.expiry_date,
                description=row.description,
                value=moved.get(row.id) if doc.status != DocumentStatus.DRAFT else None,
            )
        )
    return StockDocOut(**summary(doc).model_dump(), lines=out, warnings=warnings or [])


async def list_stock_documents(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    doc_types: list[StockDocType],
    status: DocumentStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[StockDocSummary]:
    if not doc_types:
        return []
    stmt = select(Document).where(
        Document.business_id == business_id, Document.doc_type.in_([t.value for t in doc_types])
    )
    if status is not None:
        stmt = stmt.where(Document.status == status.value)
    stmt = stmt.order_by(Document.doc_date.desc(), Document.created_at.desc()).limit(limit).offset(offset)
    return [summary(d) for d in (await session.scalars(stmt)).all()]
