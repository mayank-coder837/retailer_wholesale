"""Queries for tenancy tables. Row-level security already limits every query to the current tenant."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tenancy.models import Branch, Business, Node, NodeKind, Tenant, Warehouse


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant | None:
    return await session.get(Tenant, tenant_id)


async def get_business(session: AsyncSession, business_id: uuid.UUID) -> Business | None:
    return await session.get(Business, business_id)


async def list_businesses(session: AsyncSession) -> Sequence[Business]:
    return (await session.scalars(select(Business).order_by(Business.legal_name_en))).all()


async def get_branch(session: AsyncSession, branch_id: uuid.UUID) -> Branch | None:
    return await session.get(Branch, branch_id)


async def list_branches(session: AsyncSession, business_id: uuid.UUID) -> Sequence[Branch]:
    stmt = select(Branch).where(Branch.business_id == business_id).order_by(Branch.code)
    return (await session.scalars(stmt)).all()


async def list_warehouses(session: AsyncSession, branch_id: uuid.UUID) -> Sequence[Warehouse]:
    stmt = select(Warehouse).where(Warehouse.branch_id == branch_id).order_by(Warehouse.code)
    return (await session.scalars(stmt)).all()


async def get_main_node(session: AsyncSession) -> Node | None:
    """The tenant's first branch main PC: the node this local installation writes as."""
    stmt = (
        select(Node)
        .where(Node.kind == NodeKind.BRANCH_MAIN_PC.value)
        .order_by(Node.created_at, Node.id)
        .limit(1)
    )
    return (await session.scalars(stmt)).first()
