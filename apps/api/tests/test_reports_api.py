"""Reports (M1.11a): profit and loss, balance sheet and day book against a hand-computed trading month.

The month, all on one day:
1. capital in:       Dr 1110 Cash 10,000         Cr 3100 Capital 10,000
2. a credit sale:    Dr 1200 Ali 5,000           Cr 4100 Sales 5,000
3. cost of the sale: Dr 5100 COGS 2,000          Cr 1500 Prepayments 2,000
4. other income:     Dr 1110 Cash 300            Cr 4800 Other income 300
5. rent, 5% VAT:     Dr 6100 Rent 1,000, Dr 1400 Input VAT 50   Cr 1110 Cash 1,050
6. Ali pays:         Dr 1110 Cash 2,000          Cr 1200 Ali 2,000

P&L: sales 5,000 - COGS 2,000 = gross 3,000; + other income 300 - expenses 1,000 = net 2,300.
Balance sheet: cash 11,250 + receivable 3,000 + input VAT 50 + prepayments -2,000 = assets 12,300;
no liabilities; equity: capital 10,000 + profit for the year 2,300 = 12,300.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_accounting_api import Books, books
from tests.test_pricing_api import ok
from tests.test_roles_api import add_member, login

TODAY = dubai_today()


async def trading_month(client: AsyncClient) -> Books:
    b = await books(client, "reports@shop.ae")

    def journal(dr: str, cr: str, amount: str) -> dict[str, Any]:
        return b.voucher(
            "journal",
            [{"account_id": b.account(dr), "debit": amount}, {"account_id": b.account(cr), "credit": amount}],
        )

    await b.post(journal("1110", "3100", "10000"))
    await b.invoice("5000", "INV-1")
    await b.post(journal("5100", "1500", "2000"))
    await b.post(journal("1110", "4800", "300"))
    rent = [{"account_id": b.account("6100"), "amount": "1000", "tax_code": "SR"}]
    await b.post(b.voucher("expense", rent, account_id=b.cash))
    await b.post(b.voucher("receipt", [{"party_id": b.customer, "amount": "2000"}], account_id=b.cash))
    return b


def rows(section: dict[str, Any]) -> list[tuple[str | None, str]]:
    return [(r["code"] or r["key"], r["amount"]) for r in section["rows"]]


async def get(b: Books, path: str, **params: Any) -> Any:
    query = {"business_id": b.shop.business_id, **params}
    return await ok(await b.client.get(path, params=query, headers=b.h))


async def test_profit_and_loss_of_the_month(client: AsyncClient) -> None:
    b = await trading_month(client)
    pl = await get(b, "/reports/profit-loss", **{"from": str(TODAY.replace(day=1)), "to": str(TODAY)})
    sections = {s["key"]: s for s in pl["sections"]}
    assert [s["key"] for s in pl["sections"]] == ["sales", "cogs", "other_income", "expenses"]
    assert rows(sections["sales"]) == [("4100", "5000.0000")]
    assert rows(sections["cogs"]) == [("5100", "2000.0000")]
    assert rows(sections["other_income"]) == [("4800", "300.0000")]
    assert rows(sections["expenses"]) == [("6100", "1000.0000")]
    assert (pl["gross_profit"], pl["net_profit"]) == ("3000.0000", "2300.0000")

    # A period before the month: nothing moved.
    before = TODAY - timedelta(days=40)
    empty = await get(b, "/reports/profit-loss", **{"from": str(before), "to": str(before)})
    assert all(s["rows"] == [] for s in empty["sections"])
    assert empty["net_profit"] == "0.0000"


async def test_balance_sheet_balances_and_shows_the_years_profit(client: AsyncClient) -> None:
    b = await trading_month(client)
    bs = await get(b, "/reports/balance-sheet", as_of=str(TODAY))
    assets = {s["key"]: s for s in bs["assets"]}
    assert rows(assets["current_assets"]) == [
        ("1110", "11250.0000"),
        ("1200", "3000.0000"),
        ("1400", "50.0000"),
        ("1500", "-2000.0000"),
    ]
    assert assets["fixed_assets"]["rows"] == []
    assert all(s["rows"] == [] for s in bs["liabilities"])
    assert rows(bs["equity"]) == [("3100", "10000.0000"), ("profit_year", "2300.0000")]
    assert (bs["total_assets"], bs["total_liabilities"], bs["total_equity"]) == (
        "12300.0000",
        "0.0000",
        "12300.0000",
    )
    assert bs["balanced"] is True
    assert bs["fiscal_year_start"] == str(TODAY.replace(month=1, day=1))

    # The trial balance agrees: every balance above is one of its rows.
    tb = await get(b, "/trial-balance", as_of=str(TODAY))
    # Debits: cash 11,250, receivable 3,000, VAT 50, COGS 2,000, rent 1,000; credits: capital 10,000, sales
    # 5,000, other income 300, prepayments 2,000.
    assert tb["total_debit"] == tb["total_credit"] == "17300.0000"
    shown = {r["account_id"]: r["amount"] for s in bs["assets"] for r in s["rows"]}
    for r in tb["rows"]:
        if r["account_id"] in shown:
            assert Decimal(shown[r["account_id"]]) == Decimal(r["debit"]) - Decimal(r["credit"])


async def test_day_book_lists_every_entry_with_its_lines(client: AsyncClient) -> None:
    b = await trading_month(client)
    book = await get(b, "/reports/day-book", **{"from": str(TODAY), "to": str(TODAY)})
    entries = book["entries"]
    assert [e["doc_type"] for e in entries] == [
        "journal",
        "journal",
        "journal",
        "journal",
        "expense",
        "receipt",
    ]
    assert all(e["number"] and e["entry_date"] == str(TODAY) for e in entries)
    rent = entries[4]
    assert [(x["code"], x["debit"], x["credit"]) for x in rent["lines"]] == [
        ("6100", "1000.0000", "0.0000"),
        ("1400", "50.0000", "0.0000"),
        ("1110", "0.0000", "1050.0000"),
    ]
    receipt = entries[5]
    assert {x["party_name_en"] for x in receipt["lines"]} == {None, "Ali Traders"}
    assert book["total_debit"] == book["total_credit"] == "20350.0000"
    assert book["truncated"] is False


async def test_reports_need_report_view_and_a_valid_period(client: AsyncClient) -> None:
    b = await trading_month(client)
    await add_member(
        client, b.shop, "cashier@shop.ae", "cashier", business=b.shop.business_id, branch=b.shop.branch_a
    )
    cashier = bearer(await login(client, "cashier@shop.ae"))
    period = {"business_id": b.shop.business_id, "from": str(TODAY), "to": str(TODAY)}
    for path, params in (
        ("/reports/profit-loss", period),
        ("/reports/day-book", period),
        ("/reports/balance-sheet", {"business_id": b.shop.business_id, "as_of": str(TODAY)}),
    ):
        assert (await client.get(path, params=params, headers=cashier)).status_code == 403
    backwards = await client.get(
        "/reports/profit-loss",
        params={"business_id": b.shop.business_id, "from": str(TODAY), "to": str(TODAY - timedelta(days=1))},
        headers=b.h,
    )
    assert (backwards.status_code, backwards.json()["code"]) == (422, "invalid_report_period")
