"""Sales API (M1.8a): quotation → order → partial deliveries → invoice (COGS through GDNI, A8); direct and
cash sales; returns at the cost the goods left at; last price and the customer history (A9); the minimum
price and
the credit check at post (posting rules 1 to 4)."""

import uuid
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import apply_context
from app.core.platform_models import AuditLog
from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_pricing_api import ok
from tests.test_roles_api import Shop, add_member, login, make_shop

TODAY = dubai_today()
CODES = ("1110", "1200", "1300", "1310", "2130", "4100", "4200", "5100")


class Seller:
    def __init__(self, client: AsyncClient, shop: Shop, warehouse: str, accounts: dict[str, str]) -> None:
        self.client = client
        self.shop = shop
        self.h = bearer(shop.owner)
        self.warehouse = warehouse
        self.accounts = accounts
        self.customer = ""
        self.fabric = ""
        self.product = ""

    def doc(self, doc_type: str, lines: list[dict[str, Any]], **header: Any) -> dict[str, Any]:
        body = {
            "doc_type": doc_type,
            "business_id": self.shop.business_id,
            "branch_id": self.shop.branch_a,
            "doc_date": str(TODAY),
            "party_id": header.pop("party_id", self.customer),
            "lines": lines,
            **header,
        }
        if doc_type in ("delivery_note", "sales_return", "sales_invoice") and "warehouse_id" not in body:
            body["warehouse_id"] = self.warehouse
        return body

    def line(self, qty: str, unit: str = "pcs", **more: Any) -> dict[str, Any]:
        return {"variant_id": self.fabric, "unit": unit, "qty": qty, **more}

    async def post(
        self, body: dict[str, Any], status: int = 201, headers: dict[str, str] | None = None
    ) -> Any:
        response = await self.client.post(
            "/sales-documents", params={"post": "true"}, json=body, headers=headers or self.h
        )
        return await ok(response, status)

    async def journal(self, doc_id: str) -> list[tuple[str, str, str]]:
        """(account code, debit, credit) of every line a document posted, in code order."""
        out = []
        for code in CODES:
            ledger = await ok(
                await self.client.get(f"/accounts/{self.accounts[code]}/ledger", headers=self.h)
            )
            out += [(code, x["debit"], x["credit"]) for x in ledger["lines"] if x["document_id"] == doc_id]
        return out

    async def balance(self, code: str) -> str:
        ledger = await ok(await self.client.get(f"/accounts/{self.accounts[code]}/ledger", headers=self.h))
        return str(ledger["closing"])

    async def receivables(self, party: str | None = None) -> dict[str, str]:
        params = {"business_id": self.shop.business_id, "party_id": party or self.customer}
        return {
            b["reference"]: b["amount_open"]
            for b in await ok(await self.client.get("/bills", params=params, headers=self.h))
        }

    async def customer_named(self, name: str, **profile: Any) -> str:
        body = {"name_en": name, "name_ar": "عميل", "is_customer": True}
        party = (await ok(await self.client.post("/parties", json=body, headers=self.h), 201))["id"]
        await ok(
            await self.client.put(
                f"/parties/{party}/customer-profile",
                json={"business_id": self.shop.business_id, "credit_days": 30, **profile},
                headers=self.h,
            )
        )
        return str(party)


