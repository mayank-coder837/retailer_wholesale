"""Vouchers: receipt, payment, contra, journal, expense, credit note, debit note (M1.5).

A voucher is a ``document`` row (ADR 0009) with the lines as typed (``voucher_line``) and their bill-wise
choices (``voucher_allocation``). Saving validates it; posting validates it again (accounts may have changed
since), checks the period, takes the next gap-free number, writes the journal entry and settles bills, all in
the request's transaction, so any failure leaves nothing behind, the number included.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, numbering
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.money import RoundOffMode, RoundOffPlace, currency_decimals, round_off, to_currency
from app.core.numbering import NotNumberOwnerError
from app.modules.accounting import accounts as chart
from app.modules.accounting import ledger, periods, posting
from app.modules.accounting.models import (
    CASH_OR_BANK,
    CONTROL,
    Account,
    AccountType,
    AllocationType,
    JournalLine,
    Role,
    VoucherAllocation,
    VoucherLine,
)
from app.modules.accounting.posting import (
    DOUBLE_ENTRY,
    HEADER,
    VAT_RATES,
    WITH_CASH_ACCOUNT,
    WITH_PARTY,
    WITH_VAT,
    VoucherType,
)
from app.modules.accounting.schemas import (
    AllocationIn,
    AllocationOut,
    JournalLineOut,
    VoucherIn,
    VoucherLineIn,
    VoucherLineOut,
    VoucherOut,
    VoucherSummary,
)
from app.modules.documents.models import Document, DocumentStatus
from app.modules.parties import service as parties
from app.modules.platform import settings as settings_service
from app.modules.tax import codes as tax
from app.modules.tax.codes import Treatment
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Business

ZERO = Decimal(0)
# Accounts a single-entry line may not use: money between cash and bank is a contra, and control accounts are
# reached through a party.
NOT_ON_LINES: dict[VoucherType, frozenset[AccountType]] = {
    VoucherType.RECEIPT: CASH_OR_BANK,
    VoucherType.PAYMENT: CASH_OR_BANK,
    VoucherType.EXPENSE: CASH_OR_BANK | CONTROL | {AccountType.TAX, AccountType.STOCK},
    VoucherType.CREDIT_NOTE: CASH_OR_BANK | CONTROL | {AccountType.TAX},
    VoucherType.DEBIT_NOTE: CASH_OR_BANK | CONTROL | {AccountType.TAX},
    VoucherType.JOURNAL: frozenset(),
    VoucherType.CONTRA: frozenset(),
}
DEFAULT_LINE_ROLE = {
    VoucherType.CREDIT_NOTE: Role.SALES_RETURNS,
    VoucherType.DEBIT_NOTE: Role.PURCHASE_PRICE_VARIANCE,
}
PARTY_ROLE = {VoucherType.RECEIPT: Role.AR_CONTROL, VoucherType.PAYMENT: Role.AP_CONTROL}
ROUND_OFF_PLACE = {VoucherType.RECEIPT: RoundOffPlace.RECEIPT, VoucherType.PAYMENT: RoundOffPlace.PAYMENT}
VOUCHER_TYPES = frozenset(t.value for t in VoucherType)


class VoucherNotFoundError(NotFoundError):
    code = "voucher_not_found"
    title = "There is no such voucher"


class InvalidVoucherError(UnprocessableError):
    code = "invalid_voucher"
    title = "The voucher is not valid"


class VoucherNotDraftError(ConflictError):
    code = "voucher_not_draft"
    title = "Only a draft voucher can change"


@dataclass(slots=True)
class Checked:
    """A validated voucher: what posting needs, plus the bill-wise choices keyed like ``JournalLine``."""

    business: Business
    voucher: posting.Voucher
    posted: posting.Posted
    allocations: dict[int, list[AllocationIn]] = field(default_factory=dict)


def _invalid(message: str, **where: object) -> InvalidVoucherError:
    return InvalidVoucherError(message, **where)


def _check_money(amount: Decimal, currency: str, where: str) -> None:
    if amount != to_currency(amount, currency):
        raise _invalid(f"{currency} amounts have {currency_decimals(currency)} decimal places.", field=where)


async def _check(session: AsyncSession, data: VoucherIn) -> Checked:
    business = await tenancy.get_business(session, data.business_id)
    branch = await tenancy.get_branch(session, data.branch_id)
    if branch.business_id != business.id:
        raise _invalid("The branch belongs to another business.", field="branch_id")
    currency = business.base_currency
    accounts = await chart.accounts_by_id(session, business.id)
    roles = await chart.role_map(session, business.id)
    kind = data.doc_type

    def account_of(account_id: uuid.UUID | None, where: str) -> Account:
        account = accounts.get(account_id) if account_id else None
        if account is None:
            raise _invalid("There is no such account in this business.", field=where)
        if account.is_group or account.archived_at is not None:
            raise _invalid("Post to an active ledger, not a group or an archived account.", field=where)
        if not account.allow_manual_posting:
            raise _invalid(f"{account.name_en} is posted by the system only.", field=where)
        return account

    def role_account(role: Role, where: str) -> Account:
        if role not in roles:
            raise _invalid(f"No account has the role {role.value}.", field=where)
        return account_of(roles[role], where)

    async def check_party(party_id: uuid.UUID, account: Account, where: str) -> None:
        party = await parties.get_party_row(session, party_id)
        if account.account_type == AccountType.RECEIVABLE and not party.is_customer:
            raise _invalid("Receivable lines name a customer.", field=where)
        if account.account_type == AccountType.PAYABLE and not party.is_supplier:
            raise _invalid("Payable lines name a supplier.", field=where)

    # Header.
    header_account: Account | None = None
    party_account: Account | None = None
    if kind in WITH_CASH_ACCOUNT:
        header_account = account_of(data.account_id, "account_id")
        if header_account.account_type not in CASH_OR_BANK:
            raise _invalid("Money moves through a cash or bank account.", field="account_id")
    elif data.account_id is not None:
        raise _invalid(f"A {kind.value} has no cash or bank account.", field="account_id")
    if kind in WITH_PARTY:
        if data.party_id is None:
            raise _invalid(f"A {kind.value} names the party.", field="party_id")
        party_role = Role.AR_CONTROL if kind is VoucherType.CREDIT_NOTE else Role.AP_CONTROL
        party_account = role_account(party_role, "party_id")
        await check_party(data.party_id, party_account, "party_id")
    elif data.party_id is not None or data.allocations:
        raise _invalid(f"A {kind.value} names its parties on the lines.", field="party_id")

    # Lines.
    lines: list[posting.Line] = []
    allocations: dict[int, list[AllocationIn]] = {}
    for i, raw in enumerate(data.lines):
        where = f"lines[{i}]"
        if kind in DOUBLE_ENTRY:
            if raw.amount is not None or (raw.debit is None) == (raw.credit is None):
                raise _invalid("A journal or contra line has a debit or a credit.", field=where)
            debit, credit = raw.debit or ZERO, raw.credit or ZERO
        else:
            if raw.amount is None or raw.debit is not None or raw.credit is not None:
                raise _invalid(f"A {kind.value} line has an amount.", field=where)
            debit, credit = (raw.amount, ZERO) if posting.LINE_SIDE[kind] == "debit" else (ZERO, raw.amount)
        _check_money(debit or credit, currency, where)

        account_id = raw.account_id
        if account_id is None:
            if raw.party_id is not None and kind in PARTY_ROLE:
                account_id = role_account(PARTY_ROLE[kind], where).id
            elif kind in DEFAULT_LINE_ROLE:
                account_id = role_account(DEFAULT_LINE_ROLE[kind], where).id
            else:
                raise _invalid("Choose the account.", field=f"{where}.account_id")
        account = account_of(account_id, f"{where}.account_id")
        account_type = AccountType(account.account_type) if account.account_type else None
        if account_type in NOT_ON_LINES[kind]:
            raise _invalid(f"A {kind.value} line cannot use {account.name_en}.", field=f"{where}.account_id")
        if kind is VoucherType.CONTRA and account_type not in CASH_OR_BANK:
            raise _invalid(
                "A contra moves money between cash and bank accounts.", field=f"{where}.account_id"
            )
        if account_type in CONTROL:
            if raw.party_id is None:
                raise _invalid(
                    f"{account.name_en} lines name a customer or supplier.", field=f"{where}.party_id"
                )
            await check_party(raw.party_id, account, f"{where}.party_id")
        elif raw.party_id is not None:
            raise _invalid("Only receivable and payable lines name a party.", field=f"{where}.party_id")

        code = _line_code(kind, raw, where)
        if raw.allocations:
            if raw.party_id is None:
                raise _invalid("Only party lines are settled bill-wise.", field=f"{where}.allocations")
            _check_allocations(raw.allocations, debit or credit, currency, f"{where}.allocations")
            allocations[i] = raw.allocations
        lines.append(
            posting.Line(
                account.id,
                debit,
                credit,
                raw.party_id,
                code.rate if code else ZERO,
                raw.description,
                code.code if code else None,
                code.treatment if code else Treatment.NONE,
            )
        )

    if kind in DOUBLE_ENTRY and len(lines) < 2:
        raise _invalid(f"A {kind.value} has at least two lines.", field="lines")
    voucher = posting.Voucher(
        doc_type=kind,
        lines=tuple(lines),
        currency=currency,
        account_id=header_account.id if header_account else None,
        party_id=data.party_id,
        party_account_id=party_account.id if party_account else None,
        narration=data.narration,
    )
    try:
        posted = posting.journal_for(voucher, _role_accounts(roles))
    except posting.UnbalancedError as exc:
        raise _invalid(str(exc), field="lines") from exc
    if data.allocations:
        _check_allocations(data.allocations, posted.total, currency, "allocations")
        allocations[HEADER] = data.allocations
    return Checked(business, voucher, posted, allocations)


def _line_code(kind: VoucherType, raw: VoucherLineIn, where: str) -> tax.TaxCode | None:
    """A voucher line's tax code (M1.10): expense vouchers and debit notes take purchase codes, credit notes
    sales
    codes. A line sent with only a VAT rate (before tax codes): 5 is SR, 0 no code."""
    typed = raw.tax_code
    if typed is None:
        if raw.vat_rate not in VAT_RATES:
            raise _invalid("The VAT rate is 0 or 5 percent.", field=f"{where}.vat_rate")
        typed = tax.STANDARD if raw.vat_rate else None
    if typed is None:
        return None
    if kind not in WITH_VAT:
        raise _invalid(f"A {kind.value} carries no VAT.", field=f"{where}.tax_code")
    side = tax.Side.SALES if kind is VoucherType.CREDIT_NOTE else tax.Side.PURCHASE
    code = tax.get(side, typed)
    if code is None:
        raise _invalid(f"{typed} is not a {side.value} tax code.", field=f"{where}.tax_code")
    return code


def _role_accounts(roles: dict[Role, uuid.UUID]) -> posting.Accounts:
    missing = [r.value for r in (Role.VAT_INPUT, Role.VAT_OUTPUT, Role.ROUNDING) if r not in roles]
    if missing:
        raise _invalid(f"Give these roles an account first: {', '.join(missing)}.", roles=missing)
    return posting.Accounts(roles[Role.VAT_INPUT], roles[Role.VAT_OUTPUT], roles[Role.ROUNDING])


def _check_allocations(items: list[AllocationIn], amount: Decimal, currency: str, where: str) -> None:
    for item in items:
        _check_money(item.amount, currency, where)
    allocated = sum((item.amount for item in items), ZERO)
    if allocated != amount:
        raise _invalid(
            f"Bill-wise amounts add up to {allocated}, not {amount}.", field=where, allocated=str(allocated)
        )
    bills = [item.bill_id for item in items if item.bill_id is not None]
    if len(bills) != len(set(bills)):
        raise _invalid("A bill appears twice.", field=where)


# --- drafts ------------------------------------------------------------------------------------------------


async def _write_lines(session: AsyncSession, doc: Document, data: VoucherIn, checked: Checked) -> None:
    for i, line in enumerate(checked.voucher.lines):
        row = VoucherLine(
            tenant_id=doc.tenant_id,
            document_id=doc.id,
            business_id=doc.business_id,
            line_no=i + 1,
            account_id=line.account_id,
            party_id=line.party_id,
            debit=line.debit,
            credit=line.credit,
            tax_code=line.tax_code,
            vat_rate=line.vat_rate,
            vat_amount=checked.posted.line_vat[i],
            description=line.description,
        )
        session.add(row)
        await session.flush()
        for item in data.lines[i].allocations:
            session.add(_allocation_row(doc, row.id, item))
    for item in data.allocations:
        session.add(_allocation_row(doc, None, item))
    await session.flush()


def _allocation_row(doc: Document, line_id: uuid.UUID | None, item: AllocationIn) -> VoucherAllocation:
    return VoucherAllocation(
        tenant_id=doc.tenant_id,
        document_id=doc.id,
        business_id=doc.business_id,
        voucher_line_id=line_id,
        allocation_type=item.type.value,
        bill_id=item.bill_id,
        reference=item.reference,
        due_date=item.due_date,
        amount=item.amount,
    )


async def create_draft(session: AsyncSession, tenant_id: uuid.UUID, data: VoucherIn) -> Document:
    checked = await _check(session, data)
    doc = Document(
        tenant_id=tenant_id,
        doc_type=data.doc_type.value,
        business_id=data.business_id,
        branch_id=data.branch_id,
        doc_date=data.doc_date,
        reference=data.reference,
        narration=data.narration,
        party_id=data.party_id,
        account_id=data.account_id,
        currency=checked.business.base_currency,
        total=checked.posted.total,
    )
    session.add(doc)
    await session.flush()
    await _write_lines(session, doc, data, checked)
    return doc


async def _locked(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = (
        await session.scalars(
            select(Document)
            .where(Document.id == doc_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).first()
    if doc is None or doc.doc_type not in VOUCHER_TYPES:
        raise VoucherNotFoundError()
    return doc


async def _locked_draft(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = await _locked(session, doc_id)
    if doc.status != DocumentStatus.DRAFT:
        raise VoucherNotDraftError(current=doc.status)
    return doc


async def _drop_lines(session: AsyncSession, doc: Document) -> None:
    await session.execute(delete(VoucherAllocation).where(VoucherAllocation.document_id == doc.id))
    await session.execute(delete(VoucherLine).where(VoucherLine.document_id == doc.id))


async def replace_draft(session: AsyncSession, doc_id: uuid.UUID, data: VoucherIn) -> Document:
    doc = await _locked_draft(session, doc_id)
    if data.doc_type.value != doc.doc_type or data.business_id != doc.business_id:
        raise _invalid("A draft keeps its voucher type and business.", field="doc_type")
    checked = await _check(session, data)
    await _drop_lines(session, doc)
    doc.branch_id = data.branch_id
    doc.doc_date = data.doc_date
    doc.reference = data.reference
    doc.narration = data.narration
    doc.party_id = data.party_id
    doc.account_id = data.account_id
    doc.total = checked.posted.total
    await session.flush()
    await _write_lines(session, doc, data, checked)
    return doc


async def delete_draft(session: AsyncSession, doc_id: uuid.UUID) -> None:
    doc = await _locked_draft(session, doc_id)
    await _drop_lines(session, doc)
    await session.delete(doc)
    await session.flush()


async def get_document(session: AsyncSession, doc_id: uuid.UUID) -> Document:
    doc = await session.get(Document, doc_id, populate_existing=True)  # as stored: totals to 4 places
    if doc is None or doc.doc_type not in VOUCHER_TYPES:
        raise VoucherNotFoundError()
    return doc


async def _stored_input(session: AsyncSession, doc: Document) -> VoucherIn:
    """The draft as it was typed, rebuilt from its rows (posting validates it again)."""
    kind = VoucherType(doc.doc_type)
    rows = (
        await session.scalars(
            select(VoucherLine).where(VoucherLine.document_id == doc.id).order_by(VoucherLine.line_no)
        )
    ).all()
    items = (
        await session.scalars(
            select(VoucherAllocation)
            .where(VoucherAllocation.document_id == doc.id)
            .order_by(VoucherAllocation.created_at, VoucherAllocation.id)
        )
    ).all()
    by_line: dict[uuid.UUID | None, list[AllocationIn]] = {}
    for a in items:
        by_line.setdefault(a.voucher_line_id, []).append(
            AllocationIn(
                type=AllocationType(a.allocation_type),
                amount=a.amount,
                bill_id=a.bill_id,
                reference=a.reference,
                due_date=a.due_date,
            )
        )
    lines = [
        VoucherLineIn(
            account_id=r.account_id,
            party_id=r.party_id,
            amount=None if kind in DOUBLE_ENTRY else (r.debit or r.credit),
            debit=r.debit if kind in DOUBLE_ENTRY and r.debit else None,
            credit=r.credit if kind in DOUBLE_ENTRY and r.credit else None,
            tax_code=r.tax_code,
            vat_rate=ZERO if r.tax_code else (r.vat_rate.normalize() if r.vat_rate else ZERO),
            description=r.description,
            allocations=by_line.get(r.id, []),
        )
        for r in rows
    ]
    return VoucherIn(
        doc_type=kind,
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        doc_date=doc.doc_date,
        reference=doc.reference,
        narration=doc.narration,
        account_id=doc.account_id,
        party_id=doc.party_id,
        allocations=by_line.get(None, []),
        lines=lines,
    )


# --- posting -----------------------------------------------------------------------------------------------


async def _round_off(session: AsyncSession, business: Business, kind: VoucherType, total: Decimal) -> Decimal:
    place = ROUND_OFF_PLACE.get(kind)
    if place is None:
        return ZERO
    values = await settings_service.effective(session, business_id=business.id)
    mode = RoundOffMode(values[f"rounding.{place.value}.mode"].value)
    step = Decimal(values[f"rounding.{place.value}.step"].value)
    return round_off(total, mode, step, business.base_currency).adjustment


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
    checked = await _check(session, data)
    business, kind = checked.business, data.doc_type

    year = await periods.year_for_posting(session, business, doc.doc_date)
    period = await periods.period_of(session, year, doc.doc_date)
    label = f"{kind.value} {doc.id}"
    await periods.check_open(
        session, period, may_override=may_override_lock, override_reason=override_reason, document=label
    )
    adjustment = await _round_off(session, business, kind, checked.posted.total)
    try:
        posted = posting.journal_for(
            checked.voucher, _role_accounts(await chart.role_map(session, business.id)), round_off=adjustment
        )
    except ValueError as exc:  # rounded down to nothing
        raise _invalid("After round-off this voucher moves no money.", round_off=str(adjustment)) from exc

    if node_id is None:
        raise NotNumberOwnerError()
    number = await numbering.allocate(
        session,
        tenant_id=doc.tenant_id,
        branch_id=doc.branch_id,
        doc_type=kind.value,
        fiscal_year=year.code,
        node_id=node_id,
        document_id=doc.id,
        reuse=reuse_number,
    )
    rows = await ledger.write_entry(
        session,
        ledger.EntryHeader(
            tenant_id=doc.tenant_id,
            business_id=doc.business_id,
            branch_id=doc.branch_id,
            entry_date=doc.doc_date,
            fiscal_period_id=period.id,
            number=number.formatted,
            narration=doc.narration,
            source_document_id=doc.id,
        ),
        posted.lines,
    )
    for line, row in zip(posted.lines, rows, strict=True):
        if line.allocation_key is not None:
            await _settle(
                session, doc, number.formatted, row, checked.allocations.get(line.allocation_key, [])
            )

    doc.status = DocumentStatus.POSTED.value
    doc.series_id = number.series_id
    doc.number_seq = number.number
    doc.number = number.formatted
    doc.reissue_of_id = number.reissue_of
    doc.total = posted.total
    doc.posted_at = datetime.now(UTC)
    doc.posted_by = user_id
    await session.flush()
    await audit.record(
        session,
        doc,
        "post",
        changes={"number": [None, number.formatted], "total": [None, str(posted.total)]},
    )
    return doc


async def _settle(
    session: AsyncSession,
    doc: Document,
    number: str,
    line: JournalLine,
    items: list[AllocationIn],
) -> None:
    """The bill-wise choices of one party line; without any, the amount is on account."""
    assert line.party_id is not None
    terms: int | None = None
    for item in items:
        if item.type is AllocationType.AGAINST_REF:
            assert item.bill_id is not None
            bill = await ledger.lock_bill(session, item.bill_id)
            if bill is None:
                raise ledger.BillNotOpenError("There is no such bill.", bill_id=str(item.bill_id))
            await ledger.settle(session, bill, line, item.amount)
        elif item.type in (AllocationType.NEW_REF, AllocationType.ADVANCE):
            due = item.due_date
            if due is None and item.type is AllocationType.NEW_REF:
                if terms is None:
                    account = await chart.get_account(session, line.account_id)
                    terms = await parties.terms_days(
                        session,
                        line.party_id,
                        doc.business_id,
                        customer=account.account_type == AccountType.RECEIVABLE,
                    )
                due = doc.doc_date + timedelta(days=terms) if terms is not None else None
            await ledger.open_bill(
                session,
                line,
                amount=item.amount,
                reference=item.reference or number,
                due_date=due,
                is_advance=item.type is AllocationType.ADVANCE,
                document_id=doc.id,
            )


# --- reading -----------------------------------------------------------------------------------------------


def summary(doc: Document) -> VoucherSummary:
    return VoucherSummary(
        id=doc.id,
        doc_type=VoucherType(doc.doc_type),
        status=DocumentStatus(doc.status),
        number=doc.number,
        doc_date=doc.doc_date,
        business_id=doc.business_id,
        branch_id=doc.branch_id,
        reference=doc.reference,
        narration=doc.narration,
        account_id=doc.account_id,
        party_id=doc.party_id,
        total=doc.total,
        posted_at=doc.posted_at,
    )


async def get_voucher(session: AsyncSession, doc_id: uuid.UUID) -> VoucherOut:
    doc = await get_document(session, doc_id)
    rows = (
        await session.scalars(
            select(VoucherLine).where(VoucherLine.document_id == doc.id).order_by(VoucherLine.line_no)
        )
    ).all()
    # A reversal (M1.7b) has no lines of its own: only the mirror entry, shown below.
    data = await _stored_input(session, doc) if doc.reversal_of_id is None else None
    accounts = await chart.accounts_by_id(session, doc.business_id)
    journal = [
        JournalLineOut(
            line_no=j.line_no,
            account_id=j.account_id,
            account_code=accounts[j.account_id].code,
            account_name_en=accounts[j.account_id].name_en,
            account_name_ar=accounts[j.account_id].name_ar,
            party_id=j.party_id,
            debit=j.debit,
            credit=j.credit,
            description=j.description,
        )
        for j in await ledger.entries_of_document(session, doc.id)
    ]
    return VoucherOut(
        **summary(doc).model_dump(),
        vat_total=sum((r.vat_amount for r in rows), ZERO),
        allocations=[AllocationOut(**a.model_dump()) for a in data.allocations] if data else [],
        lines=[
            VoucherLineOut(
                id=r.id,
                line_no=r.line_no,
                account_id=r.account_id,
                party_id=r.party_id,
                debit=r.debit,
                credit=r.credit,
                tax_code=r.tax_code,
                vat_rate=r.vat_rate,
                vat_amount=r.vat_amount,
                description=r.description,
                allocations=[AllocationOut(**a.model_dump()) for a in line_in.allocations],
            )
            for r, line_in in zip(rows, data.lines if data else [], strict=True)
        ],
        journal=journal,
    )


async def list_vouchers(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    doc_types: list[VoucherType],
    status: DocumentStatus | None = None,
    party_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[VoucherSummary]:
    if not doc_types:
        return []
    stmt = select(Document).where(
        Document.business_id == business_id, Document.doc_type.in_([t.value for t in doc_types])
    )
    if status is not None:
        stmt = stmt.where(Document.status == status.value)
    if party_id is not None:
        stmt = stmt.where(Document.party_id == party_id)
    if date_from is not None:
        stmt = stmt.where(Document.doc_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Document.doc_date <= date_to)
    stmt = stmt.order_by(Document.doc_date.desc(), Document.created_at.desc()).limit(limit).offset(offset)
    return [summary(d) for d in (await session.scalars(stmt)).all()]
