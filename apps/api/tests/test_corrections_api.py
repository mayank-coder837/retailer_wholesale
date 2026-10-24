"""Cancel and Reverse (M1.7b; posting-rules.md rules 32 and 33): every effect of the document comes back out —
ledger, stock, payables — by new rows; numbers are freed and reused; dependants block; permissions apply."""

import uuid
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import apply_context
from app.core.platform_models import AuditLog
from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_accounting_api import books
from tests.test_inventory_api import store
from tests.test_pricing_api import ok
from tests.test_purchasing_api import Buyer, buyer
from tests.test_roles_api import add_member, login

TODAY = dubai_today()
CODES = ("1300", "1400", "2110", "2120", "5400")


async def cancel(client: AsyncClient, doc_id: str, h: dict[str, str], status: int = 200) -> Any:
    return await ok(
        await client.post(f"/documents/{doc_id}/cancel", json={"reason": "mistake"}, headers=h), status
    )


async def reverse(client: AsyncClient, doc_id: str, h: dict[str, str], status: int = 200, **body: Any) -> Any:
    response = await client.post(
        f"/documents/{doc_id}/reverse", json={"reason": "withdrawn", **body}, headers=h
    )
    return await ok(response, status)


async def balances(b: Buyer) -> dict[str, str]:
    return {code: await b.balance(code) for code in CODES}


async def received(b: Buyer) -> tuple[dict[str, Any], dict[str, Any]]:
    """An order of 2 cartons at 240 and a receipt of 20 pieces against it."""
    order = await b.post(
        b.doc(
            "purchase_order",
            [{"variant_id": b.fabric, "unit": "carton", "qty": "2", "unit_price": "240", "vat_rate": "5"}],
        )
    )
    receipt = await b.post(
        b.doc(
            "goods_receipt",
            [{"variant_id": b.fabric, "unit": "pcs", "qty": "20", "source_line_id": order["lines"][0]["id"]}],
        )
    )
    return order, receipt


def bill_of(b: Buyer, receipt: dict[str, Any], ref: str = "S-1") -> dict[str, Any]:
    line = {
        "variant_id": b.fabric,
        "unit": "pcs",
        "qty": "20",
        "unit_price": "10.5",
        "source_line_id": receipt["lines"][0]["id"],
    }
    return b.doc("purchase_bill", [line], supplier_ref=ref)


async def test_cancelling_a_bill_takes_it_out_of_the_books_and_frees_its_number(
    client: AsyncClient, app_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    b = await buyer(client)
    _order, receipt = await received(b)
    before = await balances(b)
    bill = await b.post(bill_of(b, receipt))
    assert bill["number"] == f"PB-{TODAY.year}-00001"
    assert await b.payables() == {"S-1": "220.5000"}

    cancelled = await cancel(client, bill["id"], b.h)
    assert (cancelled["status"], cancelled["cancel_reason"], cancelled["reversal_id"]) == (
        "cancelled",
        "mistake",
        None,
    )
    assert await balances(b) == before, "the ledger is back to before the bill"
    assert await b.journal(bill["id"]) == [], "a cancelled document and its mirror are not shown"
    assert await b.payables() == {}
    open_lines = await ok(await client.get(f"/purchase-documents/{receipt['id']}/open-lines", headers=b.h))
    assert [x["qty_open"] for x in open_lines] == ["20.000000"], "the receipt may be billed again"
    again = await client.post(f"/documents/{bill['id']}/cancel", json={"reason": "twice"}, headers=b.h)
    assert (again.status_code, again.json()["code"]) == (409, "document_not_posted")

    # The full cancelled version stays in the audit log.
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=uuid.UUID(b.shop.owner["tenant_id"]))
        entry = (
            await s.scalars(
                select(AuditLog).where(AuditLog.action == "cancel", AuditLog.entity_id == bill["id"])
            )
        ).one()
    assert entry.reason == "mistake"
    assert entry.snapshot is not None
    assert entry.snapshot["document"]["number"] == bill["number"]
    assert len(entry.snapshot["lines"]) == 1
    assert len(entry.snapshot["journal"]) == 4  # GRNI, variance, input VAT, supplier

    # Its number is offered to the next bill; taking it marks the bill as reissued.
    params = {"branch_id": b.shop.branch_a, "doc_type": "purchase_bill"}
    offered = await ok(await client.get("/documents/released-numbers", params=params, headers=b.h))
    assert [(x["formatted"], x["cancelled_document_id"]) for x in offered] == [(bill["number"], bill["id"])]
    draft = await ok(
        await client.post("/purchase-documents", json=bill_of(b, receipt, "S-2"), headers=b.h), 201
    )
    reissued = await ok(
        await client.post(f"/purchase-documents/{draft['id']}/post", json={"reuse_number": 1}, headers=b.h)
    )
    assert reissued["number"] == bill["number"]
    assert await ok(await client.get("/documents/released-numbers", params=params, headers=b.h)) == []
    taken = await client.post(
        f"/purchase-documents/{draft['id']}/post", json={"reuse_number": 1}, headers=b.h
    )
    assert taken.status_code == 409
    assert await b.payables() == {"S-2": "220.5000"}


