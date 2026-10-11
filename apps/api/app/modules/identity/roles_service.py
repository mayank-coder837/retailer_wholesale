"""Roles, role permissions and role assignments (M0.3).

Guards:
- the Owner role cannot be changed, and system roles cannot be deleted;
- nobody can grant or take away a role whose permissions they do not hold themselves in that scope;
- the tenant always keeps at least one active, tenant-wide Owner.
"""

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PermissionDeniedError, PermissionSet, Scope
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.core.permissions import RESOURCES, SPECIAL, is_known
from app.core.security import hash_password
from app.modules.identity.default_roles import OWNER, default_roles
from app.modules.identity.models import (
    Membership,
    MembershipStatus,
    Role,
    RoleAssignment,
    RolePermission,
    UserAccount,
)
from app.modules.identity.roles_schemas import (
    AssignmentIn,
    AssignmentOut,
    MemberIn,
    MemberOut,
    PermissionOut,
    RoleIn,
    RoleOut,
    RolePatch,
)
from app.modules.tenancy import service as tenancy


class OwnerRoleLockedError(ConflictError):
    code = "owner_role_locked"
    title = "The Owner role always has every permission"


class SystemRoleError(ConflictError):
    code = "system_role"
    title = "Default roles cannot be deleted"


class RoleInUseError(ConflictError):
    code = "role_in_use"
    title = "Remove this role from all users first"


class RoleNameTakenError(ConflictError):
    code = "role_name_taken"
    title = "A role with this name already exists"


class LastOwnerError(ConflictError):
    code = "last_owner"
    title = "The account must keep at least one Owner"


class AlreadyMemberError(ConflictError):
    code = "already_member"
    title = "This user already has access to the account"


class UnknownPermissionError(UnprocessableError):
    code = "unknown_permission"
    title = "Unknown permission"


class InvalidScopeError(UnprocessableError):
    code = "invalid_scope"
    title = "The branch does not belong to that business"


# --- Permissions and roles -------------------------------------------------------------------------------


def list_permissions() -> list[PermissionOut]:
    """In registry order (platform, masters, documents, vouchers, …), so screens can group them logically."""
    out = [
        PermissionOut(code=f"{resource}.{action}", resource=resource, action=action)
        for resource, actions in RESOURCES.items()
        for action in actions
    ]
    for code in SPECIAL:
        resource, _, action = code.partition(".")
        out.append(PermissionOut(code=code, resource=resource, action=action))
    return out


def _validate_codes(codes: Iterable[str]) -> list[str]:
    unknown = sorted({c for c in codes if not is_known(c)})
    if unknown:
        raise UnknownPermissionError(unknown=unknown)
    return sorted(set(codes))


