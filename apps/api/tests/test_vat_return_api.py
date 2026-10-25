"""The VAT 201 draft (M1.10b; VAT 201 table, A10, R3): boxes per tax code, Box 1 by the branch's emirate,
credit notes and reversals in their own period, cancelled documents gone, drill-down adding up to each box."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient

from tests.test_pricing_api import ok
from tests.test_sales_api import TODAY, Seller, seller

EARLIER = TODAY.replace(day=1) - timedelta(days=10)  # a day in the previous month
MONTH = {"from": str(TODAY.replace(day=1)), "to": str(TODAY)}
LAST_MONTH = {"from": str(EARLIER.replace(day=1)), "to": str(TODAY.replace(day=1) - timedelta(days=1))}


async def vat_return(s: Seller, period: dict[str, str]) -> dict[str, tuple[str, str]]:
    params = {"business_id": s.shop.business_id, **period}
    body = await ok(await s.client.get("/vat-returns/draft", params=params, headers=s.h))
    return {b["box"]: (b["amount"], b["vat"]) for b in body["boxes"]}


async def lines(s: Seller, period: dict[str, str], box: str) -> list[dict[str, Any]]:
    params = {"business_id": s.shop.business_id, "box": box, **period}
    return list(await ok(await s.client.get("/vat-returns/draft/lines", params=params, headers=s.h)))


async def supplier_and_accounts(s: Seller) -> tuple[str, dict[str, str]]:
    body = {"name_en": "Overseas", "name_ar": "مورد", "is_supplier": True}
    supplier = (await ok(await s.client.post("/parties", json=body, headers=s.h), 201))["id"]
    chart = await ok(await s.client.get("/accounts", params={"business_id": s.shop.business_id}, headers=s.h))
    return supplier, {a["code"]: a["id"] for a in chart}


async def bill(s: Seller, supplier: str, account: str, amount: str, code: str) -> Any:
    body = {
        "doc_type": "purchase_bill",
        "business_id": s.shop.business_id,
        "branch_id": s.shop.branch_a,
        "doc_date": str(TODAY),
        "party_id": supplier,
        "supplier_ref": f"B-{code}-{amount}",
        "lines": [{"account_id": account, "qty": "1", "unit_price": amount, "tax_code": code}],
    }
    response = await s.client.post("/purchase-documents", params={"post": "true"}, json=body, headers=s.h)
    return await ok(response, 201)


async def test_every_tax_code_lands_in_its_box_and_the_totals_add_up(client: AsyncClient) -> None:
    s = await seller(client)
    for price, code in (("1000", "SR"), ("200", "ZR"), ("100", "EX"), ("50", "OS")):
        await s.post(s.doc("sales_invoice", [s.line("1", unit_price=price, tax_code=code)]))
    supplier, accounts = await supplier_and_accounts(s)
    await bill(s, supplier, accounts["6100"], "500", "SR")
    await bill(s, supplier, accounts["6100"], "100", "SR-B")
    await bill(s, supplier, accounts["6100"], "1000", "RC-S")
    expense = {
        "doc_type": "expense",
        "business_id": s.shop.business_id,
        "branch_id": s.shop.branch_a,
        "doc_date": str(TODAY),
        "account_id": accounts["1110"],
        "lines": [{"account_id": accounts["6100"], "amount": "200", "tax_code": "SR"}],
    }
    await ok(await client.post("/vouchers", params={"post": "true"}, json=expense, headers=s.h), 201)

    boxes = await vat_return(s, MONTH)
    zero = ("0.0000", "0.0000")
    assert boxes["1b"] == ("1000.0000", "50.0000"), "Dubai: the head office's emirate"
    assert boxes["1a"] == boxes["1c"] == zero
    assert (boxes["3"], boxes["4"], boxes["5"], boxes["6"]) == (
        ("1000.0000", "50.0000"),
        ("200.0000", "0.0000"),
        ("100.0000", "0.0000"),
        zero,
    )
    assert boxes["8"] == ("2300.0000", "100.0000")
    assert boxes["9"] == ("700.0000", "35.0000"), "the SR bill and the expense voucher; SR-B is not reported"
    assert boxes["10"] == ("1000.0000", "50.0000"), "reverse charge recovered"
    assert boxes["11"] == ("1700.0000", "85.0000")
    assert (boxes["12"][1], boxes["13"][1], boxes["14"][1]) == ("100.0000", "85.0000", "15.0000")

    behind = await lines(s, MONTH, "9")
    assert sorted((x["doc_type"], x["amount"], x["vat"]) for x in behind) == [
        ("expense", "200.0000", "10.0000"),
        ("purchase_bill", "500.0000", "25.0000"),
    ]
    assert sum(Decimal(x["amount"]) for x in behind) == Decimal(boxes["9"][0])
    assert [x["party_name_en"] for x in await lines(s, MONTH, "1b")] == ["Ali Traders"]


async def test_box_one_follows_the_branch_not_the_customer(client: AsyncClient) -> None:
    s = await seller(client)
    store = {"code": "SHJ", "name_en": "Sharjah store", "name_ar": "مخزن الشارقة", "is_default": True}
    response = await client.post(f"/branches/{s.shop.branch_b}/warehouses", json=store, headers=s.h)
    sharjah_store = (await ok(response, 201))["id"]
    await s.post(s.doc("sales_invoice", [s.line("2", unit_price="100")]))
    sharjah_sale = s.doc(
        "sales_invoice",
        [s.line("1", unit_price="300")],
        branch_id=s.shop.branch_b,
        warehouse_id=sharjah_store,
    )
    await s.post(sharjah_sale)
    boxes = await vat_return(s, MONTH)
    assert (boxes["1b"], boxes["1c"]) == (("200.0000", "10.0000"), ("300.0000", "15.0000"))


async def test_a_return_reduces_the_sale_in_its_own_period(client: AsyncClient) -> None:
    s = await seller(client)
    inv = await s.post(s.doc("sales_invoice", [s.line("10", unit_price="100")], doc_date=str(EARLIER)))
    await s.post(s.doc("sales_return", [s.line("4", source_line_id=inv["lines"][0]["id"])]))
    before = await vat_return(s, LAST_MONTH)
    assert before["1b"] == ("1000.0000", "50.0000"), "the invoice's period is unchanged"
    now = await vat_return(s, MONTH)
    assert now["1b"] == ("-400.0000", "-20.0000")
    assert now["14"][1] == "-20.0000", "refundable this period"


async def test_cancelled_invoices_disappear_and_reversals_count_on_their_own_date(
    client: AsyncClient,
) -> None:
    s = await seller(client)
    gone = await s.post(s.doc("sales_invoice", [s.line("1", unit_price="70")]))
    await ok(await client.post(f"/documents/{gone['id']}/cancel", json={"reason": "typo"}, headers=s.h))
    kept = await s.post(s.doc("sales_invoice", [s.line("1", unit_price="100")], doc_date=str(EARLIER)))
    await ok(await client.post(f"/documents/{kept['id']}/reverse", json={"reason": "returned"}, headers=s.h))
    assert (await vat_return(s, LAST_MONTH))["1b"] == ("100.0000", "5.0000")
    assert (await vat_return(s, MONTH))["1b"] == ("-100.0000", "-5.0000")
    reversal = await lines(s, MONTH, "1b")
    assert [(x["doc_type"], x["amount"]) for x in reversal] == [("credit_note", "-100.0000")]


async def test_bad_periods_and_boxes_are_refused(client: AsyncClient) -> None:
    s = await seller(client)
    params = {"business_id": s.shop.business_id, "from": str(TODAY), "to": str(EARLIER)}
    assert (await client.get("/vat-returns/draft", params=params, headers=s.h)).status_code == 422
    params = {"business_id": s.shop.business_id, "box": "99", **MONTH}
    response = await client.get("/vat-returns/draft/lines", params=params, headers=s.h)
    assert (response.status_code, response.json()["code"]) == (422, "unknown_vat_box")
