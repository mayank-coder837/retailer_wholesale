"""Opening stock (M1.12b-2), against stock worked out by hand.

    FAB-100 cotton fabric   120 m   at 8.50          = 1,020.00   main warehouse
    BOLT-1  bolts         1,000 pcs total value 150  =   150.00   main warehouse (cost 0.15 each)
    BOLT-1  bolts           200 Nos at 0.20          =    40.00   second warehouse ("Nos" is pieces)
    MED-1   medicine         50 pcs at 3.00, batch B-1 =  150.00   second warehouse
    total 1,360.00 in two stock documents (one per warehouse)

With a trial balance of cash 8,640 + inventory 1,360 = 10,000 Dr against capital 10,000 Cr, the trial balance
posts cash and capital and puts 1,360 Dr against opening balance equity; the stock puts 1,360 Cr there: nil.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from hypothesis import given
from hypothesis import strategies as st

from app.modules.imports.kinds.base import Reader, Row
from app.modules.imports.kinds.opening_stock import FIELDS, OpeningStock
from app.modules.inventory.costing import fils
from tests.api_helpers import bearer
from tests.test_accounting_api import Books, books
from tests.test_import_opening_api import OPENING, balances, reconciliation
from tests.test_imports_api import check, commit, csv_file, ok, problems, run, upload
from tests.test_roles_api import add_member, login

ITEMS = (
    "SKU,Name,Unit,Type,Batches,Expiry\n"
    "FAB-100,Cotton fabric,m,stock,,\n"
    "BOLT-1,Bolt,pcs,stock,,\n"
    "MED-1,Medicine,pcs,stock,yes,yes\n"
    "SVC-1,Delivery,,service,,\n"
)
STOCK = (
    "Item,Warehouse,Qty,Unit,Unit Cost,Total Value,Batch,Expiry\n"
    "FAB-100,,120,m,8.50,,,\n"
    'BOLT-1,,"1,000",,,150.00,,\n'
    "BOLT-1,WH2,200,Nos,0.2,,,\n"
    "MED-1,WH2,50,,3.00,,B-1,31/12/2027\n"
)
TRIAL_BALANCE = "Account,Debit,Credit\n1110,8640,\n1300,1360,\n3100,,10000\n"


async def stocked_shop(client: AsyncClient, email: str) -> tuple[Books, dict[str, str]]:
    b = await books(client, email)
    await run(b, "products", ITEMS)
    branch = b.shop.branch_a
    second = await ok(
        await client.post(
            f"/branches/{branch}/warehouses",
            json={"code": "WH2", "name_en": "Second warehouse", "name_ar": "المستودع الثاني"},
            headers=b.h,
        ),
        201,
    )
    listed = await ok(await client.get(f"/branches/{branch}/warehouses", headers=b.h))
    ids = {w["code"]: w["id"] for w in listed}
    assert second["id"] == ids["WH2"]
    return b, ids


async def stock(b: Books) -> dict[tuple[str, str], tuple[Decimal, Decimal]]:
    rows = await ok(
        await b.client.get("/stock/balances", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    return {
        (r["sku"], r["warehouse_id"]): (Decimal(r["qty"]), Decimal(r["value"]))
        for r in rows
        if Decimal(r["qty"])
    }


async def test_opening_stock_ties_to_the_inventory_account(client: AsyncClient) -> None:
    b, wh = await stocked_shop(client, "stock@shop.ae")
    main = next(code for code in wh if code != "WH2")
    await run(b, "opening_balances", TRIAL_BALANCE, **OPENING)
    before = await reconciliation(b)
    assert before["ties"] is False
    inventory = next(r for r in before["rows"] if r["code"] == "1300")
    assert (inventory["control"], Decimal(inventory["difference"])) == (True, Decimal(-1360))

    batch = await upload(b, "opening_stock", csv_file(STOCK))
    assert batch["suggested_mapping"]["item"] == 0
    assert batch["suggested_mapping"]["quantity"] == 2
    assert batch["suggested_mapping"]["unit_cost"] == 4
    assert batch["suggested_mapping"]["value"] == 5
    checked = await check(b, batch, **OPENING)
    assert (checked["new"], checked["errors"]) == (4, 0), problems(checked)
    assert checked["summary"]["lines"] == 4
    assert checked["summary"]["warehouses"] == 2
    assert Decimal(checked["summary"]["value"]) == Decimal(1360)
    assert await stock(b) == {}  # checking wrote nothing

    done = await commit(b, batch)
    assert done["result"]["counts"]["lines"] == 4
    assert done["result"]["counts"]["documents"] == 2
    assert Decimal(done["result"]["counts"]["value"]) == Decimal(1360)
    assert len(done["result"]["documents"]) == 2

    on_hand = await stock(b)
    assert on_hand[("FAB-100", wh[main])] == (Decimal(120), Decimal("1020.00"))
    assert on_hand[("BOLT-1", wh[main])] == (Decimal(1000), Decimal("150.00"))
    assert on_hand[("BOLT-1", wh["WH2"])] == (Decimal(200), Decimal("40.00"))
    assert on_hand[("MED-1", wh["WH2"])] == (Decimal(50), Decimal("150.00"))
    medicine = [
        r
        for r in await ok(
            await b.client.get("/stock/balances", params={"business_id": b.shop.business_id}, headers=b.h)
        )
        if r["sku"] == "MED-1"
    ]
    assert (medicine[0]["batch_no"], medicine[0]["expiry_date"]) == ("B-1", "2027-12-31")

    valuation = await ok(
        await b.client.get("/stock/valuation", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert (Decimal(valuation["stock_value"]), Decimal(valuation["inventory_account"])) == (
        Decimal(1360),
        Decimal(1360),
    )
    assert Decimal(valuation["difference"]) == 0

    final = await balances(b)
    assert final["1300"] == Decimal(1360)
    assert final.get("3900", 0) == 0  # opening balance equity nets to nothing
    assert sum(final.values()) == 0
    recon = await reconciliation(b)
    assert recon["ties"] is True
    assert Decimal(recon["equity_balance"]) == 0

    documents = await ok(
        await b.client.get(
            "/stock-documents",
            params={"business_id": b.shop.business_id, "doc_type": "opening_stock"},
            headers=b.h,
        )
    )
    assert len(documents) == 2
    assert all(d["number"].startswith("OS-") and d["status"] == "posted" for d in documents)
    assert all(d["doc_type"] == "opening_stock" for d in documents)
    entries = await ok(
        await b.client.get(
            "/audit", params={"entity_type": "document", "entity_id": documents[0]["id"]}, headers=b.h
        )
    )
    assert {r["client"] for r in entries} == {"import"}


async def test_stock_rows_with_problems_are_named(client: AsyncClient) -> None:
    b, _wh = await stocked_shop(client, "stockbad@shop.ae")
    file = (
        "Item,Warehouse,Qty,Unit,Unit Cost,Total Value,Batch,Expiry\n"
        "NOPE,,1,,1,,,\n"  # 2
        "SVC-1,,1,,1,,,\n"  # 3 a service
        "FAB-100,Mars,1,,1,,,\n"  # 4
        "FAB-100,,1,lightyear,1,,,\n"  # 5
        "FAB-100,,1,,,,,\n"  # 6 no cost
        "FAB-100,,1,,1,1,,\n"  # 7 cost and value
        "FAB-100,,-1,,1,,,\n"  # 8 negative
        "FAB-100,,7000000,,,1.00,,\n"  # 9 value not exact
        "FAB-100,,1,,,10.005,,\n"  # 10 value with three decimals
        "FAB-100,,1,,-2,,,\n"  # 11 negative cost
        "MED-1,,5,,3,,,\n"  # 12 batch item without batch or expiry
        "BOLT-1,,5,,1,,X1,\n"  # 13 not a batch item
        "FAB-100,,0.0001,,1,,,\n"  # 14 too many decimals for metres
        "BOLT-1,,5,,1,,,\n"  # 15 fine
        "bolt-1,,5,,1,,,\n"  # 16 the same item and warehouse twice
        "FAB-100,,abc,,1,,,\n"  # 17 not a number
        ",,5,,1,,,\n"  # 18 no item
        "FAB-100,,5,,0,,,\n"  # 19 a zero cost
        "FAB-100,,10000000000,,1,,,\n"  # 20 a quantity too large to be right
        "FAB-100,,5,,99999999999,,,\n"  # 21 a cost too large to be right
        "Cotton fabric,,5,,2,,,\n"  # 22 found by its name: fine
    )
    batch = await upload(b, "opening_stock", csv_file(file))
    checked = await check(b, batch, **OPENING)
    found = problems(checked)
    assert found[2] == [("item", "unknown_item")]
    assert found[3] == [("item", "not_stock")]
    assert found[4] == [("warehouse", "unknown_warehouse")]
    assert found[5] == [("unit", "unknown_unit")]
    assert found[6] == [("unit_cost", "no_cost")]
    assert found[7] == [("value", "cost_and_value")]
    assert found[8] == [("quantity", "negative")]
    assert found[9] == [("value", "value_not_exact")]
    assert found[10] == [("value", "decimals")]
    assert found[11] == [("unit_cost", "negative")]
    assert found[12] == [("batch_no", "batch_needed"), ("expiry_date", "expiry_needed")]
    assert found[13] == [("batch_no", "not_batch_item")]
    assert found[14] == [("quantity", "decimals")]
    assert 15 not in found
    assert found[16] == [("item", "duplicate_in_file")]
    assert found[17] == [("quantity", "not_a_number")]
    assert found[18] == [("item", "required")]
    assert found[19] == [("unit_cost", "zero_cost")]
    assert found[20] == [("quantity", "too_large")]
    assert found[21] == [("unit_cost", "too_large")]
    assert 22 not in found
    assert (await commit(b, batch, 422))["code"] == "import_has_errors"
    assert await stock(b) == {}


async def test_a_second_file_cannot_double_the_stock(client: AsyncClient) -> None:
    b, _wh = await stocked_shop(client, "twice12@shop.ae")
    await run(b, "opening_stock", "Item,Qty,Cost\nFAB-100,10,5\n", **OPENING)
    batch = await upload(b, "opening_stock", csv_file("Item,Qty,Cost\nFAB-100,10,5\nBOLT-1,3,2\n"))
    checked = await check(b, batch, **OPENING)
    assert problems(checked) == {2: [("item", "stock_exists")]}
    assert "10" in checked["rows"][0]["problems"][0]["message"]
    assert (checked["new"], checked["errors"]) == (1, 1)


async def test_a_batch_cannot_be_doubled_either(client: AsyncClient) -> None:
    b, _wh = await stocked_shop(client, "batch12s@shop.ae")
    header = "Item,Qty,Cost,Batch,Expiry\n"
    await run(b, "opening_stock", header + "MED-1,50,3,B-1,31/12/2027\n", **OPENING)
    again = await upload(
        b, "opening_stock", csv_file(header + "MED-1,5,3,B-1,31/12/2027\nMED-1,5,3,B-2,31/12/2027\n")
    )
    checked = await check(b, again, **OPENING)
    assert problems(checked) == {2: [("item", "stock_exists")]}  # B-1 is there; B-2 is a new batch
    assert (checked["new"], checked["errors"]) == (1, 1)


async def test_an_item_is_found_by_its_name_when_the_name_is_unique(client: AsyncClient) -> None:
    b, _wh = await stocked_shop(client, "names12s@shop.ae")
    await run(b, "products", "SKU,Name\nFAB-200,Cotton fabric\n")  # a second item with the same name
    batch = await upload(b, "opening_stock", csv_file("Item,Qty,Cost\nBolt,3,2\nCotton fabric,3,2\n"))
    checked = await check(b, batch, **OPENING)
    assert problems(checked) == {3: [("item", "ambiguous_item")]}
    assert checked["new"] == 1


async def test_a_quantity_can_carry_its_unit_as_tally_writes_it(client: AsyncClient) -> None:
    b, wh = await stocked_shop(client, "tallyqty@shop.ae")
    await run(b, "opening_stock", "Item,Quantity,Rate\nBOLT-1,10 Nos,1.5\n", **OPENING)
    main = next(code for code in wh if code != "WH2")
    assert (await stock(b))[("BOLT-1", wh[main])] == (Decimal(10), Decimal("15.00"))


async def test_undo_takes_the_stock_out_and_the_file_can_come_again(client: AsyncClient) -> None:
    b, _wh = await stocked_shop(client, "undostock@shop.ae")
    batch = await upload(b, "opening_stock", csv_file(STOCK))
    await check(b, batch, **OPENING)
    await commit(b, batch)
    assert len(await stock(b)) == 4

    undone = await ok(await b.client.post(f"/imports/{batch['id']}/undo", headers=b.h))
    assert undone["status"] == "undone"
    assert await stock(b) == {}
    valuation = await ok(
        await b.client.get("/stock/valuation", params={"business_id": b.shop.business_id}, headers=b.h)
    )
    assert (Decimal(valuation["stock_value"]), Decimal(valuation["inventory_account"])) == (0, 0)
    assert all(v == 0 for v in (await balances(b)).values())

    fresh = await upload(b, "opening_stock", csv_file(STOCK))
    assert fresh["duplicate_of"] is None
    assert (await check(b, fresh, **OPENING))["new"] == 4


async def test_opening_stock_needs_its_own_permission(client: AsyncClient) -> None:
    b, _wh = await stocked_shop(client, "perm12s@shop.ae")
    role = await ok(
        await client.post(
            "/roles",
            json={
                "name_en": "Importer",
                "name_ar": "مستورد",
                "permissions": ["import.view", "import.create", "import.post", "product.view"],
            },
            headers=bearer(b.shop.owner),
        ),
        201,
    )
    b.shop.roles["importer"] = role["id"]
    await add_member(client, b.shop, "imp12s@shop.ae", "importer")
    importer = bearer(await login(client, "imp12s@shop.ae"))
    response = await client.post(
        "/imports",
        data={"business_id": b.shop.business_id, "kind": "opening_stock"},
        files={"file": ("s.csv", csv_file(STOCK), "text/csv")},
        headers=importer,
    )
    assert response.status_code == 403

    await add_member(client, b.shop, "acc12s@shop.ae", "accountant")
    accountant = bearer(await login(client, "acc12s@shop.ae"))
    response = await client.post(
        "/imports",
        data={"business_id": b.shop.business_id, "kind": "opening_stock"},
        files={"file": ("s.csv", csv_file(STOCK), "text/csv")},
        headers=accountant,
    )
    assert response.status_code == 201


async def test_the_opening_stock_document_is_refused_without_a_cost(client: AsyncClient) -> None:
    b, wh = await stocked_shop(client, "doc12s@shop.ae")
    found = await ok(await client.get("/products", params={"q": "FAB-100"}, headers=b.h))
    product = await ok(await client.get(f"/products/{found[0]['id']}", headers=b.h))
    body: dict[str, Any] = {
        "doc_type": "opening_stock",
        "business_id": b.shop.business_id,
        "branch_id": b.shop.branch_a,
        "doc_date": str(date.today()),
        "warehouse_id": next(iter(wh.values())),
        "lines": [{"variant_id": product["variants"][0]["id"], "unit": "m", "qty": "5"}],
    }
    refused = await ok(await client.post("/stock-documents", json=body, headers=b.h), 422)
    assert refused["code"] == "invalid_stock_document"
    body["lines"][0]["unit_cost"] = "2"
    created = await ok(await client.post("/stock-documents", json=body, headers=b.h), 201)
    assert created["doc_type"] == "opening_stock"
    assert created["lines"][0]["direction"] == "in"  # opening stock only brings goods in


@given(
    st.integers(min_value=1, max_value=10**10).map(lambda n: Decimal(n).scaleb(-3)),
    st.integers(min_value=1, max_value=10**11).map(lambda n: Decimal(n).scaleb(-2)),
)
def test_a_total_value_is_accepted_only_when_it_is_posted_exactly(quantity: Decimal, value: Decimal) -> None:
    reader = Reader(Row(2, {}), FIELDS)
    cost = OpeningStock()._cost(reader, quantity, None, value)
    if cost is None:
        assert [p.code for p in reader.problems] == ["value_not_exact"]
    else:
        assert fils(quantity * cost) == value  # the stock value posted is the value in the file
        assert cost == cost.quantize(Decimal("0.000001"))
