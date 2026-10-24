"""M1.8b: receivables and payables ageing, the "sent" marker and the reuse warning, and Cancel / Reverse for
sales documents (allocations become advances, delivery notes return to "not invoiced", numbers reissued)."""

from datetime import timedelta
from typing import Any

from httpx import AsyncClient

from tests.test_accounting_api import books
from tests.test_pricing_api import ok
from tests.test_sales_api import TODAY, Seller, seller


def days(n: int) -> str:
    return str(TODAY + timedelta(days=n))


async def test_ageing_puts_open_bills_in_buckets_and_advances_apart(client: AsyncClient) -> None:
    b = await books(client)

    async def invoice(amount: str, ref: str, doc_date: str, due: str) -> None:
        lines = [
            {
                "account_id": b.account("1200"),
                "party_id": b.customer,
                "debit": amount,
                "allocations": [{"type": "new_ref", "amount": amount, "reference": ref, "due_date": due}],
            },
            {"account_id": b.account("4100"), "credit": amount},
        ]
        await b.post(b.voucher("journal", lines, doc_date=doc_date))

    await invoice("100", "OLD", days(-130), days(-100))  # over 90 days late
    await invoice("200", "MID", days(-50), days(-40))  # 31-60
    await invoice("300", "LATE", days(-20), days(-5))  # 1-30
    await invoice("400", "NEW", days(0), days(30))  # not due
    advance = {"party_id": b.customer, "amount": "50", "allocations": [{"type": "advance", "amount": "50"}]}
    await b.post(b.voucher("receipt", [advance], account_id=b.cash))

    params = {"business_id": b.shop.business_id}
    ageing = await ok(await client.get("/receivables/ageing", params=params, headers=b.h))
    assert ageing["as_of"] == str(TODAY)
    [row] = ageing["rows"]
    assert (row["party_id"], row["name_en"]) == (b.customer, "Ali Traders")
    assert {
        k: row[k] for k in ("not_due", "days_1_30", "days_31_60", "days_61_90", "over_90", "advances")
    } == {
        "not_due": "400.0000",
        "days_1_30": "300.0000",
        "days_31_60": "200.0000",
        "days_61_90": "0.0000",
        "over_90": "100.0000",
        "advances": "50.0000",
    }
    assert row["total"] == "950.0000"
    assert ageing["totals"]["total"] == "950.0000"
    statement = await ok(await client.get(f"/parties/{b.customer}/ledger", params=params, headers=b.h))
    assert statement["closing"] == "950.0000", "ageing agrees with the customer's ledger"

    supplier_bill = [
        {
            "account_id": b.account("2110"),
            "party_id": b.supplier,
            "credit": "700",
            "allocations": [{"type": "new_ref", "amount": "700", "reference": "S-9", "due_date": days(-70)}],
        },
        {"account_id": b.account("6100"), "debit": "700"},
    ]
    await b.post(b.voucher("journal", supplier_bill, doc_date=days(-80)))
    payables = await ok(await client.get("/payables/ageing", params=params, headers=b.h))
    assert [(r["name_en"], r["days_61_90"], r["total"]) for r in payables["rows"]] == [
        ("Mill", "700.0000", "700.0000")
    ]


async def ordered_and_delivered(s: Seller, qty: str = "10") -> tuple[dict[str, Any], dict[str, Any]]:
    order = await s.post(s.doc("sales_order", [s.line(qty, unit_price="10")]))
    delivery = await s.post(s.doc("delivery_note", [s.line(qty, source_line_id=order["lines"][0]["id"])]))
    return order, delivery


