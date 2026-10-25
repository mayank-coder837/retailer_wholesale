"""Printing posted sales documents (M1.10c; ADR 0014): a tax invoice with both TRNs, Arabic, line and total
VAT; the return as a tax credit note naming its invoice; drafts and other types refused."""

import re
from decimal import Decimal

from httpx import AsyncClient

from tests.test_pricing_api import ok
from tests.test_sales_api import seller


async def test_a_posted_invoice_prints_as_a_tax_invoice_and_its_return_as_a_credit_note(
    client: AsyncClient,
) -> None:
    s = await seller(client)
    await ok(await client.patch(f"/parties/{s.customer}", json={"trn": "100000000000011"}, headers=s.h))
    lines = [s.line("3", unit_price="10.50"), s.line("7", unit_price="1.99")]
    inv = await s.post(s.doc("sales_invoice", lines, price_mode="vat_inclusive"))

    response = await client.get(f"/documents/{inv['id']}/print", headers=s.h)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    page = response.text
    assert '<span class="primary">Tax Invoice</span>' in page
    assert inv["number"] in page
    assert "100000000000003" in page  # the business's TRN
    assert "100000000000011" in page  # the customer's TRN
    assert "قماش" in page  # the item's Arabic name
    assert "Prices include VAT" in page
    printed = [D(x) for x in re.findall(r'<td class="n vat"><span dir="ltr">([\d,.]+)</span>', page)]
    total = re.search(r'<td class="n vat-total"><span dir="ltr">([\d,.]+)</span>', page)
    assert total is not None
    assert printed == [D("1.50"), D("0.66")]
    assert sum(printed) == D(total.group(1)) == D(inv["tax_total"])

    arabic = (await client.get(f"/documents/{inv['id']}/print", params={"lang": "ar"}, headers=s.h)).text
    assert '<html lang="ar" dir="rtl">' in arabic

    ret = await s.post(
        s.doc("sales_return", [s.line("1", source_line_id=inv["lines"][0]["id"])], price_mode="vat_inclusive")
    )
    note = (await client.get(f"/documents/{ret['id']}/print", headers=s.h)).text
    assert '<span class="primary">Tax Credit Note</span>' in note
    assert inv["number"] in note, "a credit note names the invoice it corrects"


async def test_only_posted_invoices_and_returns_print(client: AsyncClient) -> None:
    s = await seller(client)
    draft = await ok(
        await client.post(
            "/sales-documents", json=s.doc("sales_invoice", [s.line("1", unit_price="10")]), headers=s.h
        ),
        201,
    )
    refused = await client.get(f"/documents/{draft['id']}/print", headers=s.h)
    assert (refused.status_code, refused.json()["code"]) == (409, "not_printable")
    order = await s.post(s.doc("sales_order", [s.line("1", unit_price="10")]))
    assert (await client.get(f"/documents/{order['id']}/print", headers=s.h)).status_code == 409
    bad_lang = await client.get(f"/documents/{order['id']}/print", params={"lang": "fr"}, headers=s.h)
    assert bad_lang.status_code == 422


def D(text: str) -> Decimal:  # noqa: N802 — reads like the Decimal it builds
    return Decimal(text.replace(",", ""))
