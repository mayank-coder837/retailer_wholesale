"""Purchasing endpoints (M1.7): orders, goods receipts, bills and returns.

Branch-scoped like every document: ``<type>.create / edit / delete / post`` for the document's business and
branch; ``<type>.view`` to read.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.authz import PermissionDeniedError, PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.documents.models import DocumentStatus
from app.modules.identity.authz import get_permissions
from app.modules.purchasing import service
from app.modules.purchasing.schemas import (
    OpenLineOut,
    PostPurchaseIn,
    PurchaseDocIn,
    PurchaseDocOut,
    PurchaseDocSummary,
    PurchaseDocType,
)

router = APIRouter(tags=["purchasing"])
Created = status.HTTP_201_CREATED
Signed = Annotated[PermissionSet, Depends(get_permissions)]


@router.get("/purchase-documents", operation_id="list_purchase_documents")
async def list_purchase_documents(
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    doc_type: PurchaseDocType | None = None,
    status: DocumentStatus | None = None,
    party_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PurchaseDocSummary]:
    wanted = [doc_type] if doc_type else list(PurchaseDocType)
    visible = [t for t in wanted if perms.can_see(f"{t.value}.view", business_id)]
    if not visible:
        raise PermissionDeniedError(permission=f"{(doc_type or PurchaseDocType.PURCHASE_BILL).value}.view")
    return await service.list_purchase_documents(
        session, business_id, doc_types=visible, status=status, party_id=party_id, limit=limit, offset=offset
    )


async def _post(
    doc_id: uuid.UUID,
    body: PostPurchaseIn,
    perms: PermissionSet,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> PurchaseDocOut:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.post", doc.business_id, doc.branch_id)
    await service.post(
        session,
        doc_id,
        node_id=principal.node_id,
        user_id=principal.user_id,
        may_override_lock=perms.allows("period.override_lock", doc.business_id),
        override_reason=body.override_lock_reason,
    )
    return await service.get_purchase_document(session, doc_id)


@router.post("/purchase-documents", operation_id="create_purchase_document", status_code=Created)
async def create_purchase_document(
    body: PurchaseDocIn,
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    post: Annotated[bool, Query(description="Save and post in one step")] = False,
) -> PurchaseDocOut:
    perms.require(f"{body.doc_type.value}.create", body.business_id, body.branch_id)
    if post:
        perms.require(f"{body.doc_type.value}.post", body.business_id, body.branch_id)
    doc = await service.create_draft(session, principal.tenant_id, body)
    if post:
        return await _post(doc.id, PostPurchaseIn(), perms, session, principal)
    return await service.get_purchase_document(session, doc.id)


@router.get("/purchase-documents/{doc_id}", operation_id="get_purchase_document")
async def get_purchase_document(
    doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep
) -> PurchaseDocOut:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.view", doc.business_id, doc.branch_id)
    return await service.get_purchase_document(session, doc_id)


@router.get("/purchase-documents/{doc_id}/open-lines", operation_id="purchase_open_lines")
async def purchase_open_lines(
    doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep
) -> list[OpenLineOut]:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.view", doc.business_id, doc.branch_id)
    return await service.open_lines(session, doc_id)


@router.put("/purchase-documents/{doc_id}", operation_id="update_purchase_document")
async def update_purchase_document(
    doc_id: uuid.UUID, body: PurchaseDocIn, perms: Signed, session: TenantSessionDep
) -> PurchaseDocOut:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.edit", doc.business_id, doc.branch_id)
    perms.require(f"{doc.doc_type}.edit", body.business_id, body.branch_id)
    await service.replace_draft(session, doc_id, body)
    return await service.get_purchase_document(session, doc_id)


@router.delete(
    "/purchase-documents/{doc_id}",
    operation_id="delete_purchase_document",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_purchase_document(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> Response:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.delete", doc.business_id, doc.branch_id)
    await service.delete_draft(session, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/purchase-documents/{doc_id}/post", operation_id="post_purchase_document")
async def post_purchase_document(
    doc_id: uuid.UUID, body: PostPurchaseIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> PurchaseDocOut:
    return await _post(doc_id, body, perms, session, principal)
