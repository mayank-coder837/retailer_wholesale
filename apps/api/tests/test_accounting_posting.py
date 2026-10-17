"""Voucher posting rules, pure (posting-rules.md rules 5, 6, 10 to 14 and 34; decision A3 for VAT)."""

import uuid
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.money import RoundOffMode, round_off
from app.modules.accounting.posting import (
    LINE_SIDE,
    Accounts,
    Line,
    UnbalancedError,
    Voucher,
    VoucherType,
    journal_for,
    vat_of,
)

BANK, CASH, CUSTOMER_AC, SUPPLIER_AC, RENT, SALES_RETURNS, PPV = (uuid.uuid4() for _ in range(7))
ALI, MILL = uuid.uuid4(), uuid.uuid4()
ROLES = Accounts(vat_input=uuid.uuid4(), vat_output=uuid.uuid4(), rounding=uuid.uuid4())
D = Decimal
ZERO = D(0)


def lines_of(voucher: Voucher, round_by: Decimal = ZERO) -> list[tuple[uuid.UUID, Decimal, Decimal]]:
    return [(x.account_id, x.debit, x.credit) for x in journal_for(voucher, ROLES, round_off=round_by).lines]


def credit_line(account: uuid.UUID, amount: str, party: uuid.UUID | None = None, vat: str = "0") -> Line:
    return Line(account, ZERO, D(amount), party, D(vat))


def debit_line(account: uuid.UUID, amount: str, party: uuid.UUID | None = None, vat: str = "0") -> Line:
    return Line(account, D(amount), ZERO, party, D(vat))


def test_rule_6_receipt_debits_the_bank_and_credits_the_customer() -> None:
    receipt = Voucher(VoucherType.RECEIPT, (credit_line(CUSTOMER_AC, "1050", ALI),), account_id=BANK)
    posted = journal_for(receipt, ROLES)
    assert lines_of(receipt) == [(CUSTOMER_AC, ZERO, D(1050)), (BANK, D(1050), ZERO)]
    assert posted.total == D(1050)
    assert posted.lines[0].party_id == ALI
    assert posted.lines[0].allocation_key == 0  # the customer's line carries the bill-wise choices


def test_rule_11_payment_debits_the_supplier_and_credits_the_bank() -> None:
    payment = Voucher(VoucherType.PAYMENT, (debit_line(SUPPLIER_AC, "1050", MILL),), account_id=BANK)
    assert lines_of(payment) == [(SUPPLIER_AC, D(1050), ZERO), (BANK, ZERO, D(1050))]


def test_rule_12_expense_with_vat_debits_expense_and_input_vat() -> None:
    rent = Voucher(VoucherType.EXPENSE, (debit_line(RENT, "200", vat="5"),), account_id=CASH)
    posted = journal_for(rent, ROLES)
    assert lines_of(rent) == [(RENT, D(200), ZERO), (ROLES.vat_input, D(10), ZERO), (CASH, ZERO, D(210))]
    assert (posted.total, posted.vat_total) == (D(210), D(10))


def test_rule_5_credit_note_credits_the_customer_with_output_vat() -> None:
    note = Voucher(
        VoucherType.CREDIT_NOTE,
        (debit_line(SALES_RETURNS, "100", vat="5"),),
        party_id=ALI,
        party_account_id=CUSTOMER_AC,
    )
    posted = journal_for(note, ROLES)
    assert lines_of(note) == [
        (SALES_RETURNS, D(100), ZERO),
        (ROLES.vat_output, D(5), ZERO),
        (CUSTOMER_AC, ZERO, D(105)),
    ]
    assert posted.lines[-1].party_id == ALI
    assert posted.lines[-1].allocation_key == -1  # the header party's bill-wise choices


def test_rule_10_debit_note_debits_the_supplier_and_reduces_input_vat() -> None:
    note = Voucher(
        VoucherType.DEBIT_NOTE,
        (credit_line(PPV, "100", vat="5"),),
        party_id=MILL,
        party_account_id=SUPPLIER_AC,
    )
    assert lines_of(note) == [(PPV, ZERO, D(100)), (ROLES.vat_input, ZERO, D(5)), (SUPPLIER_AC, D(105), ZERO)]


