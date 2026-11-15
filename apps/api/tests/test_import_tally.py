"""Tally master exports (M1.12c): every kind read from one file, and the whole opening position tied.

``fixtures/tally_masters.xml`` is the small business of the earlier opening tests, written the way Tally
writes it (a debit balance is a negative number): cash 8,640, bank 50,000, rent 1,200, customers 8,000
(Ali INV-1 5,000 less an advance of 500; Gulf INV-2 3,500), stock 1,360 (cotton 1,020, bolts 190, medicine
150 in batch B-1) against capital 50,000, sales 12,800, output VAT 400 and suppliers 6,000 (Mill MB-1 4,000
and MB-2 2,500, less 500 paid in advance). Debits 69,200 = credits 69,200.
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.modules.imports import tally
from app.modules.imports.readers import UnreadableFileError, read_table
from tests.test_accounting_api import books
from tests.test_import_opening_api import OPENING, balances, reconciliation
from tests.test_imports_api import check, commit, ok, problems, upload

TEXT = (Path(__file__).parent / "fixtures" / "tally_masters.xml").read_text(encoding="utf8")
DATA = TEXT.encode("utf8")


def rows_of(kind: str, data: bytes = DATA) -> list[dict[str, str]]:
    table = read_table("masters.xml", data, kind)
    assert table.canonical
    return [dict(zip(table.headers, (str(c) for c in row), strict=True)) for row in table.rows]


def plain_convention(text: str) -> str:
    """The same export with debits written as positive numbers (the other way a file can be)."""
    pattern = r"(<(?:OPENINGBALANCE|OPENINGVALUE)>)(-?)([\d.]+)(</)"
    return re.sub(pattern, lambda m: m[1] + ("" if m[2] else "-") + m[3] + m[4], text)


def test_customers_and_suppliers_are_read_with_their_details() -> None:
    parties = {r["name_en"]: r for r in rows_of("parties")}
    assert list(parties) == ["Ali Traders", "Gulf Fabrics", "Mill", "Textile Mill"]
    ali = parties["Ali Traders"]
    assert (ali["type"], ali["trn"], ali["phone"], ali["email"]) == (
        "customer",
        "100123456700003",
        "0501234567",
        "accounts@ali.ae",
    )
    assert (ali["address"], ali["emirate"]) == ("Al Quoz 4, Dubai", "dubai")
    gulf = parties["Gulf Fabrics"]  # in a sub-group of Sundry Debtors
    assert (gulf["type"], gulf["name_ar"], gulf["credit_days"], gulf["credit_limit"]) == (
        "customer",
        "الخليج للأقمشة",
        "30",
        "50000.00",
    )
    assert (parties["Mill"]["type"], parties["Textile Mill"]["type"]) == ("supplier", "supplier")


def test_other_ledgers_become_accounts_in_the_group_their_tally_group_maps_to() -> None:
    accounts = {r["name_en"]: (r["parent"], r["type"], r["code"]) for r in rows_of("accounts")}
    assert accounts == {
        "Cash": ("1100", "cash", ""),
        "Bank": ("1100", "bank", ""),
        "Capital": ("3000", "equity", ""),
        "Sales": ("4000", "sales", ""),
        "Rent": ("6000", "expense", ""),
        "Output VAT": ("2100", "tax", ""),
        "Staff welfare": ("6000", "expense", ""),
    }


def test_the_trial_balance_adds_customers_suppliers_and_stock_into_control_rows() -> None:
    balance = {
        r["account"]: (Decimal(r["debit"] or 0), Decimal(r["credit"] or 0))
        for r in rows_of("opening_balances")
    }
    assert balance == {
        "Cash": (Decimal(8640), Decimal(0)),
        "Bank": (Decimal(50000), Decimal(0)),
        "Capital": (Decimal(0), Decimal(50000)),
        "Sales": (Decimal(0), Decimal(12800)),
        "Rent": (Decimal(1200), Decimal(0)),
        "Output VAT": (Decimal(0), Decimal(400)),
        "@receivable": (Decimal(8000), Decimal(0)),  # 4,500 + 3,500
        "@payable": (Decimal(0), Decimal(6000)),
        "@inventory": (Decimal(1360), Decimal(0)),
    }
    debit = sum(d for d, _c in balance.values())
    credit = sum(c for _d, c in balance.values())
    assert debit == credit == Decimal(69200)


def test_bills_carry_their_dates_sides_and_credit_periods() -> None:
    bills = {r["reference"]: r for r in rows_of("party_bills")}
    assert list(bills) == [
        "INV-1",
        "ADV-1",
        "INV-2",
        "MB-1",
        "MB-2",
        "PAID-ADV",
    ]  # all bill-wise: nothing on account
    inv = bills["INV-1"]
    assert (inv["party"], inv["amount"], inv["side"], inv["ledger"]) == (
        "Ali Traders",
        "5000.00",
        "Dr",
        "customer",
    )
    assert (inv["bill_date"], inv["due_date"]) == ("2026-02-15", "2026-03-17")  # 30 days
    assert (bills["ADV-1"]["side"], bills["ADV-1"]["due_date"]) == ("Cr", "")
    assert (bills["MB-1"]["side"], bills["MB-1"]["ledger"]) == ("Cr", "supplier")
    assert (bills["PAID-ADV"]["side"], bills["PAID-ADV"]["amount"]) == ("Dr", "500.00")


def test_a_balance_that_is_not_bill_wise_is_carried_on_account() -> None:
    text = TEXT.replace(
        "<OPENINGBALANCE>-4500.00</OPENINGBALANCE>\n      <BILLALLOCATIONS.LIST>",
        "<OPENINGBALANCE>-4700.00</OPENINGBALANCE>\n      <BILLALLOCATIONS.LIST>",
    )
    on_account = [
        r for r in rows_of("party_bills", text.encode("utf8")) if r["reference"] == "Opening balance"
    ]
    assert [(r["party"], r["amount"], r["side"]) for r in on_account] == [("Ali Traders", "200.00", "Dr")]


def test_stock_items_become_items_and_opening_stock() -> None:
    products = {r["name_en"]: r for r in rows_of("products")}
    assert {n: (r["sku"], r["unit"], r["track_batch"], r["track_expiry"]) for n, r in products.items()} == {
        "Cotton fabric": ("COTTON-FABRIC", "m", "", ""),
        "Bolt": ("BOLT", "Nos", "", ""),
        "Medicine": ("MEDICINE", "Nos", "yes", "yes"),
    }
    stock = {r["item"]: r for r in rows_of("opening_stock")}
    assert (
        stock["Cotton fabric"]["quantity"],
        stock["Cotton fabric"]["value"],
        stock["Cotton fabric"]["unit"],
    ) == (
        "120",
        "1020.00",
        "m",
    )
    assert (stock["Bolt"]["quantity"], stock["Bolt"]["value"]) == ("1000", "190.00")
    medicine = stock["Medicine"]
    assert (medicine["warehouse"], medicine["batch_no"], medicine["expiry_date"], medicine["value"]) == (
        "Second warehouse",
        "B-1",
        "2027-12-31",
        "150.00",
    )


def test_the_sign_convention_is_found_from_the_file_either_way() -> None:
    assert "minus sign" in tally.read_export(DATA).notes[0]
    flipped = plain_convention(TEXT).encode("utf8")
    assert "positive numbers" in tally.read_export(flipped).notes[0]
    for kind in ("opening_balances", "party_bills", "opening_stock"):
        assert rows_of(kind, flipped) == rows_of(kind)  # the same figures, however the file writes a debit


def test_utf16_files_and_stray_control_characters_are_read() -> None:
    assert rows_of("parties", TEXT.encode("utf-16")) == rows_of("parties")  # Tally writes UTF-16 too
    dirty = TEXT.replace("accounts@ali.ae", "accounts@ali.ae&#4;").encode(
        "utf8"
    )  # a character XML 1.0 forbids
    assert {r["name_en"]: r["email"] for r in rows_of("parties", dirty)}["Ali Traders"] == "accounts@ali.ae"


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"this is not xml", "parties"),
        (b"<html><body/></html>", "parties"),  # XML, but not a Tally export
        (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aa"><!ENTITY b "&a;&a;">]>'
            b"<ENVELOPE>&b;</ENVELOPE>",
            "parties",
        ),  # entity expansion
        (
            b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///x">]><ENVELOPE>&e;</ENVELOPE>',
            "parties",
        ),  # an external file
        (DATA, None),  # no kind chosen
        (DATA, "nonsense"),
        (
            b"<ENVELOPE><TALLYMESSAGE><LEDGER NAME='X'/></TALLYMESSAGE></ENVELOPE>",
            "products",
        ),
    ],
)
def test_files_that_are_not_what_is_needed_are_refused_plainly(data: bytes, kind: str | None) -> None:
    with pytest.raises(UnreadableFileError):
        read_table("masters.xml", data, kind)


def test_a_group_tally_calls_something_else_is_left_for_the_check_to_name() -> None:
    odd = TEXT.replace("<PARENT>Indirect Expenses</PARENT>", "<PARENT>Mystery group</PARENT>", 1)
    first = rows_of("accounts", odd.encode("utf8"))[0]
    rent = next(r for r in rows_of("accounts", odd.encode("utf8")) if r["name_en"] == "Rent")
    assert (rent["parent"], rent["type"]) == ("", "")
    assert first["name_en"] == "Cash"


# --- the whole export, kind by kind, through the API -------------------------------------------------------


async def test_a_tally_export_brings_the_whole_opening_position_and_it_ties(client: AsyncClient) -> None:
    b = await books(client, "tally@shop.ae")
    await ok(
        await client.post(
            f"/branches/{b.shop.branch_a}/warehouses",
            json={"code": "WH2", "name_en": "Second warehouse", "name_ar": "المستودع الثاني"},
            headers=b.h,
        ),
        201,
    )

    async def step(kind: str, **options: str) -> dict[str, object]:
        batch = await upload(b, kind, DATA, "masters.xml")
        assert "minus sign" in batch["notes"][0]
        assert all(index is not None for index in batch["suggested_mapping"].values() if index is not None)
        checked = await check(b, batch, **options)
        assert checked["errors"] == 0, problems(checked)
        done = await commit(b, batch)
        return {"checked": checked, "done": done}

    accounts = await step("accounts")
    assert (accounts["checked"]["new"], accounts["checked"]["exists"]) == (1, 6)  # only Staff welfare is new
    chart = {
        a["name_en"]: a
        for a in await ok(
            await client.get("/accounts", params={"business_id": b.shop.business_id}, headers=b.h)
        )
    }
    assert (chart["Staff welfare"]["code"], chart["Staff welfare"]["account_type"]) == ("6010", "expense")
    assert chart["Rent"]["code"] == "6100"  # an account already on the chart is not made again

    parties = await step("parties")
    assert (parties["checked"]["new"], parties["checked"]["exists"]) == (
        2,
        2,
    )  # Gulf Fabrics and Textile Mill
    found = await ok(await client.get("/parties", params={"q": "Gulf"}, headers=b.h))
    gulf = await ok(await client.get(f"/parties/{found[0]['id']}", headers=b.h))
    assert gulf["name_ar"] == "الخليج للأقمشة"
    assert (
        Decimal(gulf["customer_profiles"][0]["credit_limit"]),
        gulf["customer_profiles"][0]["credit_days"],
    ) == (50000, 30)

    assert (await step("products"))["checked"]["new"] == 3

    balance = await step("opening_balances", **OPENING)
    assert (balance["checked"]["new"], balance["checked"]["skipped"]) == (6, 3)  # the three control rows wait
    assert Decimal(balance["checked"]["summary"]["file_debit"]) == Decimal(69200)

    bills = await step("party_bills", **OPENING)
    assert (bills["checked"]["new"], bills["checked"]["exists"]) == (6, 0)

    stock = await step("opening_stock", **OPENING)
    assert (stock["checked"]["new"], stock["done"]["result"]["counts"]["documents"]) == (3, 2)
    assert Decimal(stock["done"]["result"]["counts"]["value"]) == Decimal(1360)

    final = await balances(b)
    assert (final["1200"], final["2110"], final["1300"]) == (Decimal(8000), Decimal(-6000), Decimal(1360))
    assert final.get("3900", 0) == 0  # opening balance equity nets to nothing
    assert sum(final.values()) == 0
    recon = await reconciliation(b)
    assert recon["ties"] is True
    assert Decimal(recon["equity_balance"]) == 0
    assert all(Decimal(r["difference"]) == 0 for r in recon["rows"])
    valuation = await ok(
        await client.get("/stock/valuation", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert (Decimal(valuation["stock_value"]), Decimal(valuation["inventory_account"])) == (1360, 1360)

    again = await upload(b, "party_bills", DATA, "masters.xml")  # the same export again: nothing is doubled
    assert again["duplicate_of"] is not None
