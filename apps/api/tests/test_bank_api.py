"""Bank reconciliation over the API (P2.3): import, auto-match, manual match, the gap, permissions."""

from datetime import timedelta
from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_accounting_api import Books, books
from tests.test_pricing_api import ok
from tests.test_roles_api import add_member, login

D1 = dubai_today() - timedelta(days=6)
D2 = D1 + timedelta(days=1)
D3 = D1 + timedelta(days=2)
D4 = D1 + timedelta(days=3)


async def bank_books(client: AsyncClient, email: str) -> Books:
    b = await books(client, email)
    for day, bank_debit, bank_credit, other in (
        (D1, "1000", None, "3100"),  # capital paid in
        (D2, None, "400", "6100"),  # rent paid
        (D3, "250", None, "4800"),  # a deposit that the statement does not show yet
    ):
        bank_line = (
            {"account_id": b.bank, "debit": bank_debit}
            if bank_debit
            else {"account_id": b.bank, "credit": bank_credit}
        )
        amount = bank_debit or bank_credit
        counter = (
            {"account_id": b.account(other), "credit": amount}
            if bank_debit
            else {"account_id": b.account(other), "debit": amount}
        )
        await b.post(b.voucher("journal", [bank_line, counter], doc_date=day))
    return b


CSV = (
    "Date,Description,Reference,Amount\n"
    f"{D1},Transfer in,,1000\n"
    f"{D2},Landlord,,-400\n"
    f"{D4},Bank interest,,75.50\n"
)


async def upload(b: Books, csv: str = CSV, **form: str) -> Any:
    data = {"business_id": b.shop.business_id, "account_id": b.bank, "closing_balance": "675.50", **form}
    return await b.client.post(
        "/bank/statements", data=data, files={"file": ("nov.csv", csv.encode(), "text/csv")}, headers=b.h
    )


async def test_import_matches_what_it_can_and_the_gap_is_explained(client: AsyncClient) -> None:
    b = await bank_books(client, "bank1@shop.ae")
    done = await ok(await upload(b), 201)
    assert (done["lines"], done["duplicates_skipped"], done["auto_matched"]) == (3, 0, 2)

    detail = await ok(await client.get(f"/bank/statements/{done['statement']['id']}", headers=b.h))
    matched = {ln["description"]: ln["matched_line_id"] is not None for ln in detail["lines"]}
    assert matched == {"Transfer in": True, "Landlord": True, "Bank interest": False}

    params = {"business_id": b.shop.business_id}
    rec = await ok(await client.get(f"/bank/accounts/{b.bank}/reconciliation", params=params, headers=b.h))
    assert rec["book_balance"] == "850.0000"  # 1000 - 400 + 250
    assert rec["unmatched_statement_total"] == "75.5000"  # interest not in the books yet
    assert rec["unmatched_book_total"] == "250.0000"  # the deposit not on the statement yet
    assert (rec["adjusted_bank"], rec["adjusted_book"]) == ("925.5000", "925.5000")
    assert (rec["difference"], rec["reconciled"]) == ("0.0000", True)

    left = await ok(
        await client.get(f"/bank/accounts/{b.bank}/unmatched-book-lines", params=params, headers=b.h)
    )
    assert [(ln["amount"], ln["entry_date"]) for ln in left] == [("250.0000", str(D3))]


async def test_importing_an_overlapping_statement_again_adds_nothing(client: AsyncClient) -> None:
    b = await bank_books(client, "bank2@shop.ae")
    await ok(await upload(b), 201)
    again = await ok(await upload(b), 201)
    assert (again["lines"], again["duplicates_skipped"]) == (0, 3)


async def test_matching_by_hand_checks_the_amount_the_account_and_one_use(client: AsyncClient) -> None:
    b = await bank_books(client, "bank3@shop.ae")
    done = await ok(await upload(b), 201)
    detail = await ok(await client.get(f"/bank/statements/{done['statement']['id']}", headers=b.h))
    interest = next(ln for ln in detail["lines"] if ln["description"] == "Bank interest")
    transfer = next(ln for ln in detail["lines"] if ln["description"] == "Transfer in")
    params = {"business_id": b.shop.business_id}
    deposit = (
        await ok(
            await client.get(f"/bank/accounts/{b.bank}/unmatched-book-lines", params=params, headers=b.h)
        )
    )[0]

    different = await client.post(
        f"/bank/lines/{interest['id']}/match", json={"book_line_id": deposit["line_id"]}, headers=b.h
    )
    assert (different.status_code, different.json()["code"]) == (422, "bank_match_amounts_differ")
    taken = await client.post(
        f"/bank/lines/{interest['id']}/match", json={"book_line_id": transfer["matched_line_id"]}, headers=b.h
    )
    assert taken.status_code in (409, 422)

    # Un-matching frees both sides, and the pair can be matched again by hand.
    freed = await ok(await client.post(f"/bank/lines/{transfer['id']}/unmatch", headers=b.h))
    assert freed["matched_line_id"] is None
    again = await ok(
        await client.post(
            f"/bank/lines/{transfer['id']}/match",
            json={"book_line_id": transfer["matched_line_id"]},
            headers=b.h,
        )
    )
    assert again["matched_line_id"] == transfer["matched_line_id"]
    auto = await ok(await client.post(f"/bank/statements/{done['statement']['id']}/auto-match", headers=b.h))
    assert auto == {"matched": 0, "unmatched_statement_lines": 1}


async def test_only_a_bank_account_and_only_with_the_account_permissions(client: AsyncClient) -> None:
    b = await bank_books(client, "bank4@shop.ae")
    not_bank = await b.client.post(
        "/bank/statements",
        data={"business_id": b.shop.business_id, "account_id": b.cash},
        files={"file": ("s.csv", CSV.encode(), "text/csv")},
        headers=b.h,
    )
    assert (not_bank.status_code, not_bank.json()["code"]) == (422, "not_a_bank_account")
    unreadable = await upload(b, "Name,Notes\nx,y\n")
    assert (unreadable.status_code, unreadable.json()["code"]) == (422, "bank_statement_unreadable")

    await add_member(
        client,
        b.shop,
        "bank4-cashier@shop.ae",
        "cashier",
        business=b.shop.business_id,
        branch=b.shop.branch_a,
    )
    cashier = bearer(await login(client, "bank4-cashier@shop.ae"))
    data = {"business_id": b.shop.business_id, "account_id": b.bank}
    files = {"file": ("s.csv", CSV.encode(), "text/csv")}
    assert (await client.post("/bank/statements", data=data, files=files, headers=cashier)).status_code == 403
    listing = await client.get(
        "/bank/statements", params={"business_id": b.shop.business_id}, headers=cashier
    )
    assert listing.status_code == 403
