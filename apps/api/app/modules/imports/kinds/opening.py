"""What the opening-balance imports share (M1.12b): the opening date and branch, and posting a journal.

Opening balances are posted as ordinary journal vouchers through the accounting service, so they get the same
checks, numbering, period lock and audit as a voucher typed by hand, and Cancel works on them (Undo uses it).
Every voucher is balanced against ``opening_balance_equity``, which nets to nothing once the books are
complete.
"""

import uuid
from datetime import date
from decimal import Decimal

from app.core.errors import UnprocessableError
from app.modules.accounting import service as accounting
from app.modules.accounting.models import Role
from app.modules.accounting.posting import VoucherType
from app.modules.accounting.schemas import VoucherIn, VoucherLineIn
from app.modules.imports.kinds.base import Context, Option
from app.modules.tenancy import service as tenancy

ZERO = Decimal("0.0000")
CHUNK = 150  # lines of one voucher (a voucher holds 200)
OPENING_DATE = Option(
    "opening_date",
    "Opening date (the last day of the old books)",
    "تاريخ الافتتاح (آخر يوم في الدفاتر القديمة)",
    (),
    "",
    required=True,
    kind="date",
)
BRANCH = Option("branch", "Branch", "الفرع", (), "", kind="branch")


class OpeningNoFiscalYearError(UnprocessableError):
    code = "opening_no_fiscal_year"
    title = "There is no fiscal year for the opening date: create the year first"


class OpeningPeriodClosedError(UnprocessableError):
    code = "opening_period_closed"
    title = "The period of the opening date is locked"


def opening_day(ctx: Context) -> date:
    return date.fromisoformat(ctx.options["opening_date"])


async def check_opening_day(ctx: Context) -> date:
    """The opening date, if the books can take entries on it (nothing is created by asking)."""
    day = opening_day(ctx)
    business = await tenancy.get_business(ctx.session, ctx.business_id)
    problem = await accounting.posting_day_problem(ctx.session, business, day)
    if problem == "no_fiscal_year":
        raise OpeningNoFiscalYearError(date=day.isoformat())
    if problem == "period_closed":
        raise OpeningPeriodClosedError(date=day.isoformat())
    return day


async def equity_account(ctx: Context) -> uuid.UUID:
    return (await accounting.role_accounts(ctx.session, ctx.business_id))[Role.OPENING_BALANCE_EQUITY]


def chunks[T](items: list[T], size: int = CHUNK) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def equity_side(net_debit: Decimal) -> dict[str, Decimal]:
    """The line that balances a voucher whose lines add up to ``net_debit`` (a credit for a debit surplus)."""
    if net_debit > 0:
        return {"credit": net_debit}
    return {"debit": -net_debit} if net_debit < 0 else {}


async def post_journal(ctx: Context, lines: list[VoucherLineIn], *, net_debit: Decimal) -> uuid.UUID:
    """One journal voucher on the opening date. ``net_debit`` (debits less credits of ``lines``) goes against
    ``opening_balance_equity`` so the voucher balances."""
    assert ctx.branch_id is not None
    assert ctx.batch_id is not None
    if side := equity_side(net_debit):
        equity = await equity_account(ctx)
        lines = [*lines, VoucherLineIn(account_id=equity, description="Opening balance equity", **side)]
    data = VoucherIn(
        doc_type=VoucherType.JOURNAL,
        business_id=ctx.business_id,
        branch_id=ctx.branch_id,
        doc_date=opening_day(ctx),
        reference=f"IMPORT-{ctx.batch_id.hex[:8]}",
        narration=f"Opening balances imported from {ctx.filename}"[:2000],
        lines=lines,
    )
    doc = await accounting.post_voucher(
        ctx.session, ctx.tenant_id, data, node_id=ctx.node_id, user_id=ctx.user_id
    )
    ctx.record.setdefault("documents", []).append(str(doc.id))
    return doc.id
