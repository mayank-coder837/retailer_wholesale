"""The Phase 1 gate (docs/roadmap.md): a trader buys fabric by the roll, sells it by the metre on credit, receives a
post-dated cheque, deposits it and lets it clear. At the end the trial balance balances, the stock value equals the
inventory account, and the customer's ledger shows nothing outstanding. Offline: the API and the database only."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_pricing_api import ok
from tests.test_roles_api import make_shop

TODAY = dubai_today()

FABRIC = {
    "sku": "GATE-FAB",
    "name_en": "Cotton poplin",
    "name_ar": "بوبلين قطن",
    "base_unit": "m",
    "units": [{"unit": "roll", "factor_to_base": "50", "is_purchase_default": True}],
}


async def balance(client: AsyncClient, h: dict[str, str], accounts: dict[str, str], code: str) -> Decimal:
    ledger = await ok(await client.get(f"/accounts/{accounts[code]}/ledger", headers=h))
    return Decimal(str(ledger["closing"]))


async def test_buy_by_the_roll_sell_by_the_metre_on_credit_and_clear_the_cheque(client: AsyncClient) -> None:
    shop = await make_shop(client, "gate@shop.ae")
    h = bearer(shop.owner)
    business = {"business_id": shop.business_id}
    warehouse = (await ok(await client.get(f"/branches/{shop.branch_a}/warehouses", headers=h)))[0]["id"]
    chart = await ok(await client.get("/accounts", params=business, headers=h))
    accounts = {a["code"]: a["id"] for a in chart}
    supplier = (
        await ok(
            await client.post(
                "/parties", json={"name_en": "Gulf Mills", "name_ar": "مصانع", "is_supplier": True}, headers=h
            ),
            201,
        )
    )["id"]
    customer = (
        await ok(
            await client.post(
                "/parties", json={"name_en": "Ali Traders", "name_ar": "علي", "is_customer": True}, headers=h
            ),
            201,
        )
    )["id"]
    variant = (await ok(await client.post("/products", json=FABRIC, headers=h), 201))["variants"][0]["id"]

    def header(doc_type: str, party: str, lines: list[dict[str, Any]], **more: Any) -> dict[str, Any]:
        return {
            "doc_type": doc_type,
            **business,
            "branch_id": shop.branch_a,
            "doc_date": str(TODAY),
            "party_id": party,
            "lines": lines,
            **more,
        }

    # --- buy: 10 rolls (500 m) at 500 a roll; received into the warehouse, then billed with 5% VAT -------------
    receipt = await ok(
        await client.post(
            "/purchase-documents",
            params={"post": "true"},
            json=header(
                "goods_receipt",
                supplier,
                [{"variant_id": variant, "unit": "roll", "qty": "10", "unit_price": "500"}],
                warehouse_id=warehouse,
            ),
            headers=h,
        ),
        201,
    )
    line = receipt["lines"][0]
    assert (line["qty"], line["unit"], line["qty_base"]) == ("10.000000", "roll", "500.000000")
    bill = await ok(
        await client.post(
            "/purchase-documents",
            params={"post": "true"},
            json=header(
                "purchase_bill",
                supplier,
                [{"variant_id": variant, "unit": "roll", "qty": "10", "source_line_id": line["id"]}],
                supplier_ref="GM-1",
            ),
            headers=h,
        ),
        201,
    )
    assert bill["total"] == "5250.0000"  # 5,000 of fabric and 250 VAT
    assert await balance(client, h, accounts, "2120") == 0  # goods received not billed: cleared

    # --- sell: 120 m at 14 a metre on credit, with 5% VAT --------------------------------------------------------
    invoice = await ok(
        await client.post(
            "/sales-documents",
            params={"post": "true"},
            json=header(
                "sales_invoice",
                customer,
                [{"variant_id": variant, "unit": "m", "qty": "120", "unit_price": "14"}],
                warehouse_id=warehouse,
            ),
            headers=h,
        ),
        201,
    )
    assert invoice["total"] == "1764.0000"
    bills = await ok(
        await client.get("/bills", params={**business, "party_id": customer}, headers=h),
    )
    assert [(b["reference"], b["amount_open"]) for b in bills] == [(invoice["number"], "1764.0000")]

    # --- the customer's cheque: a memo until it clears -----------------------------------------------------------
    trial = await ok(await client.get("/trial-balance", params=business, headers=h))
    cheque = {
        **business,
        "branch_id": shop.branch_a,
        "direction": "received",
        "party_id": customer,
        "cheque_no": "000777",
        "bank_name": "Emirates NBD",
        "cheque_date": str(TODAY + timedelta(days=30)),
        "amount": "1764",
        "intended_bills": [{"bill_id": bills[0]["id"], "amount": "1764"}],
    }
    pdc = await ok(await client.post("/pdcs", json=cheque, headers=h), 201)
    assert (await ok(await client.get("/trial-balance", params=business, headers=h)))["total_debit"] == (
        trial["total_debit"]
    ), "a cheque in hand moves nothing in the ledger"

    bank = accounts["1150"]
    await ok(
        await client.post(
            f"/pdcs/{pdc['id']}/deposit", json={"bank_account_id": bank, "date": str(TODAY)}, headers=h
        )
    )
    cleared = await ok(
        await client.post(
            f"/pdcs/{pdc['id']}/clear", json={"date": str(TODAY + timedelta(days=1))}, headers=h
        )
    )
    assert cleared["status"] == "cleared"

    # --- the gate ------------------------------------------------------------------------------------------------
    final = await ok(await client.get("/trial-balance", params=business, headers=h))
    assert final["total_debit"] == final["total_credit"], "the trial balance balances"

    valuation = await ok(await client.get("/stock/valuation", params=business, headers=h))
    assert valuation["difference"] == "0.0000", "stock value equals the inventory account"
    stock = await ok(
        await client.get("/stock/balances", params={**business, "variant_id": variant}, headers=h)
    )
    assert [(x["qty"], x["value"]) for x in stock] == [("380.000000", "3800.0000")]
    assert await balance(client, h, accounts, "1300") == Decimal("3800")

    statement = await ok(await client.get(f"/parties/{customer}/ledger", params=business, headers=h))
    assert Decimal(str(statement["closing"])) == 0, "the customer owes nothing"
    assert await ok(await client.get("/bills", params={**business, "party_id": customer}, headers=h)) == []
    assert await balance(client, h, accounts, "1150") == Decimal("1764")  # the bank has the money
    assert await balance(client, h, accounts, "2120") == 0
