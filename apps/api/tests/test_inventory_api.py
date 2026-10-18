"""Inventory API (M1.6): stock adjustments, warehouse transfers and Item Transfers through the moving-average
cost pool; negative stock and rule 27; batches; backdated recost (rule 28); the books always agree."""

import uuid
from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import apply_context
from tests.api_helpers import bearer
from tests.factories import dubai_today
from tests.test_pricing_api import ok
from tests.test_roles_api import Shop, add_member, login, make_shop

TODAY = dubai_today()


class Store:
    """A signed-in owner, the head office's warehouse, a second warehouse, and stock items."""

    def __init__(self, client: AsyncClient, shop: Shop, warehouses: list[str]) -> None:
        self.client = client
        self.shop = shop
        self.h = bearer(shop.owner)
        self.main, self.second = warehouses

    async def item(self, sku: str, *, batches: bool = False, kind: str = "stock") -> str:
        body = {
            "sku": sku,
            "name_en": f"Item {sku}",
            "name_ar": "صنف",
            "base_unit": "pcs",
            "units": [{"unit": "carton", "factor_to_base": "24"}],
            "product_type": kind,
            "track_batch": batches,
            "track_expiry": batches,
        }
        product = await ok(await self.client.post("/products", json=body, headers=self.h), 201)
        return str(product["variants"][0]["id"])

    def doc(self, doc_type: str, lines: list[dict[str, Any]], **header: Any) -> dict[str, Any]:
        return {
            "doc_type": doc_type,
            "business_id": self.shop.business_id,
            "branch_id": self.shop.branch_a,
            "doc_date": str(header.pop("doc_date", TODAY)),
            "warehouse_id": header.pop("warehouse_id", self.main),
            "lines": lines,
            **header,
        }

    async def post(
        self, body: dict[str, Any], status: int = 201, headers: dict[str, str] | None = None
    ) -> Any:
        response = await self.client.post(
            "/stock-documents", params={"post": "true"}, json=body, headers=headers or self.h
        )
        return await ok(response, status)

    async def adjust(
        self, variant: str, qty: str, direction: str, cost: str | None = None, **header: Any
    ) -> Any:
        line: dict[str, Any] = {"variant_id": variant, "unit": "pcs", "qty": qty, "direction": direction}
        if cost is not None:
            line["unit_cost"] = cost
        return await self.post(self.doc("stock_adjustment", [line], **header))

    async def valuation(self) -> dict[str, Any]:
        params = {"business_id": self.shop.business_id}
        result: dict[str, Any] = await ok(
            await self.client.get("/stock/valuation", params=params, headers=self.h)
        )
        return result

    async def balances(self, variant: str) -> dict[str, tuple[str, str]]:
        params = {"business_id": self.shop.business_id, "variant_id": variant}
        rows = await ok(await self.client.get("/stock/balances", params=params, headers=self.h))
        return {r["warehouse_id"]: (r["qty"], r["value"]) for r in rows}

    async def journal(self, doc_id: str) -> list[tuple[str, str, str]]:
        """What a document posted, from the trial-balance side: (account code, debit, credit) per line."""
        params = {"business_id": self.shop.business_id}
        accounts = {
            a["id"]: a["code"]
            for a in await ok(await self.client.get("/accounts", params=params, headers=self.h))
        }
        out = []
        for code in ("1300", "5200", "5300"):
            account = next(i for i, c in accounts.items() if c == code)
            ledger = await ok(await self.client.get(f"/accounts/{account}/ledger", headers=self.h))
            out += [
                (code, line["debit"], line["credit"])
                for line in ledger["lines"]
                if line["document_id"] == doc_id
            ]
        return out


async def store(client: AsyncClient, email: str = "stock@shop.ae") -> Store:
    shop = await make_shop(client, email)
    h = bearer(shop.owner)
    main = (await ok(await client.get(f"/branches/{shop.branch_a}/warehouses", headers=h)))[0]["id"]
    second = await ok(
        await client.post(
            f"/branches/{shop.branch_a}/warehouses",
            json={"code": "W2", "name_en": "Back store", "name_ar": "المخزن الخلفي"},
            headers=h,
        ),
        201,
    )
    return Store(client, shop, [main, second["id"]])


