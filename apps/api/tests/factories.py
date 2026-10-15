"""Test data builders.

``seed_tenant`` creates a complete tenant (user, tenant, business, head office, warehouse, node, membership)
through the real services. ``ISOLATION_FACTORIES`` creates one extra row per tenant table in the current
tenant context; every tenant table must have an entry (tests/test_tenant_isolation.py enforces it).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs, numbering
from app.core.ids import new_id
from app.core.platform_models import Attachment, AuditLog, IdempotencyKey, IndustryPackApplication, Setting
from app.core.security import hash_password
from app.modules.catalog import service as catalog
from app.modules.catalog.models import UnitFamily
from app.modules.catalog.schemas import UnitIn
from app.modules.identity.models import Membership, Role, RoleAssignment, RolePermission, UserAccount
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


async def _new_role(session: AsyncSession, seed: TenantSeed) -> Role:
    role = Role(id=new_id(), tenant_id=seed.tenant_id, name_en=f"Role {new_id().hex[:6]}", name_ar="دور")
    session.add(role)
    await session.flush()
    return role


async def _role(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _new_role(session, seed)).id


async def _role_permission(session: AsyncSession, seed: TenantSeed) -> Any:
    role = await _new_role(session, seed)
    row = RolePermission(
        id=new_id(), tenant_id=seed.tenant_id, role_id=role.id, permission_code="product.view"
    )
    session.add(row)
    await session.flush()
    return row.id


async def _role_assignment(session: AsyncSession, seed: TenantSeed) -> Any:
    role = await _new_role(session, seed)
    membership_id = await _membership(session, seed)
    row = RoleAssignment(
        id=new_id(),
        tenant_id=seed.tenant_id,
        membership_id=membership_id,
        role_id=role.id,
        business_id=seed.business_id,
        branch_id=seed.branch_id,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _audit_log(session: AsyncSession, seed: TenantSeed) -> Any:
    row = AuditLog(
        id=new_id(),
        tenant_id=seed.tenant_id,
        action="create",
        entity_type="business",
        entity_id=seed.business_id,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _allocate(session: AsyncSession, seed: TenantSeed) -> numbering.Allocated:
    return await numbering.allocate(
        session,
        tenant_id=seed.tenant_id,
        branch_id=seed.branch_id,
        doc_type="sales_invoice",
        fiscal_year="2026",
        node_id=seed.node_id,
        document_id=new_id(),
    )


async def _numbering_series(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _allocate(session, seed)).series_id


async def _released_number(session: AsyncSession, seed: TenantSeed) -> Any:
    allocated = await _allocate(session, seed)
    row = await numbering.release(
        session,
        tenant_id=seed.tenant_id,
        series_id=allocated.series_id,
        number=allocated.number,
        cancelled_document_id=new_id(),
    )
    return row.id


async def _outbox_event(session: AsyncSession, seed: TenantSeed) -> Any:
    return await jobs.publish(session, tenant_id=seed.tenant_id, event_type="test.noop")


async def _idempotency_key(session: AsyncSession, seed: TenantSeed) -> Any:
    row = IdempotencyKey(
        id=new_id(), tenant_id=seed.tenant_id, user_id=seed.user_id, key=new_id().hex, request_hash="0" * 64
    )
    session.add(row)
    await session.flush()
    return row.id


async def _attachment(session: AsyncSession, seed: TenantSeed) -> Any:
    row = Attachment(
        id=new_id(),
        tenant_id=seed.tenant_id,
        entity_type="business",
        entity_id=seed.business_id,
        filename="bill.pdf",
        content_type="application/pdf",
        size=1,
        sha256="0" * 64,
        storage_key=f"{seed.tenant_id}/{new_id()}",
    )
    session.add(row)
    await session.flush()
    return row.id


async def _setting(session: AsyncSession, seed: TenantSeed) -> Any:
    row = Setting(
        id=new_id(),
        tenant_id=seed.tenant_id,
        scope="business",
        scope_id=seed.business_id,
        key="ui.x",
        value=1,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _industry_pack_application(session: AsyncSession, seed: TenantSeed) -> Any:
    row = IndustryPackApplication(
        id=new_id(), tenant_id=seed.tenant_id, business_id=seed.business_id, pack_code="textile", version=1
    )
    session.add(row)
    await session.flush()
    return row.id


async def _unit_family(session: AsyncSession, seed: TenantSeed) -> Any:
    row = UnitFamily(id=new_id(), tenant_id=seed.tenant_id, code="energy", name_en="Energy", name_ar="الطاقة")
    session.add(row)
    await session.flush()
    return row.id


async def _unit(session: AsyncSession, seed: TenantSeed) -> Any:
    data = UnitIn(
        family="count",
        code="gross",
        name_en="Gross",
        name_ar="قروس",
        symbol="gr",
        decimals=0,
        factor_to_family_base=Decimal(144),
    )
    return (await catalog.create_unit(session, seed.tenant_id, data)).id


ISOLATION_FACTORIES: dict[str, Factory] = {
    "audit_log": _audit_log,
    "numbering_series": _numbering_series,
    "released_number": _released_number,
    "outbox_event": _outbox_event,
    "idempotency_key": _idempotency_key,
    "attachment": _attachment,
    "setting": _setting,
    "industry_pack_application": _industry_pack_application,
    "tenant": _tenant,
    "business": _business,
    "branch": _branch,
    "warehouse": _warehouse,
    "node": _node,
    "membership": _membership,
    "sync_outbox": _sync_outbox,
    "role": _role,
    "role_permission": _role_permission,
    "role_assignment": _role_assignment,
    "unit_family": _unit_family,
    "unit": _unit,
}

# Column holding the tenant id, where it is not ``tenant_id``.
TENANT_COLUMN = {"tenant": "id"}
# Tenant tables that are never synced to the cloud (local queues) and tables the app may only append to.
NOT_SYNCED = frozenset({"sync_outbox", "outbox_event", "idempotency_key"})
APPEND_ONLY = frozenset({"audit_log"})
