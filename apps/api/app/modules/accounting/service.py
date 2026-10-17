"""Accounting services other modules call (never the tables), and the ledger read models (M1.5).

- ``install_for_business``: the default chart and roles for a new business (tenancy calls it).
- ``receivable_figures``: a customer's ledger balance and oldest unpaid invoice (the credit check, M1.4).
- Trial balance, account and party ledgers, open bills: read straight from ``journal_line`` and ``bill``.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting import accounts as chart
from app.modules.accounting import ledger, periods
from app.modules.accounting.models import CONTROL, AccountType, Nature
from app.modules.accounting.schemas import (
    BillOut,
    LedgerLineOut,
    LedgerOut,
    PartyLedgerOut,
    TrialBalanceOut,
    TrialBalanceRowOut,
)
from app.modules.tenancy import service as tenancy

ZERO = Decimal("0.0000")  # money columns have 4 decimal places


async def install_for_business(session: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID) -> None:
    await chart.install_chart(session, tenant_id, business_id)


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
    return PartyLedgerOut(
        **base.model_dump(),
        open_bills=[BillOut.model_validate(b) for b in bills],
        on_account=on_account,
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
