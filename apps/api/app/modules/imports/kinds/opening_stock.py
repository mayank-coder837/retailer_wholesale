"""Import opening stock (M1.12b-2): goods in the warehouse on the day the books start, at their cost.

Posted as `opening_stock` stock documents (one per warehouse) through the inventory service, so stock comes
from the stock ledger (rule 5), quantities keep their unit (rule 6), and the books get Dr Inventory / Cr
Opening balance equity in the same transaction. A cost is always needed; a total value is accepted only when
quantity times the unit cost it implies gives back that value to the fils (no silent rounding of money).
"""

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.modules.catalog import products as catalog
from app.modules.catalog.models import ProductType
from app.modules.catalog.products import InvalidProductError, StockItem
from app.modules.documents.models import LineDirection
from app.modules.imports.kinds import opening
from app.modules.imports.kinds.base import Context, Field, KindDefaults, Option, Reader, Row, RowResult
from app.modules.imports.kinds.products import UNIT_ALIASES
from app.modules.imports.values import CellError, normal_header, parse_decimal
from app.modules.inventory import service as inventory
from app.modules.inventory.costing import fils
from app.modules.inventory.schemas import StockDocIn, StockDocType, StockLineIn
from app.modules.tenancy import service as tenancy

ZERO = Decimal("0.0000")
COST_PLACES = Decimal("0.000001")  # a unit cost has up to 6 decimals
DOCUMENT_LINES = 400
_QTY_UNIT = re.compile(r"^\s*([-\d.,\s]+?)\s*([^\d\s.,-][^\d]*)$")  # Tally: "10 Nos"
FIELDS = (
    Field(
        "item",
        "Item",
        "الصنف",
        (
            "item",
            "sku",
            "item code",
            "product code",
            "code",
            "barcode",
            "item name",
            "stock item",
            "particulars",
            "رمز الصنف",
            "اسم الصنف",
            "الصنف",
            "باركود",
        ),
        required=True,
        example="FAB-100",
        hint_en="SKU, or barcode",
        hint_ar="رمز الصنف أو الباركود",
    ),
    Field(
        "warehouse",
        "Warehouse",
        "المستودع",
        ("warehouse", "godown", "location", "store", "المستودع", "المخزن"),
        example="WH1",
        hint_en="code or name; the branch's first warehouse when empty",
        hint_ar="الرمز أو الاسم؛ أول مستودع في الفرع عند تركه فارغا",
    ),
    Field(
        "quantity",
        "Quantity",
        "الكمية",
        ("quantity", "qty", "closing qty", "closing quantity", "on hand", "stock", "الكمية"),
        required=True,
        example="120",
    ),
    Field(
        "unit",
        "Unit",
        "الوحدة",
        ("unit", "uom", "units", "unit of measure", "الوحدة"),
        example="m",
        hint_en="the item's base unit when empty",
        hint_ar="وحدة الصنف الأساسية عند تركه فارغا",
    ),
    Field(
        "unit_cost",
        "Unit cost",
        "تكلفة الوحدة",
        ("unit cost", "cost", "cost price", "avg cost", "average cost", "rate", "تكلفة الوحدة", "التكلفة"),
        example="8.50",
        hint_en="per unit entered; or give the total value",
        hint_ar="للوحدة المدخلة؛ أو أعط القيمة الإجمالية",
    ),
    Field(
        "value",
        "Total value",
        "القيمة الإجمالية",
        ("value", "total value", "closing value", "stock value", "amount", "القيمة", "القيمة الإجمالية"),
        hint_en="instead of the unit cost: quantity times cost must give exactly this value",
        hint_ar="بدلا من تكلفة الوحدة: الكمية x التكلفة يجب أن تعطي هذه القيمة تماما",
    ),
    Field("batch_no", "Batch", "الدفعة", ("batch", "batch no", "lot", "رقم الدفعة", "الدفعة")),
    Field(
        "expiry_date",
        "Expiry date",
        "تاريخ الصلاحية",
        ("expiry", "expiry date", "exp", "تاريخ الانتهاء", "تاريخ الصلاحية"),
    ),
)
OPTIONS = (opening.OPENING_DATE, opening.BRANCH)


@dataclass(frozen=True)
class Line:
    warehouse_id: uuid.UUID
    variant_id: uuid.UUID
    line: StockLineIn
    value: Decimal


def _split_quantity(text: str) -> tuple[str, str | None]:
    """``10 Nos`` → (``10``, ``Nos``); anything else is returned whole."""
    match = _QTY_UNIT.match(text)
    return (match[1], match[2].strip()) if match else (text, None)


