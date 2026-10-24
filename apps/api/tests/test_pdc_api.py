"""PDC register API (M1.9; A2; rules 15 to 22): nothing in the ledger until clearance; clearance posts
the receipt or payment on its date and settles the intended bills; a bounce changes only the status (and a
bank charge); PDCs in hand beside credit, statements and ageing; illegal moves refused; permissions."""

from datetime import timedelta
from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_accounting_api import Books, books
from tests.test_pricing_api import ok
from tests.test_roles_api import add_member, login

TODAY = dubai_today()


async def invoice(b: Books, amount: str, ref: str) -> str:
    await b.invoice(amount, ref)
    return next(x["id"] for x in await b.bills() if x["reference"] == ref)


def cheque(b: Books, amount: str, *, party: str | None = None, **more: Any) -> dict[str, Any]:
    return {
        "business_id": b.shop.business_id,
        "branch_id": b.shop.branch_a,
        "direction": more.pop("direction", "received"),
        "party_id": party or b.customer,
        "cheque_no": more.pop("cheque_no", "000101"),
        "bank_name": "Emirates NBD",
        "cheque_date": str(TODAY + timedelta(days=30)),
        "amount": amount,
        **more,
    }


async def snapshot(client: AsyncClient, b: Books) -> tuple[str, str]:
    params = {"business_id": b.shop.business_id}
    tb = await ok(await client.get("/trial-balance", params=params, headers=b.h))
    statement = await ok(await client.get(f"/parties/{b.customer}/ledger", params=params, headers=b.h))
    return tb["total_debit"], statement["closing"]


async def test_a_received_cheque_is_a_memo_until_it_clears(client: AsyncClient) -> None:
    b = await books(client)
    inv1 = await invoice(b, "1000", "INV-1")
    inv2 = await invoice(b, "500", "INV-2")
    before = await snapshot(client, b)

    body = cheque(
        b,
        "1200",
        intended_bills=[{"bill_id": inv1, "amount": "1000"}, {"bill_id": inv2, "amount": "200"}],
    )
    pdc = await ok(await client.post("/pdcs", json=body, headers=b.h), 201)
    assert (pdc["status"], pdc["party_name_en"], pdc["voucher_id"]) == ("in_hand", "Ali Traders", None)
    assert [(x["reference"], x["amount"]) for x in pdc["intended_bills"]] == [
        ("INV-1", "1000.0000"),
        ("INV-2", "200.0000"),
    ]
    assert await snapshot(client, b) == before, "rule 15: nothing in the ledger"

    params = {"business_id": b.shop.business_id}
    statement = await ok(await client.get(f"/parties/{b.customer}/ledger", params=params, headers=b.h))
    assert (statement["closing"], statement["pdc_received_pending"], statement["outstanding_after_pdc"]) == (
        "1500.0000",
        "1200.0000",
        "300.0000",
    )
    credit = await ok(
        await client.post(
            f"/parties/{b.customer}/credit-check",
            json={"business_id": b.shop.business_id, "amount": "0"},
            headers=b.h,
        )
    )
    assert (credit["ledger_balance"], credit["pdc_in_hand"]) == ("1500.0000", "1200.0000"), (
        "beside, not subtracted"
    )
    ageing = await ok(await client.get("/receivables/ageing", params=params, headers=b.h))
    assert [(r["total"], r["pdc"], r["after_pdc"]) for r in ageing["rows"]] == [
        ("1500.0000", "1200.0000", "300.0000")
    ]

    deposited = await ok(
        await client.post(
            f"/pdcs/{pdc['id']}/deposit", json={"bank_account_id": b.bank, "date": str(TODAY)}, headers=b.h
        )
    )
    assert (deposited["status"], deposited["deposit_account_id"]) == ("deposited", b.bank)
    assert await snapshot(client, b) == before, "rule 16: still nothing"
    locked = await client.put(f"/pdcs/{pdc['id']}", json=body | {"amount": "1300"}, headers=b.h)
    assert (locked.status_code, locked.json()["code"]) == (409, "pdc_not_editable")

    clear_on = str(TODAY + timedelta(days=1))
    cleared = await ok(await client.post(f"/pdcs/{pdc['id']}/clear", json={"date": clear_on}, headers=b.h))
    assert (cleared["status"], cleared["cleared_on"]) == ("cleared", clear_on)
    assert cleared["voucher_number"] == f"RV-{TODAY.year}-00001"
    voucher = await ok(await client.get(f"/vouchers/{cleared['voucher_id']}", headers=b.h))
    assert (voucher["doc_type"], voucher["doc_date"], voucher["total"], voucher["account_id"]) == (
        "receipt",
        clear_on,
        "1200.0000",
        b.bank,
    )
    assert [(x["reference"], x["amount_open"]) for x in await b.bills()] == [("INV-2", "300.0000")]
    statement = await ok(await client.get(f"/parties/{b.customer}/ledger", params=params, headers=b.h))
    assert (statement["closing"], statement["pdc_received_pending"]) == ("300.0000", "0.0000")
    again = await client.post(f"/pdcs/{pdc['id']}/clear", json={"date": clear_on}, headers=b.h)
    assert (again.status_code, again.json()["code"]) == (409, "invalid_pdc_transition")


