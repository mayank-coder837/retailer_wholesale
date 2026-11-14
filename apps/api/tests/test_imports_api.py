"""Import wizards, the engine and the masters (M1.12a): upload → check → import, through the real API."""

import io
from decimal import Decimal
from typing import Any

from httpx import AsyncClient
from openpyxl import Workbook

from app.modules.catalog.products import ean13_check_digit
from tests.api_helpers import bearer
from tests.test_accounting_api import Books, books
from tests.test_pricing_api import ok
from tests.test_roles_api import add_member, login

PARTIES = """Party Name,Type,TRN,Phone,Email,Address,City,Emirate,Credit Days,Credit Limit
Gulf Fabrics LLC,Customer,100 123 456 700 003,0501234567,sales@gulf.ae,Al Quoz 4,Dubai,Dubai,30,"50,000.00"
Mill Textiles,Supplier,,0507654321,,,,,45,
الخليج للأقمشة,both,,,,,,,,
Plain Trading,,,,,,,,,
"""


def csv_file(text: str) -> bytes:
    return text.encode("utf-8")


async def upload(b: Books, kind: str, data: bytes, name: str = "data.csv", status: int = 201) -> Any:
    response = await b.client.post(
        "/imports",
        data={"business_id": b.shop.business_id, "kind": kind},
        files={"file": (name, data, "application/octet-stream")},
        headers=b.h,
    )
    return await ok(response, status)


async def check(
    b: Books, batch: dict[str, Any], mapping: dict[str, Any] | None = None, **options: str
) -> Any:
    body = {"mapping": mapping or batch["suggested_mapping"], "options": options}
    return await ok(await b.client.post(f"/imports/{batch['id']}/check", json=body, headers=b.h))


async def commit(b: Books, batch: dict[str, Any], status: int = 200) -> Any:
    return await ok(await b.client.post(f"/imports/{batch['id']}/commit", headers=b.h), status)


