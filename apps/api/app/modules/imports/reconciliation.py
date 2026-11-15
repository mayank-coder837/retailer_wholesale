"""Did the books come across right? (M1.12b) The imported trial balance against the books.

The file's figures are remembered on the batch; the books' figures are read from the ledgers (the trial
balance on the opening date), never from the file. Receivable, payable and stock accounts are compared too:
their books balance comes from the imported bills and stock, so a missing or wrong bill shows here as a
difference. Whatever does not tie stays visible in ``opening_balance_equity`` rather than being forced to
zero (question R13).
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounting import service as accounting
from app.modules.accounting.models import Role
from app.modules.imports.models import BatchStatus, ImportBatch
from app.modules.imports.schemas import ReconciliationOut, ReconciliationRowOut

ZERO = Decimal("0.0000")
TRIAL_BALANCE_KIND = "opening_balances"


async def latest_trial_balance(session: AsyncSession, business_id: uuid.UUID) -> ImportBatch | None:
    return (
        await session.scalars(
            select(ImportBatch)
            .where(
                ImportBatch.business_id == business_id,
                ImportBatch.kind == TRIAL_BALANCE_KIND,
                ImportBatch.status == BatchStatus.COMMITTED.value,
            )
            .order_by(ImportBatch.committed_at.desc())
            .limit(1)
        )
    ).first()


async def reconcile(session: AsyncSession, business_id: uuid.UUID) -> ReconciliationOut:
    batch = await latest_trial_balance(session, business_id)
    if batch is None:
        return ReconciliationOut(
            business_id=business_id,
            imported=False,
            batch_id=None,
            opening_date=None,
            rows=[],
            file_debit=ZERO,
            file_credit=ZERO,
            books_debit=ZERO,
            books_credit=ZERO,
            equity_code=None,
            equity_balance=ZERO,
            ties=False,
        )
    day = date.fromisoformat(batch.result["opening_date"])
    book = await accounting.trial_balance(session, business_id, day)
    balances = {r.account_id: r.debit - r.credit for r in book.rows}
    names = {a.id: a for a in await accounting.chart_accounts(session, business_id)}
    equity_id = (await accounting.role_accounts(session, business_id))[Role.OPENING_BALANCE_EQUITY]
    equity_balance = balances.pop(equity_id, ZERO)

    rows: list[ReconciliationRowOut] = []
    for expected in batch.result["expected"]:
        account_id = uuid.UUID(expected["account_id"])
        debit, credit = Decimal(expected["debit"]), Decimal(expected["credit"])
        books = balances.pop(account_id, ZERO)
        rows.append(
            ReconciliationRowOut(
                account_id=account_id,
                code=expected["code"],
                name_en=expected["name_en"],
                name_ar=expected["name_ar"],
                control=expected["control"],
                file_debit=debit,
                file_credit=credit,
                file_balance=debit - credit,
                books_balance=books,
                difference=books - (debit - credit),
            )
        )
    for account_id, books in balances.items():  # in the books but not in the file
        if books:
            named = names[account_id]
            rows.append(
                ReconciliationRowOut(
                    account_id=account_id,
                    code=named.code,
                    name_en=named.name_en,
                    name_ar=named.name_ar,
                    control=False,
                    file_debit=ZERO,
                    file_credit=ZERO,
                    file_balance=ZERO,
                    books_balance=books,
                    difference=books,
                )
            )
    rows.sort(key=lambda r: r.code)
    equity = names[equity_id]
    return ReconciliationOut(
        business_id=business_id,
        imported=True,
        batch_id=batch.id,
        opening_date=day,
        rows=rows,
        file_debit=sum((r.file_debit for r in rows), ZERO),
        file_credit=sum((r.file_credit for r in rows), ZERO),
        books_debit=book.total_debit,
        books_credit=book.total_credit,
        equity_code=equity.code,
        equity_balance=equity_balance,
        ties=equity_balance == 0 and all(r.difference == 0 for r in rows),
    )
