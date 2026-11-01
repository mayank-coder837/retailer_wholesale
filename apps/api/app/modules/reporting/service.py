"""Reports (M1.11): read through the accounting and parties services (one door), shaped by ``statements``."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableError
from app.modules.reporting import statements
from app.modules.reporting.schemas import (
    BalanceSheetOut,
    DayBookEntryOut,
    DayBookLineOut,
    DayBookOut,
    ProfitLossOut,
)
from app.modules.tenancy import service as tenancy

ZERO = Decimal("0.0000")
DAY_BOOK_LIMIT = 500  # entries per call; narrow the dates for more


class InvalidReportPeriodError(UnprocessableError):
    code = "invalid_report_period"
    title = "The report period is not valid"


async def _accounts(session: AsyncSession, business_id: uuid.UUID) -> list[statements.Account]:
    from app.modules.accounting import service as accounting

    return [
        statements.Account(
            id=a.id,
            code=a.code,
            name_en=a.name_en,
            name_ar=a.name_ar,
            nature=a.nature.value,
            account_type=a.account_type.value if a.account_type else None,
            is_group=a.is_group,
        )
        for a in await accounting.chart_accounts(session, business_id)
    ]


async def _balances(session: AsyncSession, business_id: uuid.UUID, as_of: date) -> dict[uuid.UUID, Decimal]:
    """Each account's balance on a date, debit positive (the trial balance's figures)."""
    from app.modules.accounting import service as accounting

    tb = await accounting.trial_balance(session, business_id, as_of)
    return {r.account_id: r.debit - r.credit for r in tb.rows}


async def _movement(
    session: AsyncSession, business_id: uuid.UUID, date_from: date, date_to: date
) -> dict[uuid.UUID, Decimal]:
    """What each account moved in the period: its balance at the end less the day before the start."""
    end = await _balances(session, business_id, date_to)
    before = await _balances(session, business_id, date_from - timedelta(days=1))
    return {k: end.get(k, ZERO) - before.get(k, ZERO) for k in end.keys() | before.keys()}


def _check_period(date_from: date, date_to: date) -> None:
    if date_to < date_from:
        raise InvalidReportPeriodError("The period ends before it starts.", field="to")


async def profit_loss(
    session: AsyncSession, business_id: uuid.UUID, date_from: date, date_to: date
) -> ProfitLossOut:
    _check_period(date_from, date_to)
    await tenancy.get_business(session, business_id)
    accounts = await _accounts(session, business_id)
    sections, gross, net = statements.profit_loss(
        accounts, await _movement(session, business_id, date_from, date_to)
    )
    return ProfitLossOut(
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
        sections=sections,
        gross_profit=gross,
        net_profit=net,
    )


async def balance_sheet(session: AsyncSession, business_id: uuid.UUID, as_of: date) -> BalanceSheetOut:
    from app.modules.accounting import service as accounting

    await tenancy.get_business(session, business_id)
    accounts = await _accounts(session, business_id)
    year_start = await accounting.fiscal_year_start(session, business_id, as_of)
    sheet = statements.balance_sheet(
        accounts,
        await _balances(session, business_id, as_of),
        await _movement(session, business_id, year_start, as_of),
    )
    return BalanceSheetOut(
        business_id=business_id,
        as_of=as_of,
        fiscal_year_start=year_start,
        assets=sheet.assets,
        liabilities=sheet.liabilities,
        equity=sheet.equity,
        total_assets=sheet.total_assets,
        total_liabilities=sheet.total_liabilities,
        total_equity=sheet.total_equity,
        balanced=sheet.balanced,
    )


async def day_book(
    session: AsyncSession, business_id: uuid.UUID, date_from: date, date_to: date
) -> DayBookOut:
    from app.modules.accounting import service as accounting
    from app.modules.parties import service as parties

    _check_period(date_from, date_to)
    await tenancy.get_business(session, business_id)
    accounts = {a.id: a for a in await _accounts(session, business_id)}
    names: dict[uuid.UUID, tuple[str, str]] = {}
    entries: dict[uuid.UUID, DayBookEntryOut] = {}
    truncated = False
    for line in await accounting.day_book(session, business_id, date_from, date_to):
        assert line.entry_id is not None
        entry = entries.get(line.entry_id)
        if entry is None:
            if len(entries) == DAY_BOOK_LIMIT:
                truncated = True
                break
            entry = entries[line.entry_id] = DayBookEntryOut(
                entry_id=line.entry_id,
                entry_date=line.entry_date,
                number=line.number,
                narration=line.narration,
                document_id=line.document_id,
                doc_type=line.doc_type,
                lines=[],
            )
        party: tuple[str, str] | None = None
        if line.party_id is not None:
            if line.party_id not in names:
                p = await parties.get_party(session, line.party_id)
                names[line.party_id] = (p.name_en, p.name_ar)
            party = names[line.party_id]
        account = accounts[line.account_id]
        entry.lines.append(
            DayBookLineOut(
                account_id=account.id,
                code=account.code,
                name_en=account.name_en,
                name_ar=account.name_ar,
                party_id=line.party_id,
                party_name_en=party[0] if party else None,
                party_name_ar=party[1] if party else None,
                description=line.description,
                debit=line.debit,
                credit=line.credit,
            )
        )
    shown = list(entries.values())
    return DayBookOut(
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
        entries=shown,
        total_debit=sum((x.debit for e in shown for x in e.lines), ZERO),
        total_credit=sum((x.credit for e in shown for x in e.lines), ZERO),
        truncated=truncated,
    )
