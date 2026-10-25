"""Accounting services other modules call (never the tables), and the ledger read models (M1.5).

- ``install_for_business``: the default chart and roles for a new business (tenancy calls it).
- ``receivable_figures``: a customer's ledger balance and oldest unpaid invoice (the credit check, M1.4).
- ``posting_period`` / ``write_journal`` / ``role_accounts``: how other modules (inventory, later sales and
  purchasing) post journal entries, with the same period lock and balance checks as vouchers.
- Trial balance, account and party ledgers, open bills: read straight from ``journal_line`` and ``bill``.
"""

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting import accounts as chart
from app.modules.accounting import ledger, periods, posting
from app.modules.accounting.models import (
    CASH_OR_BANK,
    CONTROL,
    Account,
    AccountType,
    Bill,
    BillAllocation,
    Direction,
    EntryKind,
    FiscalPeriod,
    FiscalYear,
    JournalEntry,
    JournalLine,
    Nature,
    PeriodStatus,
    Role,
)
from app.modules.accounting.schemas import (
    AgeingBuckets,
    AgeingOut,
    AgeingRowOut,
    BillOut,
    LedgerLineOut,
    LedgerOut,
    PartyLedgerOut,
    TrialBalanceOut,
    TrialBalanceRowOut,
    VoucherIn,
)
from app.modules.documents.models import Document, DocumentStatus
from app.modules.tax.codes import Side
from app.modules.tax.returns import VatFact
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Business

ZERO = Decimal("0.0000")  # money columns have 4 decimal places


async def install_for_business(session: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID) -> None:
    await chart.install_chart(session, tenant_id, business_id)


async def role_accounts(session: AsyncSession, business_id: uuid.UUID) -> dict[Role, uuid.UUID]:
    """The account each posting role uses in this business."""
    return await chart.role_map(session, business_id)


async def posting_period(
    session: AsyncSession,
    business: Business,
    day: date,
    *,
    may_override: bool = False,
    override_reason: str | None = None,
    document: str = "",
) -> tuple[FiscalYear, FiscalPeriod]:
    """The open period ``day`` posts into (its year created on demand); a locked one needs the override."""
    year = await periods.year_for_posting(session, business, day)
    period = await periods.period_of(session, year, day)
    await periods.check_open(
        session, period, may_override=may_override, override_reason=override_reason, document=document
    )
    return year, period


async def is_open(session: AsyncSession, business: Business, day: date) -> bool:
    """Whether ``day`` is in an open period (no year yet counts as open: it will be created)."""
    year = await periods.find_year(session, business.id, day)
    if year is None:
        return True
    return (await periods.period_of(session, year, day)).status == PeriodStatus.OPEN


async def write_journal(
    session: AsyncSession,
    *,
    business: Business,
    branch_id: uuid.UUID,
    day: date,
    period_id: uuid.UUID,
    number: str | None,
    narration: str | None,
    source_document_id: uuid.UUID | None,
    lines: Sequence[posting.JournalLine],
    kind: EntryKind = EntryKind.POSTING,
) -> list[JournalLine]:
    """One balanced journal entry (refused in code and again by the database when it does not balance)."""
    return await ledger.write_entry(
        session,
        ledger.EntryHeader(
            tenant_id=business.tenant_id,
            business_id=business.id,
            branch_id=branch_id,
            entry_date=day,
            fiscal_period_id=period_id,
            number=number,
            narration=narration,
            source_document_id=source_document_id,
            kind=kind,
        ),
        lines,
    )


async def posting_lines(session: AsyncSession, document_id: uuid.UUID) -> list[JournalLine]:
    """The lines of a document's own entries (not its recosts or undo entries)."""
    out: list[JournalLine] = []
    for entry in await ledger.posting_entries(session, document_id):
        out += await ledger.lines_of(session, entry.id)
    return out