async def test_adjustments_post_rules_23_and_24_and_convert_units_at_entry(client: AsyncClient) -> None:
    s = await store(client)
    soap = await s.item("SOAP")
    found = await s.post(
        s.doc(
            "stock_adjustment",
            [{"variant_id": soap, "unit": "carton", "qty": "2", "direction": "in", "unit_cost": "36"}],
            reason="found in count",
        )
    )
    assert found["number"] == f"SA-{TODAY.year}-00001"
    assert (found["lines"][0]["qty_base"], found["lines"][0]["base_unit"]) == ("48.000000", "pcs")
    assert found["lines"][0]["value"] == "72.0000"
    assert await s.journal(found["id"]) == [("1300", "72.0000", "0.0000"), ("5300", "0.0000", "72.0000")]

    damaged = await s.adjust(soap, "12", "out")
    assert damaged["lines"][0]["value"] == "-18.0000"  # 12 pcs at 1.50 average
    assert await s.journal(damaged["id"]) == [("1300", "0.0000", "18.0000"), ("5300", "18.0000", "0.0000")]
    assert await s.balances(soap) == {s.main: ("36.000000", "54.0000")}
    v = await s.valuation()
    assert (v["stock_value"], v["inventory_account"], v["difference"]) == ("54.0000", "54.0000", "0.0000")


async def test_rule_27_negative_stock_warns_and_is_revalued_when_stock_arrives(client: AsyncClient) -> None:
    s = await store(client)
    shoe = await s.item("SHOE")
    await s.adjust(shoe, "1", "in", "60")
    sold_short = await s.adjust(shoe, "11", "out")
    assert sold_short["warnings"] == [
        {"code": "negative_stock", "variant_id": shoe, "warehouse_id": s.main, "qty_after": "-10.000000"}
    ]
    assert await s.balances(shoe) == {s.main: ("-10.000000", "-600.0000")}
    negative = await ok(
        await client.get("/stock/negative", params={"business_id": s.shop.business_id}, headers=s.h)
    )
    assert [n["sku"] for n in negative] == ["SHOE"]

    arrived = await s.adjust(shoe, "20", "in", "65")
    # 20 arrive at 65 (1,300); the 10 sold below zero go from 60 to 65: Dr 5200 50 / Cr inventory 50.
    assert await s.journal(arrived["id"]) == [
        ("1300", "1250.0000", "0.0000"),
        ("5200", "50.0000", "0.0000"),
        ("5300", "0.0000", "1300.0000"),
    ]
    assert await s.balances(shoe) == {s.main: ("10.000000", "650.0000")}
    assert (await s.valuation())["difference"] == "0.0000"


async def test_warehouse_transfers_move_stock_at_average_without_a_journal(client: AsyncClient) -> None:
    s = await store(client)
    rice = await s.item("RICE")
    await s.adjust(rice, "10", "in", "8")
    moved = await s.post(
        s.doc(
            "stock_transfer",
            [{"variant_id": rice, "unit": "pcs", "qty": "4"}],
            to_warehouse_id=s.second,
        )
    )
    assert moved["number"] == f"ST-{TODAY.year}-00001"
    assert moved["lines"][0]["value"] == "-32.0000"
    assert await s.journal(moved["id"]) == []
    assert await s.balances(rice) == {s.main: ("6.000000", "48.0000"), s.second: ("4.000000", "32.0000")}

    same = s.doc("stock_transfer", [{"variant_id": rice, "unit": "pcs", "qty": "1"}], to_warehouse_id=s.main)
    response = await client.post("/stock-documents", json=same, headers=s.h)
    assert (response.status_code, response.json()["code"]) == (422, "invalid_stock_document")


