"""Sales documents: quotations, proformas, orders, delivery notes, invoices, returns (M1.8; posting rules 1
to 4
and 34; decisions A8, A9).

Each is a ``document`` + ``document_line`` draft until posted (a quotation is numbered when saved: it is what
the
customer sees). Lines follow earlier lines (``source_line_id``): an order line its quotation line, a delivery
line
its order line, an invoice line its delivery or order line, a return line its invoice line. What is still
open on
a line is always summed from the posted lines that follow it (never stored), and posting locks the source
documents first, so two posts can't take the same quantity twice.

Posting validates again, checks the period, the minimum price and the customer's credit, takes the gap-free
number, moves stock through the cost pools, writes one journal entry (``posting.py``) and opens or settles the
customer's receivable bill, all in the request's transaction.
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
from app.core.money import RoundOffMode, round_off
from app.core.numbering import NotNumberOwnerError
from app.modules.accounting import service as accounting
from app.modules.accounting.models import JournalLine, Role
from app.modules.accounting.posting import VAT_RATES
from app.modules.catalog import products as catalog
from app.modules.catalog.models import ProductType
from app.modules.documents.models import Document, DocumentLine, DocumentStatus
from app.modules.inventory import service as inventory
from app.modules.inventory.models import MoveType
from app.modules.parties import service as parties
from app.modules.parties.credit import CreditDecision
from app.modules.parties.models import Party
from app.modules.parties.schemas import CreditCheckIn, CreditCheckOut
from app.modules.platform import settings as settings_service
from app.modules.pricing import service as pricing
from app.modules.pricing.engine import LastSale
from app.modules.sales import posting
from app.modules.sales.schemas import (
    OpenSalesLineOut,
    SalesDocIn,
    SalesDocOut,
    SalesDocSummary,
    SalesDocType,
    SalesHistoryLineOut,
    SalesLineIn,
    SalesLineOut,
    SalesWarningOut,
)
from app.modules.tax import codes as tax
from app.modules.tax.codes import PriceMode
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Business

ZERO = Decimal(0)
TYPES = frozenset(t.value for t in SalesDocType)
PRICE_PLACES = Decimal("0.000001")
DEFAULT_VAT = Decimal(5)
# What each document's lines may follow.
SOURCES: dict[SalesDocType, frozenset[str]] = {
    SalesDocType.QUOTATION: frozenset(),
    SalesDocType.PROFORMA_INVOICE: frozenset(),
    SalesDocType.SALES_ORDER: frozenset({SalesDocType.QUOTATION.value}),
    SalesDocType.DELIVERY_NOTE: frozenset({SalesDocType.SALES_ORDER.value}),
    SalesDocType.SALES_INVOICE: frozenset({SalesDocType.SALES_ORDER.value, SalesDocType.DELIVERY_NOTE.value}),
    SalesDocType.SALES_RETURN: frozenset({SalesDocType.SALES_INVOICE.value}),
}
# The documents that take quantity from a line of each type (the reverse of SOURCES).
FOLLOWERS: dict[str, frozenset[str]] = {
    SalesDocType.QUOTATION.value: frozenset({SalesDocType.SALES_ORDER.value}),
    SalesDocType.SALES_ORDER.value: frozenset(
        {SalesDocType.DELIVERY_NOTE.value, SalesDocType.SALES_INVOICE.value}
    ),
    SalesDocType.DELIVERY_NOTE.value: frozenset({SalesDocType.SALES_INVOICE.value}),
    SalesDocType.SALES_INVOICE.value: frozenset({SalesDocType.SALES_RETURN.value}),
}
# Commitments: nothing in the books or the stock.
NO_EFFECT = frozenset({SalesDocType.QUOTATION, SalesDocType.PROFORMA_INVOICE, SalesDocType.SALES_ORDER})
PRICED = frozenset(SalesDocType) - {SalesDocType.DELIVERY_NOTE}


class SalesDocNotFoundError(NotFoundError):
    code = "sales_document_not_found"
    title = "There is no such sales document"


class InvalidSalesDocError(UnprocessableError):
    code = "invalid_sales_document"
    title = "The sales document is not valid"


class SalesDocNotDraftError(ConflictError):
    code = "sales_document_not_draft"
    title = "Only a draft can change"


class OverQuantityError(ConflictError):
    code = "more_than_open"
    title = "This is more than is still open on the line it follows"


class BelowMinimumPriceError(ConflictError):
    code = "below_minimum_price"
    title = "A price is below the item's minimum price"


class CreditBlockedError(ConflictError):
    code = "credit_blocked"
    title = "The customer is over their credit limit or has overdue bills"


def _invalid(message: str, **where: object) -> InvalidSalesDocError:
    return InvalidSalesDocError(message, **where)


def fils(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --- what is still open ------------------------------------------------------------------------------------


async def _followed(
    session: AsyncSession, source_ids: list[uuid.UUID], doc_types: frozenset[str]
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
    qty: Decimal  # base units still to order / deliver / invoice / return
    value: Decimal | None  # delivery lines: cost still in GDNI; invoice lines: cost still to come back


async def open_on(session: AsyncSession, lines: list[tuple[DocumentLine, str]]) -> dict[uuid.UUID, Open]:
    """What is still open on posted lines, by their document type:

    - quotation line: quoted - ordered; order line: ordered - delivered - invoiced directly;
    - delivery line: delivered - invoiced, and its cost still in GDNI (from the stock ledger, recosts
    included);
    - invoice line: invoiced - returned, and its cost still to come back on a return.
    """
    by_type: dict[str, list[DocumentLine]] = defaultdict(list)
    for line, doc_type in lines:
        by_type[doc_type].append(line)
    costs = await inventory.line_costs(
        session,
        [
            x.id
            for t in (SalesDocType.DELIVERY_NOTE.value, SalesDocType.SALES_INVOICE.value)
            for x in by_type.get(t, [])
        ],
    )
    out: dict[uuid.UUID, Open] = {}
    for doc_type, rows in by_type.items():
        followers = FOLLOWERS.get(doc_type)
        if not followers:
            continue
        followed = await _followed(session, [x.id for x in rows], followers)
        for x in rows:
            qty, cleared = followed.get(x.id, (ZERO, ZERO))
            value = None
            if doc_type == SalesDocType.DELIVERY_NOTE.value:
                value = costs.get(x.id, ZERO) - cleared
            elif doc_type == SalesDocType.SALES_INVOICE.value:
                value = costs.get(x.id, x.cleared_value or ZERO) - cleared
            out[x.id] = Open(x.qty_base - qty, value)
    return out


# --- validation --------------------------------------------------------------------------------------------


@dataclass(slots=True)
class CheckedLine:
    raw: SalesLineIn
    item: catalog.StockItem
    unit: catalog.StockUnit
    qty_base: Decimal
    factor: Decimal
    unit_price: Decimal | None  # delivery notes: none
    tax_code: str | None  # delivery notes: none
    vat_rate: Decimal | None
    net: Decimal
    vat: Decimal
    source: DocumentLine | None
    source_type: str | None
    batch_id: uuid.UUID | None
    moves_stock: bool
    below_min: bool = False

    @property
    def stocked(self) -> bool:
        return self.item.product_type == ProductType.STOCK


@dataclass(slots=True)
class Checked:
    business: Business
    lines: list[CheckedLine]
    mode: PriceMode | None = None  # priced documents

    @property
    def subtotal(self) -> Decimal:
        return sum((c.net for c in self.lines), ZERO)

    @property
    def tax_total(self) -> Decimal:
        return sum((c.vat for c in self.lines), ZERO)


async def _source(
    session: AsyncSession, data: SalesDocIn, line_id: uuid.UUID, where: str, *, lock: bool
) -> tuple[DocumentLine, str]:
    """The posted line a line follows, of the same customer and business."""
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
        raise _invalid("Follow a posted document of the same customer.", field=f"{where}.source_line_id")
    return source, doc.doc_type


async def _priced_line(session: AsyncSession, source: DocumentLine, source_type: str) -> DocumentLine | None:
    """The line whose price a line takes: the source itself, or (a delivery line) the order line it
    follows."""
    if source_type != SalesDocType.DELIVERY_NOTE.value:
        return source
    return await session.get(DocumentLine, source.source_line_id) if source.source_line_id else None


async def _check(session: AsyncSession, data: SalesDocIn, *, lock_sources: bool = False) -> Checked:
    kind = data.doc_type
    business = await tenancy.get_business(session, data.business_id)
    branch = await tenancy.get_branch(session, data.branch_id)
    if branch.business_id != business.id:
        raise _invalid("The branch belongs to another business.", field="branch_id")
    customer = await parties.get_party_row(session, data.party_id)
    if not customer.is_customer:
        raise _invalid("Sales are to a customer.", field="party_id")
    if data.warehouse_id is not None:
        warehouses = {w.id for w in await tenancy.list_warehouses(session, branch.id)}
        if data.warehouse_id not in warehouses:
            raise _invalid("The warehouse belongs to another branch.", field="warehouse_id")
    if data.cash_account_id is not None:
        if kind is not SalesDocType.SALES_INVOICE:
            raise _invalid("Only an invoice can be a cash sale.", field="cash_account_id")
        await accounting.cash_or_bank_account(session, business.id, data.cash_account_id)
    if data.valid_until is not None and kind not in (SalesDocType.QUOTATION, SalesDocType.PROFORMA_INVOICE):
        raise _invalid("Only a quotation or proforma has a validity date.", field="valid_until")
    if data.due_date is not None and (kind is not SalesDocType.SALES_INVOICE or data.cash_account_id):
        raise _invalid("Only an invoice on credit has a due date.", field="due_date")

    checked: list[CheckedLine] = []
    for i, raw in enumerate(data.lines):
        where = f"lines[{i}]"
        source, source_type = (None, None)
        if raw.source_line_id is not None:
            source, source_type = await _source(session, data, raw.source_line_id, where, lock=lock_sources)
        if kind is SalesDocType.SALES_RETURN and source is None:
            raise _invalid("A return names the invoice line it returns.", field=f"{where}.source_line_id")
        item = await catalog.stock_item(session, raw.variant_id)
        unit, quantity = item.quantity(raw.qty, raw.unit or item.base_unit)
        if source is not None and source.variant_id != raw.variant_id:
            raise _invalid("A line follows a line of the same item.", field=f"{where}.variant_id")
        stocked = item.product_type == ProductType.STOCK
        if kind in (SalesDocType.DELIVERY_NOTE, SalesDocType.SALES_RETURN) and not stocked:
            raise _invalid("Only stock items are delivered or returned.", field=f"{where}.variant_id")
        moves_stock = stocked and (
            kind in (SalesDocType.DELIVERY_NOTE, SalesDocType.SALES_RETURN)
            or (kind is SalesDocType.SALES_INVOICE and source_type != SalesDocType.DELIVERY_NOTE.value)
        )
        if moves_stock and data.warehouse_id is None:
            raise _invalid("Choose the warehouse.", field="warehouse_id")

        # Prices: a delivery note has none; a return takes its invoice's; others default from what they
        # follow.
        unit_price: Decimal | None = None
        code: tax.TaxCode | None = None
        if kind is SalesDocType.DELIVERY_NOTE:
            if raw.unit_price is not None or raw.vat_rate is not None or raw.tax_code is not None:
                raise _invalid("A delivery note carries no prices.", field=f"{where}.unit_price")
        else:
            priced = await _priced_line(session, source, source_type) if source and source_type else None
            if kind is SalesDocType.SALES_RETURN and (raw.unit_price is not None or raw.tax_code is not None):
                raise _invalid("A return takes the invoice's price and VAT.", field=f"{where}.unit_price")
            unit_price = raw.unit_price
            if unit_price is None and priced is not None and priced.unit_price is not None:
                if await _mode_of(session, priced) != data.price_mode.value:
                    raise _invalid(
                        "This line takes its price from a document in another price mode; use that mode.",
                        field="price_mode",
                    )
                unit_price = (priced.unit_price / priced.factor_to_base * unit.factor_to_base).quantize(
                    PRICE_PLACES
                )
            if unit_price is None:
                raise _invalid("Enter the price.", field=f"{where}.unit_price")
            code = _line_code(data, business, customer, item, raw, priced, where)

        # Batches: goods leaving name their batch; returned goods come back into the batch they left from.
        batch_id = None
        if moves_stock and item.track_batch:
            incoming = kind is SalesDocType.SALES_RETURN
            hint = raw.batch_id
            if incoming and hint is None and source is not None:
                hint = source.batch_id
                if hint is None and source.source_line_id is not None:  # invoiced after a delivery note
                    delivered = await session.get(DocumentLine, source.source_line_id)
                    hint = delivered.batch_id if delivered else None
            try:
                batch_id = await inventory.check_batch(
                    session, item, batch_id=hint, batch_no=None, expiry_date=None, outgoing=not incoming
                )
            except inventory.InvalidBatchError as exc:
                raise _invalid(str(exc.detail or exc.title), field=f"{where}.batch_id") from exc
        elif raw.batch_id is not None:
            raise _invalid("Only goods moving now name a batch.", field=f"{where}.batch_id")

        net, vat = ZERO, ZERO
        if unit_price is not None and code is not None:
            net, vat = tax.split(fils(raw.qty * unit_price), code.rate, data.price_mode)
        checked.append(
            CheckedLine(
                raw,
                item,
                unit,
                quantity.qty_base,
                unit.factor_to_base,
                unit_price,
                code.code if code else None,
                code.rate if code else None,
                net,
                vat,
                source,
                source_type,
                batch_id,
                moves_stock,
            )
        )

    # Quantities may not exceed what is open on the lines they follow (several lines may follow one).
    sources = [
        (c.source, c.source_type) for c in checked if c.source is not None and c.source_type is not None
    ]
    opened = await open_on(session, sources)
    wanted: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    for c in checked:
        if c.source is not None:
            wanted[c.source.id] += c.qty_base
    for source_id, qty in wanted.items():
        if qty > opened[source_id].qty:
            raise OverQuantityError(line_id=str(source_id), open=str(opened[source_id].qty), asked=str(qty))
    return Checked(business, checked, None if kind is SalesDocType.DELIVERY_NOTE else data.price_mode)


async def _mode_of(session: AsyncSession, line: DocumentLine) -> str | None:
    doc = await session.get(Document, line.document_id)
    return doc.price_mode if doc else None


def _line_code(
    data: SalesDocIn,
    business: Business,
    customer: Party,
    item: catalog.StockItem,
    raw: SalesLineIn,
    priced: DocumentLine | None,
    where: str,
) -> tax.TaxCode:
    """The line's tax code (M1.10, A10): see ``tax.resolve`` for the order of defaults."""
    typed = raw.tax_code
    if typed is None and raw.vat_rate is not None:
        if raw.vat_rate not in VAT_RATES:
            raise _invalid("The VAT rate is 0 or 5 percent.", field=f"{where}.vat_rate")
        typed = tax.legacy_code(tax.Side.SALES, raw.vat_rate)
    if data.price_mode is PriceMode.NO_VAT and raw.tax_code is not None:
        assert data.no_vat_reason is not None
        if raw.tax_code != tax.NO_VAT_CODE[data.no_vat_reason]:
            raise _invalid("A document without VAT takes the code of its reason.", field=f"{where}.tax_code")
    if data.doc_type is SalesDocType.SALES_RETURN:
        typed = None  # the invoice line's code, below
    followed = priced.tax_code if priced is not None else None
    if priced is not None and followed is None and priced.vat_rate is not None:
        followed = tax.legacy_code(tax.Side.SALES, priced.vat_rate)
    resolved = tax.resolve(
        tax.Side.SALES,
        registered=business.vat_registered,
        mode=data.price_mode,
        no_vat_reason=data.no_vat_reason,
        typed=typed,
        followed=followed,
        party=customer.sales_tax_code,
        product=item.sales_tax_code,
    )
    code = tax.get(tax.Side.SALES, resolved)
    if code is None:
        raise _invalid(f"{resolved} is not a sales tax code.", field=f"{where}.tax_code")
    return code


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
                factor_to_base=c.factor,
                qty_base=c.qty_base,
                unit_price=c.unit_price,
                price_source=c.raw.price_source,
                net_amount=c.net,
                tax_code=c.tax_code,
                vat_rate=c.vat_rate,
                vat_amount=c.vat,
                line_total=c.net + c.vat,
                source_line_id=c.source.id if c.source else None,
                direction="in" if doc.doc_type == SalesDocType.SALES_RETURN else "out",
                warehouse_id=doc.warehouse_id if c.moves_stock else None,
                batch_id=c.batch_id,
            )
        )
    _totals(doc, checked)
    await session.flush()