async def test_documents_that_others_follow_are_undone_last(client: AsyncClient) -> None:
    b = await buyer(client)
    order, receipt = await received(b)
    bill = await b.post(bill_of(b, receipt))

    blocked = await client.post(f"/documents/{receipt['id']}/cancel", json={"reason": "x"}, headers=b.h)
    assert (blocked.status_code, blocked.json()["code"], blocked.json()["documents"]) == (
        409,
        "document_has_dependants",
        [bill["number"]],
    )
    assert (
        await client.post(f"/documents/{order['id']}/cancel", json={"reason": "x"}, headers=b.h)
    ).status_code == 409
    only = await client.post(f"/documents/{order['id']}/reverse", json={"reason": "x"}, headers=b.h)
    assert (only.status_code, only.json()["code"]) == (409, "cancel_only")

    await cancel(client, bill["id"], b.h)
    await cancel(client, receipt["id"], b.h)
    assert {code: await b.balance(code) for code in ("1300", "2120")} == {"1300": "0.0000", "2120": "0.0000"}
    assert await b.valuation() == "0.0000"
    stock = await ok(
        await client.get(
            "/stock/balances", params={"business_id": b.shop.business_id, "variant_id": b.fabric}, headers=b.h
        )
    )
    assert stock == []  # nothing left anywhere
    assert (await ok(await client.get(f"/purchase-documents/{order['id']}", headers=b.h)))[
        "progress"
    ] == "open"
    assert (await cancel(client, order["id"], b.h))["status"] == "cancelled"


async def test_reversing_a_paid_bill_makes_the_payment_an_advance(client: AsyncClient) -> None:
    b = await buyer(client)
    _order, receipt = await received(b)
    before = await balances(b)
    bill = await b.post(bill_of(b, receipt))
    params = {"business_id": b.shop.business_id, "party_id": b.supplier}
    [payable] = await ok(await client.get("/bills", params=params, headers=b.h))
    cash = await ok(await client.get("/accounts", params={"business_id": b.shop.business_id}, headers=b.h))
    cash_id = next(a["id"] for a in cash if a["code"] == "1110")
    payment = {
        "doc_type": "payment",
        "business_id": b.shop.business_id,
        "branch_id": b.shop.branch_a,
        "doc_date": str(TODAY),
        "account_id": cash_id,
        "lines": [
            {
                "party_id": b.supplier,
                "amount": "220.50",
                "allocations": [{"type": "against_ref", "amount": "220.50", "bill_id": payable["id"]}],
            }
        ],
    }
    paid = await ok(await client.post("/vouchers", params={"post": "true"}, json=payment, headers=b.h), 201)
    assert await b.payables() == {}

    reversed_ = await reverse(client, bill["id"], b.h)
    assert (reversed_["status"], reversed_["reversal_doc_type"], reversed_["reversal_number"]) == (
        "reversed",
        "debit_note",
        f"DBN-{TODAY.year}-00001",
    )
    assert reversed_["reversal_date"] == str(TODAY)
    assert await b.payables() == {paid["number"]: "220.5000"}, "the payment is now an advance to the supplier"
    after = await balances(b)
    assert after == before | {"2110": "220.5000"}, "only the payment is left with the supplier"
    assert len(await b.journal(bill["id"])) == 4, "a reversed bill stays visible"
    assert len(await b.journal(reversed_["reversal_id"])) == 4

    no = await client.post(f"/documents/{reversed_['reversal_id']}/cancel", json={"reason": "x"}, headers=b.h)
    assert (no.status_code, no.json()["code"]) == (409, "document_is_a_reversal")
    early = await client.post(
        f"/documents/{receipt['id']}/reverse",
        json={"reason": "x", "date": str(TODAY - timedelta(days=1))},
        headers=b.h,
    )
    assert (early.status_code, early.json()["code"]) == (422, "invalid_reversal")


