"""Business, branch and warehouse endpoints, with permissions scoped to business and branch (M0.3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, status

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.identity.authz import require
from app.modules.tenancy import service
from app.modules.tenancy.schemas import (
    BranchIn,
    BranchOut,
    BusinessIn,
    BusinessOut,
    WarehouseIn,
    WarehouseOut,
)

router = APIRouter(tags=["tenancy"])

BusinessView = Annotated[PermissionSet, require("business.view")]
BusinessCreate = Annotated[PermissionSet, require("business.create")]
BranchView = Annotated[PermissionSet, require("branch.view")]
BranchCreate = Annotated[PermissionSet, require("branch.create")]
WarehouseView = Annotated[PermissionSet, require("warehouse.view")]
WarehouseCreate = Annotated[PermissionSet, require("warehouse.create")]


@router.get("/businesses", operation_id="list_businesses")
async def list_businesses(perms: BusinessView, session: TenantSessionDep) -> list[BusinessOut]:
    return [
        BusinessOut.model_validate(b)
        for b in await service.list_businesses(session)
        if perms.can_see("business.view", b.id)
    ]


@router.post("/businesses", operation_id="create_business", status_code=status.HTTP_201_CREATED)
async def create_business(
    body: BusinessIn, perms: BusinessCreate, session: TenantSessionDep, principal: PrincipalDep
) -> BusinessOut:
    perms.require("business.create")
    business = await service.create_business(session, principal.tenant_id, body)
    await session.refresh(business)
    return BusinessOut.model_validate(business)


@router.get("/businesses/{business_id}", operation_id="get_business")
async def get_business(business_id: uuid.UUID, perms: BusinessView, session: TenantSessionDep) -> BusinessOut:
    business = await service.get_business(session, business_id)
    perms.require_see("business.view", business.id)
    return BusinessOut.model_validate(business)


@router.get("/businesses/{business_id}/branches", operation_id="list_branches")
async def list_branches(
    business_id: uuid.UUID, perms: BranchView, session: TenantSessionDep
) -> list[BranchOut]:
    branches = await service.list_branches(session, business_id)
    perms.require_see("branch.view", business_id)
    return [BranchOut.model_validate(b) for b in branches if perms.allows("branch.view", business_id, b.id)]


@router.post(
    "/businesses/{business_id}/branches", operation_id="create_branch", status_code=status.HTTP_201_CREATED
)
async def create_branch(
    business_id: uuid.UUID,
    body: BranchIn,
    perms: BranchCreate,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> BranchOut:
    await service.get_business(session, business_id)
    perms.require("branch.create", business_id)
    branch = await service.create_branch(session, principal.tenant_id, business_id, body)
    await session.refresh(branch)
    return BranchOut.model_validate(branch)


@router.get("/branches/{branch_id}/warehouses", operation_id="list_warehouses")
async def list_warehouses(
    branch_id: uuid.UUID, perms: WarehouseView, session: TenantSessionDep
) -> list[WarehouseOut]:
    branch = await service.get_branch(session, branch_id)
    perms.require("warehouse.view", branch.business_id, branch.id)
    return [WarehouseOut.model_validate(w) for w in await service.list_warehouses(session, branch_id)]


@router.post(
    "/branches/{branch_id}/warehouses", operation_id="create_warehouse", status_code=status.HTTP_201_CREATED
)
async def create_warehouse(
    branch_id: uuid.UUID,
    body: WarehouseIn,
    perms: WarehouseCreate,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> WarehouseOut:
    branch = await service.get_branch(session, branch_id)
    perms.require("warehouse.create", branch.business_id, branch.id)
    warehouse = await service.create_warehouse(session, principal.tenant_id, branch_id, body)
    await session.refresh(warehouse)
    return WarehouseOut.model_validate(warehouse)
