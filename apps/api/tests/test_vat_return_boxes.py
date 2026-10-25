"""VAT 201 box mapping per tax code, pure (M1.10b; decisions-round-1 VAT 201 table; R3)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.modules.tax.codes import Side
from app.modules.tax.returns import VatFact, boxes, boxes_of, build

D = Decimal


def fact(side: Side, code: str, net: str, vat: str, emirate: str = "dubai") -> VatFact:
    return VatFact(uuid.uuid4(), "X-1", "x", date(2026, 10, 1), None, side, code, D(net), D(vat), emirate)


@pytest.mark.parametrize(
    ("side", "code", "vat", "expected"),
    [
        (Side.SALES, "SR", "5", [("1b", "100", "5")]),
        (Side.SALES, "ZR", "0", [("4", "100", "0")]),
        (Side.SALES, "EX", "0", [("5", "100", "0")]),
        (Side.SALES, "OS", "0", []),
        (Side.PURCHASE, "SR", "5", [("9", "100", "5")]),
        (Side.PURCHASE, "SR-B", "5", []),
        (Side.PURCHASE, "ZR", "0", []),
        (Side.PURCHASE, "EX", "0", []),
        (Side.PURCHASE, "OS", "0", []),
        (Side.PURCHASE, "RC-S", "5", [("3", "100", "5"), ("10", "100", "5")]),
        (Side.PURCHASE, "RC-G", "5", [("6", "100", "5"), ("10", "100", "5")]),
    ],
)
def test_each_code_reaches_its_boxes(
    side: Side, code: str, vat: str, expected: list[tuple[str, str, str]]
) -> None:
    assert boxes_of(fact(side, code, "100", vat)) == [(b, D(a), D(v)) for b, a, v in expected]


def test_box_one_has_a_line_per_emirate_in_the_forms_order() -> None:
    emirates = ["abu_dhabi", "dubai", "sharjah", "ajman", "umm_al_quwain", "ras_al_khaimah", "fujairah"]
    lines = [boxes_of(fact(Side.SALES, "SR", "1", "0.05", e))[0][0] for e in emirates]
    assert lines == ["1a", "1b", "1c", "1d", "1e", "1f", "1g"]
    assert [b.box for b in boxes()] == [*lines, *(str(n) for n in range(2, 15))]


def test_totals_and_the_net_payable() -> None:
    values = {
        v.box.box: (v.amount, v.vat)
        for v in build(
            [
                fact(Side.SALES, "SR", "1000", "50"),
                fact(Side.SALES, "SR", "-200", "-10", "sharjah"),  # a credit note from the Sharjah branch
                fact(Side.PURCHASE, "SR", "300", "15"),
                fact(Side.PURCHASE, "RC-S", "100", "5"),
            ]
        )
    }
    assert (values["1b"], values["1c"]) == ((D(1000), D(50)), (D(-200), D(-10)))
    assert values["8"] == (D(900), D(45))
    assert values["11"] == (D(400), D(20))
    assert (values["12"][1], values["13"][1], values["14"][1]) == (D(45), D(20), D(25))