async def test_item_transfer_moves_value_from_one_item_to_another(client: AsyncClient) -> None:
    """Rule 26: one pair of size 41 (average 40) was sold as size 42; size 42 is now short."""
    s = await store(client)
    size41 = await s.item("SH-41")
    size42 = await s.item("SH-42")
    await s.adjust(size41, "3", "in", "40")
    fixed = await s.post(
        s.doc(
            "item_transfer",
            [
                {"variant_id": size41, "unit": "pcs", "qty": "1", "direction": "out"},
                {"variant_id": size42, "unit": "pcs", "qty": "1", "direction": "in"},
            ],
            reason="sold as size 42",
        )
    )
    assert fixed["number"] == f"IT-{TODAY.year}-00001"
    assert [line["value"] for line in fixed["lines"]] == ["-40.0000", "40.0000"]
    assert await s.journal(fixed["id"]) == []
    assert await s.balances(size41) == {s.main: ("2.000000", "80.0000")}
    assert await s.balances(size42) == {s.main: ("1.000000", "40.0000")}

    one_sided = s.doc(
        "item_transfer", [{"variant_id": size41, "unit": "pcs", "qty": "1", "direction": "out"}]
    )
    assert (await client.post("/stock-documents", json=one_sided, headers=s.h)).status_code == 422


async def test_only_stock_items_move_and_quantities_respect_their_unit(client: AsyncClient) -> None:
    s = await store(client)
    delivery = await s.item("DELIVERY", kind="service")
    soap = await s.item("SOAP")

    async def code(lines: list[dict[str, Any]]) -> tuple[int, str]:
        response = await client.post("/stock-documents", json=s.doc("stock_adjustment", lines), headers=s.h)
        return response.status_code, response.json().get("code", "")

    assert await code([{"variant_id": delivery, "unit": "pcs", "qty": "1", "direction": "in"}]) == (
        422,
        "invalid_stock_document",
    )
    assert (await code([{"variant_id": soap, "unit": "pcs", "qty": "1.5", "direction": "in"}]))[0] == 422
    assert (await code([{"variant_id": soap, "unit": "roll", "qty": "1", "direction": "in"}]))[0] == 422
    assert (
        await code([{"variant_id": soap, "unit": "pcs", "qty": "1", "direction": "out", "unit_cost": "2"}])
    )[0] == 422


async def test_batch_items_name_their_batch_and_batches_list_earliest_expiry_first(
    client: AsyncClient,
) -> None:
    s = await store(client)
    milk = await s.item("MILK", batches=True)
    later, sooner = str(TODAY + timedelta(days=60)), str(TODAY + timedelta(days=10))
    for batch_no, expiry in (("L2", later), ("L1", sooner)):
        line = {
            "variant_id": milk,
            "unit": "pcs",
            "qty": "5",
            "direction": "in",
            "unit_cost": "3",
            "batch_no": batch_no,
            "expiry_date": expiry,
        }
        await s.post(s.doc("stock_adjustment", [line]))
    params = {"business_id": s.shop.business_id, "variant_id": milk}
    batches = await ok(await client.get("/stock/batches", params=params, headers=s.h))
    assert [(b["batch_no"], b["expiry_date"], b["qty"]) for b in batches] == [
        ("L1", sooner, "5.000000"),
        ("L2", later, "5.000000"),
    ]

    no_batch = s.doc(
        "stock_adjustment", [{"variant_id": milk, "unit": "pcs", "qty": "1", "direction": "out"}]
    )
    assert (await client.post("/stock-documents", json=no_batch, headers=s.h)).status_code == 422
    no_expiry = s.doc(
        "stock_adjustment",
        [{"variant_id": milk, "unit": "pcs", "qty": "1", "direction": "in", "batch_no": "L3"}],
    )
    assert (await client.post("/stock-documents", json=no_expiry, headers=s.h)).status_code == 422
    out = s.doc(
        "stock_adjustment",
        [{"variant_id": milk, "unit": "pcs", "qty": "2", "direction": "out", "batch_id": batches[0]["id"]}],
    )
    taken = await s.post(out)
    assert taken["lines"][0]["batch_no"] == "L1"
    balances = await ok(await client.get("/stock/balances", params=params, headers=s.h))
    assert sorted((b["batch_no"], b["qty"]) for b in balances) == [("L1", "3.000000"), ("L2", "5.000000")]


