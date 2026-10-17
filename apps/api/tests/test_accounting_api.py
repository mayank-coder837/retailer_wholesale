"""Accounting API (M1.5): chart of accounts, roles, fiscal periods and the lock, vouchers and numbering,
bill-wise settlement, ledgers and the trial balance, permissions, and the credit check's real figures."""

import uuid
from datetime import date, timedelta
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


class Books:
    """A signed-in owner with the default chart, plus a customer and a supplier."""

    def __init__(self, client: AsyncClient, shop: Shop, accounts: list[dict[str, Any]]) -> None:
        self.client = client
        self.shop = shop
        self.h = bearer(shop.owner)
        self.by_code = {a["code"]: a for a in accounts}
        self.customer = ""
        self.supplier = ""

    def account(self, code: str) -> str:
        return str(self.by_code[code]["id"])

    @property
    def cash(self) -> str:
        return self.account("1110")

    @property
    def bank(self) -> str:
        return self.account("1150")

    def voucher(self, doc_type: str, lines: list[dict[str, Any]], **header: Any) -> dict[str, Any]:
        return {
            "doc_type": doc_type,
            "business_id": self.shop.business_id,
            "branch_id": header.pop("branch_id", self.shop.branch_a),
            "doc_date": str(header.pop("doc_date", TODAY)),
            "lines": lines,
            **header,
        }

    async def post(
        self, body: dict[str, Any], status: int = 201, headers: dict[str, str] | None = None
    ) -> Any:
        response = await self.client.post(
            "/vouchers", params={"post": "true"}, json=body, headers=headers or self.h
        )
        return await ok(response, status)

    async def invoice(self, amount: str, reference: str, party: str | None = None) -> dict[str, Any]:
        """A journal billing the customer: the shape of an opening invoice (sales invoices come in M1.8)."""
        lines = [
            {
                "account_id": self.account("1200"),
                "party_id": party or self.customer,
                "debit": amount,
                "allocations": [{"type": "new_ref", "amount": amount, "reference": reference}],
            },
            {"account_id": self.account("4100"), "credit": amount},
        ]
        result: dict[str, Any] = await self.post(self.voucher("journal", lines))
        return result

    async def bills(self, party: str | None = None) -> list[dict[str, Any]]:
        params = {"business_id": self.shop.business_id, "party_id": party or self.customer}
        result: list[dict[str, Any]] = await ok(
            await self.client.get("/bills", params=params, headers=self.h)
        )
        return result


async def books(client: AsyncClient, email: str = "books@shop.ae") -> Books:
    shop = await make_shop(client, email)
    h = bearer(shop.owner)
    accounts = await ok(await client.get("/accounts", params={"business_id": shop.business_id}, headers=h))
    b = Books(client, shop, accounts)
    customer = {"name_en": "Ali Traders", "name_ar": "علي", "is_customer": True}
    supplier = {"name_en": "Mill", "name_ar": "مصنع", "is_supplier": True}
    b.customer = (await ok(await client.post("/parties", json=customer, headers=h), 201))["id"]
    b.supplier = (await ok(await client.post("/parties", json=supplier, headers=h), 201))["id"]
    return b


# --- chart of accounts -------------------------------------------------------------------------------------