async def test_a_bounce_changes_only_the_status_and_posts_a_bank_charge_if_given(client: AsyncClient) -> None:
    b = await books(client)
    await invoice(b, "300", "INV-1")
    before = await snapshot(client, b)
    pdc = await ok(await client.post("/pdcs", json=cheque(b, "300"), headers=b.h), 201)
    no_bank = await client.post(
        f"/pdcs/{pdc['id']}/bounce",
        json={"date": str(TODAY), "reason": "insufficient funds", "charge": "25"},
        headers=b.h,
    )
    assert no_bank.status_code == 422, "a charge needs the bank account the cheque went through"
    await ok(
        await client.post(
            f"/pdcs/{pdc['id']}/deposit", json={"bank_account_id": b.bank, "date": str(TODAY)}, headers=b.h
        )
    )
    bounced = await ok(
        await client.post(
            f"/pdcs/{pdc['id']}/bounce",
            json={"date": str(TODAY), "reason": "insufficient funds", "charge": "25"},
            headers=b.h,
        )
    )
    assert (bounced["status"], bounced["bounce_reason"], bounced["voucher_id"]) == (
        "bounced",
        "insufficient funds",
        None,
    )
    charge = await ok(await client.get(f"/vouchers/{bounced['charge_voucher_id']}", headers=b.h))
    assert [(j["account_code"], j["debit"], j["credit"]) for j in charge["journal"]] == [
        ("6200", "25.0000", "0.0000"),
        ("1150", "0.0000", "25.0000"),
    ]
    _tb, closing = await snapshot(client, b)
    assert closing == before[1], "the customer still owes it all"
    final = await client.post(
        f"/pdcs/{pdc['id']}/deposit", json={"bank_account_id": b.bank, "date": str(TODAY)}, headers=b.h
    )
    assert (final.status_code, final.json()["code"]) == (409, "invalid_pdc_transition")

    returned = await ok(
        await client.post("/pdcs", json=cheque(b, "300", cheque_no="000102"), headers=b.h), 201
    )
    assert (await ok(await client.post(f"/pdcs/{returned['id']}/return", headers=b.h)))[
        "status"
    ] == "returned"
    cancel = await client.post(f"/pdcs/{returned['id']}/cancel", headers=b.h)
    assert cancel.status_code == 409, "a received cheque is returned, not cancelled"


