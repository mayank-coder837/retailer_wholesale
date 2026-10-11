"""Roles and members admin API (M0.3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, status

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.identity import roles_service as service
from app.modules.identity.authz import require
from app.modules.identity.roles_schemas import (
    AssignmentsIn,
    MemberIn,
    MemberOut,
    PermissionOut,
    RoleIn,
    RoleOut,
    RolePatch,
)

router = APIRouter(tags=["roles"])

# Role administration is tenant-level: holding the permission in any scope gets past require(); the handlers
# then demand it tenant-wide (perms.require(code) with no business/branch).
RoleView = Annotated[PermissionSet, require("role.view")]
RoleCreate = Annotated[PermissionSet, require("role.create")]
RoleEdit = Annotated[PermissionSet, require("role.edit")]
RoleDelete = Annotated[PermissionSet, require("role.delete")]
MemberView = Annotated[PermissionSet, require("membership.view")]
MemberCreate = Annotated[PermissionSet, require("membership.create")]
MemberEdit = Annotated[PermissionSet, require("membership.edit")]


@router.get("/permissions", operation_id="list_permissions")
async def list_permissions(perms: RoleView) -> list[PermissionOut]:
    return service.list_permissions()


@router.get("/roles", operation_id="list_roles")
async def list_roles(perms: RoleView, session: TenantSessionDep) -> list[RoleOut]:
    return await service.list_roles(session)


@router.post("/roles", operation_id="create_role", status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleIn, perms: RoleCreate, session: TenantSessionDep, principal: PrincipalDep
) -> RoleOut:
    perms.require("role.create")
    return await service.create_role(session, principal.tenant_id, body)


@router.get("/roles/{role_id}", operation_id="get_role")
async def get_role(role_id: uuid.UUID, perms: RoleView, session: TenantSessionDep) -> RoleOut:
    return await service.get_role(session, role_id)


@router.patch("/roles/{role_id}", operation_id="update_role")
async def update_role(
    role_id: uuid.UUID, body: RolePatch, perms: RoleEdit, session: TenantSessionDep, principal: PrincipalDep
) -> RoleOut:
    perms.require("role.edit")
    return await service.update_role(session, principal.tenant_id, role_id, body)


@router.delete("/roles/{role_id}", operation_id="delete_role", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: uuid.UUID, perms: RoleDelete, session: TenantSessionDep) -> None:
    perms.require("role.delete")
    await service.delete_role(session, role_id)


@router.get("/memberships", operation_id="list_members")
async def list_members(perms: MemberView, session: TenantSessionDep) -> list[MemberOut]:
    return await service.list_members(session)


@router.post("/memberships", operation_id="add_member", status_code=status.HTTP_201_CREATED)
async def add_member(
    body: MemberIn, perms: MemberCreate, session: TenantSessionDep, principal: PrincipalDep
) -> MemberOut:
    return await service.add_member(session, perms, principal.tenant_id, body)


@router.put("/memberships/{membership_id}/roles", operation_id="set_member_roles")
async def set_member_roles(
    membership_id: uuid.UUID,
    body: AssignmentsIn,
    perms: MemberEdit,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> MemberOut:
    await service.set_assignments(session, perms, principal.tenant_id, membership_id, body.assignments)
    return next(m for m in await service.list_members(session) if m.membership_id == membership_id)
