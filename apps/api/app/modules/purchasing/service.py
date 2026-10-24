"""Purchase documents: orders, goods receipts, bills, returns (M1.7; posting rules 7 to 9; decision A4).

Each is a ``document`` + ``document_line`` draft until posted. Lines follow earlier lines
(``source_line_id``): a receipt line its order line, a bill line its receipt line, a return line its bill or
receipt line. What is still open on a line is always summed from the posted lines that follow it (never
stored), and posting locks the source documents first, so two posts can't take the same quantity twice.

Posting validates again, checks the period, takes the gap-free number, moves stock through the cost pools
(inventory), writes one journal entry (``posting.py``) and opens or settles the supplier's payable bill, all
in the request's transaction.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, numbering
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.numbering import NotNumberOwnerError
from app.modules.accounting import service as accounting
from app.modules.accounting.models import JournalLine, Role
from app.modules.accounting.posting import VAT_RATES, vat_of
from app.modules.catalog import products as catalog
from app.modules.catalog.models import ProductType
from app.modules.documents.models import Document, DocumentLine, DocumentStatus
from app.modules.inventory import service as inventory
from app.modules.inventory.models import MoveType
from app.modules.parties import service as parties
from app.modules.purchasing import posting
from app.modules.purchasing.schemas import (
    OpenLineOut,
    PurchaseDocIn,
    PurchaseDocOut,
    PurchaseDocSummary,
    PurchaseDocType,
    PurchaseLineIn,
    PurchaseLineOut,
)
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Business

ZERO = Decimal(0)
TYPES = frozenset(t.value for t in PurchaseDocType)
PRICE_PLACES = Decimal("0.000001")
# What each document's lines may follow.
SOURCES: dict[PurchaseDocType, frozenset[str]] = {
    PurchaseDocType.PURCHASE_ORDER: frozenset(),
    PurchaseDocType.GOODS_RECEIPT: frozenset({PurchaseDocType.PURCHASE_ORDER.value}),
    PurchaseDocType.PURCHASE_BILL: frozenset({PurchaseDocType.GOODS_RECEIPT.value}),
    PurchaseDocType.PURCHASE_RETURN: frozenset(
        {PurchaseDocType.PURCHASE_BILL.value, PurchaseDocType.GOODS_RECEIPT.value}
    ),
}


class PurchaseDocNotFoundError(NotFoundError):
    code = "purchase_document_not_found"
    title = "There is no such purchase document"


class InvalidPurchaseDocError(UnprocessableError):
    code = "invalid_purchase_document"
    title = "The purchase document is not valid"


class PurchaseDocNotDraftError(ConflictError):
    code = "purchase_document_not_draft"
    title = "Only a draft can change"


class OverQuantityError(ConflictError):
    code = "more_than_open"
    title = "This is more than is still open on the line it follows"


def _invalid(message: str, **where: object) -> InvalidPurchaseDocError:
    return InvalidPurchaseDocError(message, **where)


def fils(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --- what is still open ------------------------------------------------------------------------------------


async def _followed(
    session: AsyncSession, source_ids: list[uuid.UUID], doc_types: set[str]
) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    """Per source line: the base quantity and cleared value of the posted ``doc_types`` lines following it."""
    if not source_ids:
        return {}
    rows = await session.execute(
        select(
            DocumentLine.source_line_id,
            func.sum(DocumentLine.qty_base),
            func.coalesce(func.sum(DocumentLine.cleared_value), ZERO),
        )
        .join(Document, Document.id == DocumentLine.document_id)
        .where(
            DocumentLine.source_line_id.in_(source_ids),
            Document.status == DocumentStatus.POSTED.value,
            Document.doc_type.in_(doc_types),
        )
        .group_by(DocumentLine.source_line_id)
    )
    return {source: (qty, value or ZERO) for source, qty, value in rows if source is not None}


@dataclass(frozen=True, slots=True)
class Open:
    qty: Decimal  # base units still to receive / bill / return
    value: Decimal | None  # receipt lines: GRNI still to clear


async def open_on(session: AsyncSession, lines: list[tuple[DocumentLine, str]]) -> dict[uuid.UUID, Open]:
    """What is still open on posted lines, by their document type:

    - order line: ordered - received;
    - receipt line: received - billed - returned, and its GRNI not yet cleared;
    - bill line: billed - returned.
    """
    by_type: dict[str, list[DocumentLine]] = defaultdict(list)
    for line, doc_type in lines:
        by_type[doc_type].append(line)
    out: dict[uuid.UUID, Open] = {}
    orders = by_type.get(PurchaseDocType.PURCHASE_ORDER.value, [])
    received = await _followed(session, [x.id for x in orders], {PurchaseDocType.GOODS_RECEIPT.value})
    for x in orders:
        out[x.id] = Open(x.qty_base - received.get(x.id, (ZERO, ZERO))[0], None)
    receipts = by_type.get(PurchaseDocType.GOODS_RECEIPT.value, [])
    followed = await _followed(
        session,
        [x.id for x in receipts],
        {PurchaseDocType.PURCHASE_BILL.value, PurchaseDocType.PURCHASE_RETURN.value},
    )
    for x in receipts:
        qty, cleared = followed.get(x.id, (ZERO, ZERO))
        out[x.id] = Open(x.qty_base - qty, (x.net_amount or ZERO) - cleared)
    bills = by_type.get(PurchaseDocType.PURCHASE_BILL.value, [])
    returned = await _followed(session, [x.id for x in bills], {PurchaseDocType.PURCHASE_RETURN.value})
    for x in bills:
        out[x.id] = Open(x.qty_base - returned.get(x.id, (ZERO, ZERO))[0], None)
    return out


# --- validation --------------------------------------------------------------------------------------------


@dataclass(slots=True)
class CheckedLine:
    raw: PurchaseLineIn
    item: catalog.StockItem | None
    unit: catalog.StockUnit | None
    qty_base: Decimal
    factor: Decimal
    unit_price: Decimal
    vat_rate: Decimal
    net: Decimal
    vat: Decimal
    source: DocumentLine | None
    source_type: str | None
    batch_id: uuid.UUID | None
    account_id: uuid.UUID | None

    @property
    def stocked(self) -> bool:
        return self.item is not None and self.item.product_type == ProductType.STOCK


@dataclass(slots=True)
class Checked:
    business: Business
    lines: list[CheckedLine]

    @property
    def subtotal(self) -> Decimal:
        return sum((c.net for c in self.lines), ZERO)

    @property
    def tax_total(self) -> Decimal:
        return sum((c.vat for c in self.lines), ZERO)


async def _source(
    session: AsyncSession, data: PurchaseDocIn, line_id: uuid.UUID, where: str, *, lock: bool
) -> tuple[DocumentLine, str]:
    """The posted line a line follows, of the same supplier and business."""
    source = await session.get(DocumentLine, line_id)
    stmt = select(Document).where(Document.id == (source.document_id if source else None))
    if lock:
        stmt = stmt.with_for_update()
    doc = (await session.scalars(stmt.execution_options(populate_existing=True))).first() if source else None
    if source is None or doc is None or doc.doc_type not in SOURCES[data.doc_type]:
        raise _invalid("There is no such line to follow.", field=f"{where}.source_line_id")
    if (
        doc.status != DocumentStatus.POSTED
        or doc.business_id != data.business_id
        or doc.party_id != data.party_id
    ):
        raise _invalid("Follow a posted document of the same supplier.", field=f"{where}.source_line_id")
    return source, doc.doc_type


async def _check(session: AsyncSession, data: PurchaseDocIn, *, lock_sources: bool = False) -> Checked:
    kind = data.doc_type
    business = await tenancy.get_business(session, data.business_id)
    branch = await tenancy.get_branch(session, data.branch_id)
    if branch.business_id != business.id:
        raise _invalid("The branch belongs to another business.", field="branch_id")
    supplier = await parties.get_party_row(session, data.party_id)
    if not supplier.is_supplier:
        raise _invalid("Purchases are from a supplier.", field="party_id")
    if data.warehouse_id is not None:
        warehouses = {w.id for w in await tenancy.list_warehouses(session, branch.id)}
        if data.warehouse_id not in warehouses:
            raise _invalid("The warehouse belongs to another branch.", field="warehouse_id")
    if kind is not PurchaseDocType.PURCHASE_BILL and (data.supplier_ref or data.due_date):
        raise _invalid("Only a bill has the supplier's bill number and a due date.", field="supplier_ref")

    checked: list[CheckedLine] = []
    for i, raw in enumerate(data.lines):
        where = f"lines[{i}]"
        source, source_type = (None, None)
        if raw.source_line_id is not None:
            source, source_type = await _source(session, data, raw.source_line_id, where, lock=lock_sources)
        if kind is PurchaseDocType.PURCHASE_RETURN and source is None:
            raise _invalid(
                "A return names the bill or receipt line it returns.", field=f"{where}.source_line_id"
            )

        item = unit = None
        if raw.variant_id is not None:
            item = await catalog.stock_item(session, raw.variant_id)
            unit, quantity = item.quantity(raw.qty, raw.unit or item.base_unit)
            qty_base, factor = quantity.qty_base, unit.factor_to_base
        else:
            if kind is not PurchaseDocType.PURCHASE_BILL or raw.account_id is None or raw.unit:
                raise _invalid(
                    "A line names an item (expense lines: a bill's account).", field=f"{where}.variant_id"
                )
            qty_base, factor = raw.qty, Decimal(1)
        if source is not None and source.variant_id != raw.variant_id:
            raise _invalid("A line follows a line of the same item.", field=f"{where}.variant_id")

        stocked = item is not None and item.product_type == ProductType.STOCK
        if kind in (PurchaseDocType.GOODS_RECEIPT, PurchaseDocType.PURCHASE_RETURN) and not stocked:
            raise _invalid("Only stock items are received or returned.", field=f"{where}.variant_id")
        account_id = None
        if kind is PurchaseDocType.PURCHASE_BILL and not stocked:
            if raw.account_id is None:
                raise _invalid("Choose the account for this expense or service.", field=f"{where}.account_id")
            await accounting.expense_account(session, business.id, raw.account_id)
            account_id = raw.account_id
        elif raw.account_id is not None:
            raise _invalid(
                "Only expense and service lines of a bill have an account.", field=f"{where}.account_id"
            )
        needs_warehouse = kind in (PurchaseDocType.GOODS_RECEIPT, PurchaseDocType.PURCHASE_RETURN) or (
            kind is PurchaseDocType.PURCHASE_BILL and stocked and source is None
        )
        if needs_warehouse and data.warehouse_id is None:
            raise _invalid("Choose the warehouse.", field="warehouse_id")

        unit_price = raw.unit_price
        if unit_price is None and source is not None and source.unit_price is not None:
            unit_price = (source.unit_price / source.factor_to_base * factor).quantize(PRICE_PLACES)
        if unit_price is None:
            raise _invalid("Enter the price.", field=f"{where}.unit_price")
        vat_rate = raw.vat_rate if raw.vat_rate is not None else (source.vat_rate if source else None) or ZERO
        if vat_rate not in VAT_RATES:
            raise _invalid("The VAT rate is 0 or 5 percent.", field=f"{where}.vat_rate")

        # Batches: goods coming in name theirs (new or existing); returns take the batch they came in with.
        # A bill line for goods already received moves no stock, so it has no batch.
        batch_id = None
        moves_stock = kind in (PurchaseDocType.GOODS_RECEIPT, PurchaseDocType.PURCHASE_RETURN) or (
            kind is PurchaseDocType.PURCHASE_BILL and source is None
        )
        if item is not None and stocked and moves_stock:
            outgoing = kind is PurchaseDocType.PURCHASE_RETURN
            batch_hint = raw.batch_id or (source.batch_id if outgoing and source else None)
            try:
                batch_id = await inventory.check_batch(
                    session,
                    item,
                    batch_id=batch_hint,
                    batch_no=raw.batch_no,
                    expiry_date=raw.expiry_date,
                    outgoing=outgoing,
                )
            except inventory.InvalidBatchError as exc:
                raise _invalid(str(exc.detail or exc.title), field=f"{where}.batch_id") from exc

        net = fils(raw.qty * unit_price)
        vat = vat_of(net, vat_rate) if kind is not PurchaseDocType.GOODS_RECEIPT else ZERO
        checked.append(
            CheckedLine(
                raw,
                item,
                unit,
                qty_base,
                factor,
                unit_price,
                vat_rate,
                net,
                vat,
                source,
                source_type,
                batch_id,
                account_id,
            )
        )

    # Quantities may not exceed what is open on the lines they follow (several lines may follow one).
    sources = [
        (c.source, c.source_type) for c in checked if c.source is not None and c.source_type is not None
    ]
    opened = await open_on(session, [(s, t) for s, t in sources])
    wanted: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for c in checked:
        if c.source is not None:
            wanted[c.source.id] += c.qty_base
    for source_id, qty in wanted.items():
        if qty > opened[source_id].qty:
            raise OverQuantityError(line_id=str(source_id), open=str(opened[source_id].qty), asked=str(qty))
    return Checked(business, checked)


# --- drafts ------------------------------------------------------------------------------------------------


async def _write_lines(session: AsyncSession, doc: Document, checked: Checked) -> None:
    for n, c in enumerate(checked.lines, start=1):
        session.add(
            DocumentLine(
                tenant_id=doc.tenant_id,
                document_id=doc.id,
                business_id=doc.business_id,
                line_no=n,
                variant_id=c.item.variant_id if c.item else None,
                description=c.raw.description,
                qty_entered=c.raw.qty,
                unit_id=c.unit.unit_id if c.unit else None,
                factor_to_base=c.factor,
                qty_base=c.qty_base,
                unit_price=c.unit_price,
                net_amount=c.net,
                vat_rate=c.vat_rate,
                vat_amount=c.vat,
                line_total=c.net + c.vat,
                account_id=c.account_id,
                source_line_id=c.source.id if c.source else None,
                direction="out" if doc.doc_type == PurchaseDocType.PURCHASE_RETURN else "in",
                warehouse_id=doc.warehouse_id if c.stocked else None,
                batch_id=c.batch_id,
                batch_no=c.raw.batch_no if c.batch_id is None and c.stocked else None,
                expiry_date=c.raw.expiry_date if c.batch_id is None and c.stocked else None,
            )
        )
    _totals(doc, checked)
    await session.flush()


def _totals(doc: Document, checked: Checked) -> None:
    doc.subtotal = checked.subtotal
    doc.tax_total = checked.tax_total
    doc.total = checked.subtotal + checked.tax_total


def _header(doc: Document, data: PurchaseDocIn) -> None:
    doc.branch_id = data.branch_id
    doc.doc_date = data.doc_date
    doc.party_id = data.party_id
    doc.warehouse_id = data.warehouse_id
    doc.supplier_ref = data.supplier_ref
    doc.due_date = data.due_date
    doc.reference = data.reference
    doc.narration = data.narration


async def create_draft(session: AsyncSession, tenant_id: uuid.UUID, data: PurchaseDocIn) -> Document:
    checked = await _check(session, data)
    doc = Document(
        tenant_id=tenant_id,
        doc_type=data.doc_type.value,
        business_id=data.business_id,
        currency=checked.business.base_currency,
    )
    _header(doc, data)
    session.add(doc)
    await session.flush()
    await _write_lines(session, doc, checked)
    return doc


async def get_document(session: AsyncSession, doc_id: uuid.UUID, *, lock: bool = False) -> Document:
    stmt = select(Document).where(Document.id == doc_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    doc = (await session.scalars(stmt)).first()
    if doc is None or doc.doc_type not in TYPES:
        raise PurchaseDocNotFoundError()
    return doc


async def _locked_draft(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = await get_document(session, doc_id, lock=True)
    if doc.status != DocumentStatus.DRAFT:
        raise PurchaseDocNotDraftError(current=doc.status)
    return doc


async def replace_draft(session: AsyncSession, doc_id: uuid.UUID, data: PurchaseDocIn) -> Document:
    doc = await _locked_draft(session, doc_id)
    if data.doc_type.value != doc.doc_type or data.business_id != doc.business_id:
        raise _invalid("A draft keeps its document type and business.", field="doc_type")
    checked = await _check(session, data)
    await session.execute(delete(DocumentLine).where(DocumentLine.document_id == doc.id))
    _header(doc, data)
    await session.flush()
    await _write_lines(session, doc, checked)
    return doc


async def delete_draft(session: AsyncSession, doc_id: uuid.UUID) -> None:
    doc = await _locked_draft(session, doc_id)
    await session.execute(delete(DocumentLine).where(DocumentLine.document_id == doc.id))
    await session.delete(doc)
    await session.flush()


async def _lines(session: AsyncSession, doc: Document) -> list[DocumentLine]:
    stmt = select(DocumentLine).where(DocumentLine.document_id == doc.id).order_by(DocumentLine.line_no)
    return list((await session.scalars(stmt)).all())


async def _unit_code(session: AsyncSession, row: DocumentLine) -> tuple[catalog.StockItem | None, str | None]:
    if row.variant_id is None:
        return None, None
    item = await catalog.stock_item(session, row.variant_id)
    return item, next(code for code, u in item.units.items() if u.unit_id == row.unit_id)


async def _stored_input(session: AsyncSession, doc: Document) -> PurchaseDocIn:
    lines: list[PurchaseLineIn] = []
    for row in await _lines(session, doc):
        _item, unit = await _unit_code(session, row)
        lines.append(
            PurchaseLineIn(
                variant_id=row.variant_id,
                account_id=row.account_id,
                unit=unit,
                qty=row.qty_entered,
                unit_price=row.unit_price,
                vat_rate=row.vat_rate,
                source_line_id=row.source_line_id,
                batch_id=row.batch_id,
                batch_no=row.batch_no,
                expiry_date=row.expiry_date,
                description=row.description,
            )
        )
    assert doc.party_id is not None
    return PurchaseDocIn(
        doc_type=PurchaseDocType(doc.doc_type),
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        doc_date=doc.doc_date,
        party_id=doc.party_id,
        warehouse_id=doc.warehouse_id,
        supplier_ref=doc.supplier_ref,
        due_date=doc.due_date,
        reference=doc.reference,
        narration=doc.narration,
        lines=lines,
    )


# --- posting -----------------------------------------------------------------------------------------------


async def _accounts(session: AsyncSession, business_id: uuid.UUID) -> posting.Accounts:
    roles = await accounting.role_accounts(session, business_id)
    needed = (
        Role.INVENTORY,
        Role.GRNI,
        Role.VAT_INPUT,
        Role.AP_CONTROL,
        Role.PURCHASE_PRICE_VARIANCE,
        Role.STOCK_COST_ADJUSTMENT,
    )
    missing = [r.value for r in needed if r not in roles]
    if missing:
        raise _invalid(f"Give these roles an account first: {', '.join(missing)}.", roles=missing)
    return posting.Accounts(
        inventory=roles[Role.INVENTORY],
        grni=roles[Role.GRNI],
        vat_input=roles[Role.VAT_INPUT],
        ap_control=roles[Role.AP_CONTROL],
        price_variance=roles[Role.PURCHASE_PRICE_VARIANCE],
        cost_adjustment=roles[Role.STOCK_COST_ADJUSTMENT],
    )


async def post(
    session: AsyncSession,
    doc_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    may_override_lock: bool,
    override_reason: str | None,
    reuse_number: int | None = None,
) -> Document:
    doc = await _locked_draft(session, doc_id)
    data = await _stored_input(session, doc)
    checked = await _check(session, data, lock_sources=True)
    business, kind = checked.business, data.doc_type
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
    if kind is not PurchaseDocType.PURCHASE_ORDER:
        await _post_effects(session, doc, checked, business, period.id, number.formatted)

    doc.status = DocumentStatus.POSTED.value
    doc.series_id = number.series_id
    doc.number_seq = number.number
    doc.number = number.formatted
    doc.reissue_of_id = number.reissue_of
    doc.posted_at = datetime.now(UTC)
    doc.posted_by = user_id
    await session.flush()
    await audit.record(
        session, doc, "post", changes={"number": [None, number.formatted], "total": [None, str(doc.total)]}
    )
    return doc


async def _post_effects(
    session: AsyncSession,
    doc: Document,
    checked: Checked,
    business: Business,
    period_id: uuid.UUID,
    number: str,
) -> None:
    kind = PurchaseDocType(doc.doc_type)
    accounts = await _accounts(session, business.id)
    entry = posting.Entry(accounts)
    rows = await _lines(session, doc)
    sources = [(c.source, c.source_type) for c in checked.lines if c.source is not None and c.source_type]
    opened = await open_on(session, [(s, t) for s, t in sources])
    ctx = (
        await inventory.begin_posting(session, business, doc.doc_date, doc.id)
        if any(c.stocked for c in checked.lines)
        else None
    )
    taken: dict[uuid.UUID, tuple[Decimal, Decimal]] = defaultdict(lambda: (ZERO, ZERO))  # qty, value cleared

    def grni_share(source_id: uuid.UUID, qty: Decimal) -> Decimal:
        """GRNI a line clears on its receipt line, after earlier lines of this document took theirs."""
        done_qty, done_value = taken[source_id]
        still = opened[source_id]
        assert still.value is not None
        value = posting.share(still.value - done_value, still.qty - done_qty, qty)
        taken[source_id] = (done_qty + qty, done_value + value)
        return value

    for c, row in zip(checked.lines, rows, strict=True):
        if kind is PurchaseDocType.GOODS_RECEIPT:
            assert ctx is not None
            assert c.item is not None
            assert doc.warehouse_id is not None
            batch_id = await inventory.batch_for_post(session, doc.tenant_id, row)
            applied = await inventory.receive(
                session,
                ctx,
                warehouse_id=doc.warehouse_id,
                variant_id=c.item.variant_id,
                qty_base=c.qty_base,
                value=c.net,
                batch_id=batch_id,
                line_id=row.id,
                counter_account_id=accounts.grni,
            )
            posting.receipt(entry, c.net)
            entry.revaluation(applied.revaluation)
        elif kind is PurchaseDocType.PURCHASE_BILL:
            if c.source is not None:
                cleared = grni_share(c.source.id, c.qty_base)
                row.cleared_value = cleared
                posting.bill_matched(entry, c.net, c.vat, cleared)
            elif c.stocked:
                assert ctx is not None
                assert c.item is not None
                assert doc.warehouse_id is not None
                batch_id = await inventory.batch_for_post(session, doc.tenant_id, row)
                applied = await inventory.receive(
                    session,
                    ctx,
                    warehouse_id=doc.warehouse_id,
                    variant_id=c.item.variant_id,
                    qty_base=c.qty_base,
                    value=c.net,
                    batch_id=batch_id,
                    line_id=row.id,
                    counter_account_id=accounts.ap_control,
                )
                posting.bill_direct(entry, accounts.inventory, c.net, c.vat)
                entry.revaluation(applied.revaluation)
            else:
                assert c.account_id is not None
                posting.bill_direct(entry, c.account_id, c.net, c.vat)
        else:  # purchase return
            assert ctx is not None
            assert c.item is not None
            assert doc.warehouse_id is not None
            assert c.source is not None
            applied = await inventory.issue(
                session,
                ctx,
                warehouse_id=doc.warehouse_id,
                variant_id=c.item.variant_id,
                qty_base=c.qty_base,
                batch_id=c.batch_id,
                line_id=row.id,
                counter_account_id=accounts.cost_adjustment,
                move_type=MoveType.RETURN_OUT,
            )
            stock_value = -applied.value
            if c.source_type == PurchaseDocType.GOODS_RECEIPT.value:
                cleared = grni_share(c.source.id, c.qty_base)
                row.cleared_value = cleared
                posting.return_unbilled(entry, cleared, stock_value)
            else:
                row.cleared_value = stock_value
                posting.return_to_supplier(entry, c.net, c.vat, stock_value)
    await session.flush()

    lines = entry.lines(doc.party_id, doc.narration)
    journal = []
    if lines:
        journal = await accounting.write_journal(
            session,
            business=business,
            branch_id=doc.branch_id,
            day=doc.doc_date,
            period_id=period_id,
            number=number,
            narration=doc.narration or doc.supplier_ref,
            source_document_id=doc.id,
            lines=lines,
        )
    supplier_line = next(
        (j for j, x in zip(journal, lines, strict=True) if x.allocation_key is not None), None
    )
    if supplier_line is not None and kind is PurchaseDocType.PURCHASE_BILL:
        assert supplier_line.party_id is not None
        terms = await parties.terms_days(session, supplier_line.party_id, business.id, customer=False)
        due = doc.due_date or (doc.doc_date + timedelta(days=terms) if terms is not None else None)
        doc.due_date = due
        await accounting.open_party_bill(
            session,
            supplier_line,
            amount=supplier_line.credit,
            reference=doc.supplier_ref or number,
            due_date=due,
            document_id=doc.id,
        )
    elif supplier_line is not None and kind is PurchaseDocType.PURCHASE_RETURN:
        await _settle_return(session, doc, checked, supplier_line, number)
    if ctx is not None:
        await inventory.finish_posting(
            session, ctx, business, branch_id=doc.branch_id, document_id=doc.id, number=number
        )


async def _settle_return(
    session: AsyncSession, doc: Document, checked: Checked, line: JournalLine, number: str
) -> None:
    """A return settles the payables of the bills it returns first; anything over is an advance."""
    left = line.debit
    bills = {
        c.source.document_id
        for c in checked.lines
        if c.source is not None and c.source_type == PurchaseDocType.PURCHASE_BILL.value
    }
    for bill_doc in sorted(bills):
        if left <= 0:
            break
        left -= await accounting.settle_document_bills(session, bill_doc, line, left)
    if left > 0:
        await accounting.open_party_bill(
            session, line, amount=left, reference=number, due_date=None, document_id=doc.id, is_advance=True
        )


# --- reading -----------------------------------------------------------------------------------------------


def summary(doc: Document) -> PurchaseDocSummary:
    return PurchaseDocSummary(
        id=doc.id,
        doc_type=PurchaseDocType(doc.doc_type),
        status=DocumentStatus(doc.status),
        number=doc.number,
        doc_date=doc.doc_date,
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        party_id=doc.party_id,
        warehouse_id=doc.warehouse_id,
        supplier_ref=doc.supplier_ref,
        due_date=doc.due_date,
        reference=doc.reference,
        narration=doc.narration,
        subtotal=doc.subtotal or ZERO,
        tax_total=doc.tax_total or ZERO,
        total=doc.total,
        posted_at=doc.posted_at,
    )


async def _line_out(session: AsyncSession, row: DocumentLine) -> PurchaseLineOut:
    item, unit = await _unit_code(session, row)
    batch_no = await inventory.batch_no(session, row.batch_id) if row.batch_id is not None else row.batch_no
    return PurchaseLineOut(
        id=row.id,
        line_no=row.line_no,
        variant_id=row.variant_id,
        sku=item.sku if item else None,
        name_en=item.name_en if item else None,
        name_ar=item.name_ar if item else None,
        account_id=row.account_id,
        unit=unit,
        qty=row.qty_entered,
        qty_base=row.qty_base,
        base_unit=item.base_unit if item else None,
        unit_price=row.unit_price,
        net_amount=row.net_amount or ZERO,
        vat_rate=row.vat_rate or ZERO,
        vat_amount=row.vat_amount or ZERO,
        line_total=row.line_total or ZERO,
        source_line_id=row.source_line_id,
        batch_id=row.batch_id,
        batch_no=batch_no,
        expiry_date=row.expiry_date,
        description=row.description,
        cleared_value=row.cleared_value,
    )


async def get_purchase_document(session: AsyncSession, doc_id: uuid.UUID) -> PurchaseDocOut:
    doc = await get_document(session, doc_id)
    rows = await _lines(session, doc)
    progress = None
    if doc.status == DocumentStatus.POSTED and doc.doc_type in (
        PurchaseDocType.PURCHASE_ORDER.value,
        PurchaseDocType.GOODS_RECEIPT.value,
    ):
        opened = await open_on(session, [(r, doc.doc_type) for r in rows if r.variant_id is not None])
        left = sum((o.qty for o in opened.values()), ZERO)
        whole = sum((r.qty_base for r in rows if r.variant_id is not None), ZERO)
        done = "received" if doc.doc_type == PurchaseDocType.PURCHASE_ORDER.value else "billed"
        progress = "open" if left == whole else (done if left <= 0 else f"partly_{done}")
    return PurchaseDocOut(
        **summary(doc).model_dump(), lines=[await _line_out(session, r) for r in rows], progress=progress
    )


async def open_lines(session: AsyncSession, doc_id: uuid.UUID) -> list[OpenLineOut]:
    """A posted order's lines still to receive, or a receipt's still to bill: to start the next document."""
    doc = await get_document(session, doc_id)
    if doc.status != DocumentStatus.POSTED or doc.doc_type not in (
        PurchaseDocType.PURCHASE_ORDER.value,
        PurchaseDocType.GOODS_RECEIPT.value,
        PurchaseDocType.PURCHASE_BILL.value,
    ):
        return []
    rows = [r for r in await _lines(session, doc) if r.variant_id is not None]
    opened = await open_on(session, [(r, doc.doc_type) for r in rows])
    out: list[OpenLineOut] = []
    for r in rows:
        left = opened[r.id]
        if left.qty <= 0:
            continue
        item, unit = await _unit_code(session, r)
        in_unit = left.qty / r.factor_to_base
        exact = in_unit == in_unit.quantize(PRICE_PLACES)
        out.append(
            OpenLineOut(
                line_id=r.id,
                variant_id=r.variant_id,
                sku=item.sku if item else None,
                name_en=item.name_en if item else None,
                name_ar=item.name_ar if item else None,
                unit=unit if exact else (item.base_unit if item else None),
                factor_to_base=r.factor_to_base if exact else Decimal(1),
                qty_open_base=left.qty,
                qty_open=in_unit.quantize(PRICE_PLACES) if exact else left.qty,
                unit_price=r.unit_price,
                vat_rate=r.vat_rate or ZERO,
                value_open=left.value,
            )
        )
    return out


async def list_purchase_documents(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    doc_types: list[PurchaseDocType],
    status: DocumentStatus | None = None,
    party_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[PurchaseDocSummary]:
    if not doc_types:
        return []
    stmt = select(Document).where(
        Document.business_id == business_id, Document.doc_type.in_([t.value for t in doc_types])
    )
    if status is not None:
        stmt = stmt.where(Document.status == status.value)
    if party_id is not None:
        stmt = stmt.where(Document.party_id == party_id)
    stmt = stmt.order_by(Document.doc_date.desc(), Document.created_at.desc()).limit(limit).offset(offset)
    return [summary(d) for d in (await session.scalars(stmt)).all()]