async def test_cancelling_an_invoice_frees_its_delivery_and_makes_its_receipt_an_advance(
    client: AsyncClient,
) -> None:
    s = await seller(client)
    order, delivery = await ordered_and_delivered(s)
    invoice = await s.post(s.doc("sales_invoice", [s.line("10", source_line_id=delivery["lines"][0]["id"])]))
    [bill] = await ok(
        await client.get(
            "/bills", params={"business_id": s.shop.business_id, "party_id": s.customer}, headers=s.h
        )
    )
    receipt = {
        "doc_type": "receipt",
        "business_id": s.shop.business_id,
        "branch_id": s.shop.branch_a,
        "doc_date": str(TODAY),
        "account_id": s.accounts["1110"],
        "lines": [
            {
                "party_id": s.customer,
                "amount": "105",
                "allocations": [{"type": "against_ref", "amount": "105", "bill_id": bill["id"]}],
            }
        ],
    }
    paid = await ok(await client.post("/vouchers", params={"post": "true"}, json=receipt, headers=s.h), 201)
    assert await s.receivables() == {}

    blocked = await client.post(f"/documents/{delivery['id']}/cancel", json={"reason": "x"}, headers=s.h)
    assert (blocked.status_code, blocked.json()["documents"]) == (409, [invoice["number"]])

    # The invoice is printed and handed over, then cancelled: its number comes with a warning.
    sent = await ok(await client.post(f"/documents/{invoice['id']}/sent", headers=s.h))
    assert sent["sent_at"] is not None
    cancelled = await ok(
        await client.post(
            f"/documents/{invoice['id']}/cancel", json={"reason": "wrong customer"}, headers=s.h
        )
    )
    assert (cancelled["status"], cancelled["sent_at"]) == ("cancelled", sent["sent_at"])
    assert await s.receivables() == {paid["number"]: "105.0000"}, "the receipt is now the customer's advance"
    assert await s.balance("1310") == "60.0000", "the delivery's cost is back in GDNI"
    delivery = await ok(await client.get(f"/sales-documents/{delivery['id']}", headers=s.h))
    assert delivery["progress"] == "not_invoiced"
    offered = await ok(
        await client.get(
            "/documents/released-numbers",
            params={"branch_id": s.shop.branch_a, "doc_type": "sales_invoice"},
            headers=s.h,
        )
    )
    assert [(o["formatted"], o["was_sent"]) for o in offered] == [(invoice["number"], True)]

    # Reissued under the same number; then the delivery note can't be cancelled until that one is undone too.
    draft = await ok(
        await client.post(
            "/sales-documents",
            json=s.doc("sales_invoice", [s.line("10", source_line_id=delivery["lines"][0]["id"])]),
            headers=s.h,
        ),
        201,
    )
    reissued = await ok(
        await client.post(f"/sales-documents/{draft['id']}/post", json={"reuse_number": 1}, headers=s.h)
    )
    assert reissued["number"] == invoice["number"]
    state = await ok(await client.get(f"/documents/{reissued['id']}", headers=s.h))
    assert state["reissue_of"]["id"] == invoice["id"]
    await ok(await client.post(f"/documents/{reissued['id']}/cancel", json={"reason": "x"}, headers=s.h))
    await ok(await client.post(f"/documents/{delivery['id']}/cancel", json={"reason": "x"}, headers=s.h))
    assert await s.balance("1310") == "0.0000"
    assert (await ok(await client.get(f"/sales-documents/{order['id']}", headers=s.h)))["progress"] == "open"
    only = await client.post(f"/documents/{order['id']}/reverse", json={"reason": "x"}, headers=s.h)
    assert (only.status_code, only.json()["code"]) == (409, "cancel_only")


async def test_reversing_an_invoice_writes_a_credit_note_and_brings_the_goods_back(
    client: AsyncClient,
) -> None:
    s = await seller(client)
    invoice = await s.post(s.doc("sales_invoice", [s.line("10", unit_price="10")]))  # stock leaves at 6
    result = await ok(
        await client.post(
            f"/documents/{invoice['id']}/reverse", json={"reason": "returned in full"}, headers=s.h
        )
    )
    assert (result["status"], result["reversal_doc_type"], result["reversal_number"]) == (
        "reversed",
        "credit_note",
        f"CN-{TODAY.year}-00001",
    )
    assert await s.receivables() == {}
    assert {code: await s.balance(code) for code in ("1200", "1300", "4100", "5100")} == {
        "1200": "0.0000",
        "1300": "600.0000",
        "4100": "0.0000",
        "5100": "0.0000",
    }
    stock = await ok(
        await client.get(
            "/stock/balances", params={"business_id": s.shop.business_id, "variant_id": s.fabric}, headers=s.h
        )
    )
    assert [x["qty"] for x in stock] == ["100.000000"]
    note = await ok(await client.get(f"/vouchers/{result['reversal_id']}", headers=s.h))
    assert len(note["journal"]) >= 4

    draft = await ok(
        await client.post(
            "/sales-documents", json=s.doc("quotation", [s.line("1", unit_price="10")]), headers=s.h
        ),
        201,
    )
    not_yet = await client.post(f"/documents/{draft['id']}/sent", headers=s.h)
    assert not_yet.status_code == 200, "a quotation is posted when saved, so it can be sent"
