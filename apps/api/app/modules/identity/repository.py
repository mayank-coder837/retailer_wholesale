import uuid
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import RefreshToken, UserAccount
from app.modules.identity.schemas import MembershipOut


async def get_user_by_email(session: AsyncSession, email: str) -> UserAccount | None:
    return (await session.scalars(select(UserAccount).where(UserAccount.email == email))).first()


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> UserAccount | None:
    return await session.get(UserAccount, user_id)


async def list_memberships(session: AsyncSession, user_id: uuid.UUID) -> list[MembershipOut]:
    """Across tenants, via the SECURITY DEFINER function (the only cross-tenant read, ADR 0002)."""
    rows = await session.execute(text("SELECT * FROM auth_list_memberships(:u)"), {"u": user_id})
    return [MembershipOut.model_validate(dict(r._mapping)) for r in rows]


async def get_membership(session: AsyncSession, membership_id: uuid.UUID) -> dict[str, object] | None:
    row = (await session.execute(text("SELECT * FROM auth_get_membership(:m)"), {"m": membership_id})).first()
    return dict(row._mapping) if row else None


async def get_refresh_token_for_update(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    return (await session.scalars(stmt)).first()


async def revoke_family(session: AsyncSession, family_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