async def seller(client: AsyncClient, email: str = "seller@shop.ae") -> Seller:
    shop = await make_shop(client, email)
    h = bearer(shop.owner)
    warehouse = (await ok(await client.get(f"/branches/{shop.branch_a}/warehouses", headers=h)))[0]["id"]
    chart = await ok(await client.get("/accounts", params={"business_id": shop.business_id}, headers=h))
    s = Seller(client, shop, warehouse, {a["code"]: a["id"] for a in chart if a["code"] in CODES})
    s.customer = await s.customer_named("Ali Traders")
    product = {
        "sku": "FAB",
        "name_en": "Fabric",
        "name_ar": "قماش",
        "base_unit": "pcs",
        "units": [{"unit": "carton", "factor_to_base": "24"}],
    }
    created = await ok(await client.post("/products", json=product, headers=h), 201)
    s.product, s.fabric = created["id"], created["variants"][0]["id"]
    # 100 pieces in stock at 6 each.
    stock = {
        "doc_type": "stock_adjustment",
        "business_id": shop.business_id,
        "branch_id": shop.branch_a,
        "doc_date": str(TODAY),
        "warehouse_id": warehouse,
        "lines": [{"variant_id": s.fabric, "unit": "pcs", "qty": "100", "direction": "in", "unit_cost": "6"}],
    }
    await ok(await client.post("/stock-documents", params={"post": "true"}, json=stock, headers=h), 201)
    return s


async def test_quotation_order_partial_deliveries_and_an_invoice_through_gdni(client: AsyncClient) -> None:
    s = await seller(client)
    quote = await s.post(s.doc("quotation", [s.line("2", "carton", unit_price="240", vat_rate="5")]))
    assert (quote["status"], quote["number"], quote["total"]) == (
        "posted",
        f"QT-{TODAY.year}-00001",
        "504.0000",
    )
    draft = await client.post(
        "/sales-documents", json=s.doc("quotation", [s.line("1", unit_price="10")]), headers=s.h
    )
    assert (await ok(draft, 201))["status"] == "posted", "a quotation is numbered when saved"

    order = await s.post(
        s.doc("sales_order", [s.line("2", "carton", source_line_id=quote["lines"][0]["id"])])
    )
    assert (order["number"], order["lines"][0]["unit_price"], order["total"]) == (
        f"SO-{TODAY.year}-00001",
        "240.000000",
        "504.0000",
    )
    quote = await ok(await client.get(f"/sales-documents/{quote['id']}", headers=s.h))
    assert quote["progress"] == "ordered"
    order_line = order["lines"][0]["id"]

    # Two deliveries of 20 and 10 pieces: stock leaves at 6, into GDNI (rule 2). No prices.
    first = await s.post(s.doc("delivery_note", [s.line("20", source_line_id=order_line)]))
    assert (first["number"], first["lines"][0]["unit_price"], first["lines"][0]["cost"]) == (
        f"DN-{TODAY.year}-00001",
        None,
        "120.0000",
    )
    assert await s.journal(first["id"]) == [("1300", "0.0000", "120.0000"), ("1310", "120.0000", "0.0000")]
    second = await s.post(s.doc("delivery_note", [s.line("10", source_line_id=order_line)]))
    too_many = await client.post(
        "/sales-documents",
        json=s.doc("delivery_note", [s.line("19", source_line_id=order_line)]),
        headers=s.h,
    )
    assert (too_many.status_code, too_many.json()["code"]) == (409, "more_than_open")
    priced = await client.post(
        "/sales-documents", json=s.doc("delivery_note", [s.line("1", unit_price="10")]), headers=s.h
    )
    assert (priced.status_code, priced.json()["code"]) == (422, "invalid_sales_document")
    order = await ok(await client.get(f"/sales-documents/{order['id']}", headers=s.h))
    assert order["progress"] == "partly_delivered"
    open_lines = await ok(await client.get(f"/sales-documents/{first['id']}/open-lines", headers=s.h))
    assert [(x["qty_open"], x["unit"], x["unit_price"]) for x in open_lines] == [
        ("20.000000", "pcs", "10.000000")
    ]

    # The first delivery is invoiced at the order's price (10 a piece): rule 3, its cost moves to COGS.
    invoice = await s.post(
        s.doc("sales_invoice", [s.line("20", source_line_id=first["lines"][0]["id"])], reference="PO-77")
    )
    assert (invoice["number"], invoice["total"], invoice["lines"][0]["cost"]) == (
        f"INV-{TODAY.year}-00001",
        "210.0000",
        "120.0000",
    )
    assert await s.journal(invoice["id"]) == [
        ("1200", "210.0000", "0.0000"),
        ("1310", "0.0000", "120.0000"),
        ("2130", "0.0000", "10.0000"),
        ("4100", "0.0000", "200.0000"),
        ("5100", "120.0000", "0.0000"),
    ]
    assert invoice["due_date"] is not None
    assert await s.receivables() == {invoice["number"]: "210.0000"}
    assert await s.balance("1310") == "60.0000", "the second delivery is still delivered, not invoiced"
    first = await ok(await client.get(f"/sales-documents/{first['id']}", headers=s.h))
    second = await ok(await client.get(f"/sales-documents/{second['id']}", headers=s.h))
    assert (first["progress"], second["progress"]) == ("invoiced", "not_invoiced")


