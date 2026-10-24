"""PDC register endpoints (M1.9): ``pdc.view`` to read, ``pdc.create`` to record, ``pdc.edit`` to change a
cheque's details and move it (deposit, return, cancel, bounce), ``pdc.post`` to clear it (that posts the
voucher)
and for a bounce with a bank charge. Scoped to the cheque's business and branch."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.identity.authz import get_permissions
from app.modules.pdc import service, state
from app.modules.pdc.models import PdcDirection, PdcStatus
from app.modules.pdc.schemas import BounceIn, ClearIn, DepositIn, PdcIn, PdcOut

router = APIRouter(tags=["pdc"])
Signed = Annotated[PermissionSet, Depends(get_permissions)]


@router.get("/pdcs", operation_id="list_pdcs")
async def list_pdcs(
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    direction: PdcDirection | None = None,
    status: PdcStatus | None = None,
    party_id: uuid.UUID | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[PdcOut]:
    perms.require_see("pdc.view", business_id)
    return await service.list_pdcs(
        session,
        business_id,
        direction=direction,
        status=status,
        party_id=party_id,
        due_from=due_from,
        due_to=due_to,
        limit=limit,
    )


@router.post("/pdcs", operation_id="create_pdc", status_code=status.HTTP_201_CREATED)
async def create_pdc(
    body: PdcIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> PdcOut:
    perms.require("pdc.create", body.business_id, body.branch_id)
    pdc = await service.create(session, principal.tenant_id, body)
    return await service.pdc_out(session, pdc)


@router.get("/pdcs/{pdc_id}", operation_id="get_pdc")
async def get_pdc(pdc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> PdcOut:
    pdc = await service.get_pdc(session, pdc_id)
    perms.require("pdc.view", pdc.business_id, pdc.branch_id)
    return await service.pdc_out(session, pdc)


@router.put("/pdcs/{pdc_id}", operation_id="update_pdc")
async def update_pdc(pdc_id: uuid.UUID, body: PdcIn, perms: Signed, session: TenantSessionDep) -> PdcOut:
    pdc = await service.get_pdc(session, pdc_id)
    perms.require("pdc.edit", pdc.business_id, pdc.branch_id)
    perms.require("pdc.edit", body.business_id, body.branch_id)
    return await service.pdc_out(session, await service.update(session, pdc_id, body))


@router.post("/pdcs/{pdc_id}/deposit", operation_id="deposit_pdc")
async def deposit_pdc(pdc_id: uuid.UUID, body: DepositIn, perms: Signed, session: TenantSessionDep) -> PdcOut:
    pdc = await service.get_pdc(session, pdc_id)
    perms.require("pdc.edit", pdc.business_id, pdc.branch_id)
    return await service.pdc_out(session, await service.deposit(session, pdc_id, body))


@router.post("/pdcs/{pdc_id}/clear", operation_id="clear_pdc")
async def clear_pdc(
    pdc_id: uuid.UUID, body: ClearIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> PdcOut:
    pdc = await service.get_pdc(session, pdc_id)
    perms.require("pdc.post", pdc.business_id, pdc.branch_id)
    cleared = await service.clear(
        session,
        pdc_id,
        body,
        node_id=principal.node_id,
        user_id=principal.user_id,
        may_override_lock=perms.allows("period.override_lock", pdc.business_id),
    )
    return await service.pdc_out(session, cleared)


@router.post("/pdcs/{pdc_id}/bounce", operation_id="bounce_pdc")
async def bounce_pdc(
    pdc_id: uuid.UUID, body: BounceIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> PdcOut:
    pdc = await service.get_pdc(session, pdc_id)
    perms.require("pdc.edit", pdc.business_id, pdc.branch_id)
    if body.charge is not None:
        perms.require("pdc.post", pdc.business_id, pdc.branch_id)
    bounced = await service.bounce(
        session, pdc_id, body, node_id=principal.node_id, user_id=principal.user_id
    )
    return await service.pdc_out(session, bounced)


@router.post("/pdcs/{pdc_id}/return", operation_id="return_pdc")
async def return_pdc(pdc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> PdcOut:
    pdc = await service.get_pdc(session, pdc_id)
    perms.require("pdc.edit", pdc.business_id, pdc.branch_id)
    return await service.pdc_out(session, await service.give_back(session, pdc_id, state.Action.RETURN))


@router.post("/pdcs/{pdc_id}/cancel", operation_id="cancel_pdc")
async def cancel_pdc(pdc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> PdcOut:
    pdc = await service.get_pdc(session, pdc_id)
    perms.require("pdc.edit", pdc.business_id, pdc.branch_id)
    return await service.pdc_out(session, await service.give_back(session, pdc_id, state.Action.CANCEL))
