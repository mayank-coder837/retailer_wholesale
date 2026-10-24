"""The journal and bills: writing posted entries, bill-wise settlement, and reading balances.

Every amount read here comes from ``journal_line`` (CLAUDE.md rule 5's money twin): balances are sums, never
stored numbers. ``bill.amount_open`` is the one cache, changed in the same transaction as its allocation.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, UnprocessableError
from app.modules.accounting import posting
from app.modules.accounting.models import (
    Account,
    AccountType,
    Bill,
    BillAllocation,
    Direction,
    EntryKind,
    JournalEntry,
    JournalLine,
)
from app.modules.documents.models import Document, DocumentStatus

ZERO = Decimal("0.0000")  # money columns have 4 decimal places


class UnbalancedEntryError(UnprocessableError):
    code = "unbalanced_entry"
    title = "Debits and credits must be equal"


class BillNotOpenError(ConflictError):
    code = "bill_not_open"
    title = "This bill cannot take that amount"


@dataclass(frozen=True, slots=True)
class EntryHeader:
    tenant_id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID
    entry_date: date
    fiscal_period_id: uuid.UUID
    number: str | None
    narration: str | None
    source_document_id: uuid.UUID | None
    kind: EntryKind = EntryKind.POSTING
    reversal_of_id: uuid.UUID | None = None  # an undo entry: the posting entry it mirrors


async def write_entry(
    session: AsyncSession, header: EntryHeader, lines: Sequence[posting.JournalLine]
) -> list[JournalLine]:
    """One balanced journal entry. The database checks the balance again at commit (migration 0009)."""
    debit = sum((line.debit for line in lines), ZERO)
    credit = sum((line.credit for line in lines), ZERO)
    if not lines or debit != credit:
        raise UnbalancedEntryError(debit=str(debit), credit=str(credit))
    entry = JournalEntry(
        tenant_id=header.tenant_id,
        business_id=header.business_id,
        branch_id=header.branch_id,
        entry_date=header.entry_date,
        fiscal_period_id=header.fiscal_period_id,
        number=header.number,
        narration=header.narration,
        source_document_id=header.source_document_id,
        kind=header.kind.value,
        reversal_of_id=header.reversal_of_id,
    )
    session.add(entry)
    await session.flush()
    rows = [
        JournalLine(
            tenant_id=header.tenant_id,
            entry_id=entry.id,
            business_id=header.business_id,
            entry_date=header.entry_date,
            line_no=n,
            account_id=line.account_id,
            party_id=line.party_id,
            debit=line.debit,
            credit=line.credit,
            description=line.description,
        )
        for n, line in enumerate(lines, start=1)
    ]
    session.add_all(rows)
    await session.flush()
    return rows


def direction_of(line: JournalLine) -> Direction:
    return Direction.DEBIT if line.debit > 0 else Direction.CREDIT


async def open_bill(
    session: AsyncSession,
    line: JournalLine,
    *,
    amount: Decimal,
    reference: str,
    due_date: date | None,
    is_advance: bool,
    document_id: uuid.UUID | None,
) -> Bill:
    """A new open item in the line's direction (a new reference or an advance)."""
    assert line.party_id is not None
    bill = Bill(
        tenant_id=line.tenant_id,
        business_id=line.business_id,
        party_id=line.party_id,
        account_id=line.account_id,
        reference=reference,
        bill_date=line.entry_date,
        due_date=due_date,
        direction=direction_of(line).value,
        amount=amount,
        amount_open=amount,
        is_advance=is_advance,
        document_id=document_id,
        journal_line_id=line.id,
    )
    session.add(bill)
    await session.flush()
    return bill


async def lock_bill(session: AsyncSession, bill_id: uuid.UUID) -> Bill | None:
    return (
        await session.scalars(
            select(Bill).where(Bill.id == bill_id).with_for_update().execution_options(populate_existing=True)
        )
    ).first()


async def settle(session: AsyncSession, bill: Bill, line: JournalLine, amount: Decimal) -> None:
    """``line`` settles ``amount`` of ``bill``: same party and account, other side, at most what is open."""
    if (bill.party_id, bill.account_id, bill.business_id) != (
        line.party_id,
        line.account_id,
        line.business_id,
    ):
        raise BillNotOpenError("The bill belongs to another party or account.", bill_id=str(bill.id))
    if bill.direction == direction_of(line).value:
        raise BillNotOpenError("A line settles bills on the other side only.", bill_id=str(bill.id))
    if amount > bill.amount_open:
        raise BillNotOpenError(
            "The amount is more than the bill's open balance.",
            bill_id=str(bill.id),
            amount_open=str(bill.amount_open),
        )
    session.add(
        BillAllocation(tenant_id=bill.tenant_id, bill_id=bill.id, journal_line_id=line.id, amount=amount)
    )
    bill.amount_open = bill.amount_open - amount
    await session.flush()