async def run(
    b: Books, kind: str, text: str, **options: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    batch = await upload(b, kind, csv_file(text))
    checked = await check(b, batch, **options)
    done = await commit(b, batch)
    return batch, checked, done


async def parties_named(b: Books, q: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = await ok(await b.client.get("/parties", params={"q": q}, headers=b.h))
    return result


async def party(b: Books, q: str) -> dict[str, Any]:
    found = await parties_named(b, q)
    assert len(found) == 1, found
    detail: dict[str, Any] = await ok(await b.client.get(f"/parties/{found[0]['id']}", headers=b.h))
    return detail


def problems(checked: dict[str, Any]) -> dict[int, list[tuple[str | None, str]]]:
    return {
        r["number"]: [(p["field"], p["code"]) for p in r["problems"]]
        for r in checked["rows"]
        if r["problems"]
    }


# --- customers and suppliers -------------------------------------------------------------------------------


async def test_kinds_and_templates(client: AsyncClient) -> None:
    b = await books(client, "kinds@shop.ae")
    kinds = await ok(await client.get("/imports/kinds", headers=b.h))
    assert [k["key"] for k in kinds] == ["accounts", "parties", "products"]
    parties = next(k for k in kinds if k["key"] == "parties")
    assert [f["key"] for f in parties["fields"] if f["required"]] == ["name_en"]
    assert parties["options"][0]["key"] == "default_type"
    assert (
        parties["permissions"] == []
    )  # checked row by row: customers need customer.create, suppliers supplier.create

    template = await client.get("/imports/templates/parties", params={"lang": "ar"}, headers=b.h)
    assert template.status_code == 200
    assert template.headers["content-type"].startswith("text/csv")
    assert template.content.startswith(b"\xef\xbb\xbf")  # a BOM, so Excel opens the Arabic headers
    assert "الاسم" in template.content.decode("utf-8-sig").splitlines()[0]
    assert (await client.get("/imports/templates/nonsense", headers=b.h)).status_code == 404


async def test_customers_and_suppliers_are_imported_like_the_screen_creates_them(client: AsyncClient) -> None:
    b = await books(client, "parties@shop.ae")
    batch = await upload(b, "parties", csv_file(PARTIES))
    assert batch["status"] == "uploaded"
    assert batch["row_count"] == 4
    assert batch["headers"][0] == "Party Name"
    assert batch["suggested_mapping"]["name_en"] == 0
    assert batch["suggested_mapping"]["credit_limit"] == 9
    assert batch["sample"][0][:2] == ["Gulf Fabrics LLC", "Customer"]
    assert batch["duplicate_of"] is None

    checked = await check(b, batch, default_type="customer")
    assert (checked["total"], checked["new"], checked["exists"], checked["errors"]) == (4, 4, 0, 0)
    assert [r["status"] for r in checked["rows"]] == ["new"] * 4
    assert (await parties_named(b, "Gulf")) == []  # checking wrote nothing

    done = await commit(b, batch)
    assert done["status"] == "committed"
    assert done["result"]["counts"] == {"created": 4, "customers": 3, "suppliers": 2, "skipped": 0}

    gulf = await party(b, "Gulf")
    assert gulf["trn"] == "100123456700003"  # spaces taken out
    assert (gulf["is_customer"], gulf["is_supplier"]) == (True, False)
    assert gulf["email"] == "sales@gulf.ae"
    assert [(a["line1"], a["city"], a["emirate"]) for a in gulf["addresses"]] == [
        ("Al Quoz 4", "Dubai", "dubai")
    ]
    profile = gulf["customer_profiles"][0]
    assert (Decimal(profile["credit_limit"]), profile["credit_days"]) == (Decimal(50000), 30)

    mill = await party(b, "Mill Textiles")
    assert (mill["is_customer"], mill["is_supplier"]) == (False, True)
    assert mill["supplier_profiles"][0]["payment_terms_days"] == 45

    arabic = await party(b, "الخليج")
    assert arabic["name_en"] == arabic["name_ar"] == "الخليج للأقمشة"  # one name serves both languages
    assert (arabic["is_customer"], arabic["is_supplier"]) == (True, True)
    assert (await party(b, "Plain"))["is_customer"] is True  # the file did not say: the chosen default


async def test_the_default_type_applies_where_the_file_is_silent(client: AsyncClient) -> None:
    b = await books(client, "default@shop.ae")
    await run(b, "parties", "Name\nMill Two\n", default_type="supplier")
    mill = await party(b, "Mill Two")
    assert (mill["is_customer"], mill["is_supplier"]) == (False, True)


async def test_importing_twice_imports_once(client: AsyncClient) -> None:
    b = await books(client, "twice@shop.ae")
    batch = await upload(b, "parties", csv_file(PARTIES))
    await check(b, batch)
    first = await commit(b, batch)
    again = await commit(b, batch)  # a retry after a lost reply
    assert again["result"] == first["result"]
    assert again["committed_at"] == first["committed_at"]
    assert len(await parties_named(b, "Gulf")) == 1

    # The same file uploaded again is recognised, can be checked, and is refused at the door.
    second = await upload(b, "parties", csv_file(PARTIES))
    assert second["duplicate_of"] == batch["id"]
    await check(b, second)
    refused = await commit(b, second, 409)
    assert refused["code"] == "already_imported"
    assert len(await parties_named(b, "Gulf")) == 1


async def test_a_party_already_in_the_books_is_skipped_never_changed(client: AsyncClient) -> None:
    b = await books(client, "skip@shop.ae")
    await run(b, "parties", PARTIES)
    text = (
        "Name,TRN,Phone\n"
        "Gulf Fabrics LLC,,0509999999\n"  # same name
        "Another Name,100123456700003,0509999999\n"  # same TRN
        "الخليج للأقمشة,,\n"  # same Arabic name
        "Ali Traders,,\n"  # already made by the test fixture
        "New Customer,,0501111111\n"
    )
    batch = await upload(b, "parties", csv_file(text))
    checked = await check(b, batch)
    assert (checked["new"], checked["exists"], checked["errors"]) == (1, 4, 0)
    assert [r["status"] for r in checked["rows"]] == ["exists", "exists", "exists", "exists", "new"]
    done = await commit(b, batch)
    assert done["result"]["counts"]["created"] == 1
    assert done["result"]["counts"]["skipped"] == 4
    assert (await party(b, "Gulf"))["phone"] == "0501234567"  # untouched
    assert await parties_named(b, "Another Name") == []


async def test_one_bad_row_imports_nothing(client: AsyncClient) -> None:
    b = await books(client, "bad@shop.ae")
    text = (
        "Name,Type,TRN,Emirate,Credit Days,Email\n"
        "Good One,customer,,,,\n"
        "Short TRN,customer,123,,,\n"
        "Odd Type,friend,,,,\n"
        "Odd Emirate,customer,,Mars,,\n"
        ",customer,,,,\n"
        "Days,customer,,,abc,\n"
        "Good One,customer,,,,\n"
        "Bad Mail,customer,,,,not-an-email\n"
    )
    batch = await upload(b, "parties", csv_file(text))
    checked = await check(b, batch)
    assert (checked["new"], checked["errors"]) == (1, 7)
    assert problems(checked) == {
        3: [("trn", "invalid")],
        4: [("type", "unknown_value")],
        5: [("emirate", "unknown_value")],
        6: [("name_en", "required")],
        7: [("credit_days", "not_a_number")],
        8: [(None, "duplicate_in_file")],
        9: [("email", "invalid")],
    }
    assert [r["number"] for r in checked["rows"][:7]] == [3, 4, 5, 6, 7, 8, 9]  # errors listed first, by row

    refused = await commit(b, batch, 422)
    assert refused["code"] == "import_has_errors"
    assert await parties_named(b, "Good One") == []  # not even the good row
    again = await ok(await b.client.get(f"/imports/{batch['id']}", headers=b.h))
    assert again["status"] == "checked"  # it can be fixed and checked again


async def test_a_uae_address_needs_its_emirate(client: AsyncClient) -> None:
    b = await books(client, "emirate@shop.ae")
    batch = await upload(b, "parties", csv_file("Name,Address\nAl Ain Store,Main street\n"))
    checked = await check(b, batch)
    assert problems(checked) == {2: [("emirate", "invalid")]}


# --- items -------------------------------------------------------------------------------------------------

EAN = "629104150021" + ean13_check_digit("629104150021")
PRODUCTS = f"""SKU,Name,Unit,Type,Price,Barcode,VAT
FAB-100,Cotton fabric,Mtr,stock,12.50,{EAN},SR
FAB-200,Silk,m,,"1,200.00",,ZR
SVC-1,Delivery,,service,50,,SR
7700,Thread,Nos,,0.5,,
"""


async def test_items_are_imported_with_units_prices_and_barcodes(client: AsyncClient) -> None:
    b = await books(client, "items@shop.ae")
    batch = await upload(b, "products", csv_file(PRODUCTS))
    assert batch["suggested_mapping"]["sku"] == 0
    assert batch["suggested_mapping"]["sales_price"] == 4
    checked = await check(b, batch)
    assert (checked["new"], checked["errors"]) == (4, 0), problems(checked)
    done = await commit(b, batch)
    assert done["result"]["counts"] == {"created": 4, "skipped": 0, "priced": 4, "barcodes": 1}

    found = await ok(await b.client.get("/products", params={"q": "FAB-100"}, headers=b.h))
    product = await ok(await b.client.get(f"/products/{found[0]['id']}", headers=b.h))
    assert (product["base_unit"], product["product_type"], product["sales_tax_code"]) == ("m", "stock", "SR")
    assert Decimal(product["units"][0]["sale_price"]) == Decimal("12.50")
    scan = await ok(await b.client.get(f"/barcodes/{EAN}", headers=b.h))
    assert (scan["product_sku"], scan["kind"]) == ("FAB-100", "ean13")

    silk = await ok(await b.client.get("/products", params={"q": "FAB-200"}, headers=b.h))
    assert (
        Decimal(
            (await ok(await b.client.get(f"/products/{silk[0]['id']}", headers=b.h)))["units"][0][
                "sale_price"
            ]
        )
        == 1200
    )
    service = await ok(await b.client.get("/products", params={"q": "SVC-1"}, headers=b.h))
    assert service[0]["product_type"] == "service"
    thread = await ok(await b.client.get("/products", params={"q": "7700"}, headers=b.h))
    assert thread[0]["base_unit"] == "pcs"  # "Nos" is how Tally says pieces


async def test_item_rows_with_problems_are_named(client: AsyncClient) -> None:
    b = await books(client, "itemerr@shop.ae")
    text = (
        "SKU,Name,Unit,Type,Price,Barcode,Purchase VAT\n"
        "A-1,Good,pcs,,1,,\n"
        "A-2,Odd unit,lightyear,,1,,\n"
        "A-3,Negative,pcs,,-5,,\n"
        "A-4,Blocked VAT,pcs,stock,1,,SR-B\n"
        "A-1,Duplicate,pcs,,1,,\n"
        ",No sku,pcs,,1,,\n"
        "B@D,Bad sku,pcs,,1,,\n"
        "A-7,Many decimals,pcs,,1.23456,,\n"
        "A-8,Same barcode,pcs,,1,12345678,\n"
        "A-9,Same barcode,pcs,,1,12345678,\n"
        "B-1,Spaced barcode,pcs,,1,AB 12,\n"
        "B-2,Trailing zeros,pcs,,12.500000,,\n"
    )
    batch = await upload(b, "products", csv_file(text))
    checked = await check(b, batch)
    assert problems(checked) == {
        3: [("unit", "unknown_unit")],
        4: [("sales_price", "invalid_price")],
        5: [("purchase_tax_code", "invalid_tax_code")],
        6: [("sku", "duplicate_in_file")],
        7: [("sku", "required")],
        8: [("sku", "invalid")],
        9: [("sales_price", "invalid_price")],
        11: [("barcode", "duplicate_in_file")],
        12: [("barcode", "invalid_barcode")],  # row 13, 12.500000, is fine: its value has 1 decimal
    }
    assert (await commit(b, batch, 422))["code"] == "import_has_errors"
    assert await ok(await b.client.get("/products", params={"q": "A-1"}, headers=b.h)) == []


async def test_an_item_already_in_the_books_is_skipped(client: AsyncClient) -> None:
    b = await books(client, "itemskip@shop.ae")
    await run(b, "products", "SKU,Name,Price\nX-1,First,10\n")
    _batch, checked, done = await run(b, "products", "SKU,Name,Price\nx-1,Second,99\nX-2,Other,5\n")
    assert [r["status"] for r in checked["rows"]] == ["exists", "new"]
    assert done["result"]["counts"]["created"] == 1
    found = await ok(await b.client.get("/products", params={"q": "X-1"}, headers=b.h))
    product = await ok(await b.client.get(f"/products/{found[0]['id']}", headers=b.h))
    assert (product["name_en"], Decimal(product["units"][0]["sale_price"])) == ("First", 10)


async def test_an_excel_workbook_is_imported_like_a_csv(client: AsyncClient) -> None:
    b = await books(client, "xlsx@shop.ae")
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Item Code", "Item Name", "Rate"])
    sheet.append([1001, "Brass hook", 12.5])  # numbers typed into Excel
    sheet.append([1002, "Brass ring", 0.1])
    out = io.BytesIO()
    book.save(out)
    batch = await upload(b, "products", out.getvalue(), "stock.xlsx")
    assert batch["suggested_mapping"] == {
        "sku": 0,
        "name_en": 1,
        "sales_price": 2,
        **{k: None for k in batch["suggested_mapping"] if k not in ("sku", "name_en", "sales_price")},
    }
    await check(b, batch)
    await commit(b, batch)
    found = await ok(await b.client.get("/products", params={"q": "1002"}, headers=b.h))
    product = await ok(await b.client.get(f"/products/{found[0]['id']}", headers=b.h))
    assert (product["sku"], Decimal(product["units"][0]["sale_price"])) == ("1002", Decimal("0.1"))


# --- chart of accounts -------------------------------------------------------------------------------------


async def test_ledgers_are_added_under_existing_groups(client: AsyncClient) -> None:
    b = await books(client, "chart@shop.ae")
    text = (
        "Code,Account Name,Group,Type\n"
        "6300,Staff accommodation,6000,expense\n"
        "6310,Staff visas,Operating expenses,\n"
        "1160,Savings bank,Current assets,bank\n"
        "6100,Rent again,6000,expense\n"  # the code exists
    )
    _batch, checked, done = await run(b, "accounts", text)
    assert [r["status"] for r in checked["rows"]] == ["new", "new", "new", "exists"]
    assert done["result"]["counts"] == {"created": 3, "skipped": 1}
    accounts = {
        a["code"]: a
        for a in await ok(
            await b.client.get("/accounts", params={"business_id": b.shop.business_id}, headers=b.h)
        )
    }
    assert (accounts["6300"]["account_type"], accounts["6300"]["nature"]) == ("expense", "expense")
    assert accounts["6310"]["account_type"] == "expense"  # taken from the group
    assert accounts["1160"]["account_type"] == "bank"
    assert accounts["6100"]["name_en"] == "Rent"  # untouched


async def test_ledger_rows_with_problems_are_named(client: AsyncClient) -> None:
    b = await books(client, "charterr@shop.ae")
    text = (
        "Code,Account Name,Group,Type\n"
        "7000,Nowhere,Nowhere,\n"
        "7001,Debtor,6000,receivable\n"
        "7002,Wrong group,Current assets,expense\n"
        "7003,Odd type,6000,banana\n"
        "6 0,Bad code,6000,\n"
        "7005,Twice,6000,\n"
        "7005,Twice again,6000,\n"
    )
    batch = await upload(b, "accounts", csv_file(text))
    checked = await check(b, batch)
    assert problems(checked) == {
        2: [("parent", "unknown_group")],
        3: [("type", "system_type")],
        4: [("type", "wrong_group")],
        5: [("type", "unknown_value")],
        6: [("code", "invalid")],
        8: [("code", "duplicate_in_file")],
    }


# --- the rules of the flow ---------------------------------------------------------------------------------


async def test_the_flow_is_in_order(client: AsyncClient) -> None:
    b = await books(client, "order@shop.ae")
    batch = await upload(b, "parties", csv_file(PARTIES))
    refused = await commit(b, batch, 409)  # not checked yet
    assert refused["code"] == "import_not_checked"

    no_name = {"mapping": {"name_en": None, "trn": 2}, "options": {}}
    missing = await ok(await b.client.post(f"/imports/{batch['id']}/check", json=no_name, headers=b.h), 422)
    assert missing["code"] == "mapping_incomplete"
    wrong = await ok(
        await b.client.post(f"/imports/{batch['id']}/check", json={"mapping": {"name_en": 99}}, headers=b.h),
        422,
    )
    assert wrong["code"] == "mapping_incomplete"
    option = await ok(
        await b.client.post(
            f"/imports/{batch['id']}/check",
            json={"mapping": batch["suggested_mapping"], "options": {"default_type": "alien"}},
            headers=b.h,
        ),
        422,
    )
    assert option["code"] == "invalid_import_option"

    await check(b, batch)
    await commit(b, batch)
    closed = await ok(
        await b.client.post(
            f"/imports/{batch['id']}/check", json={"mapping": batch["suggested_mapping"]}, headers=b.h
        ),
        409,
    )
    assert closed["code"] == "import_closed"

    listed = await ok(await b.client.get("/imports", params={"business_id": b.shop.business_id}, headers=b.h))
    assert [x["id"] for x in listed] == [batch["id"]]
    assert listed[0]["counts"]["new"] == 4


async def test_unreadable_files_are_refused_at_upload(client: AsyncClient) -> None:
    b = await books(client, "unreadable@shop.ae")
    for name, data in (
        ("old.xls", b"x"),
        ("empty.csv", b""),
        ("book.xlsx", b"PK\x03\x04junk"),
        ("x.pdf", b"%PDF"),
    ):
        refused = await upload(b, "parties", data, name, status=422)
        assert refused["code"] == "unreadable_file", name
    assert (await upload(b, "nonsense", csv_file("a\n1\n"), status=404))["code"] == "unknown_import_kind"


async def test_what_an_import_creates_is_audited_as_an_import(client: AsyncClient) -> None:
    b = await books(client, "audit@shop.ae")
    await run(b, "parties", "Name\nAudited Co\n")
    found = await party(b, "Audited")
    rows = await ok(
        await b.client.get("/audit", params={"entity_type": "party", "entity_id": found["id"]}, headers=b.h)
    )
    assert [(r["action"], r["client"]) for r in rows] == [("create", "import")]
    batches = await ok(await b.client.get("/audit", params={"entity_type": "import_batch"}, headers=b.h))
    assert {r["client"] for r in batches if r["action"] == "update"} == {"import", "desktop"}


# --- who may import ----------------------------------------------------------------------------------------


async def test_an_import_never_lets_anyone_do_more_than_the_screens(client: AsyncClient) -> None:
    b = await books(client, "perm@shop.ae")
    owner = bearer(b.shop.owner)
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
                    "customer.create",
                    "customer.view",
                ],
            },
            headers=owner,
        ),
        201,
    )
    b.shop.roles["importer"] = role["id"]
    await add_member(client, b.shop, "importer@shop.ae", "importer")
    importer = bearer(await login(client, "importer@shop.ae"))

    def post(kind: str) -> Any:
        return client.post(
            "/imports",
            data={"business_id": b.shop.business_id, "kind": kind},
            files={"file": ("f.csv", csv_file("Name\nX\n"), "text/csv")},
            headers=importer,
        )

    assert (await post("parties")).status_code == 201  # customers only is fine: checked row by row
    assert (await post("accounts")).status_code == 403  # may not create accounts
    assert (await post("products")).status_code == 403
    assert (await client.get("/imports/kinds", headers=importer)).status_code == 200

    await add_member(client, b.shop, "sales@shop.ae", "salesperson")
    sales = bearer(await login(client, "sales@shop.ae"))
    assert (await client.get("/imports/kinds", headers=sales)).status_code == 403
    assert (
        await client.get("/imports", params={"business_id": b.shop.business_id}, headers=sales)
    ).status_code == 403

    await add_member(client, b.shop, "acc@shop.ae", "accountant")
    accountant = bearer(await login(client, "acc@shop.ae"))
    assert (await client.get("/imports/kinds", headers=accountant)).status_code == 200


