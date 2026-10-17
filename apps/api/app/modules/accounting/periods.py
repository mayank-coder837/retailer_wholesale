"""Fiscal years and monthly periods, and the period lock (docs/milestones/m1.5-accounting.md).

A year follows the business's ``fiscal_year_start_month``. Posting on a date with no year creates it when the
date is in the current or the next fiscal year (so 1 January never stops the counter); other years are created
on purpose. A locked period refuses postings unless the user holds ``period.override_lock`` and says why.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import ConflictError, NotFoundError
from app.core.ids import new_id
from app.modules.accounting.models import FiscalPeriod, FiscalYear, PeriodStatus
from app.modules.tenancy.models import Business


class NoFiscalYearError(ConflictError):
    code = "no_fiscal_year"
    title = "This date is outside every fiscal year: create the year first"


class FiscalYearOverlapError(ConflictError):
    code = "fiscal_year_overlap"
    title = "This fiscal year overlaps one that already exists"


class PeriodLockedError(ConflictError):
    code = "period_locked"
    title = "This period is locked"


class PeriodNotFoundError(NotFoundError):
    code = "fiscal_period_not_found"
    title = "There is no such fiscal period"


@dataclass(frozen=True, slots=True)
class YearBounds:
    code: str
    start: date
    end: date


def _add_months(day: date, months: int) -> date:
    total = day.month - 1 + months
    return date(day.year + total // 12, total % 12 + 1, 1)


def year_bounds(start_month: int, day: date) -> YearBounds:
    """The fiscal year containing ``day``: ``2026`` for calendar years, ``2026-27`` otherwise."""
    start_year = day.year if day.month >= start_month else day.year - 1
    start = date(start_year, start_month, 1)
    end = _add_months(start, 12) - timedelta(days=1)
    code = str(start_year) if start_month == 1 else f"{start_year}-{(start_year + 1) % 100:02d}"
    return YearBounds(code, start, end)


def month_bounds(start: date) -> list[tuple[date, date]]:
    return [(_add_months(start, n), _add_months(start, n + 1) - timedelta(days=1)) for n in range(12)]


def business_today(business: Business) -> date:
    return datetime.now(ZoneInfo(business.time_zone)).date()


async def find_year(session: AsyncSession, business_id: uuid.UUID, day: date) -> FiscalYear | None:
    return (
        await session.scalars(
            select(FiscalYear).where(
                FiscalYear.business_id == business_id,
                FiscalYear.start_date <= day,
                FiscalYear.end_date >= day,
            )
        )
    ).first()


async def create_year(session: AsyncSession, business: Business, day: date) -> FiscalYear:
    """The fiscal year containing ``day``, with its twelve open months. Idempotent under concurrency."""
    bounds = year_bounds(business.fiscal_year_start_month, day)
    overlapping = (
        await session.scalars(
            select(FiscalYear).where(
                FiscalYear.business_id == business.id,
                FiscalYear.start_date <= bounds.end,
                FiscalYear.end_date >= bounds.start,
                FiscalYear.code != bounds.code,
            )
        )
    ).first()
    if overlapping is not None:
        raise FiscalYearOverlapError(existing=overlapping.code)
    year_id = await session.scalar(
        insert(FiscalYear)
        .values(
            id=new_id(),
            tenant_id=business.tenant_id,
            business_id=business.id,
            code=bounds.code,
            start_date=bounds.start,
            end_date=bounds.end,
        )
        .on_conflict_do_nothing(index_elements=["business_id", "code"])
        .returning(FiscalYear.id)
    )
    if year_id is not None:
        await session.execute(
            insert(FiscalPeriod),
            [
                {
                    "id": new_id(),
                    "tenant_id": business.tenant_id,
                    "business_id": business.id,
                    "fiscal_year_id": year_id,
                    "period_no": n,
                    "start_date": start,
                    "end_date": end,
                }
                for n, (start, end) in enumerate(month_bounds(bounds.start), start=1)
            ],
        )
    year = (
        await session.scalars(
            select(FiscalYear)
            .where(FiscalYear.business_id == business.id, FiscalYear.code == bounds.code)
            .execution_options(populate_existing=True)
        )
    ).one()
    if year_id is not None:
        await audit.record(session, year, "create")
    return year


async def year_for_posting(session: AsyncSession, business: Business, day: date) -> FiscalYear:
    year = await find_year(session, business.id, day)
    if year is not None:
        return year
    today = business_today(business)
    current = year_bounds(business.fiscal_year_start_month, today)
    allowed = {
        current.start,
        year_bounds(business.fiscal_year_start_month, current.end + timedelta(days=1)).start,
    }
    if year_bounds(business.fiscal_year_start_month, day).start not in allowed:
        raise NoFiscalYearError(date=day.isoformat())
    return await create_year(session, business, day)


async def period_of(session: AsyncSession, year: FiscalYear, day: date) -> FiscalPeriod:
    return (
        await session.scalars(
            select(FiscalPeriod).where(
                FiscalPeriod.fiscal_year_id == year.id,
                FiscalPeriod.start_date <= day,
                FiscalPeriod.end_date >= day,
            )
        )
    ).one()


async def list_years(
    session: AsyncSession, business_id: uuid.UUID
) -> list[tuple[FiscalYear, list[FiscalPeriod]]]:
    years = (
        await session.scalars(
            select(FiscalYear)
            .where(FiscalYear.business_id == business_id)
            .order_by(FiscalYear.start_date.desc())
        )
    ).all()
    periods = (
        await session.scalars(
            select(FiscalPeriod)
            .where(FiscalPeriod.business_id == business_id)
            .order_by(FiscalPeriod.start_date)
        )
    ).all()
    by_year: dict[uuid.UUID, list[FiscalPeriod]] = {}
    for p in periods:
        by_year.setdefault(p.fiscal_year_id, []).append(p)
    return [(y, by_year.get(y.id, [])) for y in years]


async def get_period(session: AsyncSession, period_id: uuid.UUID) -> FiscalPeriod:
    period = await session.get(FiscalPeriod, period_id)
    if period is None:
        raise PeriodNotFoundError()
    return period


async def set_locked(
    session: AsyncSession, period: FiscalPeriod, *, locked: bool, user_id: uuid.UUID | None
) -> FiscalPeriod:
    """Lock or unlock one month; the change is audited like any other update."""
    if locked and period.status != PeriodStatus.LOCKED:
        period.status = PeriodStatus.LOCKED.value
        period.locked_at = datetime.now(UTC)
        period.locked_by = user_id
    elif not locked and period.status != PeriodStatus.OPEN:
        period.status = PeriodStatus.OPEN.value
        period.locked_at = None
        period.locked_by = None
    await session.flush()
    return period


async def check_open(
    session: AsyncSession,
    period: FiscalPeriod,
    *,
    may_override: bool,
    override_reason: str | None,
    document: str,
) -> None:
    """Posting into a locked period: refused, unless overridden with permission and a reason (audited)."""
    if period.status != PeriodStatus.LOCKED:
        return
    reason = (override_reason or "").strip()
    if not (may_override and reason):
        raise PeriodLockedError(
            period_start=period.start_date.isoformat(),
            may_override=may_override,
            detail="Say why to post into it." if may_override else None,
        )
    await audit.record(
        session,
        period,
        "override",
        reason=reason,
        changes={"override": "period_lock", "document": document},
    )
