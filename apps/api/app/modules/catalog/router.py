"""Units of measure endpoints (M1.1). Units are tenant-wide master data."""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.catalog import service
from app.modules.catalog.schemas import ConversionOut, UnitIn, UnitOut, UnitPatch
from app.modules.identity.authz import require

router = APIRouter(tags=["units"])

UnitView = Annotated[PermissionSet, require("unit.view")]
UnitCreate = Annotated[PermissionSet, require("unit.create")]
UnitEdit = Annotated[PermissionSet, require("unit.edit")]


@router.get("/units", operation_id="list_units")
async def list_units(
    perms: UnitView, session: TenantSessionDep, include_archived: bool = False
) -> list[UnitOut]:
    perms.require_held("unit.view")
    return await service.list_units(session, include_archived=include_archived)


@router.post("/units", operation_id="create_unit", status_code=status.HTTP_201_CREATED)
async def create_unit(
    body: UnitIn, perms: UnitCreate, session: TenantSessionDep, principal: PrincipalDep
) -> UnitOut:
    perms.require("unit.create")  # tenant-wide: units are shared by every business of the tenant
    return await service.create_unit(session, principal.tenant_id, body)


@router.get("/units/convert", operation_id="convert_units")
async def convert_units(
    perms: UnitView,
    session: TenantSessionDep,
    qty: Annotated[Decimal, Query(max_digits=19, decimal_places=6)],
    from_unit: Annotated[str, Query(alias="from", max_length=20)],
    to_unit: Annotated[str, Query(alias="to", max_length=20)],
) -> ConversionOut:
    perms.require_held("unit.view")
    return await service.convert(session, qty, from_unit, to_unit)


@router.get("/units/{unit_id}", operation_id="get_unit")
async def get_unit(unit_id: uuid.UUID, perms: UnitView, session: TenantSessionDep) -> UnitOut:
    perms.require_held("unit.view")
    return await service.get_unit(session, unit_id)


@router.patch("/units/{unit_id}", operation_id="update_unit")
async def update_unit(
    unit_id: uuid.UUID, body: UnitPatch, perms: UnitEdit, session: TenantSessionDep
) -> UnitOut:
    perms.require("unit.edit")
    return await service.update_unit(session, unit_id, body)