async def undo_journal(
    session: AsyncSession,
    *,
    business: Business,
    document_id: uuid.UUID,
    branch_id: uuid.UUID,
    day: date,
    period_id: uuid.UUID,
    number: str | None,
    narration: str | None,
    source_document_id: uuid.UUID,
) -> None:
    """Rules 32 / 33 for the books: a mirror of each of the document's posting entries, dated ``day``.

    Bill-wise, the document leaves no trace: what its lines settled is open again, payments other documents
    allocated to its bills become advances on those payments, and the mirror lines close its bills.
    """
    mirror_of: dict[uuid.UUID, JournalLine] = {}
    for entry in await ledger.posting_entries(session, document_id):
        lines = await ledger.lines_of(session, entry.id)
        rows = await ledger.write_entry(
            session,
            ledger.EntryHeader(
                tenant_id=business.tenant_id,
                business_id=business.id,
                branch_id=branch_id,
                entry_date=day,
                fiscal_period_id=period_id,
                number=number,
                narration=narration,
                source_document_id=source_document_id,
                kind=EntryKind.UNDO,
                reversal_of_id=entry.id,
            ),
            ledger.mirror(lines),
        )
        mirror_of.update({line.id: row for line, row in zip(lines, rows, strict=True)})
    own = list(mirror_of)
    if not own:
        return
    for allocation in await ledger.live_allocations(session, BillAllocation.journal_line_id.in_(own)):
        await ledger.unsettle(session, allocation)
    for bill in await ledger.bills_opened_by(session, own):
        for allocation in await ledger.live_allocations(session, BillAllocation.bill_id == bill.id):
            await ledger.unsettle(session, allocation)
            paid_by = await session.get(JournalLine, allocation.journal_line_id)
            assert paid_by is not None
            paying_entry = await session.get(JournalEntry, paid_by.entry_id)
            assert paying_entry is not None
            await ledger.open_bill(
                session,
                paid_by,
                amount=allocation.amount,
                reference=paying_entry.number or bill.reference,
                due_date=None,
                is_advance=True,
                document_id=paying_entry.source_document_id,
            )
        if bill.amount_open > 0:
            await ledger.settle(session, bill, mirror_of[bill.journal_line_id], bill.amount_open)


async def receivable_figures(
    session: AsyncSession, business_id: uuid.UUID, party_id: uuid.UUID
) -> tuple[Decimal, int | None]:
    """(ledger balance owed, age in days of the oldest unpaid invoice) as of the business's today."""
    figures = await ledger.receivable_figures(session, business_id, party_id)
    if figures.oldest_open_bill_date is None:
        return figures.balance, None
    today = periods.business_today(await tenancy.get_business(session, business_id))
    return figures.balance, (today - figures.oldest_open_bill_date).days


def _ledger_out(opening: Decimal, lines: list[ledger.LedgerLine]) -> LedgerOut:
    running = opening
    out: list[LedgerLineOut] = []
    for line in lines:
        running += line.debit - line.credit
        out.append(
            LedgerLineOut(
                entry_date=line.entry_date,
                document_id=line.document_id,
                doc_type=line.doc_type,
                number=line.number,
                narration=line.narration,
                account_id=line.account_id,
                party_id=line.party_id,
                description=line.description,
                debit=line.debit,
                credit=line.credit,
                balance=running,
            )
        )
    return LedgerOut(
        opening=opening,
        debit_total=sum((x.debit for x in lines), ZERO),
        credit_total=sum((x.credit for x in lines), ZERO),
        closing=running,
        lines=out,
    )


async def account_ledger(
    session: AsyncSession, account_id: uuid.UUID, date_from: date | None, date_to: date | None
) -> LedgerOut:
    """A ledger's lines, or a group's: every ledger inside it."""
    account = await chart.get_account(session, account_id)
    all_accounts = await chart.accounts_by_id(session, account.business_id)
    ids: list[uuid.UUID] = []
    pending = [account.id]
    while pending:
        current = pending.pop()
        ids.append(current)
        pending.extend(a.id for a in all_accounts.values() if a.parent_id == current)
    opening, lines = await ledger.ledger(
        session, account.business_id, account_ids=ids, date_from=date_from, date_to=date_to
    )
    return _ledger_out(opening, lines)


