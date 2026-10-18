"""Purchasing API (M1.7a): order → partial receipts → bills (price variance) → returns; GRNI clears to zero;
payables bill-wise; expense bills; the books agree throughout (posting rules 7 to 9)."""

from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_pricing_api import ok
from tests.test_roles_api import Shop, add_member, login, make_shop

TODAY = dubai_today()


class Buyer:
    def __init__(self, client: AsyncClient, shop: Shop, warehouse: str, accounts: dict[str, str]) -> None:
        self.client = client
        self.shop = shop
        self.h = bearer(shop.owner)
        self.warehouse = warehouse
        self.accounts = accounts
        self.supplier = ""
        self.fabric = ""

    def doc(self, doc_type: str, lines: list[dict[str, Any]], **header: Any) -> dict[str, Any]:
        body = {
            "doc_type": doc_type,
            "business_id": self.shop.business_id,
            "branch_id": self.shop.branch_a,
            "doc_date": str(TODAY),
            "party_id": self.supplier,
            "lines": lines,
            **header,
        }
        if doc_type in ("goods_receipt", "purchase_return") and "warehouse_id" not in header:
            body["warehouse_id"] = self.warehouse
        return body

    async def post(
        self, body: dict[str, Any], status: int = 201, headers: dict[str, str] | None = None
    ) -> Any:
        response = await self.client.post(
            "/purchase-documents", params={"post": "true"}, json=body, headers=headers or self.h
        )
        return await ok(response, status)

    async def journal(self, doc_id: str) -> list[tuple[str, str, str]]:
        """(account code, debit, credit) of every line a document posted, in code order."""
        out = []
        for code in sorted(self.accounts):
            ledger = await ok(
                await self.client.get(f"/accounts/{self.accounts[code]}/ledger", headers=self.h)
            )
            out += [(code, x["debit"], x["credit"]) for x in ledger["lines"] if x["document_id"] == doc_id]
        return out

    async def balance(self, code: str) -> str:
        ledger = await ok(await self.client.get(f"/accounts/{self.accounts[code]}/ledger", headers=self.h))
        return str(ledger["closing"])

    async def payables(self) -> dict[str, str]:
        params = {"business_id": self.shop.business_id, "party_id": self.supplier}
        return {
            b["reference"]: b["amount_open"]
            for b in await ok(await self.client.get("/bills", params=params, headers=self.h))
        }

    async def valuation(self) -> str:
        params = {"business_id": self.shop.business_id}
        return str(
            (await ok(await self.client.get("/stock/valuation", params=params, headers=self.h)))["difference"]
        )


async def buyer(client: AsyncClient, email: str = "buyer@shop.ae") -> Buyer:
    shop = await make_shop(client, email)
    h = bearer(shop.owner)
    warehouse = (await ok(await client.get(f"/branches/{shop.branch_a}/warehouses", headers=h)))[0]["id"]
    chart = await ok(await client.get("/accounts", params={"business_id": shop.business_id}, headers=h))
    codes = ("1300", "1400", "2110", "2120", "5200", "5400", "6100")
    b = Buyer(client, shop, warehouse, {a["code"]: a["id"] for a in chart if a["code"] in codes})
    supplier = {"name_en": "Gulf Mills", "name_ar": "مصانع الخليج", "is_supplier": True}
    b.supplier = (await ok(await client.post("/parties", json=supplier, headers=h), 201))["id"]
    await ok(
        await client.put(
            f"/parties/{b.supplier}/supplier-profile",
            json={"business_id": shop.business_id, "payment_terms_days": 30},
            headers=h,
        )
    )
    product = {
        "sku": "FAB",
        "name_en": "Fabric",
        "name_ar": "قماش",
        "base_unit": "pcs",
        "units": [{"unit": "carton", "factor_to_base": "24"}],
    }
    b.fabric = (await ok(await client.post("/products", json=product, headers=h), 201))["variants"][0]["id"]
    return b


