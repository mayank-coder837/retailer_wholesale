"""Opening balances and unpaid bills (M1.12b-1), against a small business worked out by hand.

The old books (trial balance on the opening date, today):

    1110 Cash 20,000 Dr    1150 Bank 50,000 Dr    1400 Input VAT 250 Dr    6100 Rent 1,200 Dr
    2130 Output VAT 400 Cr    3100 Capital 60,000 Cr    4100 Sales 13,050 Cr
    1200 Receivables 8,000 Dr    2110 Payables 6,000 Cr          (control: they come from the bills)
    total 79,450 Dr = 79,450 Cr

Bills: customers Ali INV-1 5,000, Gulf INV-2 3,500, Ali advance 500 (Cr)  = 8,000 receivable;
       supplier Mill MB-1 4,000, MB-2 2,500, advance paid 500 (Dr)          = 6,000 payable.

Posted from the trial balance: 71,450 Dr and 73,450 Cr, so 2,000 Dr goes to opening balance equity. The bills'
voucher is 8,000 Dr - 6,000 Cr = 2,000 Dr on the control accounts, so 2,000 Cr goes to equity: it nets to 0.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from hypothesis import given
from hypothesis import strategies as st

from app.modules.imports.kinds import opening
from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_accounting_api import Books, books
from tests.test_imports_api import check, commit, csv_file, ok, problems, run, upload
from tests.test_roles_api import add_member, login

TODAY = dubai_today()
OPENING = {"opening_date": TODAY.isoformat()}


def day(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).strftime("%d/%m/%Y")


TRIAL_BALANCE = """Account,Debit,Credit
1110,"20,000.00",
1150,50000,
1200,8000,
1400,250,
1120,,
Rent,1200,
2110,,6000
2130,,400
3100,,"60,000.00"
4100,,13050
"""


def bills_file() -> str:
    return (
        "Party,Invoice No,Invoice Date,Due Date,Amount,Dr/Cr\n"
        f'Ali Traders,INV-1,{day(40)},{day(10)},"5,000.00",\n'
        f"Gulf Fabrics,INV-2,{day(20)},,3500,\n"
        f"Ali Traders,ADV-1,{day(5)},,500.00,Cr\n"
        f"Mill,MB-1,{day(30)},{day(0)},4000,\n"
        f"Mill,MB-2,{day(10)},,2500,\n"
        f"Mill,PAID-ADV,{day(3)},,500,Dr\n"
    )


async def shop(client: AsyncClient, email: str) -> Books:
    b = await books(client, email)
    # Gulf Fabrics gives 30 days' credit, imported like the real thing.
    await run(b, "parties", "Name,Type,Credit Days\nGulf Fabrics,customer,30\n")
    return b


async def balances(b: Books, as_of: date = TODAY) -> dict[str, Decimal]:
    params = {"business_id": b.shop.business_id, "as_of": str(as_of)}
    report = await ok(await b.client.get("/trial-balance", params=params, headers=b.h))
    return {r["code"]: Decimal(r["debit"]) - Decimal(r["credit"]) for r in report["rows"]}


async def reconciliation(b: Books) -> dict[str, Any]:
    params = {"business_id": b.shop.business_id}
    result: dict[str, Any] = await ok(
        await b.client.get("/imports/reconciliation", params=params, headers=b.h)
    )
    return result


async def bills_of(b: Books, name: str) -> dict[str, dict[str, Any]]:
    found = await ok(await b.client.get("/parties", params={"q": name}, headers=b.h))
    params = {"business_id": b.shop.business_id, "party_id": found[0]["id"], "include_settled": "true"}
    rows = await ok(await b.client.get("/bills", params=params, headers=b.h))
    return {r["reference"]: r for r in rows}


async def test_the_trial_balance_and_the_bills_tie_to_the_source(client: AsyncClient) -> None:
    b = await shop(client, "tie@shop.ae")
    batch = await upload(b, "opening_balances", csv_file(TRIAL_BALANCE))
    assert batch["suggested_mapping"] == {"account": 0, "debit": 1, "credit": 2, "balance": None}
    checked = await check(b, batch, **OPENING)
    assert (checked["total"], checked["new"], checked["skipped"], checked["errors"]) == (10, 7, 3, 0)
    assert {r["label"]: r["status"] for r in checked["rows"] if r["status"] == "skipped"} == {
        "1200 Accounts receivable": "skipped",
        "2110 Accounts payable": "skipped",
        "1120 Petty cash": "skipped",
    }
    control = next(r for r in checked["rows"] if r["label"].startswith("1200"))
    assert "bills and stock make this balance" in control["note"]
    assert control["note_code"] == "control_account"
    summary = checked["summary"]
    assert (Decimal(summary["file_debit"]), Decimal(summary["file_credit"])) == (
        Decimal(79450),
        Decimal(79450),
    )
    assert Decimal(summary["file_difference"]) == 0
    assert (summary["equity_side"], Decimal(summary["equity_amount"])) == ("debit", Decimal(2000))
    assert {c["code"] for c in summary["controls"]} == {"1200", "2110"}
    assert await balances(b) == {}  # checking wrote nothing

    done = await commit(b, batch)
    assert done["result"]["counts"] == {"accounts": 7, "controls": 2, "skipped": 1, "vouchers": 1}
    assert done["result"]["opening_date"] == TODAY.isoformat()
    assert len(done["result"]["documents"]) == 1
    after_balance = await balances(b)
    assert after_balance == {
        "1110": Decimal(20000),
        "1150": Decimal(50000),
        "1400": Decimal(250),
        "6100": Decimal(1200),
        "2130": Decimal(-400),
        "3100": Decimal(-60000),
        "4100": Decimal(-13050),
        "3900": Decimal(2000),  # opening balance equity, until the bills come
    }

    early = await reconciliation(b)
    assert early["imported"] is True
    assert early["ties"] is False
    assert Decimal(early["equity_balance"]) == 2000
    by_code = {r["code"]: r for r in early["rows"]}
    assert (Decimal(by_code["1200"]["difference"]), by_code["1200"]["control"]) == (Decimal(-8000), True)
    assert Decimal(by_code["2110"]["difference"]) == 6000

    bills = await upload(b, "party_bills", csv_file(bills_file()))
    assert bills["suggested_mapping"]["party"] == 0
    assert bills["suggested_mapping"]["reference"] == 1
    assert bills["suggested_mapping"]["side"] == 5
    checked = await check(b, bills, **OPENING)
    assert (checked["new"], checked["errors"]) == (6, 0), problems(checked)
    summary = checked["summary"]
    assert [
        Decimal(summary[k]) for k in ("receivable", "receivable_advances", "payable", "payable_advances")
    ] == [
        Decimal(8500),
        Decimal(500),
        Decimal(6500),
        Decimal(500),
    ]
    done = await commit(b, bills)
    assert done["result"]["counts"] == {"bills": 4, "advances": 2, "skipped": 0, "vouchers": 1}

    final = await balances(b)
    assert final["1200"] == Decimal(8000)
    assert final["2110"] == Decimal(-6000)
    assert "3900" not in final or final["3900"] == 0  # opening balance equity nets to nothing
    assert sum(final.values()) == 0

    ok_recon = await reconciliation(b)
    assert ok_recon["ties"] is True
    assert Decimal(ok_recon["equity_balance"]) == 0
    assert all(Decimal(r["difference"]) == 0 for r in ok_recon["rows"])
    assert (Decimal(ok_recon["file_debit"]), Decimal(ok_recon["file_credit"])) == (
        Decimal(79450),
        Decimal(79450),
    )
    assert ok_recon["equity_code"] == "3900"
    assert ok_recon["opening_date"] == TODAY.isoformat()


async def test_the_bills_keep_their_own_dates_and_credit_terms(client: AsyncClient) -> None:
    b = await shop(client, "dates@shop.ae")
    await run(b, "opening_balances", TRIAL_BALANCE, **OPENING)
    await run(b, "party_bills", bills_file(), **OPENING)

    ali = await bills_of(b, "Ali Traders")
    inv = ali["INV-1"]
    assert (inv["bill_date"], inv["due_date"], inv["direction"], inv["is_advance"]) == (
        str(TODAY - timedelta(days=40)),
        str(TODAY - timedelta(days=10)),
        "debit",
        False,
    )
    assert Decimal(inv["amount_open"]) == 5000
    adv = ali["ADV-1"]
    assert (adv["direction"], adv["is_advance"], adv["bill_date"]) == (
        "credit",
        True,
        str(TODAY - timedelta(days=5)),
    )

    gulf = (await bills_of(b, "Gulf Fabrics"))["INV-2"]
    assert gulf["due_date"] == str(
        TODAY - timedelta(days=20) + timedelta(days=30)
    )  # invoice date + 30 credit days

    mill = await bills_of(b, "Mill")
    assert (mill["MB-1"]["direction"], mill["MB-1"]["is_advance"]) == ("credit", False)
    assert (
        mill["MB-2"]["due_date"] == mill["MB-2"]["bill_date"] == str(TODAY - timedelta(days=10))
    )  # no terms
    assert (mill["PAID-ADV"]["direction"], mill["PAID-ADV"]["is_advance"]) == ("debit", True)

    ageing = await ok(
        await b.client.get("/receivables/ageing", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert Decimal(ageing["totals"]["advances"]) == 500
    assert Decimal(ageing["totals"]["total"]) == 8000


async def test_a_trial_balance_that_does_not_tie_shows_by_how_much(client: AsyncClient) -> None:
    b = await books(client, "nottie@shop.ae")
    file = "Account,Debit,Credit\n1110,10000,\n1200,9000,\n3100,,19000\n"
    await run(b, "opening_balances", file, **OPENING)
    await run(b, "party_bills", "Party,Ref,Amount\nAli Traders,INV-1,8000\n", **OPENING)
    recon = await reconciliation(b)
    assert recon["ties"] is False
    assert Decimal(recon["equity_balance"]) == 1000  # 1,000 of receivables are missing
    by_code = {r["code"]: r for r in recon["rows"]}
    assert (Decimal(by_code["1200"]["file_balance"]), Decimal(by_code["1200"]["books_balance"])) == (
        9000,
        8000,
    )
    assert Decimal(by_code["1200"]["difference"]) == -1000
    assert Decimal(by_code["1110"]["difference"]) == 0


async def test_there_is_nothing_to_reconcile_before_an_import(client: AsyncClient) -> None:
    b = await books(client, "none@shop.ae")
    recon = await reconciliation(b)
    assert (recon["imported"], recon["ties"], recon["rows"], recon["batch_id"]) == (False, False, [], None)


async def test_a_balance_column_with_debit_and_credit_marks_is_read(client: AsyncClient) -> None:
    b = await books(client, "marks@shop.ae")
    file = 'Ledger,Closing Balance\n1110,"10,000.00 Dr"\n3100,"10,000.00 Cr"\nRent,(0)\n'
    batch = await upload(b, "opening_balances", csv_file(file))
    assert batch["suggested_mapping"]["account"] == 0
    assert batch["suggested_mapping"]["balance"] == 1
    checked = await check(b, batch, **OPENING)
    assert (checked["new"], checked["skipped"]) == (2, 1)
    await commit(b, batch)
    assert await balances(b) == {"1110": Decimal(10000), "3100": Decimal(-10000)}  # balanced: no equity line


async def test_trial_balance_rows_with_problems_are_named(client: AsyncClient) -> None:
    b = await books(client, "tbbad@shop.ae")
    file = (
        "Account,Debit,Credit\n"
        "9999,100,\n"  # 2: not on the chart
        "1100,100,\n"  # 3: a group
        "3900,100,\n"  # 4: opening balance equity itself
        "1110,100,\n"  # 5: fine
        "1110,100,\n"  # 6: twice
        "1150,-5,\n"  # 7: negative
        "1120,5,5\n"  # 8: both sides
        "6100,1.234,\n"  # 9: three decimals
        "6110,abc,\n"  # 10: not a number
        ",5,\n"  # 11: no account
    )
    batch = await upload(b, "opening_balances", csv_file(file))
    checked = await check(b, batch, **OPENING)
    assert problems(checked) == {
        2: [("account", "unknown_account")],
        3: [("account", "is_group")],
        4: [("account", "is_equity")],
        6: [("account", "duplicate_in_file")],
        7: [(None, "negative")],
        8: [(None, "both_sides")],
        9: [("debit", "decimals")],
        10: [("debit", "not_a_number")],
        11: [("account", "required")],
    }
    refused = await commit(b, batch, 422)
    assert refused["code"] == "import_has_errors"
    assert await balances(b) == {}


async def test_the_balance_column_cannot_be_mixed_with_debit_and_credit(client: AsyncClient) -> None:
    b = await books(client, "mixed@shop.ae")
    batch = await upload(b, "opening_balances", csv_file("Account,Debit,Balance\n1110,5,5\n"))
    assert problems(await check(b, batch, **OPENING)) == {2: [("balance", "balance_and_columns")]}


async def test_the_opening_date_must_be_in_a_year_that_exists_and_is_open(client: AsyncClient) -> None:
    b = await books(client, "dateerr@shop.ae")
    batch = await upload(b, "opening_balances", csv_file("Account,Debit\n1110,5\n"))
    body = {
        "mapping": batch["suggested_mapping"],
        "options": {"opening_date": str(date(TODAY.year - 3, 6, 30))},
    }
    refused = await ok(await b.client.post(f"/imports/{batch['id']}/check", json=body, headers=b.h), 422)
    assert refused["code"] == "opening_no_fiscal_year"

    missing = await ok(
        await b.client.post(
            f"/imports/{batch['id']}/check",
            json={"mapping": batch["suggested_mapping"], "options": {}},
            headers=b.h,
        ),
        422,
    )
    assert missing["code"] == "invalid_import_option"  # an opening date is required

    # Once the old year is created it works, until its month is locked.
    created = await ok(
        await b.client.post(
            "/fiscal-years",
            json={"business_id": b.shop.business_id, "day": str(date(TODAY.year - 3, 6, 30))},
            headers=b.h,
        ),
        201,
    )
    old = {"opening_date": str(date(TODAY.year - 3, 6, 30))}
    assert (await check(b, batch, **old))["new"] == 1
    month = next(p for p in created["periods"] if p["start_date"] <= old["opening_date"] <= p["end_date"])
    await ok(await b.client.put(f"/fiscal-periods/{month['id']}/lock", headers=b.h))
    body["options"] = old
    locked = await ok(await b.client.post(f"/imports/{batch['id']}/check", json=body, headers=b.h), 422)
    assert locked["code"] == "opening_period_closed"


async def test_a_trial_balance_is_imported_once_until_it_is_undone(client: AsyncClient) -> None:
    b = await books(client, "once@shop.ae")
    first = await upload(b, "opening_balances", csv_file("Account,Debit,Credit\n1110,10,\n3100,,10\n"))
    await check(b, first, **OPENING)
    await commit(b, first)

    second = await upload(b, "opening_balances", csv_file("Account,Debit,Credit\n1110,20,\n3100,,20\n"))
    refused = await ok(
        await b.client.post(
            f"/imports/{second['id']}/check",
            json={"mapping": second["suggested_mapping"], "options": OPENING},
            headers=b.h,
        ),
        409,
    )
    assert refused["code"] == "opening_already_imported"

    undone = await ok(await b.client.post(f"/imports/{first['id']}/undo", headers=b.h))
    assert undone["status"] == "undone"
    assert all(v == 0 for v in (await balances(b)).values())  # the cancel mirrors every line
    assert (await reconciliation(b))["imported"] is False

    await check(b, second, **OPENING)
    await commit(b, second)
    assert (await balances(b))["1110"] == 20
    # The first file, undone, can be imported again too (the second is now the one in place).
    again = await upload(b, "opening_balances", csv_file("Account,Debit,Credit\n1110,10,\n3100,,10\n"))
    assert again["duplicate_of"] is None  # an undone import does not count as imported


async def test_undo_takes_the_bills_out_and_the_file_can_come_again(client: AsyncClient) -> None:
    b = await shop(client, "undo@shop.ae")
    await run(b, "opening_balances", TRIAL_BALANCE, **OPENING)
    batch = await upload(b, "party_bills", csv_file(bills_file()))
    await check(b, batch, **OPENING)
    await commit(b, batch)
    assert (await balances(b))["1200"] == 8000

    # A receipt against an opening invoice does not stop the undo: it becomes an advance on that payment.
    await b.post(
        b.voucher(
            "receipt",
            [
                {
                    "party_id": b.customer,
                    "amount": "1000",
                    "allocations": [
                        {
                            "type": "against_ref",
                            "amount": "1000",
                            "bill_id": (await bills_of(b, "Ali Traders"))["INV-1"]["id"],
                        }
                    ],
                }
            ],
            account_id=b.cash,
        )
    )
    undone = await ok(await b.client.post(f"/imports/{batch['id']}/undo", headers=b.h))
    assert undone["status"] == "undone"
    now = await balances(b)
    assert now["1200"] == -1000  # only the receipt is left on receivables
    assert now.get("2110", 0) == 0
    assert (await ok(await b.client.get(f"/imports/{batch['id']}", headers=b.h)))["status"] == "undone"
    again = await ok(await b.client.post(f"/imports/{batch['id']}/undo", headers=b.h), 409)
    assert again["code"] == "import_not_committed"

    # Imported again from a fresh upload of the same file.
    fresh = await upload(b, "party_bills", csv_file(bills_file()))
    assert fresh["duplicate_of"] is None
    assert (await check(b, fresh, **OPENING))["new"] == 6


async def test_only_masters_cannot_be_undone(client: AsyncClient) -> None:
    b = await books(client, "noundo@shop.ae")
    _batch, _checked, done = await run(b, "parties", "Name\nOnly Co\n")
    refused = await ok(await b.client.post(f"/imports/{done['id']}/undo", headers=b.h), 422)
    assert refused["code"] == "not_undoable"


async def test_a_bill_already_in_the_books_is_skipped(client: AsyncClient) -> None:
    b = await shop(client, "skipbill@shop.ae")
    await run(b, "party_bills", bills_file(), **OPENING)
    more = bills_file() + f"Ali Traders,INV-9,{day(2)},,100,\n"
    batch = await upload(b, "party_bills", csv_file(more))
    checked = await check(b, batch, **OPENING)
    assert [r["status"] for r in checked["rows"]].count("exists") == 6
    assert (checked["new"], checked["exists"], checked["errors"]) == (1, 6, 0)
    done = await commit(b, batch)
    assert done["result"]["counts"]["bills"] == 1
    assert sorted(await bills_of(b, "Ali Traders")) == ["ADV-1", "INV-1", "INV-9"]  # nothing twice


async def test_bill_rows_with_problems_are_named(client: AsyncClient) -> None:
    b = await shop(client, "billbad@shop.ae")
    future = (TODAY + timedelta(days=2)).strftime("%d/%m/%Y")
    file = (
        "Party,Ref,Date,Due,Amount,Type\n"
        f"Nobody,R1,,,100,\n"  # 2: unknown party
        f"Ali Traders,R2,,,abc,\n"  # 3: not a number
        f"Ali Traders,R3,{future},,100,\n"  # 4: invoice after the opening date
        f"Ali Traders,R4,{day(10)},{day(20)},100,\n"  # 5: due before invoice
        f"Ali Traders,R5,,,10.555,\n"  # 6: three decimals
        f"Ali Traders,R6,,,100,supplier\n"  # 7: Ali is not a supplier
        f"Ali Traders,R7,,,100,\n"  # 8: fine
        f"Ali Traders,r7,,,100,\n"  # 9: the same reference again
        f"Mill,,,,100,\n"  # 10: no reference
        f"Ali Traders,R8,,,0,\n"  # 11: zero: nothing to import
    )
    batch = await upload(b, "party_bills", csv_file(file))
    checked = await check(b, batch, **OPENING)
    assert problems(checked) == {
        2: [("party", "unknown_party")],
        3: [("amount", "not_a_number")],
        4: [("bill_date", "after_opening")],
        5: [("due_date", "due_before_invoice")],
        6: [("amount", "decimals")],
        7: [("ledger", "wrong_role")],
        9: [("reference", "duplicate_in_file")],
        10: [("reference", "required")],
    }
    statuses = {r["number"]: r["status"] for r in checked["rows"]}
    assert (statuses[8], statuses[11]) == ("new", "skipped")  # a zero amount has nothing to import
    assert (await commit(b, batch, 422))["code"] == "import_has_errors"
    assert await balances(b) == {}


async def test_a_party_that_is_both_needs_a_side(client: AsyncClient) -> None:
    b = await books(client, "both@shop.ae")
    await run(b, "parties", "Name,Type\nTwo Ways,both\n")
    file = "Party,Ref,Amount\nTwo Ways,B1,100\n"
    batch = await upload(b, "party_bills", csv_file(file))
    checked = await check(
        b, batch, default_type="supplier", **OPENING
    )  # the option decides when the file is silent
    assert checked["new"] == 1
    assert Decimal(checked["summary"]["payable"]) == 100
    typed = await upload(b, "party_bills", csv_file("Party,Ref,Amount,Type\nTwo Ways,B2,100,customer\n"))
    checked = await check(b, typed, **OPENING)
    assert Decimal(checked["summary"]["receivable"]) == 100


async def test_a_large_trial_balance_is_posted_in_balanced_vouchers(client: AsyncClient) -> None:
    b = await books(client, "large@shop.ae")
    accounts = "Code,Name,Group\n" + "".join(f"7{n:03d},Branch cost {n},6000\n" for n in range(160))
    await run(b, "accounts", accounts)
    rows = "".join(f"7{n:03d},1,\n" for n in range(160)) + "3100,,160\n"
    _batch, checked, done = await run(b, "opening_balances", "Account,Debit,Credit\n" + rows, **OPENING)
    assert checked["new"] == 161
    assert done["result"]["counts"]["vouchers"] == 2  # 150 lines and 11
    final = await balances(b)
    assert final["3100"] == -160
    assert final["7000"] == 1
    assert final["7159"] == 1
    assert final.get("3900", 0) == 0  # the two vouchers' equity lines cancel


async def test_the_vouchers_are_ordinary_journals_audited_as_an_import(client: AsyncClient) -> None:
    b = await books(client, "journal@shop.ae")
    await run(b, "opening_balances", "Account,Debit,Credit\n1110,10,\n3100,,10\n", **OPENING)
    params = {"business_id": b.shop.business_id, "doc_type": "journal"}
    vouchers = await ok(await b.client.get("/vouchers", params=params, headers=b.h))
    assert len(vouchers) == 1
    assert vouchers[0]["number"].startswith("JV-")
    assert vouchers[0]["status"] == "posted"
    entries = await ok(
        await b.client.get(
            "/audit", params={"entity_type": "document", "entity_id": vouchers[0]["id"]}, headers=b.h
        )
    )
    assert {r["client"] for r in entries} == {"import"}


async def test_opening_imports_need_the_journal_permissions(client: AsyncClient) -> None:
    b = await books(client, "perm12b@shop.ae")
    role = await ok(
        await client.post(
            "/roles",
            json={
                "name_en": "Importer",
                "name_ar": "مستورد",
                "permissions": [
                    "import.view",
                    "import.create",
                    "import.post",
                    "account.view",
                    "journal.view",
                ],
            },
            headers=bearer(b.shop.owner),
        ),
        201,
    )
    b.shop.roles["importer"] = role["id"]
    await add_member(client, b.shop, "importer12b@shop.ae", "importer")
    importer = bearer(await login(client, "importer12b@shop.ae"))
    for kind in ("opening_balances", "party_bills"):
        response = await client.post(
            "/imports",
            data={"business_id": b.shop.business_id, "kind": kind},
            files={"file": ("f.csv", csv_file("Account,Debit\n1110,5\n"), "text/csv")},
            headers=importer,
        )
        assert response.status_code == 403, kind

    await add_member(client, b.shop, "acc12b@shop.ae", "accountant")
    accountant = bearer(await login(client, "acc12b@shop.ae"))
    response = await client.post(
        "/imports",
        data={"business_id": b.shop.business_id, "kind": "opening_balances"},
        files={"file": ("f.csv", csv_file("Account,Debit,Credit\n1110,5,\n3100,,5\n"), "text/csv")},
        headers=accountant,
    )
    assert response.status_code == 201
    undo = await client.post(f"/imports/{response.json()['id']}/undo", headers=importer)
    assert undo.status_code == 403


# --- the arithmetic that keeps every voucher balanced ------------------------------------------------------


@given(st.lists(st.integers(min_value=-(10**9), max_value=10**9), max_size=400))
def test_the_equity_line_always_balances_a_voucher(cents: list[int]) -> None:
    amounts = [Decimal(c).scaleb(-2) for c in cents]
    net_debit = sum((a for a in amounts), Decimal(0))
    side = opening.equity_side(net_debit)
    debit = sum((a for a in amounts if a > 0), Decimal(0)) + side.get("debit", Decimal(0))
    credit = sum((-a for a in amounts if a < 0), Decimal(0)) + side.get("credit", Decimal(0))
    assert debit == credit


@given(st.lists(st.integers(), max_size=500), st.integers(min_value=1, max_value=400))
def test_chunks_keep_every_item_once_and_in_order(items: list[int], size: int) -> None:
    parts = opening.chunks(items, size)
    assert [x for part in parts for x in part] == items
    assert all(0 < len(part) <= size for part in parts)


@pytest.mark.parametrize("net", [Decimal("5.25"), Decimal("-5.25")])
def test_a_voucher_that_already_balances_gets_no_equity_line(net: Decimal) -> None:
    assert opening.equity_side(Decimal(0)) == {}
    assert opening.equity_side(net) != {}