async def test_a_backdated_receipt_recosts_the_later_issue(client: AsyncClient) -> None:
    """Rule 28: 10 in at 10, then 5 out (value 50); a receipt dated between them changes that issue's cost."""
    s = await store(client)
    oil = await s.item("OIL")
    first, middle, last = TODAY, TODAY + timedelta(days=1), TODAY + timedelta(days=2)
    await s.adjust(oil, "10", "in", "10", doc_date=first)
    issue = await s.adjust(oil, "5", "out", doc_date=last)
    assert issue["lines"][0]["value"] == "-50.0000"

    backdated = await s.adjust(oil, "10", "in", "16", doc_date=middle)
    # At the issue's date the average is now (100 + 160) / 20 = 13: the issue cost 65, not 50.
    params = {"business_id": s.shop.business_id}
    ledger = await ok(await client.get(f"/stock/variants/{oil}/ledger", params=params, headers=s.h))
    recost = [line for line in ledger["lines"] if line["move_type"] == "recost"]
    assert [(r["move_date"], r["value"]) for r in recost] == [(str(last), "-15.0000")]
    assert (ledger["closing_qty"], ledger["closing_value"]) == ("15.000000", "195.0000")
    assert ledger["avg_cost"] == "13.000000"
    assert ("1300", "0.0000", "15.0000") in await s.journal(backdated["id"])  # dated on the issue's day
    assert (await s.valuation())["difference"] == "0.0000"


async def test_negative_stock_can_be_refused_per_business(
    client: AsyncClient, app_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    s = await store(client)
    cup = await s.item("CUP")
    async with app_sessionmaker() as session, session.begin():
        await apply_context(session, tenant_id=uuid.UUID(s.shop.owner["tenant_id"]))
        await session.execute(
            text("UPDATE business SET negative_stock_allowed = false WHERE id = :id"),
            {"id": s.shop.business_id},
        )
    response = await client.post(
        "/stock-documents",
        params={"post": "true"},
        json=s.doc("stock_adjustment", [{"variant_id": cup, "unit": "pcs", "qty": "1", "direction": "out"}]),
        headers=s.h,
    )
    assert (response.status_code, response.json()["code"]) == (409, "negative_stock")
    listed = await ok(
        await client.get("/stock-documents", params={"business_id": s.shop.business_id}, headers=s.h)
    )
    assert listed == []  # nothing left behind, not even a draft


async def test_drafts_change_and_posted_documents_do_not(client: AsyncClient) -> None:
    s = await store(client)
    tea = await s.item("TEA")
    body = s.doc(
        "stock_adjustment",
        [{"variant_id": tea, "unit": "pcs", "qty": "5", "direction": "in", "unit_cost": "2"}],
    )
    draft = await ok(await client.post("/stock-documents", json=body, headers=s.h), 201)
    assert (draft["status"], draft["number"], draft["lines"][0]["value"]) == ("draft", None, None)
    body["lines"][0]["qty"] = "6"
    edited = await ok(await client.put(f"/stock-documents/{draft['id']}", json=body, headers=s.h))
    assert edited["lines"][0]["qty"] == "6.000000"
    posted = await ok(await client.post(f"/stock-documents/{draft['id']}/post", json={}, headers=s.h))
    assert posted["total"] == "12.0000"
    again = await client.put(f"/stock-documents/{draft['id']}", json=body, headers=s.h)
    gone = await client.delete(f"/stock-documents/{draft['id']}", headers=s.h)
    assert (again.status_code, again.json()["code"]) == (409, "stock_document_not_draft")
    assert gone.status_code == 409


async def test_storekeepers_count_stock_but_do_not_post_adjustments(client: AsyncClient) -> None:
    s = await store(client)
    tea = await s.item("TEA")
    await s.adjust(tea, "10", "in", "2")
    await add_member(
        client, s.shop, "store@shop.ae", "warehouse", business=s.shop.business_id, branch=s.shop.branch_a
    )
    keeper = bearer(await login(client, "store@shop.ae"))
    count = s.doc("stock_adjustment", [{"variant_id": tea, "unit": "pcs", "qty": "1", "direction": "out"}])
    draft = await client.post("/stock-documents", json=count, headers=keeper)
    post = await client.post("/stock-documents", params={"post": "true"}, json=count, headers=keeper)
    transfer = s.doc(
        "stock_transfer", [{"variant_id": tea, "unit": "pcs", "qty": "1"}], to_warehouse_id=s.second
    )
    moved = await client.post("/stock-documents", params={"post": "true"}, json=transfer, headers=keeper)
    valuation = await client.get(
        "/stock/valuation", params={"business_id": s.shop.business_id}, headers=keeper
    )
    assert (draft.status_code, post.status_code, moved.status_code, valuation.status_code) == (
        201,
        403,
        201,
        403,
    )
