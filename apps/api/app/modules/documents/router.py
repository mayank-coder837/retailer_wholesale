"""Cancel and Reverse for every document type (M1.7b): ``<type>.cancel`` / ``<type>.reverse`` on the
document's business and branch. Freed numbers are listed for whoever may create that type."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.core.numbering import PREFIXES, UnknownDocumentTypeError
from app.modules.documents import printing, service
from app.modules.documents.schemas import CancelIn, CorrectedOut, ReleasedNumberOut, ReverseIn
from app.modules.identity.authz import get_permissions
from app.modules.tenancy import service as tenancy

router = APIRouter(tags=["documents"])
Signed = Annotated[PermissionSet, Depends(get_permissions)]


@router.post("/documents/{doc_id}/cancel", operation_id="cancel_document")
async def cancel_document(
    doc_id: uuid.UUID, body: CancelIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> CorrectedOut:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.cancel", doc.business_id, doc.branch_id)
    doc = await service.cancel(session, doc_id, user_id=principal.user_id, reason=body.reason)
    return await service.corrected_out(session, doc)


@router.post("/documents/{doc_id}/reverse", operation_id="reverse_document")
async def reverse_document(
    doc_id: uuid.UUID, body: ReverseIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> CorrectedOut:
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.reverse", doc.business_id, doc.branch_id)
    doc = await service.reverse(
        session,
        doc_id,
        node_id=principal.node_id,
        user_id=principal.user_id,
        reason=body.reason,
        day=body.date,
        may_override_lock=perms.allows("period.override_lock", doc.business_id),
        override_reason=body.override_lock_reason,
    )
    return await service.corrected_out(session, doc)


@router.post("/documents/{doc_id}/sent", operation_id="mark_document_sent")
async def mark_document_sent(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> CorrectedOut:
    """Printed, emailed or sent on WhatsApp: whoever may export the document may send it."""
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.export", doc.business_id, doc.branch_id)
    doc = await service.mark_sent(session, doc_id)
    return await service.corrected_out(session, doc)


@router.get("/documents/{doc_id}/print", operation_id="print_document", response_class=HTMLResponse)
async def print_document(
    doc_id: uuid.UUID,
    perms: Signed,
    session: TenantSessionDep,
    lang: Annotated[Literal["en", "ar"], Query()] = "en",
) -> HTMLResponse:
    """A posted sales invoice or return as a printable tax invoice / credit note in English and Arabic (ADR
    0014): the browser or desktop app prints it or saves it as PDF. Printing is exporting."""
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.export", doc.business_id, doc.branch_id)
    return HTMLResponse(await printing.print_html(session, doc_id, lang))


@router.get("/documents/released-numbers", operation_id="list_released_numbers")
async def list_released_numbers(
    branch_id: uuid.UUID, doc_type: str, perms: Signed, session: TenantSessionDep
) -> list[ReleasedNumberOut]:
    if doc_type not in PREFIXES:
        raise UnknownDocumentTypeError(doc_type=doc_type)
    branch = await tenancy.get_branch(session, branch_id)
    perms.require(f"{doc_type}.create", branch.business_id, branch.id)
    return await service.released_numbers(session, branch_id, doc_type)


# After /documents/released-numbers, which it would otherwise swallow.
@router.get("/documents/{doc_id}", operation_id="get_document_state")
async def get_document_state(doc_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> CorrectedOut:
    """Where any document stands for Cancel and Reverse (every page shows it the same way)."""
    doc = await service.get_document(session, doc_id)
    perms.require(f"{doc.doc_type}.view", doc.business_id, doc.branch_id)
    return await service.corrected_out(session, doc)