async def test_an_issued_cheque_pays_the_supplier_when_it_clears(client: AsyncClient) -> None:
    b = await books(client)
    bill = [
        {
            "account_id": b.account("2110"),
            "party_id": b.supplier,
            "credit": "700",
            "allocations": [{"type": "new_ref", "amount": "700", "reference": "S-9"}],
        },
        {"account_id": b.account("6100"), "debit": "700"},
    ]
    await b.post(b.voucher("journal", bill))
    [payable] = await b.bills(b.supplier)
    no_bank = await client.post(
        "/pdcs", json=cheque(b, "700", party=b.supplier, direction="issued"), headers=b.h
    )
    assert no_bank.status_code == 422, "an issued cheque names the bank it is drawn on"
    wrong_party = await client.post(
        "/pdcs", json=cheque(b, "700", direction="issued", deposit_account_id=b.bank), headers=b.h
    )
    assert wrong_party.status_code == 422, "issued cheques go to suppliers"
    body = cheque(
        b,
        "800",
        party=b.supplier,
        direction="issued",
        deposit_account_id=b.bank,
        intended_bills=[{"bill_id": payable["id"], "amount": "700"}],
    )
    pdc = await ok(await client.post("/pdcs", json=body, headers=b.h), 201)
    assert pdc["status"] == "issued"
    payables = await ok(
        await client.get("/payables/ageing", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert [(r["total"], r["pdc"], r["after_pdc"]) for r in payables["rows"]] == [
        ("700.0000", "800.0000", "-100.0000")
    ]

    cleared = await ok(await client.post(f"/pdcs/{pdc['id']}/clear", json={"date": str(TODAY)}, headers=b.h))
    assert cleared["voucher_number"] == f"PV-{TODAY.year}-00001"
    bills = await b.bills(b.supplier)
    assert [(x["reference"], x["amount_open"], x["is_advance"]) for x in bills] == [
        (f"PDC {pdc['cheque_no']}", "100.0000", True)
    ], "the bill is paid; the 100 over is an advance to the supplier"
    assert (await client.post(f"/pdcs/{pdc['id']}/cancel", headers=b.h)).status_code == 409


async def test_intended_bills_must_be_this_partys_open_bills(client: AsyncClient) -> None:
    b = await books(client)
    inv = await invoice(b, "100", "INV-1")
    other = (
        await ok(
            await client.post(
                "/parties", json={"name_en": "Other", "name_ar": "آخر", "is_customer": True}, headers=b.h
            ),
            201,
        )
    )["id"]
    cases = [
        cheque(b, "100", party=other, intended_bills=[{"bill_id": inv, "amount": "50"}]),
        cheque(b, "500", intended_bills=[{"bill_id": inv, "amount": "150"}]),
        cheque(b, "50", intended_bills=[{"bill_id": inv, "amount": "80"}]),
        cheque(b, "100", intended_bills=[{"bill_id": inv, "amount": "30"}, {"bill_id": inv, "amount": "30"}]),
    ]
    for body in cases:
        response = await client.post("/pdcs", json=body, headers=b.h)
        assert (response.status_code, response.json()["code"]) == (422, "invalid_pdc"), body
    register = await ok(
        await client.get(
            "/pdcs", params={"business_id": b.shop.business_id, "status": "in_hand"}, headers=b.h
        )
    )
    assert register == []


async def test_the_cashier_records_cheques_and_the_accountant_clears_them(client: AsyncClient) -> None:
    b = await books(client)
    await add_member(client, b.shop, "cashier@shop.ae", "cashier")
    await add_member(client, b.shop, "accounts@shop.ae", "accountant")
    cashier = bearer(await login(client, "cashier@shop.ae"))
    accountant = bearer(await login(client, "accounts@shop.ae"))
    pdc = await ok(await client.post("/pdcs", json=cheque(b, "250"), headers=cashier), 201)
    refused = await client.post(
        f"/pdcs/{pdc['id']}/clear", json={"date": str(TODAY), "bank_account_id": b.bank}, headers=cashier
    )
    assert refused.status_code == 403
    cleared = await ok(
        await client.post(
            f"/pdcs/{pdc['id']}/clear",
            json={"date": str(TODAY), "bank_account_id": b.bank},
            headers=accountant,
        )
    )
    assert cleared["status"] == "cleared"
    assert [(x["reference"], x["is_advance"]) for x in await b.bills()] == [(f"PDC {pdc['cheque_no']}", True)]
    soon = await ok(
        await client.get(
            "/pdcs",
            params={"business_id": b.shop.business_id, "due_to": str(TODAY + timedelta(days=31))},
            headers=accountant,
        )
    )
    assert [p["id"] for p in soon] == [pdc["id"]], "the calendar finds it by its maturity"
