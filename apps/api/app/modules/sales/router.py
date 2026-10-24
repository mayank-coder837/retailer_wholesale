"""Sales endpoints (M1.8): quotations, proformas, orders, delivery notes, invoices and returns.

Branch-scoped like every document: ``<type>.create / edit / delete / post`` for the document's business and
branch; ``<type>.view`` to read. Posting below the minimum price needs ``price.override_min``; past a credit
block,
``credit.override`` and a reason. A quotation is numbered when saved.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.authz import PermissionDeniedError, PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.documents.models import DocumentStatus
from app.modules.identity.authz import get_permissions
from app.modules.sales import service
from app.modules.sales.schemas import (
    OpenSalesLineOut,
    PostSalesIn,
    SalesDocIn,
    SalesDocOut,
    SalesDocSummary,
    SalesDocType,
    SalesHistoryLineOut,
)

router = APIRouter(tags=["sales"])
Created = status.HTTP_201_CREATED
Signed = Annotated[PermissionSet, Depends(get_permissions)]


@router.get("/sales-documents", operation_id="list_sales_documents")
async def list_sales_documents(
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    doc_type: SalesDocType | None = None,
    status: DocumentStatus | None = None,
    party_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SalesDocSummary]:
    wanted = [doc_type] if doc_type else list(SalesDocType)
    visible = [t for t in wanted if perms.can_see(f"{t.value}.view", business_id)]
    if not visible:
        raise PermissionDeniedError(permission=f"{(doc_type or SalesDocType.SALES_INVOICE).value}.view")
    return await service.list_sales_documents(
        session, business_id, doc_types=visible, status=status, party_id=party_id, limit=limit, offset=offset
    )


async def _post(
    doc_id: uuid.UUID,
    body: PostSalesIn,
    perms: PermissionSet,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> SalesDocOut:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.post", doc.business_id, doc.branch_id)
    _posted, warnings = await service.post(
        session,
        doc_id,
        node_id=principal.node_id,
        user_id=principal.user_id,
        may_override_lock=perms.allows("period.override_lock", doc.business_id),
        override_reason=body.override_lock_reason,
        may_override_min=perms.allows("price.override_min", doc.business_id),
        may_override_credit=perms.allows("credit.override", doc.business_id),
        credit_override_reason=body.credit_override_reason,
        reuse_number=body.reuse_number,
    )
    return await service.get_sales_document(session, doc_id, warnings)


@router.post("/sales-documents", operation_id="create_sales_document", status_code=Created)
async def create_sales_document(
    body: SalesDocIn,
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    post: Annotated[bool, Query(description="Save and post in one step (always, for a quotation)")] = False,
) -> SalesDocOut:
    post = post or body.doc_type is SalesDocType.QUOTATION
    perms.require(f"{body.doc_type.value}.create", body.business_id, body.branch_id)
    if post:
        perms.require(f"{body.doc_type.value}.post", body.business_id, body.branch_id)
    doc = await service.create_draft(session, principal.tenant_id, body)
    if post:
        return await _post(doc.id, PostSalesIn(), perms, session, principal)
    return await service.get_sales_document(session, doc.id)


@router.get("/sales-documents/{doc_id}", operation_id="get_sales_document")
async def get_sales_document(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> SalesDocOut:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.view", doc.business_id, doc.branch_id)
    return await service.get_sales_document(session, doc_id)


@router.get("/sales-documents/{doc_id}/open-lines", operation_id="sales_open_lines")
async def sales_open_lines(
    doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep
) -> list[OpenSalesLineOut]:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.view", doc.business_id, doc.branch_id)
    return await service.open_lines(session, doc_id)


@router.put("/sales-documents/{doc_id}", operation_id="update_sales_document")
async def update_sales_document(
    doc_id: uuid.UUID, body: SalesDocIn, perms: Signed, session: TenantSessionDep
) -> SalesDocOut:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.edit", doc.business_id, doc.branch_id)
    perms.require(f"{doc.doc_type}.edit", body.business_id, body.branch_id)
    await service.replace_draft(session, doc_id, body)
    return await service.get_sales_document(session, doc_id)


@router.delete(
    "/sales-documents/{doc_id}", operation_id="delete_sales_document", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_sales_document(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> Response:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.delete", doc.business_id, doc.branch_id)
    await service.delete_draft(session, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sales-documents/{doc_id}/post", operation_id="post_sales_document")
async def post_sales_document(
    doc_id: uuid.UUID, body: PostSalesIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> SalesDocOut:
    return await _post(doc_id, body, perms, session, principal)


@router.get("/parties/{party_id}/sales-history", operation_id="customer_sales_history")
async def customer_sales_history(
    party_id: uuid.UUID,
    business_id: uuid.UUID,
    perms: Signed,
    session: TenantSessionDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[SalesHistoryLineOut]:
    """The customer history panel (A9): every item invoiced to this customer, newest first."""
    perms.require_see("sales_invoice.view", business_id)
    return await service.sales_history(session, business_id, party_id, q)
