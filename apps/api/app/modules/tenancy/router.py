"""Business, branch and warehouse endpoints. Tenant-scoped; role permissions are added in M0.3."""

import uuid

from fastapi import APIRouter, status

from app.core.deps import PrincipalDep, TenantSessionDep
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


@router.get("/businesses", operation_id="list_businesses")
async def list_businesses(session: TenantSessionDep) -> list[BusinessOut]:
    return [BusinessOut.model_validate(b) for b in await service.list_businesses(session)]


@router.post("/businesses", operation_id="create_business", status_code=status.HTTP_201_CREATED)
async def create_business(
    body: BusinessIn, session: TenantSessionDep, principal: PrincipalDep
) -> BusinessOut:
    business = await service.create_business(session, principal.tenant_id, body)
    await session.refresh(business)
    return BusinessOut.model_validate(business)


@router.get("/businesses/{business_id}", operation_id="get_business")
async def get_business(business_id: uuid.UUID, session: TenantSessionDep) -> BusinessOut:
    return BusinessOut.model_validate(await service.get_business(session, business_id))


@router.get("/businesses/{business_id}/branches", operation_id="list_branches")
async def list_branches(business_id: uuid.UUID, session: TenantSessionDep) -> list[BranchOut]:
    return [BranchOut.model_validate(b) for b in await service.list_branches(session, business_id)]


@router.post(
    "/businesses/{business_id}/branches", operation_id="create_branch", status_code=status.HTTP_201_CREATED
)
async def create_branch(
    business_id: uuid.UUID, body: BranchIn, session: TenantSessionDep, principal: PrincipalDep
) -> BranchOut:
    branch = await service.create_branch(session, principal.tenant_id, business_id, body)
    await session.refresh(branch)
    return BranchOut.model_validate(branch)


@router.get("/branches/{branch_id}/warehouses", operation_id="list_warehouses")
async def list_warehouses(branch_id: uuid.UUID, session: TenantSessionDep) -> list[WarehouseOut]:
    return [WarehouseOut.model_validate(w) for w in await service.list_warehouses(session, branch_id)]


@router.post(
    "/branches/{branch_id}/warehouses", operation_id="create_warehouse", status_code=status.HTTP_201_CREATED
)
async def create_warehouse(
    branch_id: uuid.UUID, body: WarehouseIn, session: TenantSessionDep, principal: PrincipalDep
) -> WarehouseOut:
    warehouse = await service.create_warehouse(session, principal.tenant_id, branch_id, body)
    await session.refresh(warehouse)
    return WarehouseOut.model_validate(warehouse)
