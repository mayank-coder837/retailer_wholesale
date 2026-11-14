"""Import items (M1.12a), through the catalog service like the Products screen.

An item already in the books (same SKU) is skipped, never changed. The selling price is the base unit's price;
purchase cost comes from bills and opening stock, so a purchase price column is not imported.
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.modules.catalog import products
from app.modules.catalog import service as catalog
from app.modules.catalog.models import BarcodeKind, ProductType
from app.modules.catalog.products import InvalidBarcodeError, InvalidProductError
from app.modules.catalog.schemas import BarcodeIn, ProductIn
from app.modules.imports.kinds.base import Context, Field, Option, Reader, Row, RowResult, from_validation
from app.modules.imports.values import normal_header

TYPE_WORDS = {
    "stock": ProductType.STOCK,
    "goods": ProductType.STOCK,
    "inventory": ProductType.STOCK,
    "item": ProductType.STOCK,
    "مخزون": ProductType.STOCK,
    "بضاعة": ProductType.STOCK,
    "service": ProductType.SERVICE,
    "services": ProductType.SERVICE,
    "خدمة": ProductType.SERVICE,
    "non stock": ProductType.NON_STOCK,
    "nonstock": ProductType.NON_STOCK,
    "consumable": ProductType.NON_STOCK,
    "غير مخزني": ProductType.NON_STOCK,
}
# What traders (and Tally) call the standard units.
UNIT_ALIASES = {
    "nos": "pcs",
    "no": "pcs",
    "pc": "pcs",
    "piece": "pcs",
    "pieces": "pcs",
    "each": "pcs",
    "unit": "pcs",
    "units": "pcs",
    "meter": "m",
    "metre": "m",
    "meters": "m",
    "metres": "m",
    "mtr": "m",
    "kgs": "kg",
    "kilo": "kg",
    "ltr": "l",
    "litre": "l",
    "liter": "l",
    "ctn": "carton",
    "cartons": "carton",
    "doz": "dozen",
    "rolls": "roll",
    "boxes": "box",
    "bags": "bag",
    "قطعة": "pcs",
    "متر": "m",
    "كيلو": "kg",
    "لتر": "l",
    "كرتون": "carton",
    "رول": "roll",
}
FIELDS = (
    Field(
        "sku",
        "SKU",
        "رمز الصنف",
        ("sku", "item code", "product code", "code", "item no", "part number", "رمز", "كود", "رقم الصنف"),
        required=True,
        example="FAB-100",
    ),
    Field(
        "name_en",
        "Name",
        "الاسم",
        (
            "name",
            "item name",
            "product name",
            "description",
            "stock item",
            "اسم",
            "اسم الصنف",
            "الاسم بالانجليزي",
        ),
        required=True,
        example="Cotton fabric",
    ),
    Field(
        "name_ar",
        "Arabic name",
        "الاسم بالعربية",
        ("arabic name", "name arabic", "name ar", "الاسم بالعربي", "اسم عربي"),
        example="قماش قطني",
    ),
    Field(
        "unit",
        "Unit",
        "الوحدة",
        ("unit", "uom", "base unit", "units", "unit of measure", "وحدة", "وحدة القياس"),
        example="m",
        hint_en="pcs when empty",
        hint_ar="قطعة عند تركه فارغا",
    ),
    Field(
        "type",
        "Type",
        "النوع",
        ("type", "item type", "product type", "نوع"),
        example="stock",
        hint_en="stock, service or non-stock",
        hint_ar="مخزني أو خدمة أو غير مخزني",
    ),
    Field(
        "sales_price",
        "Selling price",
        "سعر البيع",
        ("selling price", "sales price", "sale price", "price", "rate", "mrp", "سعر", "سعر البيع"),
        example="12.50",
        hint_en="excluding VAT, per unit",
        hint_ar="بدون ضريبة، للوحدة",
    ),
    Field("barcode", "Barcode", "الباركود", ("barcode", "ean", "upc", "gtin", "باركود", "الباركود")),
    Field(
        "sales_tax_code",
        "VAT on sales",
        "ضريبة المبيعات",
        ("sales tax code", "sales vat", "vat code", "tax code", "vat", "tax", "ضريبة المبيعات"),
        example="SR",
        hint_en="SR, ZR, EX or OS; SR when empty",
        hint_ar="SR أو ZR أو EX أو OS؛ SR عند تركه فارغا",
    ),
    Field(
        "purchase_tax_code",
        "VAT on purchases",
        "ضريبة المشتريات",
        ("purchase tax code", "purchase vat", "ضريبة المشتريات"),
        example="SR",
    ),
    Field("track_batch", "Batches", "الدفعات", ("track batch", "batch", "batches", "دفعات")),
    Field(
        "track_expiry", "Expiry dates", "تواريخ الصلاحية", ("track expiry", "expiry", "expiry date", "صلاحية")
    ),
)
OPTIONS = (
    Option(
        "default_type",
        "When the file does not say, items are",
        "عندما لا يحدد الملف، الأصناف",
        ("stock", "service", "non_stock"),
        "stock",
    ),
)


@dataclass(frozen=True)
class Prepared:
    product: ProductIn
    price: Decimal | None
    barcode: str | None
    barcode_kind: BarcodeKind


def _field_of(path: tuple[str | int, ...]) -> str | None:
    return str(path[0]) if path and path[0] in {f.key for f in FIELDS} else None


def _barcode_kind(code: str) -> BarcodeKind:
    if re.fullmatch(r"\d{13}", code) and products.ean13_check_digit(code[:12]) == code[12]:
        return BarcodeKind.EAN13
    return BarcodeKind.CODE128


def _unit_words(units: dict[str, Any]) -> dict[str, str]:
    words: dict[str, str] = {}
    for code, unit in units.items():
        for word in (code, unit.name_en, unit.name_ar, unit.symbol):
            words.setdefault(normal_header(word), code)
    for alias, code in UNIT_ALIASES.items():
        if code in units:
            words.setdefault(normal_header(alias), code)
    return words


class Products:
    key = "products"
    label_en = "Items"
    label_ar = "الأصناف"
    description_en = "SKU, names, unit, selling price, barcode, VAT code."
    description_ar = "رمز الصنف والأسماء والوحدة وسعر البيع والباركود ورمز الضريبة."
    fields: tuple[Field, ...] = FIELDS
    options: tuple[Option, ...] = OPTIONS
    permissions: tuple[str, ...] = ("product.create",)
    order = 30

    async def check(self, ctx: Context, rows: list[Row]) -> list[RowResult]:
        index = await products.item_index(ctx.session)
        units = await catalog.units_by_code(ctx.session)
        words = _unit_words(units)
        default = ProductType(ctx.options.get("default_type", "stock"))
        seen_sku: dict[str, int] = {}
        seen_barcode: dict[str, int] = {}
        results: list[RowResult] = []
        for row in rows:
            reader = Reader(row, FIELDS)
            sku = reader.text("sku", required=True, limit=50)
            label = sku or "?"
            existing = bool(sku and (sku.lower() in index.products or sku.lower() in index.variants))
            if sku and sku.lower() in seen_sku:
                reader.add(
                    "sku", "duplicate_in_file", f"The SKU {sku} is also on row {seen_sku[sku.lower()]}."
                )
            elif sku:
                seen_sku[sku.lower()] = row.number
            if existing:
                results.append(reader.result("exists", label))
                continue
            results.append(self._build(reader, row, sku, label, default, words, index, seen_barcode))
        return results

    def _build(
        self,
        reader: Reader,
        row: Row,
        sku: str | None,
        label: str,
        default: ProductType,
        words: dict[str, str],
        index: products.ItemIndex,
        seen_barcode: dict[str, int],
    ) -> RowResult:
        name = reader.text("name_en", required=True, limit=100)
        arabic = reader.text("name_ar", limit=100) or name
        name = name or arabic
        kind = reader.choice("type", TYPE_WORDS, default=default)
        unit_text = reader.text("unit")
        unit = (
            words.get(normal_header(unit_text)) if unit_text else ("pcs" if "pcs" in words.values() else None)
        )
        if unit is None:
            reader.add(
                "unit", "unknown_unit", f"'{unit_text or ''}' is not a unit; add it under Units first."
            )
        price = reader.decimal("sales_price")
        if price is not None and (price < 0 or price != price.quantize(Decimal("0.0001"))):
            reader.add("sales_price", "invalid_price", "A price is zero or more, with at most 4 decimals.")
        barcode = reader.text("barcode", limit=48)
        if barcode:
            try:
                products.check_barcode(barcode, _barcode_kind(barcode))
            except InvalidBarcodeError as exc:
                reader.add("barcode", "invalid_barcode", exc.detail or "This is not a valid barcode.")
            if barcode in index.barcodes:
                reader.add("barcode", "barcode_taken", f"The barcode {barcode} belongs to another item.")
            elif barcode in seen_barcode:
                reader.add(
                    "barcode", "duplicate_in_file", f"The barcode is also on row {seen_barcode[barcode]}."
                )
            else:
                seen_barcode[barcode] = row.number
        sales_code = (reader.text("sales_tax_code") or "").upper() or None
        purchase_code = (reader.text("purchase_tax_code") or "").upper() or None
        batch, expiry = reader.flag("track_batch"), reader.flag("track_expiry")
        if reader.problems or sku is None or name is None or arabic is None or unit is None or kind is None:
            return reader.result("new", label)
        try:
            data = ProductIn(
                sku=sku,
                name_en=name,
                name_ar=arabic,
                product_type=kind,
                base_unit=unit,
                track_batch=batch or expiry,
                track_expiry=expiry,
                sales_tax_code=sales_code,
                purchase_tax_code=purchase_code,
            )
            products.check_purchase_code(kind.value, purchase_code)
        except ValidationError as exc:
            from_validation(reader, exc, _field_of)
            return reader.result("new", label)
        except InvalidProductError as exc:
            reader.add("purchase_tax_code", "invalid_tax_code", exc.detail or "This VAT code cannot be used.")
            return reader.result("new", label)
        return reader.result("new", label, Prepared(data, price, barcode, _barcode_kind(barcode or "")))

    async def apply(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        counts = {"created": 0, "skipped": 0, "priced": 0, "barcodes": 0}
        for result in results:
            if result.status != "new":
                counts["skipped"] += 1
                continue
            prepared: Prepared = result.prepared
            product = await products.create_product(ctx.session, ctx.tenant_id, prepared.product)
            counts["created"] += 1
            if prepared.price is not None:
                await products.set_unit_prices(
                    ctx.session,
                    product.id,
                    prepared.product.base_unit,
                    sale_price=prepared.price,
                    min_price=None,
                )
                counts["priced"] += 1
            if prepared.barcode:
                await products.add_barcode(
                    ctx.session,
                    product.id,
                    BarcodeIn(
                        variant_id=product.variants[0].id,
                        unit=prepared.product.base_unit,
                        code=prepared.barcode,
                        kind=prepared.barcode_kind,
                    ),
                )
                counts["barcodes"] += 1
        return counts
