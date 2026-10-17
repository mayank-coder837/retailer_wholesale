"""Tenancy business rules. Other modules call these functions, never the repository or tables."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import apply_context
from app.core.errors import ConflictError, NotFoundError
from app.core.ids import new_id
from app.core.models import Base
from app.modules.tenancy import repository as repo
from app.modules.tenancy.models import Branch, Business, Node, NodeKind, Tenant, Warehouse
from app.modules.tenancy.schemas import BranchIn, BusinessIn, WarehouseIn

DEFAULT_WAREHOUSE_CODE = "MAIN"


class DuplicateCodeError(ConflictError):
    code = "duplicate_code"
    title = "This code is already used"


async def _insert(session: AsyncSession, row: Base) -> None:
    """Adds a row; a unique-constraint clash becomes a 409 instead of aborting the request transaction."""
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateCodeError() from exc


async def get_business(session: AsyncSession, business_id: uuid.UUID) -> Business:
    business = await repo.get_business(session, business_id)
    if business is None:
        raise NotFoundError("Business not found.")
    return business


async def list_businesses(session: AsyncSession) -> Sequence[Business]:
    return await repo.list_businesses(session)


async def create_business(session: AsyncSession, tenant_id: uuid.UUID, data: BusinessIn) -> Business:
    business = Business(id=new_id(), tenant_id=tenant_id, **data.model_dump())
    await _insert(session, business)
    from app.modules.accounting import service as accounting  # accounting builds on tenancy

    await accounting.install_for_business(session, tenant_id, business.id)
    return business


async def get_branch(session: AsyncSession, branch_id: uuid.UUID) -> Branch:
    branch = await repo.get_branch(session, branch_id)
    if branch is None:
        raise NotFoundError("Branch not found.")
    return branch


async def list_branches(session: AsyncSession, business_id: uuid.UUID) -> Sequence[Branch]:
    await get_business(session, business_id)
    return await repo.list_branches(session, business_id)


async def create_branch(
    session: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID, data: BranchIn
) -> Branch:
    await get_business(session, business_id)
    branch = Branch(id=new_id(), tenant_id=tenant_id, business_id=business_id, **data.model_dump())
    await _insert(session, branch)
    return branch


async def list_warehouses(session: AsyncSession, branch_id: uuid.UUID) -> Sequence[Warehouse]:
    await get_branch(session, branch_id)
    return await repo.list_warehouses(session, branch_id)


async def create_warehouse(
    session: AsyncSession, tenant_id: uuid.UUID, branch_id: uuid.UUID, data: WarehouseIn
) -> Warehouse:
    branch = await get_branch(session, branch_id)
    warehouse = Warehouse(
        id=new_id(),
        tenant_id=tenant_id,
        business_id=branch.business_id,
        branch_id=branch_id,
        **data.model_dump(),
    )
    await _insert(session, warehouse)
    return warehouse


async def get_main_node(session: AsyncSession) -> Node | None:
    return await repo.get_main_node(session)


async def get_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await repo.get_tenant(session, tenant_id)
    if tenant is None:
        raise NotFoundError("Tenant not found.")
    return tenant


@dataclass(frozen=True, slots=True)
class NewTenant:
    tenant_id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID
    warehouse_id: uuid.UUID
    node_id: uuid.UUID


async def create_tenant(
    session: AsyncSession,
    *,
    tenant_name: str,
    business: BusinessIn,
    head_office: BranchIn,
    user_id: uuid.UUID | None = None,
) -> NewTenant:
    """Creates a tenant with its first business, head-office branch, default warehouse and main-PC node.

    Switches the transaction's tenant context to the new tenant first, so every insert passes row-level
    security.
    The node id is decided up front so every row records it as ``origin_node_id``.
    """
    ids = NewTenant(new_id(), new_id(), new_id(), new_id(), new_id())
    await apply_context(session, tenant_id=ids.tenant_id, user_id=user_id, node_id=ids.node_id)

    session.add(Tenant(id=ids.tenant_id, name=tenant_name))
    await session.flush()
    session.add(Business(id=ids.business_id, tenant_id=ids.tenant_id, **business.model_dump()))
    await session.flush()
    branch_data = head_office.model_dump() | {"is_head_office": True}
    session.add(Branch(id=ids.branch_id, tenant_id=ids.tenant_id, business_id=ids.business_id, **branch_data))
    await session.flush()
    session.add(
        Warehouse(
            id=ids.warehouse_id,
            tenant_id=ids.tenant_id,
            business_id=ids.business_id,
            branch_id=ids.branch_id,
            code=DEFAULT_WAREHOUSE_CODE,
            name_en="Main warehouse",
            name_ar="المستودع الرئيسي",
            is_default=True,
        )
    )
    session.add(
        Node(
            id=ids.node_id,
            tenant_id=ids.tenant_id,
            kind=NodeKind.BRANCH_MAIN_PC.value,
            branch_id=ids.branch_id,
            name=f"{head_office.name_en} main PC",
        )
    )
    await session.flush()
    from app.modules.accounting import service as accounting
    from app.modules.catalog import service as catalog

    await catalog.create_standard_units(session, ids.tenant_id)
    await accounting.install_for_business(session, ids.tenant_id, ids.business_id)
    return ids
