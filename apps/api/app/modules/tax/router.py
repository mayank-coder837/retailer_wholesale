"""VAT return endpoints (M1.10b): the VAT 201 draft for a period and the documents behind each box
(report.view)."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.identity.authz import require
from app.modules.tax import codes, service
from app.modules.tax.schemas import TaxCodeOut, VatBoxLineOut, VatReturnOut

router = APIRouter(tags=["tax"])


@router.get("/tax-codes", operation_id="list_tax_codes")
async def list_tax_codes(_principal: PrincipalDep, side: codes.Side | None = None) -> list[TaxCodeOut]:
    """The fixed tax codes (seed registry, not editable), for the code on a line; any signed-in user."""
    return [
        TaxCodeOut(
            code=c.code,
            side=c.side.value,
            rate=c.rate,
            treatment=c.treatment.value,
            name_en=c.name_en,
            name_ar=c.name_ar,
        )
        for s in ([side] if side else list(codes.Side))
        for c in codes.codes(s)
    ]


@router.get("/vat-returns/draft", operation_id="vat_return_draft")
async def vat_return_draft(
    perms: Annotated[PermissionSet, require("report.view")],
    session: TenantSessionDep,
    business_id: uuid.UUID,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> VatReturnOut:
    """Every VAT 201 box for the period from posted documents; credit notes and reversals in their own
    period."""
    perms.require_see("report.view", business_id)
    return await service.draft(session, business_id, date_from, date_to)


@router.get("/vat-returns/draft/lines", operation_id="vat_return_box_lines")
async def vat_return_box_lines(
    perms: Annotated[PermissionSet, require("report.view")],
    session: TenantSessionDep,
    business_id: uuid.UUID,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    box: Annotated[str, Query(max_length=3, examples=["1b"])],
) -> list[VatBoxLineOut]:
    """The documents behind one box figure; they add up to it."""
    perms.require_see("report.view", business_id)
    return await service.box_lines(session, business_id, date_from, date_to, box)
