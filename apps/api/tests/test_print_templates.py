"""Tax invoice and credit note templates, pure (M1.10c; A3; ADR 0014): snapshots per language, escaping,
and the printed line VAT adding up to the printed total. Run with UPDATE_SNAPSHOTS=1 to accept a change."""

import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.documents.printing import PrintDoc, PrintLine, PrintParty, render

SNAPSHOTS = Path(__file__).parent / "snapshots"
D = Decimal

SUPPLIER = PrintParty("Al Noor Trading LLC", "شركة النور للتجارة", "100000000000003", ["Deira, Dubai"])
CUSTOMER = PrintParty("Gulf Fabrics LLC", "أقمشة الخليج", "100000000000011", ["Al Qasimia", "Sharjah"])
LINES = [
    PrintLine(
        "Poplin fabric",
        "قماش بوبلين",
        "FAB-01",
        D("20"),
        "m",
        D("10.5"),
        D("200.00"),
        D(5),
        D("10.00"),
        D("210.00"),
    ),
    PrintLine("Buttons", "أزرار", "BTN", D("3"), "box", D("1.99"), D("5.69"), D(5), D("0.28"), D("5.97")),
]


def invoice(**more: object) -> PrintDoc:
    fields: dict[str, object] = {
        "kind": "tax_invoice",
        "number": "INV-2026-00001",
        "doc_date": date(2026, 10, 25),
        "due_date": date(2026, 11, 24),
        "currency": "AED",
        "supplier": SUPPLIER,
        "customer": CUSTOMER,
        "lines": LINES,
        "net_total": D("205.69"),
        "vat_total": D("10.28"),
        "round_off": D(0),
        "total": D("215.97"),
    }
    fields.update(more)
    return PrintDoc(**fields)  # type: ignore[arg-type]


def check_snapshot(name: str, html: str) -> None:
    path = SNAPSHOTS / name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_text(html, encoding="utf-8", newline="\n")
    assert path.exists(), f"no snapshot {name}; run with UPDATE_SNAPSHOTS=1 to create it"
    assert html == path.read_text(encoding="utf-8"), (
        f"{name} changed; run with UPDATE_SNAPSHOTS=1 if intended"
    )


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_tax_invoice_snapshot(lang: str) -> None:
    check_snapshot(f"tax_invoice_{lang}.html", render(invoice(), lang))


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_credit_note_snapshot(lang: str) -> None:
    note = invoice(
        kind="tax_credit_note", number="SR-2026-00001", due_date=None, original_invoice="INV-2026-00001"
    )
    check_snapshot(f"tax_credit_note_{lang}.html", render(note, lang))


def test_the_page_language_sets_direction_and_order() -> None:
    en, ar = render(invoice(), "en"), render(invoice(), "ar")
    assert '<html lang="en" dir="ltr">' in en
    assert '<html lang="ar" dir="rtl">' in ar
    assert '<span class="primary">Tax Invoice</span> <span class="secondary">فاتورة ضريبية</span>' in en
    assert '<span class="primary">فاتورة ضريبية</span> <span class="secondary">Tax Invoice</span>' in ar
    for page in (en, ar):
        assert "100000000000003" in page  # supplier TRN
        assert "100000000000011" in page  # customer TRN


def test_printed_line_vat_adds_up_to_the_printed_total() -> None:
    page = render(invoice(), "en")
    lines = re.findall(r'<td class="n vat"><span dir="ltr">([\d,.]+)</span>', page)
    total = re.search(r'<td class="n vat-total"><span dir="ltr">([\d,.]+)</span>', page)
    assert total is not None
    assert lines == ["10.00", "0.28"]
    assert sum(D(x.replace(",", "")) for x in lines) == D(total.group(1).replace(",", ""))


def test_text_from_users_is_escaped() -> None:
    party = PrintParty('<script>alert("x")</script>', "س", None)
    page = render(invoice(customer=party), "en")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_simplified_and_plain_invoices_have_their_titles() -> None:
    assert "Simplified Tax Invoice" in render(invoice(kind="simplified"), "en")
    plain = render(invoice(kind="invoice", supplier=PrintParty("Small Shop", "متجر", None)), "en")
    assert '<span class="primary">Invoice</span>' in plain
    assert "Tax Invoice" not in plain
