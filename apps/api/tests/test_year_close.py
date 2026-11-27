"""Year-end close (P2.2): only a finished year with no drafts; every month locked; the close can be undone."""

from datetime import date
from typing import Any

from httpx import AsyncClient

from tests.api_helpers import bearer
from tests.test_accounting_api import Books, books
from tests.test_pricing_api import ok
from tests.test_roles_api import add_member, login

LAST_YEAR = date(2024, 6, 15)


async def make_year(b: Books, day: date) -> dict[str, Any]:
    body = {"business_id": b.shop.business_id, "day": str(day)}
    year: dict[str, Any] = await ok(await b.client.post("/fiscal-years", json=body, headers=b.h), 201)
    return year


def journal(b: Books, day: date) -> dict[str, Any]:
    lines = [
        {"account_id": b.account("1110"), "debit": "100"},
        {"account_id": b.account("3100"), "credit": "100"},
    ]
    return b.voucher("journal", lines, doc_date=day)


async def test_a_finished_year_without_drafts_is_closed_locked_and_can_be_reopened(
    client: AsyncClient,
) -> None:
    b = await books(client, "yc1@shop.ae")
    year = await make_year(b, LAST_YEAR)
    await b.post(journal(b, LAST_YEAR))

    closed = await ok(await client.post(f"/fiscal-years/{year['id']}/close", headers=b.h))
    assert closed["status"] == "closed"
    assert {p["status"] for p in closed["periods"]} == {"locked"}
    # Nothing more can be posted into it without the override.
    refused = await client.post("/vouchers", params={"post": "true"}, json=journal(b, LAST_YEAR), headers=b.h)
    assert refused.status_code == 409
    assert refused.json()["code"] == "period_locked"

    again = await client.post(f"/fiscal-years/{year['id']}/close", headers=b.h)
    assert (again.status_code, again.json()["code"]) == (409, "fiscal_year_closed")

    reopened = await ok(await client.post(f"/fiscal-years/{year['id']}/reopen", headers=b.h))
    assert reopened["status"] == "open"
    assert {p["status"] for p in reopened["periods"]} == {"open"}
    await b.post(journal(b, LAST_YEAR))  # posting works again
    nothing = await client.post(f"/fiscal-years/{year['id']}/reopen", headers=b.h)
    assert (nothing.status_code, nothing.json()["code"]) == (409, "fiscal_year_not_closed")


async def test_a_year_still_running_or_with_a_draft_cannot_be_closed(client: AsyncClient) -> None:
    b = await books(client, "yc2@shop.ae")
    running = await ok(
        await client.get("/fiscal-years", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    if not running:
        await b.post(journal(b, date.today()))
        running = await ok(
            await client.get("/fiscal-years", params={"business_id": b.shop.business_id}, headers=b.h)
        )
    not_over = await client.post(f"/fiscal-years/{running[0]['id']}/close", headers=b.h)
    assert (not_over.status_code, not_over.json()["code"]) == (409, "fiscal_year_not_ended")

    year = await make_year(b, LAST_YEAR)
    draft = await ok(await client.post("/vouchers", json=journal(b, LAST_YEAR), headers=b.h), 201)
    blocked = await client.post(f"/fiscal-years/{year['id']}/close", headers=b.h)
    assert (blocked.status_code, blocked.json()["code"]) == (409, "drafts_in_fiscal_year")
    assert blocked.json()["drafts"] == 1
    assert (await client.delete(f"/vouchers/{draft['id']}", headers=b.h)).status_code == 204
    assert (await client.post(f"/fiscal-years/{year['id']}/close", headers=b.h)).status_code == 200


async def test_closing_a_year_needs_the_period_permission_in_that_business(client: AsyncClient) -> None:
    b = await books(client, "yc3@shop.ae")
    year = await make_year(b, LAST_YEAR)
    await add_member(
        client, b.shop, "yc3-cashier@shop.ae", "cashier", business=b.shop.business_id, branch=b.shop.branch_a
    )
    cashier = bearer(await login(client, "yc3-cashier@shop.ae"))
    assert (await client.post(f"/fiscal-years/{year['id']}/close", headers=cashier)).status_code == 403
    assert (await client.post(f"/fiscal-years/{year['id']}/reopen", headers=cashier)).status_code == 403
