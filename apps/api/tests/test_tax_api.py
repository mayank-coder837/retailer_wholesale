"""VAT by tax code (M1.10a; A3, A10; posting rules 1, 8, 29 to 31): price modes, defaults from party and
product, blocked VAT, reverse charge, invoice kind, and the old ``vat_rate`` input."""

from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api_helpers import bearer, signup_body
from tests.test_accounting_api import books
from tests.test_pricing_api import ok
from tests.test_purchasing_api import buyer
from tests.test_sales_api import seller

RENT = "6100"


async def test_a_vat_inclusive_invoice_takes_vat_out_of_each_line_and_lines_add_up(
    client: AsyncClient,
) -> None:
    s = await seller(client)
    lines = [
        s.line("3", unit_price="10.50"),  # 31.50 incl.: VAT 1.50, net 30.00
        s.line("1", unit_price="0.07"),  # 0.07 incl.: VAT 0.003 → 0.00, net 0.07
        s.line("7", unit_price="1.99"),  # 13.93 incl.: VAT 0.6633 → 0.66, net 13.27
    ]
    inv = await s.post(s.doc("sales_invoice", lines, price_mode="vat_inclusive"))
    assert [(x["tax_code"], x["net_amount"], x["vat_amount"], x["line_total"]) for x in inv["lines"]] == [
        ("SR", "30.0000", "1.5000", "31.5000"),
        ("SR", "0.0700", "0.0000", "0.0700"),
        ("SR", "13.2700", "0.6600", "13.9300"),
    ]
    assert (inv["price_mode"], inv["subtotal"], inv["tax_total"], inv["total"]) == (
        "vat_inclusive",
        "43.3400",
        "2.1600",
        "45.5000",
    )
    assert sum(Decimal(x["vat_amount"]) for x in inv["lines"]) == Decimal(inv["tax_total"])
    journal = await s.journal(inv["id"])
    assert ("2130", "0.0000", "2.1600") in journal
    assert ("4100", "0.0000", "43.3400") in journal


async def test_no_vat_needs_its_reason_and_decides_every_lines_code(client: AsyncClient) -> None:
    s = await seller(client)
    no_reason = await client.post(
        "/sales-documents",
        json=s.doc("sales_invoice", [s.line("1", unit_price="10")], price_mode="no_vat"),
        headers=s.h,
    )
    assert no_reason.status_code == 422
    clash = await client.post(
        "/sales-documents",
        json=s.doc(
            "sales_invoice",
            [s.line("1", unit_price="10", tax_code="SR")],
            price_mode="no_vat",
            no_vat_reason="zero_rated",
        ),
        headers=s.h,
    )
    assert clash.status_code == 422
    export = await s.post(
        s.doc(
            "sales_invoice", [s.line("2", unit_price="10")], price_mode="no_vat", no_vat_reason="zero_rated"
        )
    )
    assert [(x["tax_code"], x["vat_amount"]) for x in export["lines"]] == [("ZR", "0.0000")]
    assert (export["no_vat_reason"], export["tax_total"], export["total"]) == (
        "zero_rated",
        "0.0000",
        "20.0000",
    )


async def test_a_blocked_code_saved_before_the_rule_never_blocks_other_edits(
    client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    s = await seller(client)
    # Before M1.10d-3 a stock item could take SR-B: plant one, as an older database may hold it.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE product SET purchase_tax_code = 'SR-B' WHERE id = :id"), {"id": s.product}
        )
    renamed = await client.patch(f"/products/{s.product}", json={"name_en": "Fabric roll"}, headers=s.h)
    assert renamed.status_code == 200, renamed.text
    sales = await client.patch(f"/products/{s.product}", json={"sales_tax_code": "ZR"}, headers=s.h)
    assert sales.status_code == 200, sales.text
    again = await client.patch(f"/products/{s.product}", json={"purchase_tax_code": "SR-B"}, headers=s.h)
    assert again.status_code == 422  # setting it is still refused
    cleared = await ok(
        await client.patch(f"/products/{s.product}", json={"purchase_tax_code": None}, headers=s.h)
    )
    assert cleared["purchase_tax_code"] is None


