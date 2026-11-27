"""Bank reconciliation (P2.3): import a statement, match it to the bank ledger lines, report the gap.

Reads the books only through the accounting service. A match is stored on the statement line; the ledger is
never touched.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.modules.accounting import service as accounting
from app.modules.banking import matching
from app.modules.banking.models import BankStatement, BankStatementLine
from app.modules.banking.schemas import (
    AutoMatchOut,
    BookLineOut,
    ImportedOut,
    ReconciliationOut,
    StatementDetailOut,
    StatementLineOut,
    StatementOut,
)
from app.modules.imports.readers import read_table
from app.modules.tenancy import service as tenancy

ZERO = Decimal("0.0000")


class NotABankAccountError(UnprocessableError):
    code = "not_a_bank_account"
    title = "Choose a bank account of this business"


class StatementNotFoundError(NotFoundError):
    code = "bank_statement_not_found"
    title = "There is no such bank statement"


class StatementLineNotFoundError(NotFoundError):
    code = "bank_statement_line_not_found"
    title = "There is no such statement line"


class LineAlreadyMatchedError(ConflictError):
    code = "bank_line_already_matched"
    title = "One of these is already matched"


class AmountsDifferError(UnprocessableError):
    code = "bank_match_amounts_differ"
    title = "The statement line and the ledger line are for different amounts"


class WrongAccountError(UnprocessableError):
    code = "bank_match_wrong_account"
    title = "That ledger line is not on this bank account"


async def _bank_account(session: AsyncSession, business_id: uuid.UUID, account_id: uuid.UUID) -> None:
    await tenancy.get_business(session, business_id)
    for account in await accounting.chart_accounts(session, business_id):
        if account.id == account_id and account.account_type == "bank" and not account.is_group:
            return
    raise NotABankAccountError()


async def _lines(session: AsyncSession, statement_id: uuid.UUID) -> list[BankStatementLine]:
    return list(
        await session.scalars(
            select(BankStatementLine)
            .where(BankStatementLine.statement_id == statement_id)
            .order_by(BankStatementLine.line_no)
        )
    )


async def get_statement(session: AsyncSession, statement_id: uuid.UUID) -> BankStatement:
    statement = await session.get(BankStatement, statement_id)
    if statement is None:
        raise StatementNotFoundError()
    return statement


async def _matched_book_ids(session: AsyncSession, account_id: uuid.UUID) -> set[uuid.UUID]:
    return set(
        await session.scalars(
            select(BankStatementLine.matched_line_id).where(
                BankStatementLine.account_id == account_id, BankStatementLine.matched_line_id.is_not(None)
            )
        )
    )


def _book_text(line: accounting.BookLine) -> str:
    return " ".join(part for part in (line.number, line.narration) if part)


async def _run_auto_match(
    session: AsyncSession,
    business_id: uuid.UUID,
    account_id: uuid.UUID,
    lines: list[BankStatementLine],
    *,
    user_id: uuid.UUID | None,
) -> int:
    open_lines = [line for line in lines if line.matched_line_id is None]
    if not open_lines:
        return 0
    margin = timedelta(days=matching.WINDOW_DAYS)
    taken = await _matched_book_ids(session, account_id)
    book = [
        matching.BookSide(b.line_id, b.entry_date, b.amount, _book_text(b))
        for b in await accounting.bank_book_lines(
            session,
            business_id,
            account_id,
            date_from=min(line.line_date for line in open_lines) - margin,
            date_to=max(line.line_date for line in open_lines) + margin,
        )
        if b.line_id not in taken
    ]
    sides = [
        (
            matching.StatementSide(
                line.id, line.line_date, line.amount, f"{line.reference or ''} {line.description or ''}"
            ),
            line.reference,
        )
        for line in open_lines
    ]
    by_id = {line.id: line for line in open_lines}
    pairs = matching.auto_match(sides, book)
    for line_id, book_id in pairs:
        line = by_id[line_id]
        line.matched_line_id = book_id
        line.matched_at = datetime.now(UTC)
        line.matched_by = user_id
    await session.flush()
    return len(pairs)


async def import_statement(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    business_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    filename: str,
    data: bytes,
    opening_balance: Decimal | None,
    closing_balance: Decimal | None,
    user_id: uuid.UUID | None,
) -> ImportedOut:
    await _bank_account(session, business_id, account_id)
    parsed = matching.parse_statement(read_table(filename, data))
    known = {
        (line_date, amount, reference or "", description or "")
        for line_date, amount, reference, description in (
            await session.execute(
                select(
                    BankStatementLine.line_date,
                    BankStatementLine.amount,
                    BankStatementLine.reference,
                    BankStatementLine.description,
                ).where(BankStatementLine.account_id == account_id)
            )
        )
    }
    fresh = [
        p for p in parsed if (p.line_date, p.amount, p.reference or "", p.description or "") not in known
    ]
    statement = BankStatement(
        tenant_id=tenant_id,
        business_id=business_id,
        account_id=account_id,
        filename=filename[:255],
        date_from=min(p.line_date for p in parsed),
        date_to=max(p.line_date for p in parsed),
        opening_balance=opening_balance,
        closing_balance=closing_balance,
    )
    session.add(statement)
    await session.flush()
    rows = [
        BankStatementLine(
            tenant_id=tenant_id,
            statement_id=statement.id,
            business_id=business_id,
            account_id=account_id,
            line_no=number,
            line_date=p.line_date,
            description=p.description,
            reference=p.reference,
            amount=p.amount,
        )
        for number, p in enumerate(fresh, 1)
    ]
    session.add_all(rows)
    await session.flush()
    matched = await _run_auto_match(session, business_id, account_id, rows, user_id=user_id)
    return ImportedOut(
        statement=StatementOut.model_validate(statement),
        lines=len(rows),
        duplicates_skipped=len(parsed) - len(fresh),
        auto_matched=matched,
    )


async def list_statements(
    session: AsyncSession, business_id: uuid.UUID, account_id: uuid.UUID | None
) -> list[StatementOut]:
    stmt = select(BankStatement).where(BankStatement.business_id == business_id)
    if account_id is not None:
        stmt = stmt.where(BankStatement.account_id == account_id)
    rows = await session.scalars(stmt.order_by(BankStatement.date_to.desc(), BankStatement.created_at.desc()))
    return [StatementOut.model_validate(s) for s in rows]


async def statement_detail(session: AsyncSession, statement_id: uuid.UUID) -> StatementDetailOut:
    statement = await get_statement(session, statement_id)
    return StatementDetailOut(
        **StatementOut.model_validate(statement).model_dump(),
        lines=[StatementLineOut.model_validate(line) for line in await _lines(session, statement_id)],
    )


async def auto_match_statement(
    session: AsyncSession, statement_id: uuid.UUID, *, user_id: uuid.UUID | None
) -> AutoMatchOut:
    statement = await get_statement(session, statement_id)
    lines = await _lines(session, statement_id)
    matched = await _run_auto_match(
        session, statement.business_id, statement.account_id, lines, user_id=user_id
    )
    return AutoMatchOut(
        matched=matched, unmatched_statement_lines=sum(1 for ln in lines if ln.matched_line_id is None)
    )


async def _line(session: AsyncSession, line_id: uuid.UUID) -> BankStatementLine:
    line = await session.get(BankStatementLine, line_id, with_for_update=True)
    if line is None:
        raise StatementLineNotFoundError()
    return line


async def get_line(session: AsyncSession, line_id: uuid.UUID) -> BankStatementLine:
    line = await session.get(BankStatementLine, line_id)
    if line is None:
        raise StatementLineNotFoundError()
    return line


async def match(
    session: AsyncSession, line_id: uuid.UUID, book_line_id: uuid.UUID, *, user_id: uuid.UUID | None
) -> StatementLineOut:
    line = await _line(session, line_id)
    found = await accounting.book_line_of(session, book_line_id)
    if found is None or found[0] != line.business_id or found[1] != line.account_id:
        raise WrongAccountError()
    if found[2].amount != line.amount:
        raise AmountsDifferError(statement_amount=str(line.amount), ledger_amount=str(found[2].amount))
    if line.matched_line_id is not None or book_line_id in await _matched_book_ids(session, line.account_id):
        raise LineAlreadyMatchedError()
    line.matched_line_id = book_line_id
    line.matched_at = datetime.now(UTC)
    line.matched_by = user_id
    await session.flush()
    return StatementLineOut.model_validate(line)


async def unmatch(session: AsyncSession, line_id: uuid.UUID) -> StatementLineOut:
    line = await _line(session, line_id)
    line.matched_line_id = None
    line.matched_at = None
    line.matched_by = None
    await session.flush()
    return StatementLineOut.model_validate(line)


async def unmatched_book_lines(
    session: AsyncSession, business_id: uuid.UUID, account_id: uuid.UUID, date_to: date | None
) -> list[BookLineOut]:
    await _bank_account(session, business_id, account_id)
    taken = await _matched_book_ids(session, account_id)
    return [
        BookLineOut(**vars_of(b))
        for b in await accounting.bank_book_lines(session, business_id, account_id, date_to=date_to)
        if b.line_id not in taken
    ]


def vars_of(line: accounting.BookLine) -> dict[str, object]:
    return {
        "line_id": line.line_id,
        "entry_date": line.entry_date,
        "amount": line.amount,
        "narration": line.narration,
        "number": line.number,
        "doc_type": line.doc_type,
    }


async def reconciliation(
    session: AsyncSession, business_id: uuid.UUID, account_id: uuid.UUID, as_of: date | None
) -> ReconciliationOut:
    await _bank_account(session, business_id, account_id)
    latest = (
        await session.scalars(
            select(BankStatement)
            .where(BankStatement.account_id == account_id)
            .order_by(BankStatement.date_to.desc(), BankStatement.created_at.desc())
            .limit(1)
        )
    ).first()
    day = as_of or (latest.date_to if latest else date.today())
    book_lines = await accounting.bank_book_lines(session, business_id, account_id, date_to=day)
    book_balance = sum((b.amount for b in book_lines), ZERO)
    taken = await _matched_book_ids(session, account_id)
    unmatched_book = sum((b.amount for b in book_lines if b.line_id not in taken), ZERO)
    unmatched_statement = (
        await session.scalar(
            select(func.coalesce(func.sum(BankStatementLine.amount), ZERO)).where(
                BankStatementLine.account_id == account_id,
                BankStatementLine.matched_line_id.is_(None),
                BankStatementLine.line_date <= day,
            )
        )
    ) or ZERO
    closing = latest.closing_balance if latest else None
    adjusted_bank = None if closing is None else closing + unmatched_book
    adjusted_book = book_balance + unmatched_statement
    difference = None if adjusted_bank is None else adjusted_bank - adjusted_book
    return ReconciliationOut(
        business_id=business_id,
        account_id=account_id,
        as_of=day,
        statement_closing=closing,
        book_balance=book_balance,
        unmatched_statement_total=unmatched_statement,
        unmatched_book_total=unmatched_book,
        adjusted_bank=adjusted_bank,
        adjusted_book=adjusted_book,
        difference=difference,
        reconciled=difference == 0,
    )