async def test_party_rows_need_the_permission_of_what_they_create(client: AsyncClient) -> None:
    b = await books(client, "rowperm@shop.ae")
    role = await ok(
        await client.post(
            "/roles",
            json={
                "name_en": "Customer importer",
                "name_ar": "مستورد عملاء",
                "permissions": [
                    "import.view",
                    "import.create",
                    "import.post",
                    "customer.create",
                    "customer.view",
                ],
            },
            headers=bearer(b.shop.owner),
        ),
        201,
    )
    b.shop.roles["cust"] = role["id"]
    await add_member(client, b.shop, "cust@shop.ae", "cust")
    h = bearer(await login(client, "cust@shop.ae"))
    text = "Name,Type\nWants Goods,customer\nSells Goods,supplier\nBoth Ways,both\n"
    response = await client.post(
        "/imports",
        data={"business_id": b.shop.business_id, "kind": "parties"},
        files={"file": ("p.csv", csv_file(text), "text/csv")},
        headers=h,
    )
    batch = await ok(response, 201)
    checked = await ok(
        await client.post(
            f"/imports/{batch['id']}/check", json={"mapping": batch["suggested_mapping"]}, headers=h
        )
    )
    assert problems(checked) == {3: [("type", "not_allowed")], 4: [("type", "not_allowed")]}
    refused = await client.post(f"/imports/{batch['id']}/commit", headers=h)
    assert refused.status_code == 422
    assert await parties_named(b, "Wants Goods") == []