async def test_codes_default_from_the_customer_then_the_item(client: AsyncClient) -> None:
    s = await seller(client)
    await ok(await client.patch(f"/products/{s.product}", json={"sales_tax_code": "EX"}, headers=s.h))
    exempt = await s.post(s.doc("sales_invoice", [s.line("1", unit_price="10")]))
    assert exempt["lines"][0]["tax_code"] == "EX"
    exporter = await s.customer_named("Oman Buyer")
    await ok(await client.patch(f"/parties/{exporter}", json={"sales_tax_code": "ZR"}, headers=s.h))
    export = await s.post(s.doc("sales_invoice", [s.line("1", unit_price="10")], party_id=exporter))
    assert export["lines"][0]["tax_code"] == "ZR"
    typed = await s.post(
        s.doc("sales_invoice", [s.line("1", unit_price="10", tax_code="SR")], party_id=exporter)
    )
    assert (typed["lines"][0]["tax_code"], typed["tax_total"]) == ("SR", "0.5000")
    bad = await client.patch(f"/products/{s.product}", json={"sales_tax_code": "RC-S"}, headers=s.h)
    assert bad.status_code == 422, "a purchase code is not a sales code"
    # Blocked VAT is for expenses: a stock item may not default to it (R8), on create or later.
    blocked = await client.patch(f"/products/{s.product}", json={"purchase_tax_code": "SR-B"}, headers=s.h)
    assert (blocked.status_code, blocked.json()["field"]) == (422, "purchase_tax_code")
    service = {
        "sku": "DLV",
        "name_en": "Delivery",
        "name_ar": "توصيل",
        "product_type": "service",
        "base_unit": "pcs",
    }
    created = await client.post("/products", json=service | {"purchase_tax_code": "SR-B"}, headers=s.h)
    assert created.status_code == 201, created.text  # a service may: it is an expense
    stock = {
        "sku": "BOX",
        "name_en": "Box",
        "name_ar": "صندوق",
        "base_unit": "pcs",
        "purchase_tax_code": "SR-B",
    }
    assert (await client.post("/products", json=stock, headers=s.h)).status_code == 422
    await ok(await client.patch(f"/products/{s.product}", json={"sales_tax_code": None}, headers=s.h))
    back = await s.post(s.doc("sales_invoice", [s.line("1", unit_price="10")]))
    assert back["lines"][0]["tax_code"] == "SR"


async def test_old_clients_sending_a_vat_rate_still_work(client: AsyncClient) -> None:
    s = await seller(client)
    inv = await s.post(
        s.doc(
            "sales_invoice",
            [s.line("1", unit_price="10", vat_rate="0"), s.line("1", unit_price="10", vat_rate="5")],
        )
    )
    assert [x["tax_code"] for x in inv["lines"]] == ["ZR", "SR"]


async def test_the_invoice_kind_follows_the_customers_trn(client: AsyncClient) -> None:
    s = await seller(client)
    walk_in = await s.post(s.doc("sales_invoice", [s.line("1", unit_price="10")]))
    assert walk_in["invoice_kind"] == "simplified"
    registered = await s.customer_named("Registered Co")
    await ok(await client.patch(f"/parties/{registered}", json={"trn": "100000000000011"}, headers=s.h))
    full = await s.post(s.doc("sales_invoice", [s.line("1", unit_price="10")], party_id=registered))
    assert full["invoice_kind"] == "tax_invoice"
    await ok(
        await client.put(
            "/settings/vat.simplified_for_unregistered",
            json={"scope": "business", "scope_id": s.shop.business_id, "value": False},
            headers=s.h,
        )
    )
    always_full = await s.post(s.doc("sales_invoice", [s.line("1", unit_price="10")]))
    assert always_full["invoice_kind"] == "tax_invoice"
    order = await s.post(s.doc("sales_order", [s.line("1", unit_price="10")]))
    assert order["invoice_kind"] is None


async def test_a_return_keeps_its_invoices_price_mode_and_codes(client: AsyncClient) -> None:
    s = await seller(client)
    inv = await s.post(s.doc("sales_invoice", [s.line("2", unit_price="10.50")], price_mode="vat_inclusive"))
    back = [s.line("1", source_line_id=inv["lines"][0]["id"])]
    wrong_mode = await client.post("/sales-documents", json=s.doc("sales_return", back), headers=s.h)
    assert (wrong_mode.status_code, wrong_mode.json()["code"]) == (422, "invalid_sales_document")
    ret = await s.post(s.doc("sales_return", back, price_mode="vat_inclusive"))
    assert [(x["tax_code"], x["net_amount"], x["vat_amount"]) for x in ret["lines"]] == [
        ("SR", "10.0000", "0.5000")
    ]


