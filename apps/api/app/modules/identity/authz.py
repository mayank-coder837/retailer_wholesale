"""Loads the caller's grants and provides ``require("resource.action")`` for routes (M0.3)."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import MembershipInactiveError, PermissionSet, Scope
from app.core.deps import PrincipalDep, TenantSessionDep
from app.core.permissions import ALL_PERMISSIONS, is_known
from app.core.security import Principal
from app.modules.identity.default_roles import OWNER
from app.modules.identity.models import Membership, MembershipStatus, Role, RoleAssignment, RolePermission


async def load_permissions(session: AsyncSession, principal: Principal) -> PermissionSet:
    status = await session.scalar(select(Membership.status).where(Membership.id == principal.membership_id))
    if status != MembershipStatus.ACTIVE.value:
        raise MembershipInactiveError()
    rows = await session.execute(
        select(RolePermission.permission_code, RoleAssignment.business_id, RoleAssignment.branch_id)
        .join(RoleAssignment, RoleAssignment.role_id == RolePermission.role_id)
        .where(RoleAssignment.membership_id == principal.membership_id)
    )
    grants = [(code, Scope(business, branch)) for code, business, branch in rows]
    # Owner always holds every permission in the registry, including codes added after the role was created.
    owner_scopes = await session.execute(
        select(RoleAssignment.business_id, RoleAssignment.branch_id)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(RoleAssignment.membership_id == principal.membership_id, Role.system_key == OWNER)
    )
    for business, branch in owner_scopes:
        grants.extend((code, Scope(business, branch)) for code in ALL_PERMISSIONS)
    return PermissionSet.from_grants(grants)


async def get_permissions(
    request: Request, session: TenantSessionDep, principal: PrincipalDep
) -> PermissionSet:
    """Loaded once per request, inside the request's tenant transaction."""
    cached: PermissionSet | None = getattr(request.state, "permissions", None)
    if cached is None:
        cached = await load_permissions(session, principal)
        request.state.permissions = cached
    return cached


def require(code: str) -> Any:
    """Route dependency: the caller must hold ``code`` in at least one scope.

    Returns the full set so the route can then check the exact business/branch it acts on
    (``perms.require(code, business_id, branch_id)``).
    """
    if not is_known(code):
        raise ValueError(f"unknown permission in route: {code}")

    async def dependency(perms: PermissionSet = Depends(get_permissions)) -> PermissionSet:  # noqa: B008
        perms.require_held(code)
        return perms

    checker: Callable[..., Awaitable[PermissionSet]] = dependency
    return Depends(checker)