async def _role_permissions(
    session: AsyncSession, role_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    rows = await session.execute(
        select(RolePermission.role_id, RolePermission.permission_code).where(
            RolePermission.role_id.in_(role_ids)
        )
    )
    out: dict[uuid.UUID, list[str]] = {rid: [] for rid in role_ids}
    for rid, code in rows:
        out[rid].append(code)
    return {rid: sorted(codes) for rid, codes in out.items()}


def _role_out(role: Role, permissions: list[str]) -> RoleOut:
    return RoleOut(
        id=role.id,
        system_key=role.system_key,
        name_en=role.name_en,
        name_ar=role.name_ar,
        description=role.description,
        permissions=permissions,
        editable=role.system_key != OWNER,
    )


async def list_roles(session: AsyncSession) -> list[RoleOut]:
    """Default roles in seed order (Owner first), then custom roles by name."""
    seed_order = case(
        {r.system_key: i for i, r in enumerate(default_roles())},
        value=Role.system_key,
        else_=len(default_roles()),
    )
    roles = (await session.scalars(select(Role).order_by(seed_order, Role.name_en))).all()
    perms = await _role_permissions(session, [r.id for r in roles])
    return [_role_out(r, perms[r.id]) for r in roles]


async def _get_role(session: AsyncSession, role_id: uuid.UUID) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise NotFoundError("Role not found.")
    return role


async def get_role(session: AsyncSession, role_id: uuid.UUID) -> RoleOut:
    role = await _get_role(session, role_id)
    return _role_out(role, (await _role_permissions(session, [role.id]))[role.id])


async def _set_role_permissions(
    session: AsyncSession, tenant_id: uuid.UUID, role_id: uuid.UUID, codes: Iterable[str]
) -> None:
    await session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
    session.add_all(
        RolePermission(id=new_id(), tenant_id=tenant_id, role_id=role_id, permission_code=code)
        for code in codes
    )
    await session.flush()


async def _flush_role(session: AsyncSession, role: Role) -> None:
    try:
        async with session.begin_nested():
            session.add(role)
            await session.flush()
    except IntegrityError as exc:
        raise RoleNameTakenError() from exc


async def create_role(session: AsyncSession, tenant_id: uuid.UUID, data: RoleIn) -> RoleOut:
    codes = _validate_codes(data.permissions)
    role = Role(
        id=new_id(),
        tenant_id=tenant_id,
        name_en=data.name_en,
        name_ar=data.name_ar,
        description=data.description,
    )
    await _flush_role(session, role)
    await _set_role_permissions(session, tenant_id, role.id, codes)
    return _role_out(role, codes)


async def update_role(
    session: AsyncSession, tenant_id: uuid.UUID, role_id: uuid.UUID, data: RolePatch
) -> RoleOut:
    role = await _get_role(session, role_id)
    if role.system_key == OWNER:
        raise OwnerRoleLockedError()
    for field in ("name_en", "name_ar", "description"):
        value = getattr(data, field)
        if value is not None:
            setattr(role, field, value)
    await _flush_role(session, role)
    if data.permissions is not None:
        await _set_role_permissions(session, tenant_id, role.id, _validate_codes(data.permissions))
    return await get_role(session, role.id)


async def delete_role(session: AsyncSession, role_id: uuid.UUID) -> None:
    role = await _get_role(session, role_id)
    if role.system_key is not None:
        raise SystemRoleError()
    in_use = await session.scalar(select(func.count()).where(RoleAssignment.role_id == role_id))
    if in_use:
        raise RoleInUseError()
    await session.delete(role)
    await session.flush()


async def create_default_roles(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Called for every new tenant. Returns role ids by system key."""
    ids: dict[str, uuid.UUID] = {}
    for default in default_roles():
        role = Role(
            id=new_id(),
            tenant_id=tenant_id,
            system_key=default.system_key,
            name_en=default.name_en,
            name_ar=default.name_ar,
            description=default.description,
        )
        session.add(role)
        await session.flush()
        await _set_role_permissions(session, tenant_id, role.id, sorted(default.permissions))
        ids[default.system_key] = role.id
    return ids


# --- Assignments and members ---------------------------------------------------------------------------


async def _check_scope(session: AsyncSession, a: AssignmentIn) -> None:
    if a.branch_id is not None and a.business_id is None:
        raise InvalidScopeError("A branch assignment needs its business.")
    if a.business_id is not None:
        await tenancy.get_business(session, a.business_id)
    if a.branch_id is not None:
        branch = await tenancy.get_branch(session, a.branch_id)
        if branch.business_id != a.business_id:
            raise InvalidScopeError()


async def _guard_grantable(
    session: AsyncSession, caller: PermissionSet, assignments: Iterable[tuple[uuid.UUID, Scope]]
) -> None:
    """No privilege escalation: every permission of every role touched must be held by the caller there."""
    pairs = list(assignments)
    perms = await _role_permissions(session, sorted({rid for rid, _ in pairs}))
    for role_id, scope in pairs:
        missing = [
            c for c in perms.get(role_id, []) if not caller.allows(c, scope.business_id, scope.branch_id)
        ]
        if missing:
            raise PermissionDeniedError(
                "You cannot grant or remove a role with permissions you do not have.", missing=missing[:10]
            )


async def _guard_owner_remains(session: AsyncSession) -> None:
    owners = await session.scalar(
        select(func.count())
        .select_from(RoleAssignment)
        .join(Role, Role.id == RoleAssignment.role_id)
        .join(Membership, Membership.id == RoleAssignment.membership_id)
        .where(
            Role.system_key == OWNER,
            RoleAssignment.business_id.is_(None),
            Membership.status == MembershipStatus.ACTIVE.value,
        )
    )
    if not owners:
        raise LastOwnerError()


async def _current_assignments(session: AsyncSession, membership_id: uuid.UUID) -> list[RoleAssignment]:
    stmt = select(RoleAssignment).where(RoleAssignment.membership_id == membership_id)
    return list((await session.scalars(stmt)).all())


async def set_assignments(
    session: AsyncSession,
    caller: PermissionSet,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    assignments: Sequence[AssignmentIn],
) -> None:
    if await session.get(Membership, membership_id) is None:
        raise NotFoundError("Member not found.")
    for a in assignments:
        await _get_role(session, a.role_id)
        await _check_scope(session, a)
    current = await _current_assignments(session, membership_id)
    await _guard_grantable(
        session,
        caller,
        [(c.role_id, Scope(c.business_id, c.branch_id)) for c in current]
        + [(a.role_id, Scope(a.business_id, a.branch_id)) for a in assignments],
    )
    await session.execute(delete(RoleAssignment).where(RoleAssignment.membership_id == membership_id))
    unique = {(a.role_id, a.business_id, a.branch_id) for a in assignments}
    session.add_all(
        RoleAssignment(
            id=new_id(),
            tenant_id=tenant_id,
            membership_id=membership_id,
            role_id=role_id,
            business_id=business_id,
            branch_id=branch_id,
        )
        for role_id, business_id, branch_id in unique
    )
    await session.flush()
    await _guard_owner_remains(session)


async def assign_owner(
    session: AsyncSession, tenant_id: uuid.UUID, membership_id: uuid.UUID, owner_role_id: uuid.UUID
) -> None:
    session.add(
        RoleAssignment(id=new_id(), tenant_id=tenant_id, membership_id=membership_id, role_id=owner_role_id)
    )
    await session.flush()


async def list_members(session: AsyncSession) -> list[MemberOut]:
    members = (
        await session.execute(
            select(Membership, UserAccount)
            .join(UserAccount, UserAccount.id == Membership.user_id)
            .order_by(UserAccount.full_name)
        )
    ).all()
    rows = await session.execute(
        select(RoleAssignment, Role).join(Role, Role.id == RoleAssignment.role_id).order_by(Role.name_en)
    )
    by_member: dict[uuid.UUID, list[AssignmentOut]] = {}
    for assignment, role in rows:
        by_member.setdefault(assignment.membership_id, []).append(
            AssignmentOut(
                role_id=role.id,
                role_name_en=role.name_en,
                role_name_ar=role.name_ar,
                business_id=assignment.business_id,
                branch_id=assignment.branch_id,
            )
        )
    return [
        MemberOut(
            membership_id=m.id,
            user_id=u.id,
            email=u.email,
            full_name=u.full_name,
            status=m.status,
            assignments=by_member.get(m.id, []),
        )
        for m, u in members
    ]


async def add_member(
    session: AsyncSession, caller: PermissionSet, tenant_id: uuid.UUID, data: MemberIn
) -> MemberOut:
    """Adds a staff login (like a Tally user). An existing user keeps their password and gains access."""
    user = (await session.scalars(select(UserAccount).where(UserAccount.email == data.email))).first()
    if user is None:
        user = UserAccount(
            id=new_id(),
            email=data.email,
            password_hash=hash_password(data.password),
            full_name=data.full_name,
        )
        session.add(user)
        await session.flush()
    elif await session.scalar(select(Membership.id).where(Membership.user_id == user.id)):
        raise AlreadyMemberError()
    membership = Membership(
        id=new_id(), tenant_id=tenant_id, user_id=user.id, status=MembershipStatus.ACTIVE.value
    )
    session.add(membership)
    await session.flush()
    await set_assignments(session, caller, tenant_id, membership.id, data.assignments)
    return next(m for m in await list_members(session) if m.membership_id == membership.id)