async def party_ledger(
    session: AsyncSession,
    business_id: uuid.UUID,
    party_id: uuid.UUID,
    date_from: date | None,
    date_to: date | None,
) -> PartyLedgerOut:
    """The customer's or supplier's statement: control-account lines, open bills, and what is on account."""
    await tenancy.get_business(session, business_id)
    accounts = await chart.accounts_by_id(session, business_id)
    control = [a.id for a in accounts.values() if a.account_type and AccountType(a.account_type) in CONTROL]
    opening, lines = await ledger.ledger(
        session, business_id, account_ids=control, party_id=party_id, date_from=date_from, date_to=date_to
    )
    base = _ledger_out(opening, lines)
    bills = await ledger.open_bills(session, business_id, party_id)
    balance = sum((await ledger.party_balance_by_side(session, business_id, party_id)).values(), ZERO)
    on_account = balance - sum((ledger.signed_open(b) for b in bills), ZERO)
    from app.modules.pdc import service as pdc  # the register builds on accounting
    from app.modules.pdc.models import PdcDirection

    pending = await pdc.pending_amounts(session, business_id, party_id)
    received = pending.get((party_id, PdcDirection.RECEIVED), ZERO)
    issued = pending.get((party_id, PdcDirection.ISSUED), ZERO)
    return PartyLedgerOut(
        **base.model_dump(),
        open_bills=[BillOut.model_validate(b) for b in bills],
        on_account=on_account,
        pdc_received_pending=received,
        pdc_issued_pending=issued,
        outstanding_after_pdc=base.closing - received + issued,
    )


async def list_bills(
    session: AsyncSession,
    business_id: uuid.UUID,
    party_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None = None,
    include_settled: bool = False,
) -> list[BillOut]:
    bills = await ledger.open_bills(
        session, business_id, party_id, account_id=account_id, include_settled=include_settled
    )
    return [BillOut.model_validate(b) for b in bills]


async def trial_balance(session: AsyncSession, business_id: uuid.UUID, as_of: date) -> TrialBalanceOut:
    await tenancy.get_business(session, business_id)
    accounts = await chart.accounts_by_id(session, business_id)
    rows = [
        TrialBalanceRowOut(
            account_id=r.account_id,
            code=accounts[r.account_id].code,
            name_en=accounts[r.account_id].name_en,
            name_ar=accounts[r.account_id].name_ar,
            nature=Nature(accounts[r.account_id].nature),
            debit=r.debit,
            credit=r.credit,
        )
        for r in await ledger.trial_balance(session, business_id, as_of)
    ]
    rows.sort(key=lambda r: r.code)
    return TrialBalanceOut(
        business_id=business_id,
        as_of=as_of,
        rows=rows,
        total_debit=sum((r.debit for r in rows), ZERO),
        total_credit=sum((r.credit for r in rows), ZERO),
    )


BUCKETS = ((0, "not_due"), (30, "days_1_30"), (60, "days_31_60"), (90, "days_61_90"))
AGEING_FIELDS = (
    "not_due",
    "days_1_30",
    "days_31_60",
    "days_61_90",
    "over_90",
    "advances",
    "total",
    "pdc",
    "after_pdc",
)


def _bucket(days_late: int) -> str:
    for limit, name in BUCKETS:
        if days_late <= limit:
            return name
    return "over_90"