async def test_a_direct_invoice_moves_stock_and_a_cash_sale_has_no_receivable(client: AsyncClient) -> None:
    s = await seller(client)
    credit = await s.post(s.doc("sales_invoice", [s.line("5", unit_price="12")]))
    assert await s.journal(credit["id"]) == [
        ("1200", "63.0000", "0.0000"),
        ("1300", "0.0000", "30.0000"),
        ("2130", "0.0000", "3.0000"),
        ("4100", "0.0000", "60.0000"),
        ("5100", "30.0000", "0.0000"),
    ]
    cash = await s.post(
        s.doc("sales_invoice", [s.line("2", unit_price="12")], cash_account_id=s.accounts["1110"])
    )
    assert await s.journal(cash["id"]) == [
        ("1110", "25.2000", "0.0000"),
        ("1300", "0.0000", "12.0000"),
        ("2130", "0.0000", "1.2000"),
        ("4100", "0.0000", "24.0000"),
        ("5100", "12.0000", "0.0000"),
    ]
    assert await s.receivables() == {credit["number"]: "63.0000"}, "a cash sale is paid: no bill"
    stock = await ok(
        await client.get(
            "/stock/balances", params={"business_id": s.shop.business_id, "variant_id": s.fabric}, headers=s.h
        )
    )
    assert [(x["qty"], x["value"]) for x in stock] == [("93.000000", "558.0000")]


async def test_a_return_comes_back_at_its_cost_and_reduces_the_invoice(client: AsyncClient) -> None:
    s = await seller(client)
    invoice = await s.post(s.doc("sales_invoice", [s.line("10", unit_price="10")]))
    line = invoice["lines"][0]["id"]
    returned = await s.post(s.doc("sales_return", [s.line("4", source_line_id=line)]))
    assert (returned["number"], returned["total"], returned["lines"][0]["cost"]) == (
        f"SR-{TODAY.year}-00001",
        "42.0000",
        "24.0000",
    )
    assert await s.journal(returned["id"]) == [
        ("1200", "0.0000", "42.0000"),
        ("1300", "24.0000", "0.0000"),
        ("2130", "2.0000", "0.0000"),
        ("4200", "40.0000", "0.0000"),
        ("5100", "0.0000", "24.0000"),
    ]
    assert await s.receivables() == {invoice["number"]: "63.0000"}
    priced = await client.post(
        "/sales-documents",
        json=s.doc("sales_return", [s.line("1", source_line_id=line, unit_price="9")]),
        headers=s.h,
    )
    assert priced.status_code == 422, "a return takes the invoice's price"
    over = await client.post(
        "/sales-documents", json=s.doc("sales_return", [s.line("7", source_line_id=line)]), headers=s.h
    )
    assert (over.status_code, over.json()["code"]) == (409, "more_than_open")
    unsourced = await client.post("/sales-documents", json=s.doc("sales_return", [s.line("1")]), headers=s.h)
    assert unsourced.status_code == 422


