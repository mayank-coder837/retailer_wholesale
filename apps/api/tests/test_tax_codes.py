"""Tax codes and line VAT, pure (M1.10a; A3, A10; posting rules 8, 29 to 31)."""

import uuid
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from app.modules.accounting.posting import Accounts, Line, Voucher, VoucherType, journal_for
from app.modules.purchasing import posting as purchase
from app.modules.tax import codes as tax
from app.modules.tax.codes import NoVatReason, PriceMode, Side, Treatment

D = Decimal
ZERO = D(0)
ROLES = Accounts(vat_input=uuid.uuid4(), vat_output=uuid.uuid4(), rounding=uuid.uuid4())
CASH, RENT = uuid.uuid4(), uuid.uuid4()

fils = st.integers(min_value=0, max_value=10**11).map(lambda n: D(n) / 100)
rates = st.sampled_from([D(0), D(5)])
modes = st.sampled_from([PriceMode.VAT_EXCLUSIVE, PriceMode.VAT_INCLUSIVE])


def test_the_registry_has_the_vat_201_table() -> None:
    sales = {c.code: c for c in tax.codes(Side.SALES)}
    purchases = {c.code: c for c in tax.codes(Side.PURCHASE)}
    assert set(sales) == {"SR", "ZR", "EX", "OS"}
    assert set(purchases) == {"SR", "SR-B", "ZR", "EX", "OS", "RC-S", "RC-G"}
    assert [(sales[c].net_box, sales[c].vat_box) for c in ("SR", "ZR", "EX", "OS")] == [
        ("1", "1"),
        ("4", None),
        ("5", None),
        (None, None),
    ]
    assert (purchases["SR"].net_box, purchases["SR-B"].net_box) == ("9", None)
    assert [(purchases[c].vat_box, purchases[c].recovered_box) for c in ("RC-S", "RC-G")] == [
        ("3", "10"),
        ("6", "10"),
    ]
    assert {c.treatment for c in purchases.values() if c.rate} == {
        Treatment.RECOVERABLE,
        Treatment.BLOCKED,
        Treatment.REVERSE_CHARGE,
    }


@given(amounts=st.lists(fils, min_size=1, max_size=30), rate=rates, mode=modes)
def test_line_vat_is_to_the_fils_and_the_lines_add_up(
    amounts: list[Decimal], rate: Decimal, mode: PriceMode
) -> None:
    """A3: each line's VAT is rounded half-up to the fils; the document's VAT is the sum of the lines; an
    inclusive line's net + VAT is exactly what was typed; the VAT is within half a fil of the exact figure."""
    lines = [tax.split(a, rate, mode) for a in amounts]
    for amount, (net, vat) in zip(amounts, lines, strict=True):
        assert vat == vat.quantize(D("0.01"))
        assert net + vat == amount if mode is PriceMode.VAT_INCLUSIVE else net == amount
        exact = amount * rate / (100 + rate) if mode is PriceMode.VAT_INCLUSIVE else amount * rate / 100
        assert abs(vat - exact) <= D("0.005")
    assert sum((v for _, v in lines), ZERO) == sum((tax.split(a, rate, mode)[1] for a in amounts), ZERO)


def test_inclusive_and_exclusive_by_hand() -> None:
    assert tax.split(D("105.00"), D(5), PriceMode.VAT_INCLUSIVE) == (D("100.00"), D("5.00"))
    assert tax.split(D("0.07"), D(5), PriceMode.VAT_INCLUSIVE) == (D("0.07"), D("0.00"))
    assert tax.split(D("0.10"), D(5), PriceMode.VAT_EXCLUSIVE) == (D("0.10"), D("0.01"))  # 0.005 rounds up
    assert tax.split(D("100.00"), D(5), PriceMode.NO_VAT) == (D("100.00"), ZERO)


