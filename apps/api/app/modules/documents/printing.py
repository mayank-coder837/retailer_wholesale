"""Printable tax invoices and credit notes (M1.10c; A3, A10; ADR 0014).

``render`` is pure — a ``PrintDoc`` in, HTML out — so the templates are snapshot-tested. ``print_html``
gathers a posted sales invoice or return through the services that own the data. Chromium turns the page into
a PDF.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError

TEMPLATES = Path(__file__).parent / "templates"
PRINTABLE = frozenset({"sales_invoice", "sales_return"})

# (English, Arabic) for every word on the page; the page language decides which comes first.
LABELS: dict[str, tuple[str, str]] = {
    "tax_invoice": ("Tax Invoice", "فاتورة ضريبية"),
    "simplified": ("Simplified Tax Invoice", "فاتورة ضريبية مبسطة"),
    "invoice": ("Invoice", "فاتورة"),
    "tax_credit_note": ("Tax Credit Note", "إشعار دائن ضريبي"),
    "credit_note": ("Credit Note", "إشعار دائن"),
    "supplier": ("Supplier", "المورد"),
    "customer": ("Customer", "العميل"),
    "trn": ("TRN", "رقم التسجيل الضريبي"),
    "number": ("Number", "الرقم"),
    "date": ("Date", "التاريخ"),
    "due_date": ("Due date", "تاريخ الاستحقاق"),
    "original_invoice": ("Original invoice", "الفاتورة الأصلية"),
    "line": ("#", "م"),
    "item": ("Item", "الصنف"),
    "qty": ("Qty", "الكمية"),
    "unit_price": ("Unit price", "سعر الوحدة"),
    "net": ("Net", "الصافي"),
    "vat_rate": ("VAT %", "نسبة الضريبة"),
    "vat": ("VAT", "الضريبة"),
    "total": ("Total", "الإجمالي"),
    "net_total": ("Total excluding VAT", "الإجمالي قبل الضريبة"),
    "vat_total": ("Total VAT", "إجمالي الضريبة"),
    "round_off": ("Round-off", "التقريب"),
    "grand_total": ("Total including VAT", "الإجمالي شامل الضريبة"),
    "grand_total_no_vat": ("Total", "الإجمالي"),
    "prices_inclusive": ("Prices include VAT", "الأسعار شاملة الضريبة"),
    "amounts_in": ("Amounts in", "المبالغ بعملة"),
}


class NotPrintableError(ConflictError):
    code = "not_printable"
    title = "Only a posted sales invoice or return prints as a tax document"


@dataclass(frozen=True, slots=True)
class PrintParty:
    name_en: str
    name_ar: str
    trn: str | None
    address: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PrintLine:
    name_en: str
    name_ar: str
    sku: str | None
    qty: Decimal
    unit: str | None
    unit_price: Decimal
    net: Decimal
    vat_rate: Decimal
    vat: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class PrintDoc:
    kind: str  # a LABELS key: tax_invoice, simplified, invoice, tax_credit_note, credit_note
    number: str
    doc_date: date
    due_date: date | None
    currency: str
    supplier: PrintParty
    customer: PrintParty
    lines: list[PrintLine]
    net_total: Decimal
    vat_total: Decimal
    round_off: Decimal
    total: Decimal
    inclusive: bool = False
    original_invoice: str | None = None  # credit notes name the invoice they correct


def money(amount: Decimal) -> str:
    """1234.5 → "1,234.50" (2 dp, half-up)."""
    return f"{amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def quantity(amount: Decimal) -> str:
    text = f"{amount.normalize():f}"
    return text if "." not in text else text.rstrip("0").rstrip(".")


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters["money"] = money
    env.filters["quantity"] = quantity
    return env


ENV = _environment()


def render(doc: PrintDoc, lang: str = "en") -> str:
    """The page in ``lang`` (en: left to right, English first; ar: right to left, Arabic first)."""
    first, second = (0, 1) if lang == "en" else (1, 0)
    labels = {k: (v[first], v[second]) for k, v in LABELS.items()}
    return ENV.get_template("tax_document.html").render(
        doc=doc, label=labels, lang=lang, dir="ltr" if lang == "en" else "rtl", primary=first
    )


async def print_html(session: AsyncSession, doc_id: uuid.UUID, lang: str) -> str:
    # The modules that own the data read it (one door).
    from app.modules.catalog import products as catalog
    from app.modules.documents import service as documents
    from app.modules.parties import service as parties
    from app.modules.sales import service as sales
    from app.modules.tenancy import service as tenancy

    doc = await documents.get_document(session, doc_id)
    if doc.doc_type not in PRINTABLE or doc.status == "draft" or doc.number is None:
        raise NotPrintableError()
    out = await sales.get_sales_document(session, doc.id)
    business = await tenancy.get_business(session, doc.business_id)
    branch = await tenancy.get_branch(session, doc.branch_id)
    assert doc.party_id is not None
    party = await parties.get_party(session, doc.party_id)
    billing = next((a for a in party.addresses if a.type == "billing" and a.is_default), None)
    billing = billing or next((a for a in party.addresses if a.type == "billing"), None)
    customer_address = (
        [x for x in (billing.line1, billing.line2, billing.city) if x] if billing is not None else []
    )
    lines: list[PrintLine] = []
    for line in out.lines:
        item = await catalog.stock_item(session, line.variant_id) if line.variant_id else None
        lines.append(
            PrintLine(
                name_en=(line.description or (item.name_en if item else "")),
                name_ar=item.name_ar if item else "",
                sku=line.sku,
                qty=line.qty,
                unit=line.unit,
                unit_price=line.unit_price or Decimal(0),
                net=line.net_amount,
                vat_rate=line.vat_rate,
                vat=line.vat_amount,
                total=line.line_total,
            )
        )
    registered = doc.invoice_kind != "invoice"
    if doc.doc_type == "sales_return":
        kind = "tax_credit_note" if registered else "credit_note"
    else:
        kind = doc.invoice_kind or "tax_invoice"
    return render(
        PrintDoc(
            kind=kind,
            number=doc.number,
            doc_date=doc.doc_date,
            due_date=doc.due_date,
            currency=doc.currency,
            supplier=PrintParty(
                business.legal_name_en,
                business.legal_name_ar,
                business.trn if business.vat_registered else None,
                [x for x in (branch.address,) if x],
            ),
            customer=PrintParty(party.name_en, party.name_ar, party.trn, customer_address),
            lines=lines,
            net_total=out.subtotal,
            vat_total=out.tax_total,
            round_off=out.round_off,
            total=out.total,
            inclusive=out.price_mode == "vat_inclusive",
            original_invoice=await documents.source_number(session, doc.id),
        ),
        lang,
    )
