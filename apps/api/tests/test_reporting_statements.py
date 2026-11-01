"""The statement builders, pure (M1.11a): layout, signs, the profit split, and a property test — whatever the
balanced journals, the balance sheet balances."""

import uuid
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.reporting.statements import Account, balance_sheet, profit, profit_loss


def acc(code: str, nature: str, account_type: str | None = None, *, group: bool = False) -> Account:
    return Account(uuid.uuid4(), code, f"A{code}", f"ح{code}", nature, account_type, group)


CASH = acc("1110", "asset", "cash")
VAN = acc("1720", "asset", "fixed_asset")
VAT_IN = acc("1400", "asset", "tax")
PAYABLE = acc("2110", "liability", "payable")
LOAN = acc("2520", "liability", "non_current_liability")
CAPITAL = acc("3100", "equity", "equity")
SALES = acc("4100", "income", "sales")
OTHER = acc("4800", "income", "other_income")
COGS = acc("5100", "expense", "cogs")
RENT = acc("6100", "expense", "expense")
GROUP = acc("6000", "expense", None, group=True)
CHART = [CASH, VAN, VAT_IN, PAYABLE, LOAN, CAPITAL, SALES, OTHER, COGS, RENT, GROUP]

D = Decimal


def test_profit_and_loss_reads_income_credit_positive_and_skips_quiet_accounts() -> None:
    movement = {
        SALES.id: D("-900"),
        COGS.id: D("400"),
        OTHER.id: D("-50"),
        RENT.id: D("100"),
        CASH.id: D("450"),
    }
    sections, gross, net = profit_loss(CHART, movement)
    by_key = {s.key: s for s in sections}
    assert [(r.code, r.amount) for r in by_key["sales"].rows] == [("4100", D("900"))]
    assert by_key["cogs"].total == D("400")
    assert (gross, net) == (D("500"), D("450"))
    assert profit(CHART, movement) == net
    # A loss is negative; accounts that did not move are left out.
    _sections, _gross, loss = profit_loss(CHART, {RENT.id: D("30")})
    assert loss == D("-30")
    assert all(not s.rows for s in _sections if s.key != "expenses")


def test_balance_sheet_sections_and_the_profit_split() -> None:
    balances = {
        CASH.id: D("1000"),
        VAN.id: D("5000"),
        PAYABLE.id: D("-700"),
        LOAN.id: D("-2000"),
        CAPITAL.id: D("-3000"),
        SALES.id: D("-500"),  # 300 of it this year
        RENT.id: D("200"),  # all this year
    }
    year = {SALES.id: D("-300"), RENT.id: D("200")}
    sheet = balance_sheet(CHART, balances, year)
    assert [(s.key, s.total) for s in sheet.assets] == [
        ("current_assets", D("1000")),
        ("fixed_assets", D("5000")),
    ]
    assert [(s.key, s.total) for s in sheet.liabilities] == [
        ("current_liabilities", D("700")),
        ("non_current_liabilities", D("2000")),
    ]
    assert [(r.code or r.key, r.amount) for r in sheet.equity.rows] == [
        ("3100", D("3000")),
        ("profit_earlier", D("200")),
        ("profit_year", D("100")),
    ]
    assert sheet.balanced
    assert sheet.total_assets == D("6000")


# A journal entry: two different accounts and an amount; every entry balances on its own.
ledger_accounts = [a for a in CHART if not a.is_group]
entry = st.tuples(
    st.sampled_from(ledger_accounts),
    st.sampled_from(ledger_accounts),
    st.decimals(min_value=D("0.01"), max_value=D("100000"), places=2),
    st.booleans(),  # posted this fiscal year?
)


@settings(max_examples=200, deadline=None)
@given(entries=st.lists(entry, min_size=1, max_size=30))
def test_any_balanced_journal_gives_a_balanced_sheet(
    entries: list[tuple[Account, Account, Decimal, bool]],
) -> None:
    balances: dict[uuid.UUID, Decimal] = {}
    year: dict[uuid.UUID, Decimal] = {}
    for dr, cr, amount, this_year in entries:
        for target in (balances, year) if this_year else (balances,):
            target[dr.id] = target.get(dr.id, D(0)) + amount
            target[cr.id] = target.get(cr.id, D(0)) - amount
    sheet = balance_sheet(CHART, balances, year)
    assert sheet.balanced
    split = {r.key: r.amount for r in sheet.equity.rows if r.key}
    assert sum(split.values(), D(0)) == profit(CHART, balances)
    assert split.get("profit_year", D(0)) == profit(CHART, year)
    _sections, _gross, net = profit_loss(CHART, balances)
    assert net == profit(CHART, balances)