def test_the_code_a_line_takes() -> None:
    base = {"typed": None, "followed": None, "party": None, "product": None}
    sales = {"registered": True, "mode": PriceMode.VAT_EXCLUSIVE, "no_vat_reason": None}
    assert tax.resolve(Side.SALES, **sales, **base) == "SR"
    assert tax.resolve(Side.SALES, **sales, **(base | {"product": "EX"})) == "EX"
    assert tax.resolve(Side.SALES, **sales, **(base | {"product": "EX", "party": "ZR"})) == "ZR"
    assert tax.resolve(Side.SALES, **sales, **(base | {"party": "ZR", "followed": "SR"})) == "SR"
    assert tax.resolve(Side.SALES, **sales, **(base | {"followed": "ZR", "typed": "EX"})) == "EX"
    no_vat = sales | {"mode": PriceMode.NO_VAT, "no_vat_reason": NoVatReason.EXEMPT}
    assert tax.resolve(Side.SALES, **no_vat, **(base | {"typed": "SR"})) == "EX"
    assert tax.resolve(Side.SALES, **(sales | {"registered": False}), **(base | {"typed": "SR"})) == "OS"
    assert tax.resolve(Side.PURCHASE, **(sales | {"registered": False}), **base) == "SR"
    assert (tax.legacy_code(Side.SALES, ZERO), tax.legacy_code(Side.PURCHASE, ZERO)) == ("ZR", "OS")


def _accounts() -> purchase.Accounts:
    return purchase.Accounts(*(uuid.uuid4() for _ in range(7)))


def test_a_bill_line_by_treatment() -> None:
    a, expense = _accounts(), uuid.uuid4()
    rows = {}
    for treatment in (Treatment.RECOVERABLE, Treatment.BLOCKED, Treatment.REVERSE_CHARGE, Treatment.NONE):
        entry = purchase.Entry(a)
        purchase.bill_direct(
            entry, expense, D(1000), D(50) if treatment is not Treatment.NONE else ZERO, treatment
        )
        rows[treatment] = (dict(entry.net), entry.supplier)
    assert rows[Treatment.RECOVERABLE] == ({expense: D(1000), a.vat_input: D(50)}, D(-1050))
    assert rows[Treatment.BLOCKED] == ({expense: D(1050)}, D(-1050))
    assert rows[Treatment.REVERSE_CHARGE] == (
        {expense: D(1000), a.vat_input: D(50), a.vat_output: D(-50)},
        D(-1000),
    )
    assert rows[Treatment.NONE] == ({expense: D(1000)}, D(-1000))


def test_returning_a_reverse_charge_line_undoes_both_sides() -> None:
    a = _accounts()
    entry = purchase.Entry(a)
    purchase.return_to_supplier(entry, D(40), D(2), D(40), Treatment.REVERSE_CHARGE)
    assert entry.supplier == D(40)
    assert (entry.net[a.vat_input], entry.net[a.vat_output], entry.net[a.inventory]) == (D(-2), D(2), D(-40))


def _expense(line_amount: str, code: str) -> Voucher:
    c = tax.get(Side.PURCHASE, code)
    assert c is not None
    line = Line(RENT, D(line_amount), ZERO, None, c.rate, None, c.code, c.treatment)
    return Voucher(VoucherType.EXPENSE, (line,), account_id=CASH)


def test_expense_vouchers_by_treatment() -> None:
    blocked = journal_for(_expense("200", "SR-B"), ROLES)
    assert [(x.account_id, x.debit, x.credit) for x in blocked.lines] == [
        (RENT, D(210), ZERO),
        (CASH, ZERO, D(210)),
    ]
    assert (blocked.total, blocked.vat_total) == (D(210), D(10))
    rc = journal_for(_expense("1000", "RC-S"), ROLES)
    assert [(x.account_id, x.debit, x.credit) for x in rc.lines] == [
        (RENT, D(1000), ZERO),
        (ROLES.vat_input, D(50), ZERO),
        (ROLES.vat_output, ZERO, D(50)),
        (CASH, ZERO, D(1000)),
    ]
    assert (rc.total, rc.vat_total) == (D(1000), D(50))