async def test_every_new_business_gets_the_uae_trading_chart_with_every_role(client: AsyncClient) -> None:
    b = await books(client)
    roles = await ok(
        await client.get("/account-roles", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert len(b.by_code) == 64
    assert {r["role"] for r in roles} >= {"ar_control", "ap_control", "inventory", "vat_input", "vat_output"}
    assert b.by_code["1200"]["roles"] == ["ar_control"]
    assert b.by_code["1300"]["allow_manual_posting"] is False  # inventory: stock documents only
    assert b.by_code["4100"]["name_ar"] == "المبيعات"
    codes = list(b.by_code)
    assert codes.index("1000") < codes.index("1100") < codes.index("1110")  # tree order

    second = await ok(
        await client.post(
            "/businesses", json={"legal_name_en": "Second LLC", "legal_name_ar": "ثانية"}, headers=b.h
        ),
        201,
    )
    theirs = await ok(await client.get("/accounts", params={"business_id": second["id"]}, headers=b.h))
    assert len(theirs) == 64
    assert {a["id"] for a in theirs}.isdisjoint(a["id"] for a in b.by_code.values())


async def test_accounts_follow_their_group_and_keep_code_and_type_once_posted(client: AsyncClient) -> None:
    b = await books(client)
    new = {
        "business_id": b.shop.business_id,
        "code": "6300",
        "name_en": "Courier",
        "name_ar": "البريد",
        "parent_id": b.account("6000"),
        "account_type": "expense",
    }
    courier = await ok(await client.post("/accounts", json=new, headers=b.h), 201)
    assert courier["nature"] == "expense"
    duplicate = await client.post("/accounts", json=new, headers=b.h)
    wrong_side = await client.post(
        "/accounts", json=new | {"code": "6301", "account_type": "bank"}, headers=b.h
    )
    under_ledger = await client.post(
        "/accounts", json=new | {"code": "6302", "parent_id": b.cash}, headers=b.h
    )
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "duplicate_account")
    assert (wrong_side.status_code, wrong_side.json()["code"]) == (422, "invalid_account")
    assert under_ledger.status_code == 422

    await b.post(b.voucher("expense", [{"account_id": courier["id"], "amount": "50"}], account_id=b.cash))
    renamed = await ok(
        await client.patch(f"/accounts/{courier['id']}", json={"name_en": "Couriers"}, headers=b.h)
    )
    recoded = await client.patch(f"/accounts/{courier['id']}", json={"code": "6399"}, headers=b.h)
    retyped = await client.patch(f"/accounts/{courier['id']}", json={"account_type": "cogs"}, headers=b.h)
    assert renamed["name_en"] == "Couriers"
    assert renamed["balance"] == "50.0000"
    assert recoded.status_code == retyped.status_code == 422

    archived_role = await client.patch(f"/accounts/{b.account('1200')}", json={"archived": True}, headers=b.h)
    inventory_by_hand = await client.patch(
        f"/accounts/{b.account('1300')}", json={"allow_manual_posting": True}, headers=b.h
    )
    assert archived_role.status_code == inventory_by_hand.status_code == 422
    groups = {
        a["code"]: a
        for a in await ok(
            await client.get("/accounts", params={"business_id": b.shop.business_id}, headers=b.h)
        )
    }
    assert groups["6000"]["balance"] == "50.0000"  # a group shows the total of its ledgers
    assert groups["1100"]["balance"] == "-50.0000"


async def test_roles_point_at_ledgers_of_the_right_type(client: AsyncClient) -> None:
    b = await books(client)
    body = {"business_id": b.shop.business_id, "account_id": b.cash}
    wrong = await client.put("/account-roles/ar_control", json=body, headers=b.h)
    assert (wrong.status_code, wrong.json()["code"]) == (422, "invalid_account")
    moved = await ok(
        await client.put(
            "/account-roles/bank_charges", json=body | {"account_id": b.account("6990")}, headers=b.h
        )
    )
    assert {r["role"]: r["account_id"] for r in moved}["bank_charges"] == b.account("6990")


# --- fiscal periods ----------------------------------------------------------------------------------------


async def test_posting_creates_the_fiscal_year_and_a_locked_month_needs_the_override(
    client: AsyncClient, app_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    b = await books(client)
    params = {"business_id": b.shop.business_id}
    assert await ok(await client.get("/fiscal-years", params=params, headers=b.h)) == []
    await b.post(b.voucher("receipt", [{"party_id": b.customer, "amount": "100"}], account_id=b.cash))
    [year] = await ok(await client.get("/fiscal-years", params=params, headers=b.h))
    assert year["code"] == str(TODAY.year)
    assert len(year["periods"]) == 12
    month = next(p for p in year["periods"] if p["start_date"] <= str(TODAY) <= p["end_date"])
    locked = await ok(await client.put(f"/fiscal-periods/{month['id']}/lock", headers=b.h))
    assert locked["status"] == "locked"

    # The manager may post vouchers but not into a locked month; the owner may, with a reason.
    await add_member(client, b.shop, "manager@shop.ae", "manager")
    manager = bearer(await login(client, "manager@shop.ae"))
    receipt = b.voucher("receipt", [{"party_id": b.customer, "amount": "10"}], account_id=b.cash)
    refused = await client.post("/vouchers", params={"post": "true"}, json=receipt, headers=manager)
    assert (refused.status_code, refused.json()["code"], refused.json()["may_override"]) == (
        409,
        "period_locked",
        False,
    )
    draft = await ok(await client.post("/vouchers", json=receipt, headers=b.h), 201)
    no_reason = await client.post(f"/vouchers/{draft['id']}/post", json={}, headers=b.h)
    assert (no_reason.status_code, no_reason.json()["may_override"]) == (409, True)
    posted = await ok(
        await client.post(
            f"/vouchers/{draft['id']}/post", json={"override_lock_reason": "late bank slip"}, headers=b.h
        )
    )
    assert posted["status"] == "posted"
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=uuid.UUID(b.shop.owner["tenant_id"]))
        override = (
            await s.scalars(
                select(AuditLog).where(AuditLog.action == "override", AuditLog.entity_id == month["id"])
            )
        ).one()
    assert override.reason == "late bank slip"
    assert override.changes is not None
    assert override.changes["override"] == "period_lock"

    unlocked = await ok(await client.put(f"/fiscal-periods/{month['id']}/unlock", headers=b.h))
    assert unlocked["status"] == "open"


async def test_years_far_from_today_are_created_on_purpose(client: AsyncClient) -> None:
    b = await books(client)
    old = date(TODAY.year - 3, 6, 15)
    body = b.voucher("receipt", [{"party_id": b.customer, "amount": "10"}], account_id=b.cash, doc_date=old)
    refused = await client.post("/vouchers", params={"post": "true"}, json=body, headers=b.h)
    assert (refused.status_code, refused.json()["code"]) == (409, "no_fiscal_year")
    year = await ok(
        await client.post(
            "/fiscal-years", json={"business_id": b.shop.business_id, "day": str(old)}, headers=b.h
        ),
        201,
    )
    assert year["code"] == str(old.year)
    posted = await b.post(body)
    assert posted["number"] == f"RV-{old.year}-00001"


# --- vouchers and numbering --------------------------------------------------------------------------------


async def test_numbers_are_gap_free_per_type_and_a_failed_post_returns_its_number(
    client: AsyncClient,
) -> None:
    b = await books(client)
    await b.invoice("1000", "INV-1")
    [bill] = await b.bills()
    first = await b.post(b.voucher("receipt", [{"party_id": b.customer, "amount": "100"}], account_id=b.cash))
    too_much = b.voucher(
        "receipt",
        [
            {
                "party_id": b.customer,
                "amount": "5000",
                "allocations": [{"type": "against_ref", "amount": "5000", "bill_id": bill["id"]}],
            }
        ],
        account_id=b.cash,
    )
    failed = await client.post("/vouchers", params={"post": "true"}, json=too_much, headers=b.h)
    second = await b.post(
        b.voucher("receipt", [{"party_id": b.customer, "amount": "200"}], account_id=b.cash)
    )
    year = TODAY.year
    assert (failed.status_code, failed.json()["code"]) == (409, "bill_not_open")
    assert (first["number"], second["number"]) == (f"RV-{year}-00001", f"RV-{year}-00002")
    drafts = await ok(
        await client.get(
            "/vouchers", params={"business_id": b.shop.business_id, "status": "draft"}, headers=b.h
        )
    )
    assert drafts == []  # the failed save-and-post left nothing behind
    other_branch = await b.post(
        b.voucher(
            "receipt", [{"party_id": b.customer, "amount": "1"}], account_id=b.cash, branch_id=b.shop.branch_b
        )
    )
    assert other_branch["number"] == f"RV-{year}-00001"  # each branch has its own series


async def test_drafts_change_freely_and_posted_vouchers_never_do(client: AsyncClient) -> None:
    b = await books(client)
    body = b.voucher(
        "payment", [{"party_id": b.supplier, "amount": "300"}], account_id=b.bank, narration="rent"
    )
    draft = await ok(await client.post("/vouchers", json=body, headers=b.h), 201)
    assert (draft["status"], draft["number"], draft["journal"]) == ("draft", None, [])
    body["lines"][0]["amount"] = "350"
    edited = await ok(await client.put(f"/vouchers/{draft['id']}", json=body, headers=b.h))
    assert edited["total"] == "350.0000"
    posted = await ok(await client.post(f"/vouchers/{draft['id']}/post", json={}, headers=b.h))
    assert [(j["account_code"], j["debit"], j["credit"]) for j in posted["journal"]] == [
        ("2110", "350.0000", "0.0000"),
        ("1150", "0.0000", "350.0000"),
    ]
    again = await client.put(f"/vouchers/{draft['id']}", json=body, headers=b.h)
    deleted = await client.delete(f"/vouchers/{draft['id']}", headers=b.h)
    repost = await client.post(f"/vouchers/{draft['id']}/post", json={}, headers=b.h)
    assert {again.status_code, deleted.status_code, repost.status_code} == {409}
    assert again.json()["code"] == "voucher_not_draft"

    scrap = await ok(await client.post("/vouchers", json=body, headers=b.h), 201)
    assert (await client.delete(f"/vouchers/{scrap['id']}", headers=b.h)).status_code == 204
    assert (await client.get(f"/vouchers/{scrap['id']}", headers=b.h)).status_code == 404


async def test_each_voucher_type_posts_its_rule(client: AsyncClient) -> None:
    b = await books(client)
    rent = await b.post(
        b.voucher(
            "expense",
            [{"account_id": b.account("6100"), "amount": "200", "vat_rate": "5"}],
            account_id=b.cash,
        )
    )
    contra = await b.post(
        b.voucher(
            "contra", [{"account_id": b.bank, "debit": "5000"}, {"account_id": b.cash, "credit": "5000"}]
        )
    )
    credit_note = await b.post(
        b.voucher(
            "credit_note", [{"amount": "100", "vat_rate": "5"}], party_id=b.customer, narration="allowance"
        )
    )
    debit_note = await b.post(
        b.voucher("debit_note", [{"amount": "100", "vat_rate": "5"}], party_id=b.supplier)
    )

    def journal(v: dict[str, Any]) -> list[tuple[str, str, str]]:
        return [(j["account_code"], j["debit"], j["credit"]) for j in v["journal"]]

    assert journal(rent) == [
        ("6100", "200.0000", "0.0000"),
        ("1400", "10.0000", "0.0000"),
        ("1110", "0.0000", "210.0000"),
    ]
    assert (rent["total"], rent["vat_total"], rent["number"]) == (
        "210.0000",
        "10.0000",
        f"EV-{TODAY.year}-00001",
    )
    assert journal(contra) == [("1150", "5000.0000", "0.0000"), ("1110", "0.0000", "5000.0000")]
    assert journal(credit_note) == [
        ("4200", "100.0000", "0.0000"),
        ("2130", "5.0000", "0.0000"),
        ("1200", "0.0000", "105.0000"),
    ]
    assert journal(debit_note) == [
        ("5400", "0.0000", "100.0000"),
        ("1400", "0.0000", "5.0000"),
        ("2110", "105.0000", "0.0000"),
    ]
    assert credit_note["number"].startswith("CN-")
    assert debit_note["number"].startswith("DBN-")


async def test_vouchers_refuse_lines_that_break_the_books(client: AsyncClient) -> None:
    b = await books(client)

    async def code(body: dict[str, Any]) -> tuple[int, str]:
        response = await client.post("/vouchers", json=body, headers=b.h)
        return response.status_code, response.json().get("code", "")

    ar, sales, inventory = b.account("1200"), b.account("4100"), b.account("1300")
    bad = {
        "unbalanced journal": b.voucher(
            "journal", [{"account_id": b.cash, "debit": "10"}, {"account_id": sales, "credit": "9"}]
        ),
        "control without party": b.voucher(
            "journal", [{"account_id": ar, "debit": "10"}, {"account_id": sales, "credit": "10"}]
        ),
        "party off control": b.voucher(
            "journal",
            [
                {"account_id": b.cash, "debit": "10"},
                {"account_id": sales, "credit": "10", "party_id": b.customer},
            ],
        ),
        "inventory by hand": b.voucher(
            "journal", [{"account_id": inventory, "debit": "10"}, {"account_id": sales, "credit": "10"}]
        ),
        "a group": b.voucher(
            "journal",
            [{"account_id": b.account("6000"), "debit": "10"}, {"account_id": sales, "credit": "10"}],
        ),
        "supplier on receivable": b.voucher(
            "receipt", [{"account_id": ar, "party_id": b.supplier, "amount": "10"}], account_id=b.cash
        ),
        "receipt into a sales account": b.voucher(
            "receipt", [{"party_id": b.customer, "amount": "10"}], account_id=sales
        ),
        "cash on a receipt line": b.voucher(
            "receipt", [{"account_id": b.bank, "amount": "10"}], account_id=b.cash
        ),
        "contra to an expense": b.voucher(
            "contra",
            [{"account_id": b.cash, "debit": "10"}, {"account_id": b.account("6100"), "credit": "10"}],
        ),
        "vat on a receipt": b.voucher(
            "receipt", [{"party_id": b.customer, "amount": "10", "vat_rate": "5"}], account_id=b.cash
        ),
        "vat of 7%": b.voucher(
            "expense", [{"account_id": b.account("6100"), "amount": "10", "vat_rate": "7"}], account_id=b.cash
        ),
        "fils below a fils": b.voucher(
            "receipt", [{"party_id": b.customer, "amount": "10.005"}], account_id=b.cash
        ),
        "allocations short": b.voucher(
            "receipt",
            [{"party_id": b.customer, "amount": "10", "allocations": [{"type": "advance", "amount": "4"}]}],
            account_id=b.cash,
        ),
        "branch of nobody": b.voucher(
            "receipt",
            [{"party_id": b.customer, "amount": "10"}],
            account_id=b.cash,
            branch_id=b.shop.business_id,
        ),
    }
    results = {name: await code(body) for name, body in bad.items()}
    assert results.pop("branch of nobody")[0] == 404
    assert results == {name: (422, "invalid_voucher") for name in results}


# --- bill-wise settlement ----------------------------------------------------------------------------------


async def test_receipts_settle_bills_bill_wise_like_tally(client: AsyncClient) -> None:
    b = await books(client)
    await b.invoice("1000", "INV-1")
    await b.invoice("500", "INV-2")
    inv1, inv2 = await b.bills()
    assert (inv1["reference"], inv1["amount_open"], inv1["direction"]) == ("INV-1", "1000.0000", "debit")

    # 1,200 received: 1,000 settles INV-1, 150 part of INV-2, 50 is an advance.
    receipt = await b.post(
        b.voucher(
            "receipt",
            [
                {
                    "party_id": b.customer,
                    "amount": "1200",
                    "allocations": [
                        {"type": "against_ref", "amount": "1000", "bill_id": inv1["id"]},
                        {"type": "against_ref", "amount": "150", "bill_id": inv2["id"]},
                        {"type": "advance", "amount": "50"},
                    ],
                }
            ],
            account_id=b.bank,
        )
    )
    bills = {x["reference"]: x for x in await b.bills()}
    assert "INV-1" not in bills  # settled in full
    assert bills["INV-2"]["amount_open"] == "350.0000"
    advance = bills[receipt["number"]]
    assert (advance["is_advance"], advance["direction"], advance["amount_open"]) == (
        True,
        "credit",
        "50.0000",
    )

    # A receipt without bill-wise choices is on account.
    await b.post(b.voucher("receipt", [{"party_id": b.customer, "amount": "20"}], account_id=b.cash))
    statement = await ok(
        await client.get(
            f"/parties/{b.customer}/ledger", params={"business_id": b.shop.business_id}, headers=b.h
        )
    )
    assert statement["closing"] == "280.0000"  # 1,500 billed - 1,220 received
    assert statement["on_account"] == "-20.0000"
    assert [x["reference"] for x in statement["open_bills"]] == ["INV-2", receipt["number"]]
    assert statement["lines"][-1]["balance"] == "280.0000"

    # Settling the advance against INV-2 by journal: both open items shrink.
    await b.post(
        b.voucher(
            "journal",
            [
                {
                    "account_id": b.account("1200"),
                    "party_id": b.customer,
                    "debit": "50",
                    "allocations": [{"type": "against_ref", "amount": "50", "bill_id": advance["id"]}],
                },
                {
                    "account_id": b.account("1200"),
                    "party_id": b.customer,
                    "credit": "50",
                    "allocations": [{"type": "against_ref", "amount": "50", "bill_id": bills["INV-2"]["id"]}],
                },
            ],
        )
    )
    after = {x["reference"]: x["amount_open"] for x in await b.bills()}
    assert after == {"INV-2": "300.0000"}


async def test_a_bill_is_settled_only_by_its_own_party_and_side(client: AsyncClient) -> None:
    b = await books(client)
    other = (
        await ok(
            await client.post(
                "/parties", json={"name_en": "Other", "name_ar": "آخر", "is_customer": True}, headers=b.h
            ),
            201,
        )
    )["id"]
    await b.invoice("100", "INV-1")
    [bill] = await b.bills()

    def receipt(party: str) -> dict[str, Any]:
        return b.voucher(
            "receipt",
            [
                {
                    "party_id": party,
                    "amount": "10",
                    "allocations": [{"type": "against_ref", "amount": "10", "bill_id": bill["id"]}],
                }
            ],
            account_id=b.cash,
        )

    wrong_party = await client.post("/vouchers", params={"post": "true"}, json=receipt(other), headers=b.h)
    same_side = await client.post(
        "/vouchers",
        params={"post": "true"},
        json=b.voucher(
            "journal",
            [
                {
                    "account_id": b.account("1200"),
                    "party_id": b.customer,
                    "debit": "10",
                    "allocations": [{"type": "against_ref", "amount": "10", "bill_id": bill["id"]}],
                },
                {"account_id": b.account("4100"), "credit": "10"},
            ],
        ),
        headers=b.h,
    )
    assert (wrong_party.status_code, wrong_party.json()["code"]) == (409, "bill_not_open")
    assert (same_side.status_code, same_side.json()["code"]) == (409, "bill_not_open")
    assert (await b.bills())[0]["amount_open"] == "100.0000"


async def test_a_new_bill_is_due_after_the_customers_credit_days(client: AsyncClient) -> None:
    b = await books(client)
    profile = {"business_id": b.shop.business_id, "credit_days": 30, "credit_policy": "warn"}
    await ok(await client.put(f"/parties/{b.customer}/customer-profile", json=profile, headers=b.h))
    await b.invoice("100", "INV-1")
    [bill] = await b.bills()
    assert bill["due_date"] == str(TODAY + timedelta(days=30))


# --- credit check, round-off, trial balance ----------------------------------------------------------------


async def test_the_credit_check_now_sees_the_ledger_balance(client: AsyncClient) -> None:
    b = await books(client)
    profile = {"business_id": b.shop.business_id, "credit_limit": "1000", "credit_policy": "block"}
    await ok(await client.put(f"/parties/{b.customer}/customer-profile", json=profile, headers=b.h))
    await b.invoice("800", "INV-1")
    check = await ok(
        await client.post(
            f"/parties/{b.customer}/credit-check",
            json={"business_id": b.shop.business_id, "amount": "300"},
            headers=b.h,
        )
    )
    assert (check["ledger_balance"], check["decision"], check["reasons"]) == (
        "800.0000",
        "block",
        ["over_limit"],
    )
    await b.post(b.voucher("receipt", [{"party_id": b.customer, "amount": "500"}], account_id=b.cash))
    check = await ok(
        await client.post(
            f"/parties/{b.customer}/credit-check",
            json={"business_id": b.shop.business_id, "amount": "300"},
            headers=b.h,
        )
    )
    assert (check["ledger_balance"], check["decision"]) == ("300.0000", "ok")


async def test_receipt_round_off_is_an_option_off_by_default(client: AsyncClient) -> None:
    b = await books(client)
    exact = await b.post(
        b.voucher("receipt", [{"party_id": b.customer, "amount": "1049.97"}], account_id=b.cash)
    )
    assert exact["total"] == "1049.9700"
    for key, value in (("rounding.receipt.mode", "nearest"), ("rounding.receipt.step", "0.05")):
        body = {"scope": "business", "scope_id": b.shop.business_id, "value": value}
        await ok(await client.put(f"/settings/{key}", json=body, headers=b.h))
    rounded = await b.post(
        b.voucher("receipt", [{"party_id": b.customer, "amount": "1049.97"}], account_id=b.cash)
    )
    assert rounded["total"] == "1049.9500"
    assert [(j["account_code"], j["debit"], j["credit"]) for j in rounded["journal"]] == [
        ("1200", "0.0000", "1049.9700"),
        ("1110", "1049.9500", "0.0000"),
        ("4900", "0.0200", "0.0000"),
    ]


async def test_the_trial_balance_balances_and_ledgers_add_up(client: AsyncClient) -> None:
    b = await books(client)
    await b.invoice("1000", "INV-1")
    await b.post(b.voucher("receipt", [{"party_id": b.customer, "amount": "400"}], account_id=b.cash))
    await b.post(
        b.voucher(
            "expense",
            [{"account_id": b.account("6100"), "amount": "200", "vat_rate": "5"}],
            account_id=b.cash,
        )
    )
    tb = await ok(await client.get("/trial-balance", params={"business_id": b.shop.business_id}, headers=b.h))
    assert tb["total_debit"] == tb["total_credit"] == "1000.0000"
    assert {r["code"]: (r["debit"], r["credit"]) for r in tb["rows"]} == {
        "1110": ("190.0000", "0.0000"),
        "1200": ("600.0000", "0.0000"),
        "1400": ("10.0000", "0.0000"),
        "4100": ("0.0000", "1000.0000"),
        "6100": ("200.0000", "0.0000"),
    }
    cash = await ok(await client.get(f"/accounts/{b.cash}/ledger", headers=b.h))
    assert (cash["debit_total"], cash["credit_total"], cash["closing"]) == (
        "400.0000",
        "210.0000",
        "190.0000",
    )
    before = await ok(
        await client.get(
            "/trial-balance",
            params={"business_id": b.shop.business_id, "as_of": str(TODAY - timedelta(days=400))},
            headers=b.h,
        )
    )
    assert before["rows"] == []


# --- permissions and idempotency ---------------------------------------------------------------------------


async def test_cashiers_take_receipts_in_their_branch_only(client: AsyncClient) -> None:
    b = await books(client)
    await add_member(
        client, b.shop, "cashier@shop.ae", "cashier", business=b.shop.business_id, branch=b.shop.branch_a
    )
    cashier = bearer(await login(client, "cashier@shop.ae"))
    receipt = b.voucher("receipt", [{"party_id": b.customer, "amount": "10"}], account_id=b.cash)
    assert (
        await client.get("/accounts", params={"business_id": b.shop.business_id}, headers=cashier)
    ).status_code == 200
    taken = await b.post(receipt, headers=cashier)
    assert taken["status"] == "posted"
    other_branch = await client.post(
        "/vouchers", json=receipt | {"branch_id": b.shop.branch_b}, headers=cashier
    )
    journal = await client.post(
        "/vouchers",
        json=b.voucher(
            "journal",
            [{"account_id": b.cash, "debit": "1"}, {"account_id": b.account("4800"), "credit": "1"}],
        ),
        headers=cashier,
    )
    tb = await client.get("/trial-balance", params={"business_id": b.shop.business_id}, headers=cashier)
    lock = await client.post("/accounts", json={}, headers=cashier)
    assert {other_branch.status_code, journal.status_code, tb.status_code} == {403}
    assert lock.status_code in (403, 422)

    await add_member(client, b.shop, "sales@shop.ae", "salesperson")
    sales = bearer(await login(client, "sales@shop.ae"))
    assert (await client.post("/vouchers", json=receipt, headers=sales)).status_code == 403
    listed = await ok(
        await client.get("/vouchers", params={"business_id": b.shop.business_id}, headers=sales)
    )
    assert [v["doc_type"] for v in listed] == ["receipt"]  # receipt.view only


async def test_a_retried_save_and_post_makes_one_voucher(client: AsyncClient) -> None:
    b = await books(client)
    body = b.voucher("receipt", [{"party_id": b.customer, "amount": "75"}], account_id=b.cash)
    headers = b.h | {"Idempotency-Key": "receipt-75"}
    first = await b.post(body, headers=headers)
    second = await b.post(body, headers=headers)
    assert first["id"] == second["id"]
    listed = await ok(await client.get("/vouchers", params={"business_id": b.shop.business_id}, headers=b.h))
    assert len(listed) == 1
