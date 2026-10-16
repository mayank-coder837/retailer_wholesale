"""The credit check (M1.4): limit, overdue days, warn or block, override; PDCs never count (A2)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.modules.parties.credit import (
    CreditDecision,
    CreditFigures,
    CreditPolicy,
    CreditReason,
    CreditTerms,
    check,
)

D = Decimal
WARN = CreditTerms(credit_limit=D(10000), credit_days=30, policy=CreditPolicy.WARN)
BLOCK = CreditTerms(credit_limit=D(10000), credit_days=30, policy=CreditPolicy.BLOCK)


@pytest.mark.parametrize(
    ("balance", "orders", "amount", "overdue", "terms", "decision", "reasons"),
    [
        ("4000", "0", "5000", None, WARN, CreditDecision.OK, []),
        ("4000", "0", "6000", None, WARN, CreditDecision.OK, []),  # exactly at the limit is fine
        ("4000", "0", "6000.01", None, WARN, CreditDecision.WARN, [CreditReason.OVER_LIMIT]),
        ("4000", "0", "6000.01", None, BLOCK, CreditDecision.BLOCK, [CreditReason.OVER_LIMIT]),
        (
            "4000",
            "3000",
            "4000",
            None,
            BLOCK,
            CreditDecision.BLOCK,
            [CreditReason.OVER_LIMIT],
        ),  # open orders count
        ("0", "0", "100", 31, BLOCK, CreditDecision.BLOCK, [CreditReason.OVERDUE]),
        ("0", "0", "100", 30, BLOCK, CreditDecision.OK, []),  # 30 days is within 30 credit days
        ("9000", "0", "2000", 45, WARN, CreditDecision.WARN, [CreditReason.OVER_LIMIT, CreditReason.OVERDUE]),
    ],
)
def test_limit_and_overdue_days_decide_by_policy(
    balance: str,
    orders: str,
    amount: str,
    overdue: int | None,
    terms: CreditTerms,
    decision: CreditDecision,
    reasons: list[CreditReason],
) -> None:
    figures = CreditFigures(ledger_balance=D(balance), open_orders=D(orders), oldest_overdue_days=overdue)
    result = check(D(amount), figures, terms)
    assert (result.decision, result.reasons) == (decision, reasons)


def test_no_limit_and_no_credit_days_never_stop_a_sale() -> None:
    terms = CreditTerms(credit_limit=None, credit_days=None, policy=CreditPolicy.BLOCK)
    result = check(D(10**9), CreditFigures(ledger_balance=D(10**9), oldest_overdue_days=999), terms)
    assert result.decision is CreditDecision.OK
    assert result.available is None


def test_a_pdc_in_hand_is_shown_but_never_reduces_the_balance() -> None:
    figures = CreditFigures(ledger_balance=D(9000), pdc_in_hand=D(8000))
    result = check(D(2000), figures, BLOCK)
    assert result.decision is CreditDecision.BLOCK, "the cheque has not cleared: it is not money yet"
    assert result.exposure == D(11000)
    assert result.available == D(-1000)
    assert (result.pdc_in_hand, result.exposure_after_pdc) == (D(8000), D(3000)), (
        "shown beside, for information"
    )


def test_only_someone_with_credit_override_may_pass_a_block() -> None:
    figures = CreditFigures(ledger_balance=D(9500))
    assert check(D(1000), figures, BLOCK).may_override is False
    assert check(D(1000), figures, BLOCK, may_override=True).may_override is True


amounts = st.decimals(min_value=0, max_value=D(10**8), places=2, allow_nan=False)


@given(balance=amounts, orders=amounts, amount=amounts, pdc=amounts, limit=amounts)
def test_pdcs_never_change_the_decision(
    balance: Decimal, orders: Decimal, amount: Decimal, pdc: Decimal, limit: Decimal
) -> None:
    terms = CreditTerms(credit_limit=limit, credit_days=None, policy=CreditPolicy.BLOCK)
    without = check(amount, CreditFigures(ledger_balance=balance, open_orders=orders), terms)
    with_pdc = check(
        amount, CreditFigures(ledger_balance=balance, open_orders=orders, pdc_in_hand=pdc), terms
    )
    assert (with_pdc.decision, with_pdc.exposure) == (without.decision, without.exposure)
    assert with_pdc.decision is (
        CreditDecision.BLOCK if balance + orders + amount > limit else CreditDecision.OK
    )