async def test_order_received_in_parts_then_billed_with_a_price_difference(client: AsyncClient) -> None:
    b = await buyer(client)
    order = await b.post(
        b.doc(
            "purchase_order",
            [{"variant_id": b.fabric, "unit": "carton", "qty": "2", "unit_price": "240", "vat_rate": "5"}],
        )
    )
    assert (order["number"], order["total"], order["progress"]) == (
        f"PO-{TODAY.year}-00001",
        "504.0000",
        "open",
    )
    order_line = order["lines"][0]["id"]
    assert await b.journal(order["id"]) == []  # an order posts nothing

    # 20 pcs arrive at the order's price (240 a carton = 10 a piece): rule 7.
    first = await b.post(
        b.doc(
            "goods_receipt",
            [{"variant_id": b.fabric, "unit": "pcs", "qty": "20", "source_line_id": order_line}],
        )
    )
    assert (first["number"], first["lines"][0]["unit_price"], first["subtotal"]) == (
        f"GRN-{TODAY.year}-00001",
        "10.000000",
        "200.0000",
    )
    assert await b.journal(first["id"]) == [("1300", "200.0000", "0.0000"), ("2120", "0.0000", "200.0000")]
    order = await ok(await client.get(f"/purchase-documents/{order['id']}", headers=b.h))
    assert order["progress"] == "partly_received"
    open_lines = await ok(await client.get(f"/purchase-documents/{order['id']}/open-lines", headers=b.h))
    assert [(x["qty_open"], x["unit"], x["qty_open_base"]) for x in open_lines] == [
        ("28.000000", "pcs", "28.000000")
    ]

    too_many = await client.post(
        "/purchase-documents",
        json=b.doc(
            "goods_receipt",
            [{"variant_id": b.fabric, "unit": "pcs", "qty": "29", "source_line_id": order_line}],
        ),
        headers=b.h,
    )
    assert (too_many.status_code, too_many.json()["code"]) == (409, "more_than_open")
    second = await b.post(
        b.doc(
            "goods_receipt",
            [{"variant_id": b.fabric, "unit": "pcs", "qty": "28", "source_line_id": order_line}],
        )
    )
    order = await ok(await client.get(f"/purchase-documents/{order['id']}", headers=b.h))
    assert order["progress"] == "received"

    # The supplier bills the first 20 at 10.50:
    # Dr GRNI 200, Dr variance 10, Dr input VAT 10.50 / Cr supplier 220.50.
    bill = await b.post(
        b.doc(
            "purchase_bill",
            [
                {
                    "variant_id": b.fabric,
                    "unit": "pcs",
                    "qty": "20",
                    "unit_price": "10.5",
                    "source_line_id": first["lines"][0]["id"],
                }
            ],
            supplier_ref="GM-778",
        )
    )
    assert (bill["number"], bill["total"], bill["due_date"]) == (
        f"PB-{TODAY.year}-00001",
        "220.5000",
        str(TODAY.fromordinal(TODAY.toordinal() + 30)),
    )
    assert bill["lines"][0]["cleared_value"] == "200.0000"
    assert await b.journal(bill["id"]) == [
        ("1400", "10.5000", "0.0000"),
        ("2110", "0.0000", "220.5000"),
        ("2120", "200.0000", "0.0000"),
        ("5400", "10.0000", "0.0000"),
    ]
    over = await client.post(
        "/purchase-documents",
        json=b.doc(
            "purchase_bill",
            [{"variant_id": b.fabric, "unit": "pcs", "qty": "1", "source_line_id": first["lines"][0]["id"]}],
        ),
        headers=b.h,
    )
    assert (over.status_code, over.json()["code"]) == (409, "more_than_open")

    # The second receipt is billed in one go at the order's price: GRNI clears to exactly zero.
    await b.post(
        b.doc(
            "purchase_bill",
            [
                {
                    "variant_id": b.fabric,
                    "unit": "pcs",
                    "qty": "28",
                    "source_line_id": second["lines"][0]["id"],
                }
            ],
            supplier_ref="GM-779",
        )
    )
    assert await b.balance("2120") == "0.0000"
    assert await b.payables() == {"GM-778": "220.5000", "GM-779": "294.0000"}
    assert await b.valuation() == "0.0000"


async def test_returns_against_a_bill_and_against_an_unbilled_receipt(client: AsyncClient) -> None:
    b = await buyer(client)
    received = await b.post(
        b.doc("goods_receipt", [{"variant_id": b.fabric, "unit": "pcs", "qty": "20", "unit_price": "10"}])
    )
    bill = await b.post(
        b.doc(
            "purchase_bill",
            [
                {
                    "variant_id": b.fabric,
                    "unit": "pcs",
                    "qty": "20",
                    "unit_price": "10.5",
                    "vat_rate": "5",
                    "source_line_id": received["lines"][0]["id"],
                }
            ],
            supplier_ref="GM-1",
        )
    )
    # Five go back against the bill: price 52.50 + VAT 2.63 off the payable; stock leaves at its average.
    back = await b.post(
        b.doc(
            "purchase_return",
            [{"variant_id": b.fabric, "unit": "pcs", "qty": "5", "source_line_id": bill["lines"][0]["id"]}],
        )
    )
    assert (back["number"], back["total"]) == (f"PR-{TODAY.year}-00001", "55.1300")
    assert await b.journal(back["id"]) == [
        ("1300", "0.0000", "50.0000"),
        ("1400", "0.0000", "2.6300"),
        ("2110", "55.1300", "0.0000"),
        ("5200", "0.0000", "2.5000"),
    ]
    assert await b.payables() == {"GM-1": "165.3700"}

    # A receipt not billed yet: goods go back against GRNI at the receipt's price.
    later = await b.post(
        b.doc("goods_receipt", [{"variant_id": b.fabric, "unit": "pcs", "qty": "10", "unit_price": "12"}])
    )
    unbilled = await b.post(
        b.doc(
            "purchase_return",
            [{"variant_id": b.fabric, "unit": "pcs", "qty": "4", "source_line_id": later["lines"][0]["id"]}],
        )
    )
    lines = await b.journal(unbilled["id"])
    assert ("2120", "48.0000", "0.0000") in lines
    open_lines = await ok(await client.get(f"/purchase-documents/{later['id']}/open-lines", headers=b.h))
    assert [(x["qty_open"], x["value_open"]) for x in open_lines] == [("6.000000", "72.0000")]
    assert await b.valuation() == "0.0000"


