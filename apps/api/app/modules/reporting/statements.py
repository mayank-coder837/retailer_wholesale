"""Profit and loss and the balance sheet from account balances, pure (M1.11).

Balances come in debit-positive (as the ledger keeps them). A statement shows each section the way it reads:
assets and expenses debit-positive, income, liabilities and equity credit-positive.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.modules.reporting.schemas import SectionOut, StatementRowOut

ZERO = Decimal("0.0000")

PL_SECTIONS = ("sales", "cogs", "other_income", "expenses")
ASSET_SECTIONS = ("current_assets", "fixed_assets")
LIABILITY_SECTIONS = ("current_liabilities", "non_current_liabilities")
CREDIT_NATURES = frozenset({"income", "liability", "equity"})
PROFIT_YEAR = ("profit_year", "Profit for the year to date", "صافي الربح للسنة حتى تاريخه")
PROFIT_EARLIER = (
    "profit_earlier",
    "Earlier years' profit (not yet closed)",
    "أرباح السنوات السابقة (غير مقفلة)",
)


@dataclass(frozen=True, slots=True)
class Account:
    id: uuid.UUID
    code: str
    name_en: str
    name_ar: str
    nature: str  # asset, liability, equity, income, expense
    account_type: str | None
    is_group: bool = False


def _pl_section(a: Account) -> str | None:
    if a.nature == "income":
        return "other_income" if a.account_type == "other_income" else "sales"
    if a.nature == "expense":
        return "cogs" if a.account_type == "cogs" else "expenses"
    return None


def _bs_section(a: Account) -> str | None:
    if a.nature == "asset":
        return "fixed_assets" if a.account_type == "fixed_asset" else "current_assets"
    if a.nature == "liability":
        return (
            "non_current_liabilities" if a.account_type == "non_current_liability" else "current_liabilities"
        )
    if a.nature == "equity":
        return "equity"
    return None


def _shown(a: Account, net: Decimal) -> Decimal:
    return -net if a.nature in CREDIT_NATURES else net


def _sections(
    accounts: Sequence[Account],
    balances: Mapping[uuid.UUID, Decimal],
    keys: Sequence[str],
    section_of: Callable[[Account], str | None],
) -> dict[str, list[StatementRowOut]]:
    rows: dict[str, list[StatementRowOut]] = {k: [] for k in keys}
    for a in sorted(accounts, key=lambda x: x.code):
        net = balances.get(a.id, ZERO)
        key = section_of(a)
        if a.is_group or net == 0 or key not in rows:
            continue
        rows[key].append(
            StatementRowOut(
                account_id=a.id, code=a.code, name_en=a.name_en, name_ar=a.name_ar, amount=_shown(a, net)
            )
        )
    return rows


def _section(key: str, rows: list[StatementRowOut]) -> SectionOut:
    return SectionOut(key=key, rows=rows, total=sum((r.amount for r in rows), ZERO))


def profit(accounts: Sequence[Account], movement: Mapping[uuid.UUID, Decimal]) -> Decimal:
    """Income less expenses over a movement (credit-positive): the net profit, negative for a loss."""
    return -sum((movement.get(a.id, ZERO) for a in accounts if a.nature in ("income", "expense")), ZERO)


def profit_loss(
    accounts: Sequence[Account], movement: Mapping[uuid.UUID, Decimal]
) -> tuple[list[SectionOut], Decimal, Decimal]:
    """The trading layout: sales - cost of goods sold = gross profit; + other income - expenses = net
    profit."""
    rows = _sections(accounts, movement, PL_SECTIONS, _pl_section)
    sections = [_section(k, rows[k]) for k in PL_SECTIONS]
    total = {s.key: s.total for s in sections}
    gross = total["sales"] - total["cogs"]
    net = gross + total["other_income"] - total["expenses"]
    return sections, gross, net


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    assets: list[SectionOut]
    liabilities: list[SectionOut]
    equity: SectionOut
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal

    @property
    def balanced(self) -> bool:
        return self.total_assets == self.total_liabilities + self.total_equity


def balance_sheet(
    accounts: Sequence[Account],
    balances: Mapping[uuid.UUID, Decimal],
    year_movement: Mapping[uuid.UUID, Decimal],
) -> BalanceSheet:
    """Assets, liabilities and equity on a date; the profit not yet closed into equity is split into this
    fiscal year's (``year_movement``: from its first day to the date) and earlier years' (the rest), R10."""
    rows = _sections(accounts, balances, (*ASSET_SECTIONS, *LIABILITY_SECTIONS, "equity"), _bs_section)
    to_date = profit(accounts, balances)
    this_year = profit(accounts, year_movement)
    for (key, en, ar), amount in ((PROFIT_EARLIER, to_date - this_year), (PROFIT_YEAR, this_year)):
        if amount != 0:
            rows["equity"].append(
                StatementRowOut(account_id=None, key=key, code=None, name_en=en, name_ar=ar, amount=amount)
            )
    assets = [_section(k, rows[k]) for k in ASSET_SECTIONS]
    liabilities = [_section(k, rows[k]) for k in LIABILITY_SECTIONS]
    equity = _section("equity", rows["equity"])
    return BalanceSheet(
        assets=assets,
        liabilities=liabilities,
        equity=equity,
        total_assets=sum((s.total for s in assets), ZERO),
        total_liabilities=sum((s.total for s in liabilities), ZERO),
        total_equity=equity.total,
    )