async def test_cancelling_a_backdated_receipt_recosts_what_was_issued_after_it(client: AsyncClient) -> None:
    s = await store(client)
    rice = await s.item("RICE")
    await s.adjust(rice, "10", "in", "10", doc_date=TODAY - timedelta(days=2))
    dear = await s.adjust(rice, "10", "in", "20", doc_date=TODAY - timedelta(days=1))
    issued = await s.adjust(rice, "10", "out")
    assert issued["lines"][0]["value"] == "-150.0000"  # at the average of 15

    await cancel(client, dear["id"], s.h)
    # Without the dear receipt the issue took the first 10 at 10: 50 less stock used, dated on the issue.
    assert await s.journal(dear["id"]) == [("1300", "50.0000", "0.0000"), ("5300", "0.0000", "50.0000")]
    assert await s.balances(rice) == {}
    assert (await s.valuation())["difference"] == "0.0000"


async def test_reversing_a_receipt_after_the_average_moved_posts_the_difference(client: AsyncClient) -> None:
    s = await store(client)
    rice = await s.item("RICE")
    cheap = await s.adjust(rice, "10", "in", "10")
    await s.adjust(rice, "10", "in", "20")

    result = await reverse(client, cheap["id"], s.h)
    assert result["reversal_number"] == f"SA-{TODAY.year}-00003"
    # The 10 leave at the average of 15; the 50 more than they came in at is a cost adjustment.
    assert await s.balances(rice) == {s.main: ("10.000000", "150.0000")}
    assert (await s.valuation())["difference"] == "0.0000"


async def test_a_reversed_transfer_puts_the_stock_back_and_roles_limit_who_undoes(
    client: AsyncClient,
) -> None:
    s = await store(client)
    rice = await s.item("RICE")
    await s.adjust(rice, "10", "in", "12")
    moved = await s.post(
        s.doc("stock_transfer", [{"variant_id": rice, "unit": "pcs", "qty": "4"}], to_warehouse_id=s.second)
    )
    await add_member(client, s.shop, "keeper@shop.ae", "warehouse")
    keeper = bearer(await login(client, "keeper@shop.ae"))
    refused = await client.post(f"/documents/{moved['id']}/reverse", json={"reason": "x"}, headers=keeper)
    assert refused.status_code == 403  # the Warehouse role may cancel transfers, not reverse them

    await reverse(client, moved["id"], s.h)
    assert await s.balances(rice) == {s.main: ("10.000000", "120.0000")}
    assert (await s.valuation())["difference"] == "0.0000"

    found = await s.adjust(rice, "1", "in", "12")
    no = await client.post(f"/documents/{found['id']}/cancel", json={"reason": "x"}, headers=keeper)
    assert no.status_code == 403  # adjustments: no cancel for the Warehouse role


