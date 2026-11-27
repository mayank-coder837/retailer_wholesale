"""Bank reconciliation endpoints (P2.3): ``account.view`` to read and ``account.edit`` to import and match,
scoped to the statement's business (the bank account is part of the chart of accounts)."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, SettingsDep, TenantSessionDep
from app.modules.banking import service
from app.modules.banking.matching import BadStatementError
from app.modules.banking.schemas import (
    AutoMatchOut,
    BookLineOut,
    ImportedOut,
    MatchIn,
    ReconciliationOut,
    StatementDetailOut,
    StatementLineOut,
    StatementOut,
)
from app.modules.identity.authz import get_permissions

router = APIRouter(prefix="/bank", tags=["bank reconciliation"])
Signed = Annotated[PermissionSet, Depends(get_permissions)]


@router.post("/statements", operation_id="import_bank_statement", status_code=status.HTTP_201_CREATED)
async def import_statement(
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    settings: SettingsDep,
    business_id: Annotated[uuid.UUID, Form()],
    account_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
    opening_balance: Annotated[Decimal | None, Form()] = None,
    closing_balance: Annotated[Decimal | None, Form()] = None,
) -> ImportedOut:
    """CSV or Excel: a Date, an Amount (or Debit/Credit, Withdrawal/Deposit), and optionally Description and
    Reference. Lines already imported from an overlapping statement are skipped; the rest are matched."""
    perms.require("account.edit", business_id)
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise BadStatementError("The file is too large.", max_bytes=settings.max_upload_bytes)
    return await service.import_statement(
        session,
        principal.tenant_id,
        business_id,
        account_id,
        filename=file.filename or "statement",
        data=data,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        user_id=principal.user_id,
    )


@router.get("/statements", operation_id="list_bank_statements")
async def list_statements(
    perms: Signed, session: TenantSessionDep, business_id: uuid.UUID, account_id: uuid.UUID | None = None
) -> list[StatementOut]:
    perms.require_see("account.view", business_id)
    return await service.list_statements(session, business_id, account_id)


@router.get("/statements/{statement_id}", operation_id="get_bank_statement")
async def get_statement(
    statement_id: uuid.UUID, perms: Signed, session: TenantSessionDep
) -> StatementDetailOut:
    statement = await service.get_statement(session, statement_id)
    perms.require_see("account.view", statement.business_id)
    return await service.statement_detail(session, statement_id)


@router.post("/statements/{statement_id}/auto-match", operation_id="auto_match_bank_statement")
async def auto_match(
    statement_id: uuid.UUID, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> AutoMatchOut:
    statement = await service.get_statement(session, statement_id)
    perms.require("account.edit", statement.business_id)
    return await service.auto_match_statement(session, statement_id, user_id=principal.user_id)


@router.post("/lines/{line_id}/match", operation_id="match_bank_line")
async def match_line(
    line_id: uuid.UUID, body: MatchIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> StatementLineOut:
    line = await service.get_line(session, line_id)
    perms.require("account.edit", line.business_id)
    return await service.match(session, line_id, body.book_line_id, user_id=principal.user_id)


@router.post("/lines/{line_id}/unmatch", operation_id="unmatch_bank_line")
async def unmatch_line(line_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> StatementLineOut:
    line = await service.get_line(session, line_id)
    perms.require("account.edit", line.business_id)
    return await service.unmatch(session, line_id)


@router.get("/accounts/{account_id}/unmatched-book-lines", operation_id="unmatched_book_lines")
async def unmatched_book_lines(
    account_id: uuid.UUID,
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    date_to: date | None = None,
) -> list[BookLineOut]:
    """What is in the books for this bank account and not yet matched to a statement line."""
    perms.require_see("account.view", business_id)
    return await service.unmatched_book_lines(session, business_id, account_id, date_to)


@router.get("/accounts/{account_id}/reconciliation", operation_id="bank_reconciliation")
async def reconciliation(
    account_id: uuid.UUID,
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    as_of: date | None = None,
) -> ReconciliationOut:
    perms.require_see("account.view", business_id)
    return await service.reconciliation(session, business_id, account_id, as_of)
