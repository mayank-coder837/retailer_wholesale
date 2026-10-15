"""Sign-up, login, refresh-token rotation and tenant switching (ADR 0007).

Failures that must be remembered (failed-login counters, revoking a stolen token family) are *returned* as
errors instead of raised, so the caller can commit them before reporting the error.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditContext
from app.core.audit import set_context as set_audit_context
from app.core.config import Settings
from app.core.db import apply_context
from app.core.errors import (
    AccountLockedError,
    DomainError,
    EmailTakenError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    RefreshTokenReusedError,
    TenantSelectionRequiredError,
    UnauthorizedError,
)
from app.core.ids import new_id
from app.core.security import (
    Principal,
    create_access_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    new_refresh_token,
    verify_password,
)
from app.modules.identity import repository as repo
from app.modules.identity import roles_service
from app.modules.identity.default_roles import OWNER
from app.modules.identity.models import ClientKind, Membership, MembershipStatus, RefreshToken, UserAccount
from app.modules.identity.schemas import LoginIn, MembershipOut, MeOut, SignupIn, TenantOut, UserOut
from app.modules.tenancy import service as tenancy


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    client: ClientKind


def _now() -> datetime:
    return datetime.now(UTC)


async def _issue(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    membership_id: uuid.UUID,
    client: ClientKind,
    family_id: uuid.UUID | None = None,
) -> IssuedTokens:
    """Creates a refresh token (in ``family_id``, or a new family) and a matching access token."""
    await apply_context(session, tenant_id=tenant_id, user_id=user_id)
    node = await tenancy.get_main_node(session)
    family = family_id or new_id()
    raw, token_hash = new_refresh_token()
    session.add(
        RefreshToken(
            id=new_id(),
            user_id=user_id,
            family_id=family,
            session_tenant_id=tenant_id,
            membership_id=membership_id,
            token_hash=token_hash,
            client=client.value,
            expires_at=_now() + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    await session.flush()
    principal = Principal(
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=membership_id,
        node_id=node.id if node else None,
        session_id=family,
        client=client.value,
    )
    return IssuedTokens(
        access_token=create_access_token(principal, settings),
        refresh_token=raw,
        expires_in=settings.access_token_ttl_s,
        tenant_id=tenant_id,
        membership_id=membership_id,
        client=client,
    )


async def signup(session: AsyncSession, settings: Settings, data: SignupIn) -> IssuedTokens:
    if await repo.get_user_by_email(session, data.email):
        raise EmailTakenError()
    user = UserAccount(
        id=new_id(),
        email=data.email,
        password_hash=hash_password(data.password),
        full_name=data.full_name,
        locale=data.locale,
    )
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError as exc:  # concurrent sign-up with the same email
        raise EmailTakenError() from exc
    set_audit_context(session, AuditContext(actor_user_id=user.id, client=data.client.value))

    new_tenant = await tenancy.create_tenant(
        session,
        tenant_name=data.tenant_name,
        business=data.business,
        head_office=data.head_office,
        user_id=user.id,
    )
    membership = Membership(
        id=new_id(), tenant_id=new_tenant.tenant_id, user_id=user.id, status=MembershipStatus.ACTIVE.value
    )
    session.add(membership)
    await session.flush()
    role_ids = await roles_service.create_default_roles(session, new_tenant.tenant_id)
    await roles_service.assign_owner(session, new_tenant.tenant_id, membership.id, role_ids[OWNER])
    return await _issue(
        session,
        settings,
        user_id=user.id,
        tenant_id=new_tenant.tenant_id,
        membership_id=membership.id,
        client=data.client,
    )


def _pick_membership(
    memberships: list[MembershipOut], tenant_id: uuid.UUID | None
) -> MembershipOut | DomainError:
    active = [m for m in memberships if m.status == MembershipStatus.ACTIVE.value]
    if tenant_id is not None:
        chosen = next((m for m in active if m.tenant_id == tenant_id), None)
        return chosen or UnauthorizedError("You do not have access to that account.")
    if len(active) == 1:
        return active[0]
    if not active:
        return UnauthorizedError("Your user has no active account.")
    return TenantSelectionRequiredError(
        memberships=[{"tenant_id": str(m.tenant_id), "tenant_name": m.tenant_name} for m in active]
    )


async def login(session: AsyncSession, settings: Settings, data: LoginIn) -> IssuedTokens | DomainError:
    now = _now()
    user = await repo.get_user_by_email(session, data.email)
    if user is None or user.disabled_at is not None:
        verify_password(
            _DUMMY_HASH, data.password
        )  # same cost as a real check: no user enumeration by timing
        return InvalidCredentialsError()
    if user.locked_until is not None and user.locked_until > now:
        return AccountLockedError()
    if not verify_password(user.password_hash, data.password):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_failures:
            user.failed_logins = 0
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
        return InvalidCredentialsError()

    user.failed_logins = 0
    user.locked_until = None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)

    chosen = _pick_membership(await repo.list_memberships(session, user.id), data.tenant_id)
    if isinstance(chosen, DomainError):
        return chosen
    return await _issue(
        session,
        settings,
        user_id=user.id,
        tenant_id=chosen.tenant_id,
        membership_id=chosen.membership_id,
        client=data.client,
    )


_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


async def refresh(session: AsyncSession, settings: Settings, raw_token: str) -> IssuedTokens | DomainError:
    now = _now()
    token = await repo.get_refresh_token_for_update(session, hash_refresh_token(raw_token))
    if token is None or token.revoked_at is not None:
        return InvalidRefreshTokenError()
    if token.rotated_at is not None:
        # A token that was already exchanged is being used again: assume it was stolen, end the whole session.
        await repo.revoke_family(session, token.family_id, now)
        return RefreshTokenReusedError()
    if token.expires_at <= now:
        return InvalidRefreshTokenError()

    membership = await repo.get_membership(session, token.membership_id)
    user = await repo.get_user(session, token.user_id)
    if (
        membership is None
        or membership["status"] != MembershipStatus.ACTIVE.value
        or user is None
        or user.disabled_at is not None
    ):
        await repo.revoke_family(session, token.family_id, now)
        return InvalidRefreshTokenError()

    token.rotated_at = now
    return await _issue(
        session,
        settings,
        user_id=token.user_id,
        tenant_id=token.session_tenant_id,
        membership_id=token.membership_id,
        client=ClientKind(token.client),
        family_id=token.family_id,
    )


async def logout(session: AsyncSession, raw_token: str) -> None:
    token = await repo.get_refresh_token_for_update(session, hash_refresh_token(raw_token))
    if token is not None:
        await repo.revoke_family(session, token.family_id, _now())


async def switch_tenant(
    session: AsyncSession, settings: Settings, principal: Principal, tenant_id: uuid.UUID, client: ClientKind
) -> IssuedTokens | DomainError:
    chosen = _pick_membership(await repo.list_memberships(session, principal.user_id), tenant_id)
    if isinstance(chosen, DomainError):
        return chosen
    await repo.revoke_family(session, principal.session_id, _now())
    return await _issue(
        session,
        settings,
        user_id=principal.user_id,
        tenant_id=chosen.tenant_id,
        membership_id=chosen.membership_id,
        client=client,
    )


async def me(session: AsyncSession, principal: Principal) -> MeOut:
    user = await repo.get_user(session, principal.user_id)
    if user is None:
        raise UnauthorizedError("User no longer exists.")
    tenant = await tenancy.get_tenant(session, principal.tenant_id)
    return MeOut(
        user=UserOut.model_validate(user),
        tenant=TenantOut.model_validate(tenant),
        membership_id=principal.membership_id,
        node_id=principal.node_id,
        memberships=await repo.list_memberships(session, principal.user_id),
    )