async def test_the_last_price_comes_first_and_the_history_is_the_customers_own(client: AsyncClient) -> None:
    s = await seller(client)
    other = await s.customer_named("Other Co")
    await s.post(s.doc("sales_invoice", [s.line("1", "carton", unit_price="252")]))
    invoice = await s.post(s.doc("sales_invoice", [s.line("3", unit_price="10.5", price_source="base")]))
    quote = await ok(
        await client.post(
            "/pricing/quote",
            json={
                "business_id": s.shop.business_id,
                "variant_id": s.fabric,
                "unit": "pcs",
                "qty": "1",
                "party_id": s.customer,
            },
            headers=s.h,
        )
    )
    assert (Decimal(quote["price"]), quote["source"], quote["price_source"]) == (
        Decimal("10.5"),
        "last_price",
        f"last_price:{invoice['number']}",
    )
    params = {"business_id": s.shop.business_id}
    history = await ok(await client.get(f"/parties/{s.customer}/sales-history", params=params, headers=s.h))
    assert [(h["number"], h["qty"], h["unit"], h["unit_price"]) for h in history] == [
        (invoice["number"], "3.000000", "pcs", "10.500000"),
        (f"INV-{TODAY.year}-00001", "1.000000", "carton", "252.000000"),
    ]
    found = await ok(
        await client.get(f"/parties/{s.customer}/sales-history", params={**params, "q": "fab"}, headers=s.h)
    )
    assert len(found) == 2
    none = await ok(
        await client.get(f"/parties/{s.customer}/sales-history", params={**params, "q": "soap"}, headers=s.h)
    )
    assert none == []
    assert await ok(await client.get(f"/parties/{other}/sales-history", params=params, headers=s.h)) == []


async def test_a_price_below_the_minimum_needs_the_permission(
    client: AsyncClient, app_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await seller(client)
    await ok(
        await client.patch(
            f"/products/{s.product}/units/pcs/prices",
            json={"sale_price": "12", "min_price": "9"},
            headers=s.h,
        )
    )
    await add_member(client, s.shop, "sales@shop.ae", "salesperson")
    salesperson = bearer(await login(client, "sales@shop.ae"))
    cheap = s.doc("sales_invoice", [s.line("1", unit_price="8")])
    refused = await client.post("/sales-documents", params={"post": "true"}, json=cheap, headers=salesperson)
    assert (refused.status_code, refused.json()["code"], refused.json()["min_price"]) == (
        409,
        "below_minimum_price",
        "9.0000",
    )
    allowed = await s.post(cheap)
    assert allowed["lines"][0]["below_min"] is True
    async with app_sessionmaker() as session, session.begin():
        await apply_context(session, tenant_id=uuid.UUID(s.shop.owner["tenant_id"]))
        entry = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "post", AuditLog.entity_id == allowed["id"])
            )
        ).one()
    assert entry.changes is not None
    assert entry.changes["below_min"] == [None, [s.fabric]]