async def test_party_codes_never_collide(client: AsyncClient) -> None:
    b = await books(client, "codes@shop.ae")
    # Row 2 has no code and would take the next number; row 3 types exactly that number.
    text = "Name,Code\nTyped Twice,C0050\nAlso Typed,c0050\n"
    batch = await upload(b, "parties", csv_file(text))
    assert problems(await check(b, batch)) == {3: [("code", "duplicate_in_file")]}

    text = "Name,Code\nAuto One,\nTyped Next,C0002\nAuto Two,\nTaken,C0001\n"
    batch = await upload(b, "parties", csv_file(text))
    assert problems(await check(b, batch)) == {5: [("code", "code_taken")]}
    text = "Name,Code\nAuto One,\nTyped Next,C0002\nAuto Two,\n"
    done = await run(b, "parties", text)
    assert done[2]["result"]["counts"]["created"] == 3
    codes = {p["name_en"]: p["code"] for p in await parties_named(b, "")}
    assert codes["Typed Next"] == "C0002"
    assert codes["Auto One"] == "C0003"  # numbered after the typed ones
    assert codes["Auto Two"] == "C0004"
    assert len(set(codes.values())) == len(codes)


async def test_another_business_cannot_see_or_use_a_batch(client: AsyncClient) -> None:
    mine = await books(client, "mine@shop.ae")
    other = await books(client, "other@shop.ae")
    batch = await upload(mine, "parties", csv_file("Name\nSecret Co\n"))
    assert (await client.get(f"/imports/{batch['id']}", headers=other.h)).status_code == 404
    assert (await client.post(f"/imports/{batch['id']}/commit", headers=other.h)).status_code == 404
    taken = await client.post(
        "/imports",
        data={"business_id": mine.shop.business_id, "kind": "parties"},
        files={"file": ("f.csv", csv_file("Name\nX\n"), "text/csv")},
        headers=other.h,
    )
    assert taken.status_code in (403, 404)