def test_rule_13_contra_and_rule_14_journal_post_as_entered() -> None:
    contra = Voucher(VoucherType.CONTRA, (debit_line(BANK, "5000"), credit_line(CASH, "5000")))
    assert lines_of(contra) == [(BANK, D(5000), ZERO), (CASH, ZERO, D(5000))]
    unbalanced = Voucher(VoucherType.JOURNAL, (debit_line(RENT, "100"), credit_line(CASH, "99.99")))
    with pytest.raises(UnbalancedError, match=r"differ by 0.01"):
        journal_for(unbalanced, ROLES)


def test_vat_is_rounded_per_line_and_the_total_is_their_sum() -> None:
    """A3: three lines of 0.10 at 5% are 0.01 VAT each (half-up), so the total is 0.03, not 0.015 → 0.02."""
    lines = tuple(debit_line(RENT, "0.10", vat="5") for _ in range(3))
    posted = journal_for(Voucher(VoucherType.EXPENSE, lines, account_id=CASH), ROLES)
    assert posted.line_vat == (D("0.01"),) * 3
    assert posted.vat_total == D("0.03")
    assert vat_of(D("10.10"), D(5)) == D("0.51")  # 0.505 rounds half up


def test_rule_34_receipt_round_off_goes_to_the_round_off_account() -> None:
    """The customer owes 1,049.97 and the cash is rounded to the nearest 0.05: 1,049.95 in the bank."""
    receipt = Voucher(VoucherType.RECEIPT, (credit_line(CUSTOMER_AC, "1049.97", ALI),), account_id=CASH)
    adjustment = round_off(D("1049.97"), RoundOffMode.NEAREST, D("0.05")).adjustment
    posted = journal_for(receipt, ROLES, round_off=adjustment)
    assert lines_of(receipt, adjustment) == [
        (CUSTOMER_AC, ZERO, D("1049.97")),
        (CASH, D("1049.95"), ZERO),
        (ROLES.rounding, D("0.02"), ZERO),
    ]
    assert (posted.total, posted.round_off) == (D("1049.95"), D("-0.02"))


def test_rule_34_payment_rounded_up_debits_the_difference() -> None:
    payment = Voucher(VoucherType.PAYMENT, (debit_line(SUPPLIER_AC, "999.40", MILL),), account_id=BANK)
    adjustment = round_off(D("999.40"), RoundOffMode.UP, D("1.00")).adjustment
    assert lines_of(payment, adjustment) == [
        (SUPPLIER_AC, D("999.40"), ZERO),
        (BANK, ZERO, D("1000.00")),
        (ROLES.rounding, D("0.60"), ZERO),
    ]


amounts = st.decimals(min_value=D("0.01"), max_value=D("999999.99"), places=2)
single_entry = st.sampled_from(sorted(LINE_SIDE))


@given(
    kind=single_entry,
    raw=st.lists(st.tuples(amounts, st.sampled_from([D(0), D(5)])), min_size=1, max_size=8),
    rounding=st.sampled_from([ZERO, D("0.02"), D("-0.03"), D("0.50")]),
)
def test_every_single_entry_voucher_posts_balanced_lines(
    kind: VoucherType, raw: list[tuple[Decimal, Decimal]], rounding: Decimal
) -> None:
    side = LINE_SIDE[kind]
    with_vat = kind in (VoucherType.EXPENSE, VoucherType.CREDIT_NOTE, VoucherType.DEBIT_NOTE)
    lines = tuple(
        Line(
            RENT,
            a if side == "debit" else ZERO,
            a if side == "credit" else ZERO,
            None,
            v if with_vat else ZERO,
        )
        for a, v in raw
    )
    voucher = Voucher(kind, lines, account_id=CASH, party_id=ALI, party_account_id=CUSTOMER_AC)
    round_by = rounding if kind in (VoucherType.RECEIPT, VoucherType.PAYMENT) else ZERO
    posted = journal_for(voucher, ROLES, round_off=round_by)

    assert sum(x.debit for x in posted.lines) == sum(x.credit for x in posted.lines)
    assert all((x.debit > 0) != (x.credit > 0) and min(x.debit, x.credit) == 0 for x in posted.lines)
    assert posted.vat_total == sum(posted.line_vat, ZERO)
    assert posted.total == sum((a for a, _ in raw), ZERO) + posted.vat_total + round_by