async def test_the_credit_check_blocks_warns_and_counts_open_orders(
    client: AsyncClient, app_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await seller(client)
    tight = await s.customer_named("Tight Ltd", credit_limit="300", credit_policy="block")
    order = await s.post(
        s.doc("sales_order", [s.line("10", unit_price="20")], party_id=tight)
    )  # 210 with VAT
    figures = await ok(
        await client.post(
            f"/parties/{tight}/credit-check",
            json={"business_id": s.shop.business_id, "amount": "0"},
            headers=s.h,
        )
    )
    assert figures["open_orders"] == "210.0000"

    # Invoicing the order itself is not counted twice; a further 105 goes over the 300 limit.
    invoice = await s.post(
        s.doc("sales_invoice", [s.line("10", source_line_id=order["lines"][0]["id"])], party_id=tight)
    )
    assert invoice["warnings"] == []
    big = s.doc("sales_order", [s.line("5", unit_price="20")], party_id=tight)
    await add_member(client, s.shop, "sales@shop.ae", "salesperson")
    salesperson = bearer(await login(client, "sales@shop.ae"))
    refused = await client.post("/sales-documents", params={"post": "true"}, json=big, headers=salesperson)
    body = refused.json()
    assert (refused.status_code, body["code"], body["reasons"], body["may_override"]) == (
        409,
        "credit_blocked",
        ["over_limit"],
        False,
    )
    draft = await ok(await client.post("/sales-documents", json=big, headers=s.h), 201)
    no_reason = await client.post(f"/sales-documents/{draft['id']}/post", json={}, headers=s.h)
    assert (no_reason.status_code, no_reason.json()["may_override"]) == (409, True)
    posted = await ok(
        await client.post(
            f"/sales-documents/{draft['id']}/post",
            json={"credit_override_reason": "paying Thursday"},
            headers=s.h,
        )
    )
    assert posted["warnings"] == [{"code": "credit_overridden", "reasons": ["over_limit"]}]
    async with app_sessionmaker() as session, session.begin():
        await apply_context(session, tenant_id=uuid.UUID(s.shop.owner["tenant_id"]))
        override = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "override", AuditLog.reason == "paying Thursday")
            )
        ).one()
    assert override.changes is not None
    assert (override.changes["override"], override.changes["document"]) == ("credit", posted["number"])

    loose = await s.customer_named("Loose LLC", credit_limit="50", credit_policy="warn")
    warned = await s.post(s.doc("sales_invoice", [s.line("10", unit_price="10")], party_id=loose))
    assert warned["warnings"] == [{"code": "credit_warning", "reasons": ["over_limit"]}]
    cash = await s.post(
        s.doc(
            "sales_invoice",
            [s.line("1", unit_price="10")],
            party_id=tight,
            cash_account_id=s.accounts["1110"],
        )
    )
    assert cash["warnings"] == [], "a cash sale is not checked"


async def test_roles_reach_their_own_sales_documents(client: AsyncClient) -> None:
    s = await seller(client)
    order = await s.post(s.doc("sales_order", [s.line("10", unit_price="10")]))
    await add_member(client, s.shop, "keeper@shop.ae", "warehouse")
    keeper = bearer(await login(client, "keeper@shop.ae"))
    delivered = await s.post(
        s.doc("delivery_note", [s.line("10", source_line_id=order["lines"][0]["id"])]), headers=keeper
    )
    assert delivered["status"] == "posted", "the Warehouse role delivers"
    invoice = s.doc("sales_invoice", [s.line("10", source_line_id=delivered["lines"][0]["id"])])
    refused = await client.post("/sales-documents", params={"post": "true"}, json=invoice, headers=keeper)
    assert refused.status_code == 403, "but does not invoice"
    proforma = await s.post(s.doc("proforma_invoice", [s.line("1", unit_price="10")]))
    assert proforma["number"] == f"PI-{TODAY.year}-00001"


async def test_round_off_on_the_invoice_total_is_an_option_off_by_default(client: AsyncClient) -> None:
    s = await seller(client)
    exact = await s.post(s.doc("sales_invoice", [s.line("3", unit_price="3.333")]))  # 10.00 + 0.50 VAT
    assert (exact["total"], exact["round_off"]) == ("10.5000", "0.0000")
    for key, value in (("rounding.invoice_total.mode", "nearest"), ("rounding.invoice_total.step", "1.00")):
        body = {"scope": "business", "scope_id": s.shop.business_id, "value": value}
        await ok(await client.put(f"/settings/{key}", json=body, headers=s.h))
    rounded = await s.post(s.doc("sales_invoice", [s.line("3", unit_price="3.333")]))
    assert (rounded["subtotal"], rounded["tax_total"], rounded["round_off"], rounded["total"]) == (
        "10.0000",
        "0.5000",
        "0.5000",
        "11.0000",
    )
    assert await s.receivables() == {exact["number"]: "10.5000", rounded["number"]: "11.0000"}
