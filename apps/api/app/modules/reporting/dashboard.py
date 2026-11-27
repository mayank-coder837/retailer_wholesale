"""The owner's dashboard (P2.1): six figures, each read through the owning module's service (one door)."""

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.reporting import service
from app.modules.reporting.schemas import DashboardOut
from app.modules.tenancy import service as tenancy

ZERO = Decimal("0.0000")
CASH_TYPES = ("cash", "bank")
DUE_WITHIN_DAYS = 7


async def dashboard(session: AsyncSession, business_id: uuid.UUID) -> DashboardOut:
    from app.modules.accounting import periods
    from app.modules.accounting import service as accounting
    from app.modules.inventory import service as inventory
    from app.modules.pdc import service as pdc
    from app.modules.pdc.models import PdcDirection, PdcStatus

    business = await tenancy.get_business(session, business_id)
    today = periods.business_today(business)

    day = await service.profit_loss(session, business_id, today, today)
    sales_today = next((s.total for s in day.sections if s.key == "sales"), ZERO)

    ageing = await accounting.ageing(session, business_id, receivable=True, today=today)

    due = await pdc.list_pdcs(
        session,
        business_id,
        direction=PdcDirection.RECEIVED,
        due_from=today,
        due_to=today + timedelta(days=DUE_WITHIN_DAYS),
    )
    waiting = [c for c in due if c.status in (PdcStatus.IN_HAND.value, PdcStatus.DEPOSITED.value)]

    accounts = {a.id: a for a in await service._accounts(session, business_id)}
    balances = await service._balances(session, business_id, today)
    cash_bank = sum(
        (b for account_id, b in balances.items() if accounts[account_id].account_type in CASH_TYPES), ZERO
    )

    low = await inventory.balances(session, business_id, negative_only=True)
    return DashboardOut(
        business_id=business_id,
        as_of=today,
        sales_today=sales_today,
        receivables=ageing.totals.total,
        after_pdc=ageing.totals.after_pdc,
        cheques_due_count=len(waiting),
        cheques_due_amount=sum((c.amount for c in waiting), ZERO),
        cash_bank=cash_bank,
        low_stock_items=len({b.variant_id for b in low}),
    )
