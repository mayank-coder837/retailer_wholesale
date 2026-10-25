"""Purchase posting rules, pure (posting-rules.md rules 7 to 9; A4)."""

import uuid
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.modules.purchasing import posting

D = Decimal
A = posting.Accounts(*(uuid.uuid4() for _ in range(7)))
SUPPLIER = uuid.uuid4()
EXPENSE = uuid.uuid4()


def lines(entry: posting.Entry) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    return {x.account_id: (x.debit, x.credit) for x in entry.lines(SUPPLIER)}


def test_rule_7_receipt() -> None:
    e = posting.Entry(A)
    posting.receipt(e, D(1000))
    assert lines(e) == {A.inventory: (D(1000), D(0)), A.grni: (D(0), D(1000))}


def test_rule_8_bill_with_a_price_difference_goes_to_the_variance_account() -> None:
    """Billed 1,020 for goods received at 1,000, VAT 51 (the rule's own example)."""
    e = posting.Entry(A)
    posting.bill_matched(e, D(1020), D(51), D(1000))
    assert lines(e) == {
        A.grni: (D(1000), D(0)),
        A.price_variance: (D(20), D(0)),
        A.vat_input: (D(51), D(0)),
        A.ap_control: (D(0), D(1071)),
    }
    supplier = next(x for x in e.lines(SUPPLIER) if x.account_id == A.ap_control)
    assert supplier.party_id == SUPPLIER


def test_rule_8_cheaper_bill_credits_the_variance_and_expense_lines_debit_their_account() -> None:
    e = posting.Entry(A)
    posting.bill_matched(e, D(990), D("49.50"), D(1000))
    posting.bill_direct(e, EXPENSE, D(200), D(10))
    assert lines(e) == {
        A.grni: (D(1000), D(0)),
        A.price_variance: (D(0), D(10)),
        A.vat_input: (D("59.50"), D(0)),
        EXPENSE: (D(200), D(0)),
        A.ap_control: (D(0), D("1249.50")),
    }


def test_rule_9_return_to_the_supplier() -> None:
    """Goods costing 500 go back at their bill price of 500 + VAT 25 (the rule's example)."""
    e = posting.Entry(A)
    posting.return_to_supplier(e, D(500), D(25), D(500))
    assert lines(e) == {A.ap_control: (D(525), D(0)), A.inventory: (D(0), D(500)), A.vat_input: (D(0), D(25))}


def test_rule_9_return_when_the_average_differs_from_the_price() -> None:
    e = posting.Entry(A)
    posting.return_to_supplier(e, D("52.50"), D("2.63"), D(50))
    assert lines(e)[A.cost_adjustment] == (D(0), D("2.50"))
    e = posting.Entry(A)
    posting.return_unbilled(e, D(48), D("41.51"))
    assert lines(e) == {
        A.grni: (D(48), D(0)),
        A.inventory: (D(0), D("41.51")),
        A.cost_adjustment: (D(0), D("6.49")),
    }


def test_the_last_bill_clears_whatever_grni_is_left() -> None:
    assert posting.share(D(100), D(3), D(1)) == D("33.33")
    assert posting.share(D("66.67"), D(2), D(2)) == D("66.67")


amounts = st.decimals(min_value=D(0), max_value=D(99999), places=2)


@given(st.lists(st.tuples(st.integers(0, 4), amounts, amounts, amounts), min_size=1, max_size=10))
def test_every_purchase_entry_balances(steps: list[tuple[int, Decimal, Decimal, Decimal]]) -> None:
    e = posting.Entry(A)
    for kind, a, b, c in steps:
        if kind == 0:
            posting.receipt(e, a)
        elif kind == 1:
            posting.bill_matched(e, a, b, c)
        elif kind == 2:
            posting.bill_direct(e, EXPENSE, a, b)
        elif kind == 3:
            posting.return_to_supplier(e, a, b, c)
        else:
            posting.return_unbilled(e, a, b)
        e.revaluation(c - a)
    out = e.lines(SUPPLIER)
    assert sum(x.debit for x in out) == sum(x.credit for x in out)
    assert all((x.debit > 0) != (x.credit > 0) for x in out)