# --- undoing (Cancel and Reverse, rules 32 and 33) -----------------------------------------------------


async def posting_entries(session: AsyncSession, document_id: uuid.UUID) -> list[JournalEntry]:
    """A document's own entries (not its recosts, not undo entries), oldest first."""
    stmt = (
        select(JournalEntry)
        .where(JournalEntry.source_document_id == document_id, JournalEntry.kind == EntryKind.POSTING.value)
        .order_by(JournalEntry.posted_at, JournalEntry.id)
    )
    return list((await session.scalars(stmt)).all())


async def lines_of(session: AsyncSession, entry_id: uuid.UUID) -> list[JournalLine]:
    stmt = select(JournalLine).where(JournalLine.entry_id == entry_id).order_by(JournalLine.line_no)
    return list((await session.scalars(stmt)).all())


def mirror(lines: Sequence[JournalLine]) -> list[posting.JournalLine]:
    """The same lines with debit and credit swapped."""
    return [
        posting.JournalLine(line.account_id, line.credit, line.debit, line.party_id, line.description)
        for line in lines
    ]


async def live_allocations(session: AsyncSession, *conditions: ColumnElement[bool]) -> list[BillAllocation]:
    """Allocations that still settle their bill: not themselves undo rows, and not undone."""
    undone = select(BillAllocation.reversal_of_id).where(BillAllocation.reversal_of_id.is_not(None))
    stmt = (
        select(BillAllocation)
        .where(BillAllocation.amount > 0, BillAllocation.id.not_in(undone), *conditions)
        .order_by(BillAllocation.created_at, BillAllocation.id)
    )
    return list((await session.scalars(stmt)).all())


async def unsettle(session: AsyncSession, allocation: BillAllocation) -> Bill:
    """Undoes an allocation with a negative copy; its bill is open again by that amount."""
    bill = await lock_bill(session, allocation.bill_id)
    assert bill is not None
    session.add(
        BillAllocation(
            tenant_id=allocation.tenant_id,
            bill_id=bill.id,
            journal_line_id=allocation.journal_line_id,
            amount=-allocation.amount,
            reversal_of_id=allocation.id,
        )
    )
    bill.amount_open = bill.amount_open + allocation.amount
    await session.flush()
    return bill


async def bills_opened_by(session: AsyncSession, line_ids: Sequence[uuid.UUID]) -> list[Bill]:
    """The bills these lines opened, locked."""
    ids = (
        await session.scalars(select(Bill.id).where(Bill.journal_line_id.in_(line_ids)).order_by(Bill.id))
    ).all()
    found = [await lock_bill(session, bill_id) for bill_id in ids]
    return [bill for bill in found if bill is not None]


# --- reading -----------------------------------------------------------------------------------------------


def _balance() -> ColumnElement[Decimal]:
    return func.coalesce(func.sum(JournalLine.debit - JournalLine.credit), ZERO)


async def entries_of_document(session: AsyncSession, document_id: uuid.UUID) -> list[JournalLine]:
    stmt = (
        select(JournalLine)
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .where(JournalEntry.source_document_id == document_id)
        .order_by(JournalEntry.posted_at, JournalLine.line_no)
    )
    return list((await session.scalars(stmt)).all())


@dataclass(frozen=True, slots=True)
class TrialBalanceRow:
    account_id: uuid.UUID
    debit: Decimal
    credit: Decimal


async def trial_balance(session: AsyncSession, business_id: uuid.UUID, as_of: date) -> list[TrialBalanceRow]:
    """Each account's net balance as of a date, on its debit or credit side."""
    net = _balance().label("net")
    rows = await session.execute(
        select(JournalLine.account_id, net)
        .where(JournalLine.business_id == business_id, JournalLine.entry_date <= as_of)
        .group_by(JournalLine.account_id)
    )
    return [
        TrialBalanceRow(account_id, max(value, ZERO), max(-value, ZERO))
        for account_id, value in rows
        if value != 0
    ]


async def account_balances(session: AsyncSession, business_id: uuid.UUID) -> dict[uuid.UUID, Decimal]:
    rows = await session.execute(
        select(JournalLine.account_id, _balance())
        .where(JournalLine.business_id == business_id)
        .group_by(JournalLine.account_id)
    )
    return {account_id: value for account_id, value in rows}


async def has_postings(session: AsyncSession, account_id: uuid.UUID) -> bool:
    found = await session.scalar(select(JournalLine.id).where(JournalLine.account_id == account_id).limit(1))
    return found is not None


@dataclass(frozen=True, slots=True)
class LedgerLine:
    entry_date: date
    document_id: uuid.UUID | None
    doc_type: str | None
    number: str | None
    narration: str | None
    account_id: uuid.UUID
    party_id: uuid.UUID | None
    description: str | None
    debit: Decimal
    credit: Decimal


