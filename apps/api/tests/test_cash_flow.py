"""Cash-flow statement (P2.2): for any balanced movement the lines add up to what the cash accounts moved."""

import uuid
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.modules.reporting import statements
from app.modules.reporting.statements import Account

TYPES: list[tuple[str, str]] = [  # (nature, account_type)
    ("asset", "cash"),
    ("asset", "bank"),
    ("asset", "receivable"),
    ("asset", "stock"),
    ("asset", "fixed_asset"),
    ("asset", "current_asset"),
    ("liability", "payable"),
    ("liability", "tax"),
    ("liability", "non_current_liability"),
    ("equity", "equity"),
    ("income", "sales"),
    ("income", "other_income"),
    ("expense", "cogs"),
    ("expense", "expense"),
]
ACCOUNTS = [
    Account(uuid.uuid4(), f"{i:04d}", f"Account {i}", f"حساب {i}", nature, account_type)
    for i, (nature, account_type) in enumerate(TYPES, 1)
]
CASH = [a for a in ACCOUNTS if a.account_type in ("cash", "bank")]
amounts = st.integers(min_value=-10_000_000, max_value=10_000_000).map(lambda n: Decimal(n) / 10000)


@given(st.lists(amounts, min_size=len(ACCOUNTS), max_size=len(ACCOUNTS)))
def test_net_change_equals_what_the_cash_accounts_moved(moves: list[Decimal]) -> None:
    movement = {a.id: m for a, m in zip(ACCOUNTS, moves, strict=True)}
    # The books balance: the first cash account takes whatever makes every movement add up to zero.
    movement[CASH[0].id] = Decimal(0)
    movement[CASH[0].id] = -sum(movement.values(), Decimal(0))
    flow = statements.cash_flow(ACCOUNTS, movement)
    assert flow.net_change == flow.cash_change
    assert flow.operating.rows[0].key == "net_profit"
    assert flow.operating.total + flow.investing.total + flow.financing.total == flow.net_change


def test_each_kind_of_account_lands_in_its_section() -> None:
    by_type = {a.account_type: a for a in ACCOUNTS}
    movement = {
        by_type["receivable"].id: Decimal("500"),  # customers owe more: cash used
        by_type["fixed_asset"].id: Decimal("2000"),  # bought equipment
        by_type["equity"].id: Decimal("-10000"),  # the owner put money in
        by_type["sales"].id: Decimal("-800"),
        by_type["cash"].id: Decimal("8300"),
    }
    flow = statements.cash_flow(ACCOUNTS, movement)
    assert [r.key or r.code for r in flow.operating.rows] == ["net_profit", "0003"]
    assert flow.operating.total == Decimal("300")  # profit 800 less 500 owed by customers
    assert flow.investing.total == Decimal("-2000")
    assert flow.financing.total == Decimal("10000")
    assert flow.net_change == flow.cash_change == Decimal("8300")