async def ageing(
    session: AsyncSession, business_id: uuid.UUID, *, receivable: bool, today: date
) -> AgeingOut:
    """Receivables (or payables) ageing: per party, what is open by days past due, and advances apart.

    Read from the bills (bill-wise outstanding), so it agrees with the party ledgers. Today's open amounts;
    ageing on a past date comes with the reports (M1.11).
    """
    from app.modules.parties import service as parties  # parties builds on accounting
    from app.modules.pdc import service as pdc  # the register builds on accounting
    from app.modules.pdc.models import PdcDirection

    kind = AccountType.RECEIVABLE if receivable else AccountType.PAYABLE
    owed = Direction.DEBIT if receivable else Direction.CREDIT  # invoices to customers, bills from suppliers
    rows: dict[uuid.UUID, dict[str, Decimal]] = {}
    for bill in await ledger.open_bills_on(session, business_id, kind):
        row = rows.setdefault(bill.party_id, dict.fromkeys(AGEING_FIELDS, ZERO))
        if bill.direction == owed.value:
            row[_bucket((today - (bill.due_date or bill.bill_date)).days)] += bill.amount_open
        else:
            row["advances"] += bill.amount_open
    out: list[AgeingRowOut] = []
    totals: dict[str, Decimal] = dict.fromkeys(AGEING_FIELDS, ZERO)
    pending = await pdc.pending_amounts(session, business_id)
    side = PdcDirection.RECEIVED if receivable else PdcDirection.ISSUED
    for party_id, figures in rows.items():
        party = await parties.get_party_row(session, party_id)
        figures["total"] = (
            sum((figures[name] for _limit, name in BUCKETS), ZERO) + figures["over_90"] - figures["advances"]
        )
        figures["pdc"] = pending.get((party_id, side), ZERO)
        figures["after_pdc"] = figures["total"] - figures["pdc"]
        for name, value in figures.items():
            totals[name] += value
        out.append(
            AgeingRowOut(
                party_id=party.id, code=party.code, name_en=party.name_en, name_ar=party.name_ar, **figures
            )
        )
    out.sort(key=lambda r: (-r.total, r.code))
    return AgeingOut(as_of=today, rows=out, totals=AgeingBuckets(**totals))


async def account_balance(session: AsyncSession, business_id: uuid.UUID, account_id: uuid.UUID) -> Decimal:
    """An account's balance in the journal (debit positive)."""
    return (await ledger.account_balances(session, business_id)).get(account_id, ZERO)


async def open_party_bill(
    session: AsyncSession,
    line: JournalLine,
    *,
    amount: Decimal,
    reference: str,
    due_date: date | None,
    document_id: uuid.UUID | None,
    is_advance: bool = False,
) -> uuid.UUID:
    """A bill-wise open item for a posted party line (an invoice, a supplier bill, an advance)."""
    bill = await ledger.open_bill(
        session,
        line,
        amount=amount,
        reference=reference,
        due_date=due_date,
        is_advance=is_advance,
        document_id=document_id,
    )
    return bill.id


async def settle_document_bills(
    session: AsyncSession, document_id: uuid.UUID, line: JournalLine, amount: Decimal
) -> Decimal:
    """``line`` settles up to ``amount`` of a document's open bills, oldest first; returns the amount."""
    left = amount
    for bill_id in await session.scalars(
        select(Bill.id).where(Bill.document_id == document_id, Bill.amount_open > 0).order_by(Bill.created_at)
    ):
        if left <= 0:
            break
        bill = await ledger.lock_bill(session, bill_id)
        if bill is None or bill.party_id != line.party_id or bill.account_id != line.account_id:
            continue
        take = min(left, bill.amount_open)
        await ledger.settle(session, bill, line, take)
        left -= take
    return amount - left


async def cash_or_bank_account(session: AsyncSession, business_id: uuid.UUID, account_id: uuid.UUID) -> None:
    """Refuses an account money may not be taken into: another business's, a group, archived, or not cash or
    bank."""
    account = await session.get(Account, account_id)
    if (
        account is None
        or account.business_id != business_id
        or account.is_group
        or account.archived_at is not None
        or account.account_type is None
        or AccountType(account.account_type) not in CASH_OR_BANK
    ):
        raise chart.InvalidAccountError("Choose a cash or bank account.", field="cash_account_id")


async def bank_account(
    session: AsyncSession, business_id: uuid.UUID, account_id: uuid.UUID, field: str = "bank_account_id"
) -> None:
    """Refuses an account a cheque cannot go through: another business's, a group, archived, or not a bank."""
    account = await session.get(Account, account_id)
    if (
        account is None
        or account.business_id != business_id
        or account.is_group
        or account.archived_at is not None
        or account.account_type != AccountType.BANK.value
    ):
        raise chart.InvalidAccountError("Choose a bank account.", field=field)


async def get_bill(session: AsyncSession, bill_id: uuid.UUID) -> BillOut | None:
    bill = await session.get(Bill, bill_id)
    return BillOut.model_validate(bill) if bill is not None else None