def _ledger_select() -> Any:  # a 10-column select; rows map onto LedgerLine
    return (
        select(
            JournalLine.entry_date,
            JournalEntry.source_document_id,
            Document.doc_type,
            JournalEntry.number,
            JournalEntry.narration,
            JournalLine.account_id,
            JournalLine.party_id,
            JournalLine.description,
            JournalLine.debit,
            JournalLine.credit,
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .outerjoin(Document, Document.id == JournalEntry.source_document_id)
        .order_by(JournalLine.entry_date, JournalEntry.posted_at, JournalLine.line_no)
    )


def _not_cancelled_pair() -> ColumnElement[bool]:
    """Hides a cancelled document's entry and its mirror (they net to nothing), so reports show it once.
    Its recosts and the stock value its mirror did not cover stay visible."""
    return or_(
        Document.status.is_(None),
        Document.status != DocumentStatus.CANCELLED.value,
        and_(JournalEntry.kind != EntryKind.POSTING.value, JournalEntry.reversal_of_id.is_(None)),
    )


async def ledger(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    account_ids: Sequence[uuid.UUID] | None = None,
    party_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[Decimal, list[LedgerLine]]:
    """Opening balance (before ``date_from``) and the lines in the range, oldest first."""
    filters = [JournalLine.business_id == business_id]
    if account_ids is not None:
        filters.append(JournalLine.account_id.in_(account_ids))
    if party_id is not None:
        filters.append(JournalLine.party_id == party_id)
    opening = ZERO
    if date_from is not None:
        opening = (
            await session.scalar(select(_balance()).where(*filters, JournalLine.entry_date < date_from))
        ) or ZERO
    in_range = list(filters)
    if date_from is not None:
        in_range.append(JournalLine.entry_date >= date_from)
    if date_to is not None:
        in_range.append(JournalLine.entry_date <= date_to)
    rows = await session.execute(_ledger_select().where(*in_range, _not_cancelled_pair()))
    return opening, [LedgerLine(*row) for row in rows]


async def open_bills(
    session: AsyncSession,
    business_id: uuid.UUID,
    party_id: uuid.UUID,
    *,
    account_id: uuid.UUID | None = None,
    include_settled: bool = False,
) -> list[Bill]:
    stmt = select(Bill).where(Bill.business_id == business_id, Bill.party_id == party_id)
    if account_id is not None:
        stmt = stmt.where(Bill.account_id == account_id)
    if not include_settled:
        stmt = stmt.where(Bill.amount_open > 0)
    return list((await session.scalars(stmt.order_by(Bill.bill_date, Bill.created_at))).all())


async def open_bills_on(
    session: AsyncSession, business_id: uuid.UUID, account_type: AccountType
) -> list[Bill]:
    """Every open bill on the business's accounts of one type (receivable or payable), oldest first."""
    accounts = select(Account.id).where(
        Account.business_id == business_id, Account.account_type == account_type.value
    )
    stmt = (
        select(Bill)
        .where(Bill.business_id == business_id, Bill.account_id.in_(accounts), Bill.amount_open > 0)
        .order_by(Bill.party_id, Bill.bill_date, Bill.created_at)
    )
    return list((await session.scalars(stmt)).all())


@dataclass(frozen=True, slots=True)
class ReceivableFigures:
    balance: Decimal
    oldest_open_bill_date: date | None


async def receivable_figures(
    session: AsyncSession, business_id: uuid.UUID, party_id: uuid.UUID
) -> ReceivableFigures:
    """What the customer owes per the ledger, and the date of its oldest unpaid invoice (credit check)."""
    receivable = select(Account.id).where(
        Account.business_id == business_id, Account.account_type == AccountType.RECEIVABLE.value
    )
    balance = await session.scalar(
        select(_balance()).where(
            JournalLine.business_id == business_id,
            JournalLine.party_id == party_id,
            JournalLine.account_id.in_(receivable),
        )
    )
    oldest = await session.scalar(
        select(func.min(Bill.bill_date)).where(
            Bill.business_id == business_id,
            Bill.party_id == party_id,
            Bill.account_id.in_(receivable),
            Bill.direction == Direction.DEBIT.value,
            Bill.is_advance.is_(False),
            Bill.amount_open > 0,
        )
    )
    return ReceivableFigures(balance or ZERO, oldest)


async def party_balance_by_side(
    session: AsyncSession, business_id: uuid.UUID, party_id: uuid.UUID
) -> dict[uuid.UUID, Decimal]:
    """The party's net balance per control account (debit positive)."""
    rows = await session.execute(
        select(JournalLine.account_id, _balance())
        .where(JournalLine.business_id == business_id, JournalLine.party_id == party_id)
        .group_by(JournalLine.account_id)
    )
    return {account_id: value for account_id, value in rows}


def signed_open(bill: Bill) -> Decimal:
    return bill.amount_open if bill.direction == Direction.DEBIT.value else -bill.amount_open