async def test_a_business_not_registered_charges_no_vat(client: AsyncClient) -> None:
    body = signup_body("small@shop.ae", "Small Shop")
    body["business"]["vat_registered"] = False
    del body["business"]["trn"]
    tokens = await ok(await client.post("/auth/signup", json=body), 201)
    h = bearer(tokens)
    business = (await ok(await client.get("/businesses", headers=h)))[0]["id"]
    branch = (await ok(await client.get(f"/businesses/{business}/branches", headers=h)))[0]["id"]
    warehouse = (await ok(await client.get(f"/branches/{branch}/warehouses", headers=h)))[0]["id"]
    party = await ok(
        await client.post(
            "/parties", json={"name_en": "Ali", "name_ar": "علي", "is_customer": True}, headers=h
        ),
        201,
    )
    product = await ok(
        await client.post(
            "/products", json={"sku": "X", "name_en": "X", "name_ar": "س", "base_unit": "pcs"}, headers=h
        ),
        201,
    )
    doc: dict[str, Any] = {
        "doc_type": "sales_invoice",
        "business_id": business,
        "branch_id": branch,
        "doc_date": "2026-10-25",
        "party_id": party["id"],
        "warehouse_id": warehouse,
        "lines": [
            {"variant_id": product["variants"][0]["id"], "qty": "1", "unit_price": "100", "tax_code": "SR"}
        ],
    }
    inv = await ok(await client.post("/sales-documents", params={"post": "true"}, json=doc, headers=h), 201)
    assert (inv["lines"][0]["tax_code"], inv["tax_total"], inv["invoice_kind"]) == ("OS", "0.0000", "invoice")


# --- purchases ---------------------------------------------------------------------------------------------


def expense(b: Any, amount: str, **more: Any) -> dict[str, Any]:
    return {"account_id": b.accounts[RENT], "qty": "1", "unit_price": amount, **more}


async def test_reverse_charge_owes_the_supplier_the_net_and_self_assesses_the_vat(
    client: AsyncClient,
) -> None:
    b = await buyer(client)
    bill = await b.post(b.doc("purchase_bill", [expense(b, "1000", tax_code="RC-S")], supplier_ref="AWS-1"))
    assert (bill["subtotal"], bill["tax_total"], bill["total"]) == ("1000.0000", "50.0000", "1000.0000")
    assert bill["lines"][0]["line_total"] == "1000.0000"
    journal = await b.journal(bill["id"])
    assert sorted(journal) == [
        ("1400", "50.0000", "0.0000"),
        ("2110", "0.0000", "1000.0000"),
        ("6100", "1000.0000", "0.0000"),
    ]
    output = await ok(await client.get("/accounts", params={"business_id": b.shop.business_id}, headers=b.h))
    vat_out = next(a["id"] for a in output if a["code"] == "2130")
    ledger = await ok(await client.get(f"/accounts/{vat_out}/ledger", headers=b.h))
    assert [(x["debit"], x["credit"]) for x in ledger["lines"] if x["document_id"] == bill["id"]] == [
        ("0.0000", "50.0000")
    ]


async def test_blocked_vat_goes_into_the_expense_and_never_onto_stock(client: AsyncClient) -> None:
    b = await buyer(client)
    dinner = await b.post(b.doc("purchase_bill", [expense(b, "200", tax_code="SR-B")], supplier_ref="REST-1"))
    assert sorted(await b.journal(dinner["id"])) == [
        ("2110", "0.0000", "210.0000"),
        ("6100", "210.0000", "0.0000"),
    ]
    stock = {"variant_id": b.fabric, "qty": "1", "unit_price": "10", "tax_code": "SR-B"}
    refused = await client.post(
        "/purchase-documents", json=b.doc("purchase_bill", [stock], warehouse_id=b.warehouse), headers=b.h
    )
    assert (refused.status_code, refused.json()["field"]) == (422, "lines[0].tax_code")