def _totals(doc: Document, checked: Checked, round_off_adjustment: Decimal = ZERO) -> None:
    doc.price_mode = checked.mode.value if checked.mode else None
    doc.subtotal = checked.subtotal
    doc.tax_total = checked.tax_total
    doc.total = checked.subtotal + checked.tax_total + round_off_adjustment


def _header(doc: Document, data: SalesDocIn) -> None:
    doc.branch_id = data.branch_id
    doc.doc_date = data.doc_date
    doc.party_id = data.party_id
    doc.warehouse_id = data.warehouse_id
    doc.account_id = data.cash_account_id
    doc.valid_until = data.valid_until
    doc.due_date = data.due_date
    doc.reference = data.reference
    doc.narration = data.narration
    doc.no_vat_reason = data.no_vat_reason.value if data.no_vat_reason else None


async def create_draft(session: AsyncSession, tenant_id: uuid.UUID, data: SalesDocIn) -> Document:
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
        raise SalesDocNotFoundError()
    return doc


async def _locked_draft(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = await get_document(session, doc_id, lock=True)
    if doc.status != DocumentStatus.DRAFT:
        raise SalesDocNotDraftError(current=doc.status)
    return doc


async def replace_draft(session: AsyncSession, doc_id: uuid.UUID, data: SalesDocIn) -> Document:
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


async def _stored_input(session: AsyncSession, doc: Document) -> SalesDocIn:
    lines: list[SalesLineIn] = []
    for row in await _lines(session, doc):
        _item, unit = await _unit_code(session, row)
        assert row.variant_id is not None
        lines.append(
            SalesLineIn(
                variant_id=row.variant_id,
                unit=unit,
                qty=row.qty_entered,
                unit_price=None if doc.doc_type == SalesDocType.SALES_RETURN else row.unit_price,
                tax_code=None if doc.doc_type == SalesDocType.SALES_RETURN else row.tax_code,
                price_source=row.price_source,
                source_line_id=row.source_line_id,
                batch_id=row.batch_id if row.warehouse_id is not None and row.direction == "out" else None,
                description=row.description,
            )
        )
    assert doc.party_id is not None
    return SalesDocIn(
        doc_type=SalesDocType(doc.doc_type),
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        doc_date=doc.doc_date,
        party_id=doc.party_id,
        warehouse_id=doc.warehouse_id,
        cash_account_id=doc.account_id,
        valid_until=doc.valid_until,
        due_date=doc.due_date,
        reference=doc.reference,
        narration=doc.narration,
        price_mode=PriceMode(doc.price_mode or PriceMode.VAT_EXCLUSIVE.value),
        no_vat_reason=tax.NoVatReason(doc.no_vat_reason) if doc.no_vat_reason else None,
        lines=lines,
    )


# --- posting -----------------------------------------------------------------------------------------------


async def _accounts(session: AsyncSession, business_id: uuid.UUID) -> posting.Accounts:
    roles = await accounting.role_accounts(session, business_id)
    needed = (
        Role.AR_CONTROL,
        Role.SALES,
        Role.SALES_RETURNS,
        Role.VAT_OUTPUT,
        Role.COGS,
        Role.INVENTORY,
        Role.GOODS_DELIVERED_NOT_INVOICED,
        Role.STOCK_COST_ADJUSTMENT,
    )
    if missing := [r.value for r in needed if r not in roles]:
        raise _invalid(f"Give these roles an account first: {', '.join(missing)}.", roles=missing)
    return posting.Accounts(
        ar_control=roles[Role.AR_CONTROL],
        sales=roles[Role.SALES],
        sales_returns=roles[Role.SALES_RETURNS],
        vat_output=roles[Role.VAT_OUTPUT],
        cogs=roles[Role.COGS],
        inventory=roles[Role.INVENTORY],
        gdni=roles[Role.GOODS_DELIVERED_NOT_INVOICED],
        cost_adjustment=roles[Role.STOCK_COST_ADJUSTMENT],
        rounding=roles.get(Role.ROUNDING),
    )


async def _round_off(session: AsyncSession, business: Business, total: Decimal) -> Decimal:
    """Rule 34 on an invoice's total, when the business turned it on (L4: off by default)."""
    values = await settings_service.effective(session, business_id=business.id)
    mode = RoundOffMode(values["rounding.invoice_total.mode"].value)
    step = Decimal(values["rounding.invoice_total.step"].value)
    return round_off(total, mode, step, business.base_currency).adjustment


async def _check_min_prices(session: AsyncSession, checked: Checked, *, may_override: bool) -> None:
    """The minimum-price guard (M1.3): refused without ``price.override_min``; with it, the line is
    flagged."""
    for i, c in enumerate(checked.lines):
        if c.unit_price is None or c.source_type == SalesDocType.SALES_INVOICE.value:
            continue
        minimum = await pricing.min_price(session, c.item.variant_id, c.unit.code)
        price = c.unit_price  # minimum prices are VAT-exclusive: compare the net per unit
        if checked.mode is PriceMode.VAT_INCLUSIVE and c.vat_rate:
            price = (price * 100 / (100 + c.vat_rate)).quantize(PRICE_PLACES)
        if minimum is not None and price < minimum:
            if not may_override:
                raise BelowMinimumPriceError(
                    f"{c.item.sku}: {c.unit_price} is below the minimum {minimum}.",
                    line=i,
                    min_price=str(minimum),
                    price=str(price),
                )
            c.below_min = True


def _credit_amount(checked: Checked) -> Decimal:
    """What this document adds to the customer's exposure: lines that follow an order line take the part of
    that order the open-orders figure already counts."""
    amount = checked.subtotal + checked.tax_total
    for c in checked.lines:
        if c.source is not None and c.source_type == SalesDocType.SALES_ORDER.value and c.source.qty_base:
            amount -= fils((c.source.line_total or ZERO) * c.qty_base / c.source.qty_base)
    return max(amount, ZERO)


async def _check_credit(
    session: AsyncSession,
    doc: Document,
    checked: Checked,
    *,
    may_override: bool,
    override_reason: str | None,
) -> tuple[list[SalesWarningOut], CreditCheckOut | None]:
    """M1.4's check at order and credit-invoice post: warnings to show, or the figures of an override to
    record
    once the document has its number. Blocked without the permission and a reason."""
    kind = SalesDocType(doc.doc_type)
    if kind not in (SalesDocType.SALES_ORDER, SalesDocType.SALES_INVOICE) or doc.account_id is not None:
        return [], None
    assert doc.party_id is not None
    result = await parties.credit_check(
        session,
        doc.party_id,
        CreditCheckIn(business_id=doc.business_id, amount=_credit_amount(checked)),
        may_override=may_override,
    )
    reasons = [r.value for r in result.reasons]
    if result.decision is CreditDecision.WARN:
        return [SalesWarningOut(code="credit_warning", reasons=reasons)], None
    if result.decision is CreditDecision.BLOCK:
        if not may_override or not (override_reason or "").strip():
            raise CreditBlockedError(
                may_override=may_override,
                reasons=reasons,
                exposure=str(result.exposure),
                credit_limit=None if result.credit_limit is None else str(result.credit_limit),
                available=None if result.available is None else str(result.available),
            )
        return [SalesWarningOut(code="credit_overridden", reasons=reasons)], result
    return [], None


async def post(
    session: AsyncSession,
    doc_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    may_override_lock: bool,
    override_reason: str | None,
    may_override_min: bool = False,
    may_override_credit: bool = False,
    credit_override_reason: str | None = None,
    reuse_number: int | None = None,
) -> tuple[Document, list[SalesWarningOut]]:
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
    if kind in PRICED:
        await _check_min_prices(session, checked, may_override=may_override_min)
    warnings, override = await _check_credit(
        session, doc, checked, may_override=may_override_credit, override_reason=credit_override_reason
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
    rows = await _lines(session, doc)
    for c, row in zip(checked.lines, rows, strict=True):
        row.below_min = c.below_min
    if kind not in NO_EFFECT:
        await _post_effects(session, doc, checked, rows, business, period.id, number.formatted)
    if override is not None:
        assert doc.party_id is not None
        await parties.record_credit_override(
            session,
            doc.party_id,
            doc.business_id,
            document=number.formatted,
            check=override,
            reason=credit_override_reason or "",
        )

    if kind in (SalesDocType.SALES_INVOICE, SalesDocType.SALES_RETURN):
        doc.invoice_kind = await _invoice_kind(session, business, doc)
    doc.status = DocumentStatus.POSTED.value
    doc.series_id = number.series_id
    doc.number_seq = number.number
    doc.number = number.formatted
    doc.reissue_of_id = number.reissue_of
    doc.posted_at = datetime.now(UTC)
    doc.posted_by = user_id
    await session.flush()
    changes: dict[str, object] = {"number": [None, number.formatted], "total": [None, str(doc.total)]}
    if below := [c.raw.variant_id for c in checked.lines if c.below_min]:
        changes["below_min"] = [None, [str(v) for v in below]]
    await audit.record(session, doc, "post", changes=changes)
    return doc, warnings


async def _invoice_kind(session: AsyncSession, business: Business, doc: Document) -> str:
    """What the posted invoice prints as, fixed at post (M1.10; open question R7): a business that is not
    registered issues a plain invoice; a customer with a TRN gets a tax invoice; one without, a simplified tax
    invoice (setting ``vat.simplified_for_unregistered``)."""
    if not business.vat_registered:
        return "invoice"
    assert doc.party_id is not None
    if (await parties.get_party_row(session, doc.party_id)).trn:
        return "tax_invoice"
    values = await settings_service.effective(session, business_id=business.id, branch_id=doc.branch_id)
    return "simplified" if values.get("vat.simplified_for_unregistered", True) else "tax_invoice"


async def _post_effects(
    session: AsyncSession,
    doc: Document,
    checked: Checked,
    rows: list[DocumentLine],
    business: Business,
    period_id: uuid.UUID,
    number: str,
) -> None:
    kind = SalesDocType(doc.doc_type)
    accounts = await _accounts(session, business.id)
    entry = posting.Entry(accounts)
    sources = [(c.source, c.source_type) for c in checked.lines if c.source is not None and c.source_type]
    opened = await open_on(session, sources)
    ctx = (
        await inventory.begin_posting(session, business, doc.doc_date, doc.id)
        if any(c.moves_stock for c in checked.lines)
        else None
    )
    taken: dict[uuid.UUID, tuple[Decimal, Decimal]] = defaultdict(lambda: (ZERO, ZERO))  # qty, value taken

    def cost_share(source_id: uuid.UUID, qty: Decimal) -> Decimal:
        """The cost a line takes from the line it follows, after earlier lines of this document took
        theirs."""
        done_qty, done_value = taken[source_id]
        still = opened[source_id]
        value = posting.share((still.value or ZERO) - done_value, still.qty - done_qty, qty)
        taken[source_id] = (done_qty + qty, done_value + value)
        return value

    for c, row in zip(checked.lines, rows, strict=True):
        if kind is SalesDocType.SALES_RETURN:
            assert c.source is not None
            cost = cost_share(c.source.id, c.qty_base)
            assert ctx is not None
            assert doc.warehouse_id is not None
            applied = await inventory.receive(
                session,
                ctx,
                warehouse_id=doc.warehouse_id,
                variant_id=c.item.variant_id,
                qty_base=c.qty_base,
                value=cost,
                batch_id=c.batch_id,
                line_id=row.id,
                counter_account_id=accounts.cogs,
                move_type=MoveType.RETURN_IN,
            )
            entry.revaluation(applied.revaluation)
            row.cleared_value = cost
            posting.sales_return(entry, c.net, c.vat, cost)
            continue
        if kind is SalesDocType.SALES_INVOICE:
            posting.sale(entry, c.net, c.vat)
        if c.source_type == SalesDocType.DELIVERY_NOTE.value:
            assert c.source is not None
            cost = cost_share(c.source.id, c.qty_base)
            row.cleared_value = cost
            posting.cost_from_delivery(entry, cost)
        elif c.moves_stock:
            assert ctx is not None
            assert doc.warehouse_id is not None
            counter = accounts.gdni if kind is SalesDocType.DELIVERY_NOTE else accounts.cogs
            applied = await inventory.issue(
                session,
                ctx,
                warehouse_id=doc.warehouse_id,
                variant_id=c.item.variant_id,
                qty_base=c.qty_base,
                batch_id=c.batch_id,
                line_id=row.id,
                counter_account_id=counter,
                move_type=MoveType.ISSUE,
            )
            cost = -applied.value
            row.cleared_value = cost
            if kind is SalesDocType.DELIVERY_NOTE:
                posting.delivery(entry, cost)
            else:
                posting.cost_from_stock(entry, cost)

    adjustment = ZERO
    if kind is SalesDocType.SALES_INVOICE:
        adjustment = await _round_off(session, business, checked.subtotal + checked.tax_total)
        try:
            posting.round_off(entry, adjustment)
        except ValueError as exc:
            raise _invalid("Give the rounding role an account first.", roles=[Role.ROUNDING.value]) from exc
    _totals(doc, checked, adjustment)
    await session.flush()

    lines = entry.lines(doc.party_id, doc.narration, cash_account=doc.account_id)
    journal: list[JournalLine] = []
    if lines:
        journal = await accounting.write_journal(
            session,
            business=business,
            branch_id=doc.branch_id,
            day=doc.doc_date,
            period_id=period_id,
            number=number,
            narration=doc.narration or doc.reference,
            source_document_id=doc.id,
            lines=lines,
        )
    customer_line = next(
        (j for j, x in zip(journal, lines, strict=True) if x.allocation_key is not None), None
    )
    if customer_line is not None and kind is SalesDocType.SALES_INVOICE:
        assert customer_line.party_id is not None
        terms = await parties.terms_days(session, customer_line.party_id, business.id, customer=True)
        due = doc.due_date or (doc.doc_date + timedelta(days=terms) if terms is not None else None)
        doc.due_date = due
        await accounting.open_party_bill(
            session,
            customer_line,
            amount=customer_line.debit,
            reference=number,
            due_date=due,
            document_id=doc.id,
        )
    elif customer_line is not None and kind is SalesDocType.SALES_RETURN:
        await _settle_return(session, doc, checked, customer_line, number)
    if ctx is not None:
        await inventory.finish_posting(
            session, ctx, business, branch_id=doc.branch_id, document_id=doc.id, number=number
        )


async def _settle_return(
    session: AsyncSession, doc: Document, checked: Checked, line: JournalLine, number: str
) -> None:
    """A return settles the receivables of the invoices it returns first; anything over is the customer's
    advance (a credit they can use or be paid back)."""
    left = line.credit
    invoices = {c.source.document_id for c in checked.lines if c.source is not None}
    for invoice in sorted(invoices):
        if left <= 0:
            break
        left -= await accounting.settle_document_bills(session, invoice, line, left)
    if left > 0:
        await accounting.open_party_bill(
            session, line, amount=left, reference=number, due_date=None, document_id=doc.id, is_advance=True
        )


# --- reading -----------------------------------------------------------------------------------------------


def summary(doc: Document) -> SalesDocSummary:
    subtotal = doc.subtotal or ZERO
    vat = doc.tax_total or ZERO
    return SalesDocSummary(
        id=doc.id,
        doc_type=SalesDocType(doc.doc_type),
        status=DocumentStatus(doc.status),
        number=doc.number,
        doc_date=doc.doc_date,
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        party_id=doc.party_id,
        warehouse_id=doc.warehouse_id,
        cash_account_id=doc.account_id,
        valid_until=doc.valid_until,
        due_date=doc.due_date,
        reference=doc.reference,
        narration=doc.narration,
        price_mode=PriceMode(doc.price_mode) if doc.price_mode else None,
        no_vat_reason=tax.NoVatReason(doc.no_vat_reason) if doc.no_vat_reason else None,
        invoice_kind=doc.invoice_kind,
        subtotal=subtotal,
        tax_total=vat,
        round_off=doc.total - subtotal - vat,
        total=doc.total,
        posted_at=doc.posted_at,
    )


async def _line_out(session: AsyncSession, row: DocumentLine) -> SalesLineOut:
    item, unit = await _unit_code(session, row)
    return SalesLineOut(
        id=row.id,
        line_no=row.line_no,
        variant_id=row.variant_id,
        sku=item.sku if item else None,
        name_en=item.name_en if item else None,
        name_ar=item.name_ar if item else None,
        unit=unit,
        qty=row.qty_entered,
        qty_base=row.qty_base,
        base_unit=item.base_unit if item else None,
        unit_price=row.unit_price,
        price_source=row.price_source,
        below_min=row.below_min,
        net_amount=row.net_amount or ZERO,
        tax_code=row.tax_code,
        vat_rate=row.vat_rate or ZERO,
        vat_amount=row.vat_amount or ZERO,
        line_total=row.line_total or ZERO,
        source_line_id=row.source_line_id,
        batch_id=row.batch_id,
        description=row.description,
        cost=row.cleared_value,
    )


PROGRESS = {
    SalesDocType.QUOTATION.value: ("open", "partly_ordered", "ordered"),
    SalesDocType.SALES_ORDER.value: ("open", "partly_delivered", "delivered"),
    SalesDocType.DELIVERY_NOTE.value: ("not_invoiced", "partly_invoiced", "invoiced"),
}


async def get_sales_document(
    session: AsyncSession, doc_id: uuid.UUID, warnings: list[SalesWarningOut] | None = None
) -> SalesDocOut:
    doc = await get_document(session, doc_id)
    rows = await _lines(session, doc)
    progress = None
    if doc.status == DocumentStatus.POSTED and doc.doc_type in PROGRESS:
        opened = await open_on(session, [(r, doc.doc_type) for r in rows])
        left = sum((o.qty for o in opened.values()), ZERO)
        whole = sum((r.qty_base for r in rows), ZERO)
        start, part, done = PROGRESS[doc.doc_type]
        progress = start if left == whole else (done if left <= 0 else part)
    return SalesDocOut(
        **summary(doc).model_dump(),
        lines=[await _line_out(session, r) for r in rows],
        progress=progress,
        warnings=warnings or [],
    )


async def open_lines(session: AsyncSession, doc_id: uuid.UUID) -> list[OpenSalesLineOut]:
    """A posted document's lines still to order, deliver, invoice or return: to start the next document."""
    doc = await get_document(session, doc_id)
    if doc.status != DocumentStatus.POSTED or doc.doc_type not in FOLLOWERS:
        return []
    rows = await _lines(session, doc)
    opened = await open_on(session, [(r, doc.doc_type) for r in rows])
    out: list[OpenSalesLineOut] = []
    for r in rows:
        left = opened[r.id]
        if left.qty <= 0:
            continue
        item, unit = await _unit_code(session, r)
        priced = await _priced_line(session, r, doc.doc_type)
        in_unit = left.qty / r.factor_to_base
        exact = in_unit == in_unit.quantize(PRICE_PLACES)
        price = None
        if priced is not None and priced.unit_price is not None:
            price = (priced.unit_price / priced.factor_to_base * r.factor_to_base).quantize(PRICE_PLACES)
            if not exact:
                price = (priced.unit_price / priced.factor_to_base).quantize(PRICE_PLACES)
        out.append(
            OpenSalesLineOut(
                line_id=r.id,
                variant_id=r.variant_id,
                sku=item.sku if item else None,
                name_en=item.name_en if item else None,
                name_ar=item.name_ar if item else None,
                unit=unit if exact else (item.base_unit if item else None),
                factor_to_base=r.factor_to_base if exact else Decimal(1),
                qty_open_base=left.qty,
                qty_open=in_unit.quantize(PRICE_PLACES) if exact else left.qty,
                unit_price=price,
                price_mode=PriceMode(mode)
                if (mode := await _mode_of(session, priced) if priced else None)
                else None,
                tax_code=priced.tax_code if priced is not None else None,
                vat_rate=(
                    priced.vat_rate if priced is not None and priced.vat_rate is not None else DEFAULT_VAT
                ),
            )
        )
    return out


async def list_sales_documents(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    doc_types: list[SalesDocType],
    status: DocumentStatus | None = None,
    party_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SalesDocSummary]:
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


# --- for other modules: pricing (A9) and credit (M1.4) -----------------------------------------------------


def _invoiced_lines(business_id: uuid.UUID, party_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return (
        select(DocumentLine, Document)
        .join(Document, Document.id == DocumentLine.document_id)
        .where(
            Document.business_id == business_id,
            Document.party_id == party_id,
            Document.doc_type == SalesDocType.SALES_INVOICE.value,
            Document.status == DocumentStatus.POSTED.value,
            DocumentLine.unit_price.is_not(None),
        )
        .order_by(Document.doc_date.desc(), Document.posted_at.desc(), DocumentLine.line_no)
    )


async def last_sale(
    session: AsyncSession, business_id: uuid.UUID, party_id: uuid.UUID, variant_id: uuid.UUID
) -> LastSale | None:
    """The pricing engine's step 1: the latest posted invoice line of this item to this customer."""
    found = (
        await session.execute(
            _invoiced_lines(business_id, party_id).where(DocumentLine.variant_id == variant_id).limit(1)
        )
    ).first()
    if found is None:
        return None
    row, doc = found
    _item, unit = await _unit_code(session, row)
    assert row.unit_price is not None
    assert unit is not None
    assert doc.number is not None
    return LastSale(
        price=row.unit_price,
        unit=unit,
        factor_to_base=row.factor_to_base,
        document_number=doc.number,
        document_date=doc.doc_date,
    )


async def sales_history(
    session: AsyncSession, business_id: uuid.UUID, party_id: uuid.UUID, q: str | None = None, limit: int = 200
) -> list[SalesHistoryLineOut]:
    """Every item invoiced to this customer, newest first; ``q`` matches the SKU or name (A9 history
    panel)."""
    needle = (q or "").strip().lower()
    out: list[SalesHistoryLineOut] = []
    for row, doc in (await session.execute(_invoiced_lines(business_id, party_id))).all():
        item, unit = await _unit_code(session, row)
        if item is None or unit is None or doc.number is None:
            continue
        if (
            needle
            and needle not in item.sku.lower()
            and needle not in item.name_en.lower()
            and needle not in item.name_ar
        ):
            continue
        out.append(
            SalesHistoryLineOut(
                doc_date=doc.doc_date,
                document_id=doc.id,
                number=doc.number,
                variant_id=item.variant_id,
                sku=item.sku,
                name_en=item.name_en,
                name_ar=item.name_ar,
                qty=row.qty_entered,
                unit=unit,
                unit_price=row.unit_price,
            )
        )
        if len(out) >= limit:
            break
    return out


async def open_order_value(session: AsyncSession, business_id: uuid.UUID, party_id: uuid.UUID) -> Decimal:
    """The credit check's "open orders": what the customer's posted orders still have to deliver or invoice,
    at the order's price with VAT."""
    rows = (
        await session.scalars(
            select(DocumentLine)
            .join(Document, Document.id == DocumentLine.document_id)
            .where(
                Document.business_id == business_id,
                Document.party_id == party_id,
                Document.doc_type == SalesDocType.SALES_ORDER.value,
                Document.status == DocumentStatus.POSTED.value,
            )
        )
    ).all()
    opened = await open_on(session, [(r, SalesDocType.SALES_ORDER.value) for r in rows])
    total = ZERO
    for r in rows:
        left = opened[r.id].qty
        if left > 0 and r.qty_base:
            total += fils((r.line_total or ZERO) * left / r.qty_base)
    return total.quantize(Decimal("0.0001"))  # as money columns
