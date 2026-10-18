"""Inventory endpoints (M1.6): stock documents, balances, item ledger, negative stock, valuation, batches.

Stock documents are branch-scoped: ``<type>.create / edit / delete / post`` for the document's business and
branch. Seeing stock needs ``product.view``; the valuation and negative-stock reports need ``report.view``.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.authz import PermissionDeniedError, PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.documents.models import DocumentStatus
from app.modules.identity.authz import get_permissions, require
from app.modules.inventory import documents, service
from app.modules.inventory.schemas import (
    BalanceOut,
    BatchOut,
    ItemLedgerOut,
    PostStockIn,
    StockDocIn,
    StockDocOut,
    StockDocSummary,
    StockDocType,
    ValuationOut,
)

router = APIRouter(tags=["inventory"])
Created = status.HTTP_201_CREATED
Signed = Annotated[PermissionSet, Depends(get_permissions)]
ProductView = Annotated[PermissionSet, require("product.view")]
ReportView = Annotated[PermissionSet, require("report.view")]


@router.get("/stock-documents", operation_id="list_stock_documents")
async def list_stock_documents(
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    doc_type: StockDocType | None = None,
    status: DocumentStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[StockDocSummary]:
    wanted = [doc_type] if doc_type else list(StockDocType)
    visible = [t for t in wanted if perms.can_see(f"{t.value}.view", business_id)]
    if not visible:
        raise PermissionDeniedError(permission=f"{(doc_type or StockDocType.STOCK_ADJUSTMENT).value}.view")
    return await documents.list_stock_documents(
        session, business_id, doc_types=visible, status=status, limit=limit, offset=offset
    )


async def _post(
    doc_id: uuid.UUID,
    body: PostStockIn,
    perms: PermissionSet,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> StockDocOut:
    doc = await documents.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.post", doc.business_id, doc.branch_id)
    _, warnings = await documents.post(
        session,
        doc_id,
        node_id=principal.node_id,
        user_id=principal.user_id,
        may_override_lock=perms.allows("period.override_lock", doc.business_id),
        override_reason=body.override_lock_reason,
    )
    return await documents.get_stock_document(session, doc_id, warnings)


@router.post("/stock-documents", operation_id="create_stock_document", status_code=Created)
async def create_stock_document(
    body: StockDocIn,
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    post: Annotated[bool, Query(description="Save and post in one step")] = False,
) -> StockDocOut:
    perms.require(f"{body.doc_type.value}.create", body.business_id, body.branch_id)
    if post:
        perms.require(f"{body.doc_type.value}.post", body.business_id, body.branch_id)
    doc = await documents.create_draft(session, principal.tenant_id, body)
    if post:
        return await _post(doc.id, PostStockIn(), perms, session, principal)
    return await documents.get_stock_document(session, doc.id)


@router.get("/stock-documents/{doc_id}", operation_id="get_stock_document")
async def get_stock_document(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> StockDocOut:
    doc = await documents.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.view", doc.business_id, doc.branch_id)
    return await documents.get_stock_document(session, doc_id)


@router.put("/stock-documents/{doc_id}", operation_id="update_stock_document")
async def update_stock_document(
    doc_id: uuid.UUID, body: StockDocIn, perms: Signed, session: TenantSessionDep
) -> StockDocOut:
    doc = await documents.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.edit", doc.business_id, doc.branch_id)
    perms.require(f"{doc.doc_type}.edit", body.business_id, body.branch_id)
    await documents.replace_draft(session, doc_id, body)
    return await documents.get_stock_document(session, doc_id)


@router.delete(
    "/stock-documents/{doc_id}", operation_id="delete_stock_document", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_stock_document(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> Response:
    doc = await documents.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.delete", doc.business_id, doc.branch_id)
    await documents.delete_draft(session, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/stock-documents/{doc_id}/post", operation_id="post_stock_document")
async def post_stock_document(
    doc_id: uuid.UUID, body: PostStockIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> StockDocOut:
    return await _post(doc_id, body, perms, session, principal)


@router.get("/stock/balances", operation_id="stock_balances")
async def stock_balances(
    perms: ProductView,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = None,
    variant_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> list[BalanceOut]:
    return await service.balances(session, business_id, warehouse_id=warehouse_id, variant_id=variant_id, q=q)


@router.get("/stock/variants/{variant_id}/ledger", operation_id="stock_item_ledger")
async def stock_item_ledger(
    variant_id: uuid.UUID,
    perms: ProductView,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> ItemLedgerOut:
    return await service.item_ledger(
        session, business_id, variant_id, warehouse_id=warehouse_id, date_from=date_from, date_to=date_to
    )


@router.get("/stock/negative", operation_id="negative_stock")
async def negative_stock(
    perms: ReportView, session: TenantSessionDep, business_id: uuid.UUID
) -> list[BalanceOut]:
    perms.require_see("report.view", business_id)
    return await service.balances(session, business_id, negative_only=True)


@router.get("/stock/valuation", operation_id="stock_valuation")
async def stock_valuation(
    perms: ReportView, session: TenantSessionDep, business_id: uuid.UUID
) -> ValuationOut:
    perms.require_see("report.view", business_id)
    return await service.valuation(session, business_id)


@router.get("/stock/batches", operation_id="stock_batches")
async def stock_batches(
    perms: ProductView,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    variant_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = None,
) -> list[BatchOut]:
    return await service.batches(session, business_id, variant_id, warehouse_id)