async def test_the_suppliers_code_is_the_default_and_inclusive_bills_value_stock_at_the_net(
    client: AsyncClient,
) -> None:
    b = await buyer(client)
    await ok(await client.patch(f"/parties/{b.supplier}", json={"purchase_tax_code": "RC-S"}, headers=b.h))
    overseas = await b.post(b.doc("purchase_bill", [expense(b, "100")], supplier_ref="X-1"))
    assert overseas["lines"][0]["tax_code"] == "RC-S"
    await ok(await client.patch(f"/parties/{b.supplier}", json={"purchase_tax_code": None}, headers=b.h))
    line = {"variant_id": b.fabric, "unit": "carton", "qty": "1", "unit_price": "302.40"}
    bill = await b.post(
        b.doc(
            "purchase_bill", [line], warehouse_id=b.warehouse, supplier_ref="GM-1", price_mode="vat_inclusive"
        )
    )
    assert [(x["tax_code"], x["net_amount"], x["vat_amount"]) for x in bill["lines"]] == [
        ("SR", "288.0000", "14.4000")
    ]
    stock = await ok(
        await client.get("/stock/balances", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert [s["value"] for s in stock] == ["288.0000"]


async def test_returning_reverse_charge_goods_undoes_both_vat_sides(client: AsyncClient) -> None:
    b = await buyer(client)
    line = {"variant_id": b.fabric, "qty": "10", "unit_price": "10", "tax_code": "RC-G"}
    bill = await b.post(b.doc("purchase_bill", [line], warehouse_id=b.warehouse, supplier_ref="IMP-1"))
    assert bill["total"] == "100.0000"
    ret = await b.post(
        b.doc(
            "purchase_return",
            [{"variant_id": b.fabric, "qty": "4", "source_line_id": bill["lines"][0]["id"]}],
            warehouse_id=b.warehouse,
        )
    )
    assert (ret["lines"][0]["tax_code"], ret["lines"][0]["vat_amount"], ret["total"]) == (
        "RC-G",
        "2.0000",
        "40.0000",
    )
    assert ("1400", "0.0000", "2.0000") in await b.journal(ret["id"])


# --- vouchers ----------------------------------------------------------------------------------------------


async def test_expense_vouchers_and_notes_take_codes_of_their_side(client: AsyncClient) -> None:
    b = await books(client)
    dinner = await b.post(
        b.voucher(
            "expense",
            [{"account_id": b.account("6100"), "amount": "200", "tax_code": "SR-B"}],
            account_id=b.account("1110"),
        )
    )
    assert (dinner["total"], dinner["lines"][0]["tax_code"], dinner["lines"][0]["vat_amount"]) == (
        "210.0000",
        "SR-B",
        "10.0000",
    )
    wrong_side = await client.post(
        "/vouchers",
        json=b.voucher("credit_note", [{"amount": "100", "tax_code": "RC-S"}], party_id=b.customer),
        headers=b.h,
    )
    assert (wrong_side.status_code, wrong_side.json()["field"]) == (422, "lines[0].tax_code")
    receipt = b.voucher(
        "receipt", [{"party_id": b.customer, "amount": "100", "tax_code": "SR"}], account_id=b.account("1110")
    )
    assert (await client.post("/vouchers", json=receipt, headers=b.h)).status_code == 422


async def test_a_suggested_price_says_which_mode_it_is_in(client: AsyncClient) -> None:
    s = await seller(client)
    body = {"business_id": s.shop.business_id, "variant_id": s.fabric, "unit": "pcs", "qty": "1"}
    base = await ok(await client.post("/pricing/quote", json=body, headers=s.h))
    assert base["price_mode"] == "vat_exclusive"  # lists and base prices exclude VAT (R9)
    await s.post(s.doc("sales_invoice", [s.line("3", unit_price="10.50")], price_mode="vat_inclusive"))
    last = await ok(await client.post("/pricing/quote", json=body | {"party_id": s.customer}, headers=s.h))
    assert (last["source"], last["price"], last["price_mode"]) == ("last_price", "10.500000", "vat_inclusive")
    history = await ok(
        await client.get(
            f"/parties/{s.customer}/sales-history", params={"business_id": s.shop.business_id}, headers=s.h
        )
    )
    assert history[0]["price_mode"] == "vat_inclusive"


async def test_the_tax_codes_are_listed_by_side_for_any_signed_in_user(client: AsyncClient) -> None:
    assert (await client.get("/tax-codes")).status_code == 401
    h = bearer(await ok(await client.post("/auth/signup", json=signup_body("codes@shop.ae")), 201))
    sales = await ok(await client.get("/tax-codes", params={"side": "sales"}, headers=h))
    assert [(c["code"], c["rate"], c["treatment"]) for c in sales] == [
        ("SR", "5", "output"),
        ("ZR", "0", "none"),
        ("EX", "0", "none"),
        ("OS", "0", "none"),
    ]
    purchase = await ok(await client.get("/tax-codes", params={"side": "purchase"}, headers=h))
    assert [c["code"] for c in purchase] == ["SR", "SR-B", "ZR", "EX", "OS", "RC-S", "RC-G"]
    assert {c["side"] for c in purchase} == {"purchase"}
    assert all(c["name_en"] and c["name_ar"] for c in sales + purchase)
    every = await ok(await client.get("/tax-codes", headers=h))
    assert len(every) == len(sales) + len(purchase)
    assert (await client.get("/tax-codes", params={"side": "both"}, headers=h)).status_code == 422
