"""Accounting endpoints (M1.5): chart of accounts, roles, fiscal periods, vouchers, bills, ledgers.

Vouchers are branch-scoped: ``<type>.create / edit / delete / post`` are checked for the voucher's business
and branch. Anyone who may enter a voucher may read the chart (to pick accounts) and a party's open bills.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.authz import PermissionDeniedError, PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.accounting import accounts as chart
from app.modules.accounting import periods, service, vouchers
from app.modules.accounting.models import FiscalPeriod, FiscalYear, Role
from app.modules.accounting.posting import VoucherType
from app.modules.accounting.schemas import (
    AccountIn,
    AccountOut,
    AccountPatch,
    AccountRoleIn,
    AccountRoleOut,
    BillOut,
    FiscalPeriodOut,
    FiscalYearIn,
    FiscalYearOut,
    LedgerOut,
    PartyLedgerOut,
    PostIn,
    TrialBalanceOut,
    VoucherIn,
    VoucherOut,
    VoucherSummary,
)
from app.modules.documents.models import DocumentStatus
from app.modules.identity.authz import get_permissions, require
from app.modules.parties import service as parties
from app.modules.tenancy import service as tenancy

router = APIRouter(tags=["accounting"])
Created = status.HTTP_201_CREATED
Signed = Annotated[PermissionSet, Depends(get_permissions)]


def _may_pick_accounts(perms: PermissionSet, business_id: uuid.UUID) -> None:
    if perms.can_see("account.view", business_id):
        return
    if not any(perms.can_see(f"{t.value}.create", business_id) for t in VoucherType):
        raise PermissionDeniedError(permission="account.view")


def _fiscal_year_out(year: FiscalYear, rows: list[FiscalPeriod]) -> FiscalYearOut:
    return FiscalYearOut(
        id=year.id,
        business_id=year.business_id,
        code=year.code,
        start_date=year.start_date,
        end_date=year.end_date,
        status=year.status,
        periods=[FiscalPeriodOut.model_validate(p) for p in rows],
    )


# --- chart of accounts -------------------------------------------------------------------------------------


@router.get("/accounts", operation_id="list_accounts")
async def list_accounts(
    perms: Signed, session: TenantSessionDep, business_id: uuid.UUID, include_archived: bool = False
) -> list[AccountOut]:
    _may_pick_accounts(perms, business_id)
    await tenancy.get_business(session, business_id)
    return await chart.list_accounts(session, business_id, include_archived=include_archived)


@router.post("/accounts", operation_id="create_account", status_code=Created)
async def create_account(
    body: AccountIn,
    perms: Annotated[PermissionSet, require("account.create")],
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> AccountOut:
    perms.require("account.create", body.business_id)
    return await chart.create_account(session, principal.tenant_id, body)


@router.get("/accounts/{account_id}", operation_id="get_account")
async def get_account(account_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> AccountOut:
    account = await chart.get_account(session, account_id)
    _may_pick_accounts(perms, account.business_id)
    return await chart.get_account_out(session, account_id)


@router.patch("/accounts/{account_id}", operation_id="update_account")
async def update_account(
    account_id: uuid.UUID,
    body: AccountPatch,
    perms: Annotated[PermissionSet, require("account.edit")],
    session: TenantSessionDep,
) -> AccountOut:
    account = await chart.get_account(session, account_id)
    perms.require("account.edit", account.business_id)
    return await chart.update_account(session, account_id, body)


@router.get("/accounts/{account_id}/ledger", operation_id="account_ledger")
async def account_ledger(
    account_id: uuid.UUID,
    perms: Signed,
    session: TenantSessionDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> LedgerOut:
    account = await chart.get_account(session, account_id)
    if not (
        perms.can_see("report.view", account.business_id)
        or perms.can_see("account.view", account.business_id)
    ):
        raise PermissionDeniedError(permission="report.view")
    return await service.account_ledger(session, account_id, date_from, date_to)


@router.get("/account-roles", operation_id="list_account_roles")
async def list_account_roles(
    perms: Signed, session: TenantSessionDep, business_id: uuid.UUID
) -> list[AccountRoleOut]:
    _may_pick_accounts(perms, business_id)
    await tenancy.get_business(session, business_id)
    return await chart.list_roles(session, business_id)


@router.put("/account-roles/{role}", operation_id="set_account_role")
async def set_account_role(
    role: Role,
    body: AccountRoleIn,
    perms: Annotated[PermissionSet, require("account.edit")],
    session: TenantSessionDep,
) -> list[AccountRoleOut]:
    perms.require("account.edit", body.business_id)
    await tenancy.get_business(session, body.business_id)
    return await chart.set_role(session, body.business_id, role, body.account_id)


# --- fiscal years and periods ------------------------------------------------------------------------------


@router.get("/fiscal-years", operation_id="list_fiscal_years")
async def list_fiscal_years(
    perms: Annotated[PermissionSet, require("fiscal_period.view")],
    session: TenantSessionDep,
    business_id: uuid.UUID,
) -> list[FiscalYearOut]:
    perms.require_see("fiscal_period.view", business_id)
    await tenancy.get_business(session, business_id)
    return [_fiscal_year_out(y, p) for y, p in await periods.list_years(session, business_id)]


@router.post("/fiscal-years", operation_id="create_fiscal_year", status_code=Created)
async def create_fiscal_year(
    body: FiscalYearIn,
    perms: Annotated[PermissionSet, require("fiscal_period.edit")],
    session: TenantSessionDep,
) -> FiscalYearOut:
    perms.require("fiscal_period.edit", body.business_id)
    business = await tenancy.get_business(session, body.business_id)
    year = await periods.create_year(session, business, body.day)
    for found, rows in await periods.list_years(session, business.id):
        if found.id == year.id:
            return _fiscal_year_out(found, rows)
    raise AssertionError("the year just created is listed")


async def _lock(
    period_id: uuid.UUID,
    perms: PermissionSet,
    session: TenantSessionDep,
    principal: PrincipalDep,
    *,
    locked: bool,
) -> FiscalPeriodOut:
    period = await periods.get_period(session, period_id)
    perms.require("fiscal_period.edit", period.business_id)
    updated = await periods.set_locked(session, period, locked=locked, user_id=principal.user_id)
    return FiscalPeriodOut.model_validate(updated)


@router.put("/fiscal-periods/{period_id}/lock", operation_id="lock_fiscal_period")
async def lock_period(
    period_id: uuid.UUID,
    perms: Annotated[PermissionSet, require("fiscal_period.edit")],
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> FiscalPeriodOut:
    return await _lock(period_id, perms, session, principal, locked=True)


@router.put("/fiscal-periods/{period_id}/unlock", operation_id="unlock_fiscal_period")
async def unlock_period(
    period_id: uuid.UUID,
    perms: Annotated[PermissionSet, require("fiscal_period.edit")],
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> FiscalPeriodOut:
    return await _lock(period_id, perms, session, principal, locked=False)


# --- vouchers ----------------------------------------------------------------------------------------------


@router.get("/vouchers", operation_id="list_vouchers")
async def list_vouchers(
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    doc_type: VoucherType | None = None,
    status: DocumentStatus | None = None,
    party_id: uuid.UUID | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[VoucherSummary]:
    wanted = [doc_type] if doc_type else list(VoucherType)
    visible = [t for t in wanted if perms.can_see(f"{t.value}.view", business_id)]
    if not visible:
        raise PermissionDeniedError(permission=f"{(doc_type or VoucherType.JOURNAL).value}.view")
    await tenancy.get_business(session, business_id)
    return await vouchers.list_vouchers(
        session,
        business_id,
        doc_types=visible,
        status=status,
        party_id=party_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )


async def _post(
    doc_id: uuid.UUID, body: PostIn, perms: PermissionSet, session: TenantSessionDep, principal: PrincipalDep
) -> VoucherOut:
    doc = await vouchers.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.post", doc.business_id, doc.branch_id)
    await vouchers.post(
        session,
        doc_id,
        node_id=principal.node_id,
        user_id=principal.user_id,
        may_override_lock=perms.allows("period.override_lock", doc.business_id),
        override_reason=body.override_lock_reason,
    )
    return await vouchers.get_voucher(session, doc_id)


@router.post("/vouchers", operation_id="create_voucher", status_code=Created)
async def create_voucher(
    body: VoucherIn,
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    post: Annotated[bool, Query(description="Save and post in one step")] = False,
) -> VoucherOut:
    perms.require(f"{body.doc_type.value}.create", body.business_id, body.branch_id)
    if post:
        perms.require(f"{body.doc_type.value}.post", body.business_id, body.branch_id)
    doc = await vouchers.create_draft(session, principal.tenant_id, body)
    if post:
        return await _post(doc.id, PostIn(), perms, session, principal)
    return await vouchers.get_voucher(session, doc.id)


@router.get("/vouchers/{doc_id}", operation_id="get_voucher")
async def get_voucher(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> VoucherOut:
    doc = await vouchers.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.view", doc.business_id, doc.branch_id)
    return await vouchers.get_voucher(session, doc_id)


@router.put("/vouchers/{doc_id}", operation_id="update_voucher")
async def update_voucher(
    doc_id: uuid.UUID, body: VoucherIn, perms: Signed, session: TenantSessionDep
) -> VoucherOut:
    doc = await vouchers.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.edit", doc.business_id, doc.branch_id)
    perms.require(f"{doc.doc_type}.edit", body.business_id, body.branch_id)
    await vouchers.replace_draft(session, doc_id, body)
    return await vouchers.get_voucher(session, doc_id)


@router.delete("/vouchers/{doc_id}", operation_id="delete_voucher", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voucher(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> Response:
    doc = await vouchers.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.delete", doc.business_id, doc.branch_id)
    await vouchers.delete_draft(session, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/vouchers/{doc_id}/post", operation_id="post_voucher")
async def post_voucher(
    doc_id: uuid.UUID, body: PostIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> VoucherOut:
    return await _post(doc_id, body, perms, session, principal)


# --- bills, party ledger, trial balance --------------------------------------------------------------------


async def _may_see_party(perms: PermissionSet, session: TenantSessionDep, party_id: uuid.UUID) -> None:
    party = await parties.get_party_row(session, party_id)
    if not (
        (party.is_customer and perms.holds("customer.view"))
        or (party.is_supplier and perms.holds("supplier.view"))
    ):
        raise PermissionDeniedError(permission="customer.view")


@router.get("/bills", operation_id="list_bills")
async def list_bills(
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    party_id: uuid.UUID,
    account_id: uuid.UUID | None = None,
    include_settled: bool = False,
) -> list[BillOut]:
    await _may_see_party(perms, session, party_id)
    await tenancy.get_business(session, business_id)
    return await service.list_bills(
        session, business_id, party_id, account_id=account_id, include_settled=include_settled
    )


@router.get("/parties/{party_id}/ledger", operation_id="party_ledger")
async def party_ledger(
    party_id: uuid.UUID,
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> PartyLedgerOut:
    await _may_see_party(perms, session, party_id)
    return await service.party_ledger(session, business_id, party_id, date_from, date_to)


@router.get("/trial-balance", operation_id="trial_balance")
async def trial_balance(
    perms: Annotated[PermissionSet, require("report.view")],
    session: TenantSessionDep,
    business_id: uuid.UUID,
    as_of: date | None = None,
) -> TrialBalanceOut:
    perms.require_see("report.view", business_id)
    business = await tenancy.get_business(session, business_id)
    return await service.trial_balance(session, business_id, as_of or periods.business_today(business))