async def test_vouchers_cancel_and_reverse_bill_wise(client: AsyncClient) -> None:
    b = await books(client)
    invoice = await b.invoice("1000", "INV-1")
    [inv] = await b.bills()

    def receipt(amount: str) -> dict[str, Any]:
        allocation = {"type": "against_ref", "amount": amount, "bill_id": inv["id"]}
        return b.voucher(
            "receipt",
            [{"party_id": b.customer, "amount": amount, "allocations": [allocation]}],
            account_id=b.cash,
        )

    first = await b.post(receipt("400"))
    await cancel(client, first["id"], b.h)
    assert [(x["reference"], x["amount_open"]) for x in await b.bills()] == [("INV-1", "1000.0000")]

    second = await b.post(receipt("300"))
    assert second["number"] == f"RV-{TODAY.year}-00002"  # a freed number is only taken when asked for
    await cancel(client, invoice["id"], b.h)
    assert [(x["reference"], x["amount_open"], x["is_advance"]) for x in await b.bills()] == [
        (second["number"], "300.0000", True)
    ], "the receipt is now the customer's advance"

    # A locked month: cancel is refused; reverse needs the override like any posting.
    params = {"business_id": b.shop.business_id}
    [year] = await ok(await client.get("/fiscal-years", params=params, headers=b.h))
    month = next(p for p in year["periods"] if p["start_date"] <= str(TODAY) <= p["end_date"])
    await ok(await client.put(f"/fiscal-periods/{month['id']}/lock", headers=b.h))
    locked = await client.post(f"/documents/{second['id']}/cancel", json={"reason": "x"}, headers=b.h)
    assert (locked.status_code, locked.json()["code"]) == (409, "cancel_period_locked")
    no_reason = await client.post(f"/documents/{second['id']}/reverse", json={"reason": "x"}, headers=b.h)
    assert (no_reason.status_code, no_reason.json()["code"]) == (409, "period_locked")
    done = await reverse(client, second["id"], b.h, override_lock_reason="bounced")
    assert (done["reversal_doc_type"], done["reversal_number"]) == ("receipt", f"RV-{TODAY.year}-00003")
    assert await b.bills() == []
    ledger = await ok(await client.get(f"/accounts/{b.account('1200')}/ledger", headers=b.h))
    assert ledger["closing"] == "0.0000"


async def test_every_page_can_show_where_a_document_stands(client: AsyncClient) -> None:
    b = await buyer(client)
    _order, receipt = await received(b)
    bill = await b.post(bill_of(b, receipt))
    posted = await ok(await client.get(f"/documents/{bill['id']}", headers=b.h))
    assert (posted["status"], posted["reversal_of"], posted["reissue_of"]) == ("posted", None, None)

    result = await reverse(client, bill["id"], b.h)
    state = await ok(await client.get(f"/documents/{bill['id']}", headers=b.h))
    assert (state["status"], state["reversal_number"]) == ("reversed", result["reversal_number"])
    note = await ok(await client.get(f"/documents/{result['reversal_id']}", headers=b.h))
    assert (note["doc_type"], note["reversal_of"]["number"]) == ("debit_note", bill["number"])
    # The reversal opens on its own page (a debit note is a voucher), holding the mirror entry.
    voucher = await ok(await client.get(f"/vouchers/{result['reversal_id']}", headers=b.h))
    assert (voucher["number"], len(voucher["journal"])) == (result["reversal_number"], 4)

    grn = await reverse(client, receipt["id"], b.h)
    page = await ok(await client.get(f"/purchase-documents/{grn['reversal_id']}", headers=b.h))
    assert (page["doc_type"], page["lines"]) == ("goods_receipt", [])

    s = await store(client, "stock2@shop.ae")
    rice = await s.item("RICE")
    found = await s.adjust(rice, "5", "in", "4")
    undone = await reverse(client, found["id"], s.h)
    stock_page = await ok(await client.get(f"/stock-documents/{undone['reversal_id']}", headers=s.h))
    assert stock_page["number"] == undone["reversal_number"]

    missing = await ok(await client.get(f"/documents/{uuid.uuid4()}", headers=b.h), 404)
    assert missing["code"] == "document_not_found"
    offered = await client.get(
        "/documents/released-numbers",
        params={"branch_id": b.shop.branch_a, "doc_type": "purchase_bill"},
        headers=b.h,
    )
    assert offered.status_code == 200
