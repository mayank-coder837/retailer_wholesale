"""Products, their units, variants and barcodes (M1.2). Other modules call these functions, never the tables.

- Every product has its base unit as a product unit with factor 1, plus any other units (1 roll = 50 m).
  Factors are exact (L3) and fixed once added.
- Every product has at least one variant: one default variant without attributes, or one per combination of
  the chosen attribute values (colour x width). Stock, prices and document lines reference variants.
- Barcodes belong to a variant and one of the product's units; a code is unique per tenant, so a scan finds
  exactly one variant and unit.
"""

import itertools
import re
import secrets
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.modules.catalog import setup, uom
from app.modules.catalog.models import (
    Attribute,
    AttributeValue,
    Barcode,
    BarcodeKind,
    CustomFieldEntity,
    Product,
    ProductAttribute,
    ProductUnit,
    Unit,
    UnitFamily,
    Variant,
    VariantAttributeValue,
)
from app.modules.catalog.schemas import (
    BarcodeIn,
    BarcodeLookupOut,
    BarcodeOut,
    ProductIn,
    ProductOut,
    ProductPatch,
    ProductSummary,
    ProductUnitIn,
    ProductUnitOut,
    ProductUnitPatch,
    VariantIn,
    VariantOut,
    VariantPatch,
    VariantValueOut,
)


class ProductNotFoundError(NotFoundError):
    code = "product_not_found"
    title = "There is no such product"


class BarcodeNotFoundError(NotFoundError):
    code = "barcode_not_found"
    title = "No product has this barcode"


class DuplicateSkuError(ConflictError):
    code = "duplicate_sku"
    title = "This SKU is already used"


class BarcodeTakenError(ConflictError):
    code = "barcode_taken"
    title = "This barcode is already used by another product"


class DuplicateVariantError(ConflictError):
    code = "duplicate_variant"
    title = "A variant with these values already exists"


class InvalidProductError(UnprocessableError):
    code = "invalid_product"
    title = "The product is not valid"


class InvalidBarcodeError(UnprocessableError):
    code = "invalid_barcode"
    title = "The barcode is not valid"


# --- units --------------------------------------------------------------------------------------------------


async def _unit(session: AsyncSession, code: str) -> Unit:
    unit = (await session.scalars(select(Unit).where(Unit.code == code))).first()
    if unit is None or unit.archived_at is not None:
        raise InvalidProductError(f"There is no unit '{code}'.", field="unit")
    return unit


async def _family_code(session: AsyncSession, unit: Unit) -> str:
    family = await session.get(UnitFamily, unit.family_id)
    return family.code if family else ""


async def _factor_for(session: AsyncSession, unit: Unit, base: Unit, given: Decimal | None) -> Decimal:
    """The factor to the base unit: as given, or the standard one when both units are of one family."""
    try:
        if given is not None:
            return uom.check_factor(given)
        unit_family, base_family = await _family_code(session, unit), await _family_code(session, base)
        if unit_family != base_family:
            raise InvalidProductError(
                f"Say how many {base.code} one {unit.code} is (e.g. 1 roll = 50 m).", field="factor_to_base"
            )
        return uom.standard_factor(
            uom.UnitRef(unit.code, unit_family, unit.factor_to_family_base, unit.decimals),
            uom.UnitRef(base.code, base_family, base.factor_to_family_base, base.decimals),
        )
    except uom.UomError as exc:
        raise InvalidProductError(str(exc), field="factor_to_base") from exc