async def test_expense_bills_debit_the_account_and_need_one(client: AsyncClient) -> None:
    b = await buyer(client)
    rent = await b.post(
        b.doc(
            "purchase_bill",
            [
                {
                    "account_id": b.accounts["6100"],
                    "qty": "1",
                    "unit_price": "1000",
                    "vat_rate": "5",
                    "description": "October rent",
                }
            ],
            supplier_ref="RENT-10",
        )
    )
    assert await b.journal(rent["id"]) == [
        ("1400", "50.0000", "0.0000"),
        ("2110", "0.0000", "1050.0000"),
        ("6100", "1000.0000", "0.0000"),
    ]
    no_account = await client.post(
        "/purchase-documents", json=b.doc("purchase_bill", [{"qty": "1", "unit_price": "10"}]), headers=b.h
    )
    into_the_bank = await client.post(
        "/purchase-documents",
        json=b.doc("purchase_bill", [{"account_id": b.accounts["1300"], "qty": "1", "unit_price": "10"}]),
        headers=b.h,
    )
    assert no_account.status_code == into_the_bank.status_code == 422


async def test_a_direct_bill_brings_stock_in_and_needs_a_warehouse(client: AsyncClient) -> None:
    b = await buyer(client)
    line = {"variant_id": b.fabric, "unit": "carton", "qty": "1", "unit_price": "288"}
    no_warehouse = await client.post("/purchase-documents", json=b.doc("purchase_bill", [line]), headers=b.h)
    assert no_warehouse.status_code == 422
    direct = await b.post(b.doc("purchase_bill", [line], warehouse_id=b.warehouse, supplier_ref="GM-9"))
    assert await b.journal(direct["id"]) == [("1300", "288.0000", "0.0000"), ("2110", "0.0000", "288.0000")]
    stock = await ok(
        await client.get("/stock/balances", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert [(s["qty"], s["value"]) for s in stock] == [("24.000000", "288.0000")]


async def test_only_suppliers_and_only_their_own_documents(client: AsyncClient) -> None:
    b = await buyer(client)
    customer = (
        await ok(
            await client.post(
                "/parties", json={"name_en": "Shop", "name_ar": "محل", "is_customer": True}, headers=b.h
            ),
            201,
        )
    )["id"]
    other = (
        await ok(
            await client.post(
                "/parties", json={"name_en": "Mill 2", "name_ar": "مصنع", "is_supplier": True}, headers=b.h
            ),
            201,
        )
    )["id"]
    line = {"variant_id": b.fabric, "unit": "pcs", "qty": "1", "unit_price": "1"}
    not_supplier = await client.post(
        "/purchase-documents", json=b.doc("purchase_order", [line]) | {"party_id": customer}, headers=b.h
    )
    order = await b.post(b.doc("purchase_order", [line]))
    someone_else = await client.post(
        "/purchase-documents",
        json=b.doc("goods_receipt", [line | {"source_line_id": order["lines"][0]["id"]}])
        | {"party_id": other},
        headers=b.h,
    )
    assert not_supplier.status_code == someone_else.status_code == 422


async def test_storekeepers_receive_goods_but_do_not_book_bills(client: AsyncClient) -> None:
    b = await buyer(client)
    await add_member(
        client, b.shop, "store@shop.ae", "warehouse", business=b.shop.business_id, branch=b.shop.branch_a
    )
    keeper = bearer(await login(client, "store@shop.ae"))
    line = {"variant_id": b.fabric, "unit": "pcs", "qty": "1", "unit_price": "1"}
    received = await client.post(
        "/purchase-documents", params={"post": "true"}, json=b.doc("goods_receipt", [line]), headers=keeper
    )
    billed = await client.post(
        "/purchase-documents", json=b.doc("purchase_bill", [line], warehouse_id=b.warehouse), headers=keeper
    )
    assert (received.status_code, billed.status_code) == (201, 403)