class OpeningStock(KindDefaults):
    key = "opening_stock"
    label_en = "Opening stock"
    label_ar = "المخزون الافتتاحي"
    description_en = "How much of each item is in each warehouse, and what it cost."
    description_ar = "كمية كل صنف في كل مستودع وتكلفتها."
    fields: tuple[Field, ...] = FIELDS
    options: tuple[Option, ...] = OPTIONS
    permissions: tuple[str, ...] = ("opening_stock.create", "opening_stock.post")
    undo_permissions: tuple[str, ...] = ("opening_stock.cancel",)
    order = 60
    undoable = True

    async def check(self, ctx: Context, rows: list[Row]) -> list[RowResult]:
        await opening.check_opening_day(ctx)
        assert ctx.branch_id is not None
        index = await catalog.item_index(ctx.session)
        warehouses = list(await tenancy.list_warehouses(ctx.session, ctx.branch_id))
        by_name: dict[str, uuid.UUID] = {}
        for w in warehouses:
            for name in (w.code, w.name_en, w.name_ar):
                by_name.setdefault(normal_header(name), w.id)
        default = next((w.id for w in warehouses if w.is_default), warehouses[0].id if warehouses else None)
        items: dict[uuid.UUID, StockItem] = {}
        seen: dict[tuple[uuid.UUID, uuid.UUID, str], int] = {}
        results: list[RowResult] = []
        for row in rows:
            reader = Reader(row, FIELDS)
            results.append(await self._row(ctx, reader, row, index, items, by_name, default, seen))
        return results

    async def _item(
        self,
        ctx: Context,
        reader: Reader,
        index: catalog.ItemIndex,
        items: dict[uuid.UUID, StockItem],
        text: str,
    ) -> StockItem | None:
        key = text.lower()
        found: uuid.UUID | None = index.variants.get(key) or index.barcodes.get(text)
        if found is None and key in index.product_variants:
            variants = index.product_variants[key]
            if len(variants) > 1:
                reader.add("item", "ambiguous_item", f"{text} has several variants: use the variant's SKU.")
                return None
            found = variants[0]
        if found is None:
            reader.add("item", "unknown_item", f"'{text}' is not an item yet; import the items first.")
            return None
        if found not in items:
            items[found] = await catalog.stock_item(ctx.session, found)
        if items[found].product_type != ProductType.STOCK:
            reader.add("item", "not_stock", f"{items[found].sku} is not kept in stock.")
            return None
        return items[found]

    async def _row(
        self,
        ctx: Context,
        reader: Reader,
        row: Row,
        index: catalog.ItemIndex,
        items: dict[uuid.UUID, StockItem],
        by_name: dict[str, uuid.UUID],
        default: uuid.UUID | None,
        seen: dict[tuple[uuid.UUID, uuid.UUID, str], int],
    ) -> RowResult:
        text = reader.text("item", required=True)
        label = text or "?"
        item = await self._item(ctx, reader, index, items, text) if text else None
        quantity, suffix = self._quantity_cell(reader)
        unit_text = reader.text("unit") or suffix
        unit_cost, value = reader.decimal("unit_cost"), reader.decimal("value")
        warehouse = self._warehouse(reader, by_name, default)
        batch_no = reader.text("batch_no", limit=60)
        expiry = reader.date("expiry_date")
        if item is None or quantity is None or warehouse is None or reader.problems:
            return reader.result("new", label)
        label = f"{item.sku} {item.name_en}"
        if quantity == 0:
            return reader.result(
                "skipped", label, note=("zero_quantity", "Nothing to import: the quantity is zero.")
            )
        unit = self._unit(reader, item, unit_text)
        cost = self._cost(reader, quantity, unit_cost, value)
        self._tracking(reader, item, batch_no, expiry)
        self._quantity(reader, item, unit, quantity)
        if reader.problems or unit is None or cost is None:
            return reader.result("new", label)
        key = (warehouse, item.variant_id, (batch_no or "").lower())
        if key in seen:
            reader.add(
                "item", "duplicate_in_file", f"{item.sku} is also on row {seen[key]} for this warehouse."
            )
            return reader.result("new", label)
        seen[key] = row.number
        if not batch_no:
            there = await inventory.warehouse_stock(ctx.session, warehouse, item.variant_id, None)
            if there != 0:
                reader.add("item", "stock_exists", f"{item.sku} already has {there} in this warehouse.")
                return reader.result("new", label)
        line = StockLineIn(
            variant_id=item.variant_id,
            unit=unit,
            qty=quantity,
            direction=LineDirection.IN,
            unit_cost=cost,
            batch_no=batch_no,
            expiry_date=expiry,
        )
        return reader.result("new", label, Line(warehouse, item.variant_id, line, fils(quantity * cost)))

    def _quantity_cell(self, reader: Reader) -> tuple[Decimal | None, str | None]:
        """The quantity, and the unit written after it (``10 Nos``) when there is one."""
        raw = reader.text("quantity", required=True)
        if raw is None:
            return None, None
        number, suffix = _split_quantity(raw)
        try:
            return parse_decimal(number), suffix
        except CellError as exc:
            reader.add("quantity", exc.code, f"Quantity: {exc}")
            return None, suffix

    def _warehouse(
        self, reader: Reader, by_name: dict[str, uuid.UUID], default: uuid.UUID | None
    ) -> uuid.UUID | None:
        text = reader.text("warehouse")
        if text is None:
            if default is None:
                reader.add("warehouse", "no_warehouse", "The branch has no warehouse.")
            return default
        found = by_name.get(normal_header(text))
        if found is None:
            reader.add("warehouse", "unknown_warehouse", f"'{text}' is not a warehouse of this branch.")
        return found

    def _unit(self, reader: Reader, item: StockItem, text: str | None) -> str | None:
        if text is None:
            return item.base_unit
        words = {normal_header(code): code for code in item.units}
        for alias, code in UNIT_ALIASES.items():
            if code in item.units:
                words.setdefault(normal_header(alias), code)
        found = words.get(normal_header(text))
        if found is None:
            reader.add("unit", "unknown_unit", f"{item.sku} has no unit '{text}'.")
        return found

    def _cost(
        self, reader: Reader, quantity: Decimal, unit_cost: Decimal | None, value: Decimal | None
    ) -> Decimal | None:
        if unit_cost is not None and value is not None:
            reader.add("value", "cost_and_value", "Give a unit cost or a total value, not both.")
        elif unit_cost is None and value is None:
            reader.add("unit_cost", "no_cost", "Opening stock needs a cost: a unit cost or a total value.")
        elif (unit_cost is not None and unit_cost < 0) or (value is not None and value < 0):
            reader.add("unit_cost", "negative", "A cost is zero or more.")
        elif unit_cost is not None:
            if unit_cost != unit_cost.quantize(COST_PLACES):
                reader.add("unit_cost", "decimals", "A unit cost has at most 6 decimal places.")
            else:
                return unit_cost
        elif value is not None:
            if value != fils(value):
                reader.add("value", "decimals", "A value has at most 2 decimal places.")
                return None
            implied = (value / quantity).quantize(COST_PLACES)
            if fils(quantity * implied) == value:
                return implied
            reader.add(
                "value", "value_not_exact", "This value is not quantity x a unit cost: give the unit cost."
            )
        return None

    def _tracking(self, reader: Reader, item: StockItem, batch_no: str | None, expiry: Any) -> None:
        if item.track_batch and not batch_no:
            reader.add("batch_no", "batch_needed", f"{item.sku} is kept by batch: give the batch number.")
        if item.track_expiry and expiry is None:
            reader.add("expiry_date", "expiry_needed", f"{item.sku} has an expiry date: give it.")
        if not item.track_batch and batch_no:
            reader.add("batch_no", "not_batch_item", f"{item.sku} is not kept by batch.")

    def _quantity(self, reader: Reader, item: StockItem, unit: str | None, quantity: Decimal) -> None:
        if unit is None:
            return
        if quantity < 0:
            reader.add("quantity", "negative", "A quantity is zero or more.")
            return
        try:
            item.quantity(quantity, unit)
        except InvalidProductError as exc:
            reader.add(
                "quantity", "decimals", exc.detail or "This quantity has too many decimals for the unit."
            )

    async def summary(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        lines = [r.prepared for r in results if isinstance(r.prepared, Line)]
        return {
            "opening_date": ctx.options["opening_date"],
            "lines": len(lines),
            "warehouses": len({ln.warehouse_id for ln in lines}),
            "value": str(sum((ln.value for ln in lines), ZERO)),
        }

    async def apply(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        assert ctx.branch_id is not None
        assert ctx.batch_id is not None
        lines = [r.prepared for r in results if isinstance(r.prepared, Line)]
        per_warehouse: dict[uuid.UUID, list[Line]] = {}
        for ln in lines:
            per_warehouse.setdefault(ln.warehouse_id, []).append(ln)
        documents = 0
        for warehouse_id, group in per_warehouse.items():
            for part in opening.chunks(group, DOCUMENT_LINES):
                data = StockDocIn(
                    doc_type=StockDocType.OPENING_STOCK,
                    business_id=ctx.business_id,
                    branch_id=ctx.branch_id,
                    doc_date=opening.opening_day(ctx),
                    warehouse_id=warehouse_id,
                    reference=f"IMPORT-{ctx.batch_id.hex[:8]}",
                    narration=f"Opening stock imported from {ctx.filename}"[:2000],
                    lines=[ln.line for ln in part],
                )
                document_id = await inventory.post_stock_document(
                    ctx.session, ctx.tenant_id, data, node_id=ctx.node_id, user_id=ctx.user_id
                )
                ctx.record.setdefault("documents", []).append(str(document_id))
                documents += 1
        ctx.record["opening_date"] = ctx.options["opening_date"]
        return {
            "lines": len(lines),
            "documents": documents,
            "skipped": sum(r.status == "skipped" for r in results),
            "value": str(sum((ln.value for ln in lines), ZERO)),
        }