async def bill_account_type(session: AsyncSession, bill_id: uuid.UUID) -> AccountType | None:
    """Receivable or payable: which control account a bill sits on."""
    bill = await session.get(Bill, bill_id)
    account = await session.get(Account, bill.account_id) if bill is not None else None
    return AccountType(account.account_type) if account is not None and account.account_type else None


async def post_voucher(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    data: VoucherIn,
    *,
    node_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    may_override_lock: bool = False,
    override_reason: str | None = None,
) -> Document:
    """A voucher saved and posted in one step, through the checks of one typed by hand (PDC clearance)."""
    from app.modules.accounting import vouchers  # vouchers build on this service

    draft = await vouchers.create_draft(session, tenant_id, data)
    return await vouchers.post(
        session,
        draft.id,
        node_id=node_id,
        user_id=user_id,
        may_override_lock=may_override_lock,
        override_reason=override_reason,
    )


async def expense_account(session: AsyncSession, business_id: uuid.UUID, account_id: uuid.UUID) -> None:
    """Refuses an account an expense or service line may not debit: another business's, a group, archived,
    system-only, or cash, bank, receivable, payable, tax or stock."""
    account = await session.get(Account, account_id)
    blocked = {AccountType.CASH, AccountType.BANK, AccountType.TAX, AccountType.STOCK, *CONTROL}
    if (
        account is None
        or account.business_id != business_id
        or account.is_group
        or account.archived_at is not None
        or not account.allow_manual_posting
        or account.account_type is None
        or AccountType(account.account_type) in blocked
    ):
        raise chart.InvalidAccountError("Choose an expense or asset account.", field="account_id")


# --- VAT return (M1.10b) ------------------------------------------------------------------------------------

# Vouchers whose lines reach the VAT return, by side and sign (a credit or debit note reduces, in its own
# period).
VOUCHER_VAT_SIGN: dict[str, tuple[Side, int]] = {
    "expense": (Side.PURCHASE, 1),
    "debit_note": (Side.PURCHASE, -1),
    "credit_note": (Side.SALES, -1),
}


async def vat_facts(
    session: AsyncSession,
    business_id: uuid.UUID,
    date_from: date,
    date_to: date,
    emirate_of: dict[uuid.UUID, str],
) -> list[VatFact]:
    """Voucher lines with a tax code dated in the period, and reversals of those vouchers (negative, on the
    reversal's date). Cancelled vouchers are left out."""
    from sqlalchemy.orm import aliased

    from app.modules.accounting.models import VoucherLine

    counted = (DocumentStatus.POSTED.value, DocumentStatus.REVERSED.value)
    direct = (
        select(Document, VoucherLine)
        .join(VoucherLine, VoucherLine.document_id == Document.id)
        .where(
            Document.business_id == business_id,
            Document.doc_type.in_(VOUCHER_VAT_SIGN),
            Document.status.in_(counted),
            Document.doc_date.between(date_from, date_to),
            VoucherLine.tax_code.is_not(None),
        )
    )
    reversal = aliased(Document)
    undone = (
        select(reversal, Document, VoucherLine)
        .join(Document, Document.id == reversal.reversal_of_id)
        .join(VoucherLine, VoucherLine.document_id == Document.id)
        .where(
            reversal.business_id == business_id,
            reversal.status == DocumentStatus.POSTED.value,
            reversal.doc_date.between(date_from, date_to),
            Document.doc_type.in_(VOUCHER_VAT_SIGN),
            VoucherLine.tax_code.is_not(None),
        )
    )
    rows = [(doc, doc, line, 1) for doc, line in (await session.execute(direct)).all()]
    rows += [(rev, doc, line, -1) for rev, doc, line in (await session.execute(undone)).all()]
    out: list[VatFact] = []
    for shown, original, line, flip in rows:
        side, sign = VOUCHER_VAT_SIGN[original.doc_type]
        sign *= flip
        out.append(
            VatFact(
                document_id=shown.id,
                number=shown.number,
                doc_type=shown.doc_type,
                doc_date=shown.doc_date,
                party_id=shown.party_id or line.party_id,
                side=side,
                code=line.tax_code or "",
                net=(line.debit + line.credit) * sign,
                vat=line.vat_amount * sign,
                emirate=emirate_of[original.branch_id],
            )
        )
    return out
