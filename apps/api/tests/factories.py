"""Test data builders.

``seed_tenant`` creates a complete tenant (user, tenant, business, head office, warehouse, node, membership)
through the real services. ``ISOLATION_FACTORIES`` creates one extra row per tenant table in the current
tenant context; every tenant table must have an entry (tests/test_tenant_isolation.py enforces it).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.core.security import hash_password
from app.modules.identity.models import Membership, UserAccount
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Emirate, Node, NodeKind
from app.modules.tenancy.schemas import BranchIn, BusinessIn, WarehouseIn

PASSWORD = "correct horse battery"


@dataclass(frozen=True)
class TenantSeed:
    tenant_id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID
    warehouse_id: uuid.UUID
    node_id: uuid.UUID
    user_id: uuid.UUID


def business_in(name: str = "Al Noor Trading LLC") -> BusinessIn:
    return BusinessIn(legal_name_en=name, legal_name_ar="شركة النور للتجارة")


def branch_in(code: str = "HO", emirate: Emirate = Emirate.DUBAI) -> BranchIn:
    return BranchIn(code=code, name_en=f"Branch {code}", name_ar=f"فرع {code}", emirate=emirate)


async def add_user(session: AsyncSession, email: str | None = None) -> UserAccount:
    user = UserAccount(
        id=new_id(),
        email=email or f"{new_id().hex[:12]}@example.ae",
        password_hash=hash_password(PASSWORD),
        full_name="Test User",
    )
    session.add(user)
    await session.flush()
    return user


async def seed_tenant(session: AsyncSession, name: str = "Tenant") -> TenantSeed:
    """Leaves the session's transaction scoped to the new tenant."""
    user = await add_user(session)
    created = await tenancy.create_tenant(
        session, tenant_name=name, business=business_in(), head_office=branch_in(), user_id=user.id
    )
    session.add(Membership(id=new_id(), tenant_id=created.tenant_id, user_id=user.id))
    await session.flush()
    return TenantSeed(
        created.tenant_id,
        created.business_id,
        created.branch_id,
        created.warehouse_id,
        created.node_id,
        user.id,
    )


Factory = Callable[[AsyncSession, TenantSeed], Awaitable[Any]]


async def _tenant(_session: AsyncSession, seed: TenantSeed) -> Any:
    return seed.tenant_id


async def _business(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await tenancy.create_business(session, seed.tenant_id, business_in("Second Co"))).id


async def _branch(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await tenancy.create_branch(session, seed.tenant_id, seed.business_id, branch_in("B2"))).id


async def _warehouse(session: AsyncSession, seed: TenantSeed) -> Any:
    data = WarehouseIn(code="W2", name_en="Second", name_ar="الثاني")
    return (await tenancy.create_warehouse(session, seed.tenant_id, seed.branch_id, data)).id


async def _node(session: AsyncSession, seed: TenantSeed) -> Any:
    node = Node(id=new_id(), tenant_id=seed.tenant_id, kind=NodeKind.CLOUD.value, name="Cloud")
    session.add(node)
    await session.flush()
    return node.id


async def _membership(session: AsyncSession, seed: TenantSeed) -> Any:
    user = await add_user(session)
    membership = Membership(id=new_id(), tenant_id=seed.tenant_id, user_id=user.id)
    session.add(membership)
    await session.flush()
    return membership.id


async def _sync_outbox(session: AsyncSession, _seed: TenantSeed) -> Any:
    return (await session.execute(text("SELECT max(id) FROM sync_outbox"))).scalar_one()


ISOLATION_FACTORIES: dict[str, Factory] = {
    "tenant": _tenant,
    "business": _business,
    "branch": _branch,
    "warehouse": _warehouse,
    "node": _node,
    "membership": _membership,
    "sync_outbox": _sync_outbox,
}

# Column holding the tenant id, where it is not ``tenant_id``.
TENANT_COLUMN = {"tenant": "id"}
