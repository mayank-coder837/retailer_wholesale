"""Report endpoints (M1.11): profit and loss, balance sheet, day book (report.view)."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import PermissionSet
from app.core.deps import TenantSessionDep
from app.modules.identity.authz import require
from app.modules.reporting import service
from app.modules.reporting.schemas import BalanceSheetOut, DayBookOut, ProfitLossOut

router = APIRouter(tags=["reports"])
ReportView = Annotated[PermissionSet, require("report.view")]


@router.get("/reports/profit-loss", operation_id="profit_loss")
async def profit_loss(
    perms: ReportView,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> ProfitLossOut:
    """Income and expenses in the period: sales - cost of goods sold = gross profit; + other income - expenses
    = net profit."""
    perms.require_see("report.view", business_id)
    return await service.profit_loss(session, business_id, date_from, date_to)


@router.get("/reports/balance-sheet", operation_id="balance_sheet")
async def balance_sheet(
    perms: ReportView, session: TenantSessionDep, business_id: uuid.UUID, as_of: date
) -> BalanceSheetOut:
    """Assets, liabilities and equity on a date, with the profit not yet closed into equity (R10)."""
    perms.require_see("report.view", business_id)
    return await service.balance_sheet(session, business_id, as_of)


@router.get("/reports/day-book", operation_id="day_book")
async def day_book(
    perms: ReportView,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
) -> DayBookOut:
    """Every posted journal entry in the period with its lines, oldest first (at most 500 entries)."""
    perms.require_see("report.view", business_id)
    return await service.day_book(session, business_id, date_from, date_to)
