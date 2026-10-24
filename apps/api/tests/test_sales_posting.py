"""Sales posting rules, pure (posting-rules.md rules 1 to 4 and 34; A8): each test is the rule's example."""

import uuid
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.modules.sales import posting

D = Decimal
A = posting.Accounts(*(uuid.uuid4() for _ in range(9)))
CUSTOMER = uuid.uuid4()
CASH = uuid.uuid4()


def lines(entry: posting.Entry, cash: uuid.UUID | None = None) -> dict[uuid.UUID, tuple[Decimal, Decimal]]:
    return {x.account_id: (x.debit, x.credit) for x in entry.lines(CUSTOMER, cash_account=cash)}


def test_rule_1_credit_sale_without_a_delivery_note() -> None:
    e = posting.Entry(A)
    posting.sale(e, D(1000), D(50))
    posting.cost_from_stock(e, D(600))
    assert lines(e) == {
        A.ar_control: (D(1050), D(0)),
        A.sales: (D(0), D(1000)),
        A.vat_output: (D(0), D(50)),
        A.cogs: (D(600), D(0)),
        A.inventory: (D(0), D(600)),
    }
    customer = next(x for x in e.lines(CUSTOMER) if x.account_id == A.ar_control)
    assert customer.party_id == CUSTOMER
    assert customer.allocation_key is not None  # it opens the receivable bill


def test_rule_1_cash_sale_debits_cash_and_names_no_customer() -> None:
    e = posting.Entry(A)
    posting.sale(e, D(1000), D(50))
    assert lines(e, CASH)[CASH] == (D(1050), D(0))
    assert all(x.party_id is None and x.allocation_key is None for x in e.lines(CUSTOMER, cash_account=CASH))


def test_rules_2_and_3_delivery_then_invoice_clear_gdni() -> None:
    delivered = posting.Entry(A)
    posting.delivery(delivered, D(600))
    assert lines(delivered) == {A.gdni: (D(600), D(0)), A.inventory: (D(0), D(600))}
    invoiced = posting.Entry(A)
    posting.sale(invoiced, D(1000), D(50))
    posting.cost_from_delivery(invoiced, D(600))
    assert lines(invoiced) == {
        A.ar_control: (D(1050), D(0)),
        A.sales: (D(0), D(1000)),
        A.vat_output: (D(0), D(50)),
        A.cogs: (D(600), D(0)),
        A.gdni: (D(0), D(600)),
    }


def test_rule_4_return_against_an_invoice() -> None:
    """Goods sold for 200 (cost 120) come back."""
    e = posting.Entry(A)
    posting.sales_return(e, D(200), D(10), D(120))
    assert lines(e) == {
        A.sales_returns: (D(200), D(0)),
        A.vat_output: (D(10), D(0)),
        A.ar_control: (D(0), D(210)),
        A.inventory: (D(120), D(0)),
        A.cogs: (D(0), D(120)),
    }


def test_rule_34_round_off_on_the_total() -> None:
    e = posting.Entry(A)
    posting.sale(e, D("1049.97"), D(0))
    posting.round_off(e, D("0.03"))
    assert lines(e)[A.ar_control] == (D("1050.00"), D(0))
    assert lines(e)[A.rounding] == (D(0), D("0.03"))  # type: ignore[index]
    no_account = posting.Entry(posting.Accounts(*(uuid.uuid4() for _ in range(8)), rounding=None))
    posting.round_off(no_account, D(0))  # nothing to round: no account needed
    with pytest.raises(ValueError, match="rounding"):
        posting.round_off(no_account, D("0.01"))


def test_the_last_invoice_takes_whatever_gdni_is_left() -> None:
    assert posting.share(D("100.00"), D(3), D(1)) == D("33.33")
    assert posting.share(D("66.67"), D(2), D(2)) == D("66.67")


money = st.decimals(min_value=D(0), max_value=D("99999.99"), places=2)


@given(
    st.lists(st.tuples(st.integers(min_value=0, max_value=4), money, money, money), min_size=1, max_size=8),
    st.decimals(min_value=D("-0.50"), max_value=D("0.50"), places=2),
)
def test_every_sales_entry_balances(
    steps: list[tuple[int, Decimal, Decimal, Decimal]], rounding: Decimal
) -> None:
    e = posting.Entry(A)
    for kind, net, vat, cost in steps:
        if kind == 0:
            posting.delivery(e, cost)
        elif kind == 1:
            posting.sale(e, net, vat)
            posting.cost_from_stock(e, cost)
        elif kind == 2:
            posting.sale(e, net, vat)
            posting.cost_from_delivery(e, cost)
        elif kind == 3:
            posting.sales_return(e, net, vat, cost)
        else:
            e.revaluation(cost)
    posting.round_off(e, rounding)
    out = e.lines(CUSTOMER)
    assert sum((x.debit for x in out), D(0)) == sum((x.credit for x in out), D(0))