async def _add_unit(
    session: AsyncSession, product: Product, base: Unit, data: ProductUnitIn, units: dict[str, Unit]
) -> ProductUnit:
    unit = units.get(data.unit) or await _unit(session, data.unit)
    if unit.id == base.id:
        raise InvalidProductError(f"{unit.code} is already the base unit.", field="unit")
    row = ProductUnit(
        id=new_id(),
        tenant_id=product.tenant_id,
        product_id=product.id,
        unit_id=unit.id,
        factor_to_base=await _factor_for(session, unit, base, data.factor_to_base),
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise InvalidProductError(f"{unit.code} is already a unit of this product.", field="unit") from exc
    return row


async def _set_defaults(
    session: AsyncSession, product_id: uuid.UUID, purchase: uuid.UUID | None, sales: uuid.UUID | None
) -> None:
    """Exactly one purchase and one sales default per product (partial unique indexes enforce it)."""
    rows = list(await session.scalars(select(ProductUnit).where(ProductUnit.product_id == product_id)))
    for flag, wanted in (("is_purchase_default", purchase), ("is_sales_default", sales)):
        if wanted is None:
            continue
        for row in rows:
            setattr(row, flag, False)
        await session.flush()
        for row in rows:
            if row.id == wanted:
                setattr(row, flag, True)
        await session.flush()


# --- reading ------------------------------------------------------------------------------------------------


async def get_product_row(session: AsyncSession, product_id: uuid.UUID) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError()
    return product


async def _variant_values(
    session: AsyncSession, variant_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[VariantValueOut]]:
    found: dict[uuid.UUID, list[VariantValueOut]] = {v: [] for v in variant_ids}
    if not variant_ids:
        return found
    rows = await session.execute(
        select(
            VariantAttributeValue.variant_id, Attribute.code, AttributeValue.value_en, AttributeValue.value_ar
        )
        .join(Attribute, Attribute.id == VariantAttributeValue.attribute_id)
        .join(AttributeValue, AttributeValue.id == VariantAttributeValue.value_id)
        .where(VariantAttributeValue.variant_id.in_(variant_ids))
        .order_by(Attribute.code)
    )
    for variant_id, code, value_en, value_ar in rows:
        found[variant_id].append(VariantValueOut(attribute=code, value_en=value_en, value_ar=value_ar))
    return found


async def get_product(session: AsyncSession, product_id: uuid.UUID) -> ProductOut:
    product = await get_product_row(session, product_id)
    units = (
        await session.execute(
            select(ProductUnit, Unit)
            .join(Unit, Unit.id == ProductUnit.unit_id)
            .where(ProductUnit.product_id == product.id)
            .order_by(ProductUnit.factor_to_base, Unit.code)
        )
    ).all()
    codes = {unit.id: unit.code for _, unit in units}
    attributes = list(
        await session.scalars(
            select(Attribute.code)
            .join(ProductAttribute, ProductAttribute.attribute_id == Attribute.id)
            .where(ProductAttribute.product_id == product.id)
            .order_by(Attribute.code)
        )
    )
    variants = list(
        await session.scalars(select(Variant).where(Variant.product_id == product.id).order_by(Variant.sku))
    )
    values = await _variant_values(session, [v.id for v in variants])
    barcodes = list(
        await session.scalars(
            select(Barcode).where(Barcode.variant_id.in_([v.id for v in variants])).order_by(Barcode.code)
        )
    )
    return ProductOut(
        id=product.id,
        sku=product.sku,
        name_en=product.name_en,
        name_ar=product.name_ar,
        description=product.description,
        product_type=product.product_type,
        category_id=product.category_id,
        brand_id=product.brand_id,
        base_unit=codes[product.base_unit_id],
        track_batch=product.track_batch,
        track_expiry=product.track_expiry,
        track_serial=product.track_serial,
        custom_fields=product.custom_fields,
        archived_at=product.archived_at,
        units=[
            ProductUnitOut(
                unit=unit.code,
                name_en=unit.name_en,
                name_ar=unit.name_ar,
                symbol=unit.symbol,
                decimals=unit.decimals,
                factor_to_base=pu.factor_to_base,
                is_base=unit.id == product.base_unit_id,
                is_purchase_default=pu.is_purchase_default,
                is_sales_default=pu.is_sales_default,
            )
            for pu, unit in units
        ],
        attributes=attributes,
        variants=[
            VariantOut(
                id=v.id,
                sku=v.sku,
                values=values[v.id],
                custom_fields=v.custom_fields,
                archived_at=v.archived_at,
            )
            for v in variants
        ],
        barcodes=[
            BarcodeOut(id=b.id, code=b.code, kind=b.kind, unit=codes[b.unit_id], variant_id=b.variant_id)
            for b in barcodes
        ],
    )


async def list_products(
    session: AsyncSession,
    *,
    q: str | None = None,
    category_id: uuid.UUID | None = None,
    brand_id: uuid.UUID | None = None,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[ProductSummary]:
    variant_count = (
        select(func.count()).select_from(Variant).where(Variant.product_id == Product.id).scalar_subquery()
    )
    query = (
        select(Product, Unit.code, variant_count)
        .join(Unit, Unit.id == Product.base_unit_id)
        .order_by(func.lower(Product.name_en), Product.sku)
        .limit(limit)
        .offset(offset)
    )
    if q:
        pattern = f"%{q.strip()}%"
        by_variant = select(Variant.product_id).where(Variant.sku.ilike(pattern))
        by_barcode = (
            select(Variant.product_id)
            .join(Barcode, Barcode.variant_id == Variant.id)
            .where(Barcode.code == q.strip())
        )
        query = query.where(
            or_(
                Product.sku.ilike(pattern),
                Product.name_en.ilike(pattern),
                Product.name_ar.ilike(pattern),
                Product.id.in_(by_variant),
                Product.id.in_(by_barcode),
            )
        )
    if category_id is not None:
        query = query.where(Product.category_id == category_id)
    if brand_id is not None:
        query = query.where(Product.brand_id == brand_id)
    if not include_archived:
        query = query.where(Product.archived_at.is_(None))
    return [
        ProductSummary(
            id=p.id,
            sku=p.sku,
            name_en=p.name_en,
            name_ar=p.name_ar,
            product_type=p.product_type,
            base_unit=base,
            category_id=p.category_id,
            brand_id=p.brand_id,
            variant_count=count,
            archived_at=p.archived_at,
        )
        for p, base, count in await session.execute(query)
    ]


# --- creating -----------------------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", text.upper())


async def _new_variant(
    session: AsyncSession,
    product: Product,
    values: Sequence[AttributeValue],
    *,
    sku: str | None = None,
    custom_fields: dict[str, Any] | None = None,
    number: int = 1,
) -> Variant:
    if sku is None:
        parts = [_slug(v.value_en) for v in values]
        sku = "-".join([product.sku, *[p or str(number) for p in parts]]) if values else product.sku
    variant = Variant(
        id=new_id(),
        tenant_id=product.tenant_id,
        product_id=product.id,
        sku=sku,
        custom_fields=custom_fields or {},
    )
    try:
        async with session.begin_nested():
            session.add(variant)
            await session.flush()
            for value in values:
                session.add(
                    VariantAttributeValue(
                        id=new_id(),
                        tenant_id=product.tenant_id,
                        variant_id=variant.id,
                        attribute_id=value.attribute_id,
                        value_id=value.id,
                    )
                )
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateSkuError(f"The SKU '{sku}' is already used.") from exc
    return variant


async def _values_for(
    session: AsyncSession, attribute: Attribute, wanted: Sequence[str]
) -> list[AttributeValue]:
    rows = {
        v.value_en.lower(): v
        for v in await session.scalars(
            select(AttributeValue).where(AttributeValue.attribute_id == attribute.id)
        )
    }
    missing = [w for w in wanted if w.lower() not in rows]
    if missing:
        raise InvalidProductError(
            f"{attribute.code} has no value {', '.join(missing)}; add it to the attribute first.",
            field="attributes",
        )
    return [rows[w.lower()] for w in dict.fromkeys(wanted)]


async def create_product(session: AsyncSession, tenant_id: uuid.UUID, data: ProductIn) -> ProductOut:
    base = await _unit(session, data.base_unit)
    if data.category_id is not None:
        await setup.get_category(session, data.category_id)
    if data.brand_id is not None:
        await setup.get_brand(session, data.brand_id)
    custom = await setup.validate_custom_fields(session, CustomFieldEntity.PRODUCT, data.custom_fields)
    product = Product(
        id=new_id(),
        tenant_id=tenant_id,
        sku=data.sku,
        name_en=data.name_en,
        name_ar=data.name_ar,
        description=data.description,
        product_type=data.product_type.value,
        category_id=data.category_id,
        brand_id=data.brand_id,
        base_unit_id=base.id,
        track_batch=data.track_batch,
        track_expiry=data.track_expiry,
        track_serial=data.track_serial,
        custom_fields=custom,
    )
    try:
        async with session.begin_nested():
            session.add(product)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateSkuError(f"The SKU '{data.sku}' is already used.") from exc

    base_row = ProductUnit(
        id=new_id(),
        tenant_id=tenant_id,
        product_id=product.id,
        unit_id=base.id,
        factor_to_base=Decimal("1.000000"),
    )
    session.add(base_row)
    await session.flush()
    purchase, sales = base_row.id, base_row.id
    for unit_in in data.units:
        row = await _add_unit(session, product, base, unit_in, {})
        purchase = row.id if unit_in.is_purchase_default else purchase
        sales = row.id if unit_in.is_sales_default else sales
    await _set_defaults(session, product.id, purchase, sales)

    choices: list[list[AttributeValue]] = []
    for choice in data.attributes:
        attribute = await setup.attribute_by_code(session, choice.attribute)
        session.add(
            ProductAttribute(
                id=new_id(), tenant_id=tenant_id, product_id=product.id, attribute_id=attribute.id
            )
        )
        choices.append(await _values_for(session, attribute, choice.values))
    try:
        await session.flush()
    except IntegrityError as exc:
        raise InvalidProductError("An attribute is listed twice.", field="attributes") from exc
    for number, combination in enumerate(itertools.product(*choices), start=1):
        await _new_variant(session, product, combination, number=number)
    return await get_product(session, product.id)


async def update_product(session: AsyncSession, product_id: uuid.UUID, data: ProductPatch) -> ProductOut:
    product = await get_product_row(session, product_id)
    fields = data.model_fields_set
    if "category_id" in fields:
        if data.category_id is not None:
            await setup.get_category(session, data.category_id)
        product.category_id = data.category_id
    if "brand_id" in fields:
        if data.brand_id is not None:
            await setup.get_brand(session, data.brand_id)
        product.brand_id = data.brand_id
    if data.custom_fields is not None:
        product.custom_fields = await setup.validate_custom_fields(
            session, CustomFieldEntity.PRODUCT, data.custom_fields
        )
    for field in ("name_en", "name_ar", "description", "track_batch", "track_expiry", "track_serial"):
        if field in fields and (value := getattr(data, field)) is not None:
            setattr(product, field, value)
    if data.archived is not None:
        product.archived_at = datetime.now(UTC) if data.archived else None
    await session.flush()
    return await get_product(session, product.id)


# --- product units ------------------------------------------------------------------------------------------


async def add_product_unit(session: AsyncSession, product_id: uuid.UUID, data: ProductUnitIn) -> ProductOut:
    product = await get_product_row(session, product_id)
    base = await session.get(Unit, product.base_unit_id)
    assert base is not None
    row = await _add_unit(session, product, base, data, {})
    await _set_defaults(
        session,
        product.id,
        row.id if data.is_purchase_default else None,
        row.id if data.is_sales_default else None,
    )
    return await get_product(session, product.id)


async def _product_unit(session: AsyncSession, product: Product, code: str) -> ProductUnit:
    unit = await _unit(session, code)
    row = (
        await session.scalars(
            select(ProductUnit).where(ProductUnit.product_id == product.id, ProductUnit.unit_id == unit.id)
        )
    ).first()
    if row is None:
        raise InvalidProductError(f"{code} is not a unit of this product.", field="unit")
    return row


async def update_product_unit(
    session: AsyncSession, product_id: uuid.UUID, code: str, data: ProductUnitPatch
) -> ProductOut:
    product = await get_product_row(session, product_id)
    row = await _product_unit(session, product, code)
    await _set_defaults(
        session,
        product.id,
        row.id if data.is_purchase_default else None,
        row.id if data.is_sales_default else None,
    )
    return await get_product(session, product.id)


async def remove_product_unit(session: AsyncSession, product_id: uuid.UUID, code: str) -> ProductOut:
    product = await get_product_row(session, product_id)
    row = await _product_unit(session, product, code)
    if row.unit_id == product.base_unit_id:
        raise InvalidProductError("The base unit cannot be removed: stock is kept in it.", field="unit")
    used = await session.scalar(
        select(func.count())
        .select_from(Barcode)
        .join(Variant, Variant.id == Barcode.variant_id)
        .where(Variant.product_id == product.id, Barcode.unit_id == row.unit_id)
    )
    if used:
        raise InvalidProductError(f"Barcodes use {code}; remove them first.", field="unit")
    was_default = row.is_purchase_default or row.is_sales_default
    await session.delete(row)
    await session.flush()
    if was_default:  # fall back to the base unit
        base_unit = await session.get(Unit, product.base_unit_id)
        assert base_unit is not None
        base_row = await _product_unit(session, product, base_unit.code)
        await _set_defaults(
            session,
            product.id,
            base_row.id if row.is_purchase_default else None,
            base_row.id if row.is_sales_default else None,
        )
    return await get_product(session, product.id)


# --- variants -----------------------------------------------------------------------------------------------


async def add_variant(session: AsyncSession, product_id: uuid.UUID, data: VariantIn) -> ProductOut:
    product = await get_product_row(session, product_id)
    attributes = list(
        await session.scalars(
            select(Attribute)
            .join(ProductAttribute, ProductAttribute.attribute_id == Attribute.id)
            .where(ProductAttribute.product_id == product.id)
            .order_by(Attribute.code)
        )
    )
    if not attributes:
        raise InvalidProductError("This product has no attributes to vary by.", field="values")
    if set(data.values) != {a.code for a in attributes}:
        raise InvalidProductError(
            f"Give one value for each of: {', '.join(a.code for a in attributes)}.", field="values"
        )
    chosen = [(await _values_for(session, a, [data.values[a.code]]))[0] for a in attributes]
    existing = await _variant_values(
        session, list(await session.scalars(select(Variant.id).where(Variant.product_id == product.id)))
    )
    wanted = {(a.code, v.value_en) for a, v in zip(attributes, chosen, strict=True)}
    if any({(x.attribute, x.value_en) for x in values} == wanted for values in existing.values()):
        raise DuplicateVariantError()
    custom = await setup.validate_custom_fields(session, CustomFieldEntity.VARIANT, data.custom_fields)
    await _new_variant(session, product, chosen, sku=data.sku, custom_fields=custom, number=len(existing) + 1)
    return await get_product(session, product.id)


async def update_variant(session: AsyncSession, variant_id: uuid.UUID, data: VariantPatch) -> ProductOut:
    variant = await session.get(Variant, variant_id)
    if variant is None:
        raise ProductNotFoundError("There is no such variant.")
    if data.sku is not None:
        variant.sku = data.sku
    if data.custom_fields is not None:
        variant.custom_fields = await setup.validate_custom_fields(
            session, CustomFieldEntity.VARIANT, data.custom_fields
        )
    if data.archived is not None:
        variant.archived_at = datetime.now(UTC) if data.archived else None
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateSkuError(f"The SKU '{data.sku}' is already used.") from exc
    return await get_product(session, variant.product_id)


# --- barcodes -----------------------------------------------------------------------------------------------


def ean13_check_digit(first_twelve: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first_twelve))
    return str((10 - total % 10) % 10)


def check_barcode(code: str, kind: BarcodeKind) -> str:
    code = code.strip()
    if kind in (BarcodeKind.EAN13, BarcodeKind.INTERNAL) and re.fullmatch(r"\d{13}", code):
        if ean13_check_digit(code[:12]) != code[12]:
            raise InvalidBarcodeError(
                f"{code} is not a valid EAN-13: the last digit should be {ean13_check_digit(code[:12])}."
            )
        return code
    if kind is BarcodeKind.EAN13:
        raise InvalidBarcodeError("An EAN-13 barcode has exactly 13 digits.")
    if kind is BarcodeKind.SCALE:
        if not re.fullmatch(r"\d{12,13}", code):
            raise InvalidBarcodeError("A scale barcode has 12 or 13 digits.")
        return code
    if not re.fullmatch(r"[\x21-\x7e]{1,48}", code):  # printable ASCII, no spaces (Code 128)
        raise InvalidBarcodeError("A barcode uses printable characters without spaces, at most 48.")
    return code


def _generated() -> str:
    """An internal EAN-13 in the in-store range (starts with 2), so it never clashes with a maker's code."""
    first = "2" + "".join(secrets.choice("0123456789") for _ in range(11))
    return first + ean13_check_digit(first)


async def add_barcode(session: AsyncSession, product_id: uuid.UUID, data: BarcodeIn) -> ProductOut:
    product = await get_product_row(session, product_id)
    variant = await session.get(Variant, data.variant_id)
    if variant is None or variant.product_id != product.id:
        raise InvalidBarcodeError("The variant is not one of this product's variants.", field="variant_id")
    unit_row = await _product_unit(session, product, data.unit)
    generate = data.code is None
    for _attempt in range(5 if generate else 1):
        code = _generated() if generate else check_barcode(data.code or "", data.kind)
        barcode = Barcode(
            id=new_id(),
            tenant_id=product.tenant_id,
            variant_id=variant.id,
            unit_id=unit_row.unit_id,
            code=code,
            kind=(BarcodeKind.INTERNAL if generate else data.kind).value,
        )
        try:
            async with session.begin_nested():
                session.add(barcode)
                await session.flush()
            return await get_product(session, product.id)
        except IntegrityError as exc:
            if not generate:
                raise BarcodeTakenError(f"The barcode {code} is already used.") from exc
    raise BarcodeTakenError("Could not generate a free barcode; try again.")


async def remove_barcode(session: AsyncSession, barcode_id: uuid.UUID) -> ProductOut:
    barcode = await session.get(Barcode, barcode_id)
    if barcode is None:
        raise BarcodeNotFoundError("There is no such barcode.")
    variant = await session.get(Variant, barcode.variant_id)
    assert variant is not None
    await session.delete(barcode)
    await session.flush()
    return await get_product(session, variant.product_id)


async def lookup_barcode(session: AsyncSession, code: str) -> BarcodeLookupOut:
    """A scan: the exact variant and unit, and how many base units one scanned item is."""
    found = (
        await session.execute(
            select(Barcode, Variant, Product, Unit, ProductUnit)
            .join(Variant, Variant.id == Barcode.variant_id)
            .join(Product, Product.id == Variant.product_id)
            .join(Unit, Unit.id == Barcode.unit_id)
            .join(
                ProductUnit, (ProductUnit.product_id == Product.id) & (ProductUnit.unit_id == Barcode.unit_id)
            )
            .where(Barcode.code == code.strip())
        )
    ).first()
    if found is None:
        raise BarcodeNotFoundError()
    barcode, variant, product, unit, product_unit = found
    base = await session.get(Unit, product.base_unit_id)
    assert base is not None
    return BarcodeLookupOut(
        code=barcode.code,
        kind=barcode.kind,
        product_id=product.id,
        product_sku=product.sku,
        product_name_en=product.name_en,
        product_name_ar=product.name_ar,
        variant_id=variant.id,
        variant_sku=variant.sku,
        values=(await _variant_values(session, [variant.id]))[variant.id],
        unit=unit.code,
        factor_to_base=product_unit.factor_to_base,
        base_unit=base.code,
    )
