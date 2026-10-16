"""Catalog set-up (M1.2): categories, brands, attributes and custom field definitions.

Industry packs add attributes and custom fields by code / key; anything the tenant already has is left
exactly as the tenant set it. Custom field values on products and variants are validated here against the
definitions.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.core.models import Base
from app.modules.catalog.models import (
    Attribute,
    AttributeValue,
    Brand,
    Category,
    CustomFieldDef,
    CustomFieldEntity,
    CustomFieldType,
)
from app.modules.catalog.schemas import (
    AttributeIn,
    AttributeOut,
    AttributeValueIn,
    AttributeValueOut,
    BrandIn,
    BrandOut,
    BrandPatch,
    CategoryIn,
    CategoryOut,
    CategoryPatch,
    CustomFieldIn,
    CustomFieldOut,
    CustomFieldPatch,
)
from app.modules.platform import packs


class CatalogNotFoundError(NotFoundError):
    code = "catalog_not_found"
    title = "Not found"


class DuplicateNameError(ConflictError):
    code = "duplicate_name"
    title = "This name or code is already used"


class CategoryCycleError(UnprocessableError):
    code = "category_cycle"
    title = "A category cannot be placed inside itself or one of its own subcategories"


class InvalidCustomFieldError(UnprocessableError):
    code = "invalid_custom_field"
    title = "A custom field value is not valid"


async def insert(session: AsyncSession, row: Base, conflict: str) -> None:
    """Adds a row; a unique-constraint clash becomes a 409 instead of aborting the request transaction."""
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateNameError(conflict) from exc


async def flush_unique(session: AsyncSession, conflict: str) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateNameError(conflict) from exc


def _archive(value: bool | None, row: Any) -> None:
    if value is not None:
        row.archived_at = datetime.now(UTC) if value else None


# --- categories ---------------------------------------------------------------------------------------------


async def _category_map(session: AsyncSession) -> dict[uuid.UUID, Category]:
    return {c.id: c for c in await session.scalars(select(Category))}


def _path(category: Category, by_id: dict[uuid.UUID, Category]) -> str:
    names: list[str] = []
    seen: set[uuid.UUID] = set()
    current: Category | None = category
    while current is not None and current.id not in seen:
        seen.add(current.id)
        names.append(current.name_en)
        current = by_id.get(current.parent_id) if current.parent_id else None
    return " / ".join(reversed(names))


def _category_out(category: Category, by_id: dict[uuid.UUID, Category]) -> CategoryOut:
    return CategoryOut(
        id=category.id,
        parent_id=category.parent_id,
        name_en=category.name_en,
        name_ar=category.name_ar,
        path_en=_path(category, by_id),
        archived_at=category.archived_at,
    )


async def list_categories(session: AsyncSession, *, include_archived: bool = False) -> list[CategoryOut]:
    by_id = await _category_map(session)
    rows = [c for c in by_id.values() if include_archived or c.archived_at is None]
    return sorted((_category_out(c, by_id) for c in rows), key=lambda c: c.path_en.lower())


async def get_category(session: AsyncSession, category_id: uuid.UUID) -> Category:
    category = await session.get(Category, category_id)
    if category is None:
        raise CatalogNotFoundError("There is no such category.")
    return category


async def create_category(session: AsyncSession, tenant_id: uuid.UUID, data: CategoryIn) -> CategoryOut:
    if data.parent_id is not None:
        await get_category(session, data.parent_id)
    category = Category(id=new_id(), tenant_id=tenant_id, **data.model_dump())
    await insert(session, category, f"There is already a category '{data.name_en}' here.")
    return _category_out(category, await _category_map(session))


async def update_category(session: AsyncSession, category_id: uuid.UUID, data: CategoryPatch) -> CategoryOut:
    category = await get_category(session, category_id)
    by_id = await _category_map(session)
    if data.move_to_top:
        category.parent_id = None
    elif data.parent_id is not None:
        await get_category(session, data.parent_id)
        current: uuid.UUID | None = data.parent_id
        while current is not None:  # walking up from the new parent must never reach the category itself
            if current == category.id:
                raise CategoryCycleError()
            current = by_id[current].parent_id if current in by_id else None
        category.parent_id = data.parent_id
    for field in ("name_en", "name_ar"):
        if (value := getattr(data, field)) is not None:
            setattr(category, field, value)
    _archive(data.archived, category)
    await flush_unique(session, "There is already a category with this name here.")
    return _category_out(category, await _category_map(session))


# --- brands -------------------------------------------------------------------------------------------------


async def list_brands(session: AsyncSession, *, include_archived: bool = False) -> list[BrandOut]:
    query = select(Brand).order_by(func.lower(Brand.name_en))
    if not include_archived:
        query = query.where(Brand.archived_at.is_(None))
    return [BrandOut.model_validate(b) for b in await session.scalars(query)]


async def get_brand(session: AsyncSession, brand_id: uuid.UUID) -> Brand:
    brand = await session.get(Brand, brand_id)
    if brand is None:
        raise CatalogNotFoundError("There is no such brand.")
    return brand


async def create_brand(session: AsyncSession, tenant_id: uuid.UUID, data: BrandIn) -> BrandOut:
    brand = Brand(id=new_id(), tenant_id=tenant_id, **data.model_dump())
    await insert(session, brand, f"The brand '{data.name_en}' already exists.")
    return BrandOut.model_validate(brand)


async def update_brand(session: AsyncSession, brand_id: uuid.UUID, data: BrandPatch) -> BrandOut:
    brand = await get_brand(session, brand_id)
    for field in ("name_en", "name_ar"):
        if (value := getattr(data, field)) is not None:
            setattr(brand, field, value)
    _archive(data.archived, brand)
    await flush_unique(session, "A brand with this name already exists.")
    return BrandOut.model_validate(brand)


# --- attributes ---------------------------------------------------------------------------------------------


async def _values(
    session: AsyncSession, attribute_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[AttributeValue]]:
    by_attribute: dict[uuid.UUID, list[AttributeValue]] = {a: [] for a in attribute_ids}
    if attribute_ids:
        rows = await session.scalars(
            select(AttributeValue)
            .where(AttributeValue.attribute_id.in_(attribute_ids))
            .order_by(AttributeValue.sort, AttributeValue.value_en)
        )
        for value in rows:
            by_attribute[value.attribute_id].append(value)
    return by_attribute


def _attribute_out(attribute: Attribute, values: list[AttributeValue]) -> AttributeOut:
    return AttributeOut(
        id=attribute.id,
        code=attribute.code,
        name_en=attribute.name_en,
        name_ar=attribute.name_ar,
        values=[AttributeValueOut.model_validate(v) for v in values],
    )


async def list_attributes(session: AsyncSession) -> list[AttributeOut]:
    attributes = list(await session.scalars(select(Attribute).order_by(Attribute.code)))
    values = await _values(session, [a.id for a in attributes])
    return [_attribute_out(a, values[a.id]) for a in attributes]


async def attribute_by_code(session: AsyncSession, code: str) -> Attribute:
    attribute = (await session.scalars(select(Attribute).where(Attribute.code == code))).first()
    if attribute is None:
        raise CatalogNotFoundError(f"There is no attribute '{code}'.")
    return attribute


async def get_attribute(session: AsyncSession, attribute_id: uuid.UUID) -> AttributeOut:
    attribute = await session.get(Attribute, attribute_id)
    if attribute is None:
        raise CatalogNotFoundError("There is no such attribute.")
    return _attribute_out(attribute, (await _values(session, [attribute.id]))[attribute.id])


async def create_attribute(session: AsyncSession, tenant_id: uuid.UUID, data: AttributeIn) -> AttributeOut:
    attribute = Attribute(
        id=new_id(), tenant_id=tenant_id, code=data.code, name_en=data.name_en, name_ar=data.name_ar
    )
    await insert(session, attribute, f"The attribute '{data.code}' already exists.")
    for value in data.values:
        await add_value(session, tenant_id, attribute.id, value)
    return await get_attribute(session, attribute.id)


async def add_value(
    session: AsyncSession, tenant_id: uuid.UUID, attribute_id: uuid.UUID, data: AttributeValueIn
) -> AttributeValueOut:
    if await session.get(Attribute, attribute_id) is None:
        raise CatalogNotFoundError("There is no such attribute.")
    count = await session.scalar(
        select(func.count()).select_from(AttributeValue).where(AttributeValue.attribute_id == attribute_id)
    )
    value = AttributeValue(
        id=new_id(),
        tenant_id=tenant_id,
        attribute_id=attribute_id,
        value_en=data.value_en,
        value_ar=data.value_ar,
        sort=count or 0,
    )
    await insert(session, value, f"'{data.value_en}' is already a value of this attribute.")
    return AttributeValueOut.model_validate(value)


# --- custom fields ------------------------------------------------------------------------------------------


def _field_out(row: CustomFieldDef) -> CustomFieldOut:
    return CustomFieldOut.model_validate(
        {
            "id": row.id,
            "entity_type": row.entity_type,
            "key": row.key,
            "label_en": row.label_en,
            "label_ar": row.label_ar,
            "data_type": row.data_type,
            "options": row.options,
            "required": row.required,
            "archived_at": row.archived_at,
        }
    )


def _check_options(data_type: str, options: list[Any] | None) -> None:
    choice = data_type in (CustomFieldType.SELECT, CustomFieldType.MULTISELECT)
    if choice and not options:
        raise InvalidCustomFieldError("A select field needs at least one option.")
    if not choice and options:
        raise InvalidCustomFieldError("Only select fields have options.")


async def list_custom_fields(session: AsyncSession, entity_type: str | None = None) -> list[CustomFieldOut]:
    query = select(CustomFieldDef).order_by(CustomFieldDef.entity_type, CustomFieldDef.key)
    if entity_type is not None:
        query = query.where(CustomFieldDef.entity_type == entity_type)
    return [_field_out(r) for r in await session.scalars(query)]


async def create_custom_field(
    session: AsyncSession, tenant_id: uuid.UUID, data: CustomFieldIn
) -> CustomFieldOut:
    _check_options(data.data_type, data.options)
    row = CustomFieldDef(
        id=new_id(),
        tenant_id=tenant_id,
        entity_type=data.entity_type.value,
        key=data.key,
        label_en=data.label_en,
        label_ar=data.label_ar,
        data_type=data.data_type.value,
        options=[o.model_dump() for o in data.options] if data.options else None,
        required=data.required,
    )
    await insert(session, row, f"The field '{data.key}' already exists for {data.entity_type.value}.")
    return _field_out(row)


async def update_custom_field(
    session: AsyncSession, field_id: uuid.UUID, data: CustomFieldPatch
) -> CustomFieldOut:
    row = await session.get(CustomFieldDef, field_id)
    if row is None:
        raise CatalogNotFoundError("There is no such custom field.")
    if data.options is not None:
        _check_options(row.data_type, data.options)
        row.options = [o.model_dump() for o in data.options]
    for field in ("label_en", "label_ar", "required"):
        if (value := getattr(data, field)) is not None:
            setattr(row, field, value)
    _archive(data.archived, row)
    await session.flush()
    return _field_out(row)


def _coerce(definition: CustomFieldDef, value: Any) -> Any:
    """One value checked against its definition; returns what is stored (JSON, exact decimals as text)."""
    kind, key = definition.data_type, definition.key
    allowed = {o["value"] for o in definition.options or []}
    try:
        if kind == CustomFieldType.TEXT:
            if not isinstance(value, str) or len(value) > 500:
                raise ValueError("text of at most 500 characters")
            return value
        if kind == CustomFieldType.NUMBER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("a whole number")
            return value
        if kind == CustomFieldType.DECIMAL:
            if isinstance(value, (bool, float)):  # floats are never accepted for exact values (rule 2)
                raise ValueError("a decimal number written as text or a whole number")
            number = Decimal(str(value))
            if not number.is_finite():
                raise ValueError("a decimal number")
            return str(number)
        if kind == CustomFieldType.DATE:
            return date.fromisoformat(value).isoformat()
        if kind == CustomFieldType.BOOL:
            if not isinstance(value, bool):
                raise ValueError("true or false")
            return value
        if kind == CustomFieldType.SELECT:
            if value not in allowed:
                raise ValueError(f"one of {', '.join(sorted(allowed))}")
            return value
        if kind == CustomFieldType.MULTISELECT:
            if not isinstance(value, list) or not set(value) <= allowed:
                raise ValueError(f"a list of {', '.join(sorted(allowed))}")
            return sorted(set(value))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise InvalidCustomFieldError(f"'{key}' must be {exc}.", field=key) from exc
    raise InvalidCustomFieldError(f"'{key}' has an unknown type.", field=key)


async def validate_custom_fields(
    session: AsyncSession, entity: CustomFieldEntity, values: dict[str, Any], *, partial: bool = False
) -> dict[str, Any]:
    """Checks every value against the tenant's definitions for the entity: types, required, options."""
    definitions = {
        d.key: d
        for d in await session.scalars(
            select(CustomFieldDef).where(
                CustomFieldDef.entity_type == entity.value, CustomFieldDef.archived_at.is_(None)
            )
        )
    }
    unknown = sorted(set(values) - set(definitions))
    if unknown:
        raise InvalidCustomFieldError(f"Unknown custom field: {', '.join(unknown)}.", field=unknown[0])
    cleaned = {key: _coerce(definitions[key], value) for key, value in values.items() if value is not None}
    if not partial:
        missing = sorted(k for k, d in definitions.items() if d.required and k not in cleaned)
        if missing:
            raise InvalidCustomFieldError(f"Required: {', '.join(missing)}.", field=missing[0])
    return cleaned


# --- industry packs -----------------------------------------------------------------------------------------


async def _apply_pack_attributes(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    _business_id: uuid.UUID,
    attributes: Sequence[packs.Attribute],
) -> None:
    existing = {a.code: a for a in await list_attributes(session)}
    for item in attributes:
        if item.code not in existing:
            await create_attribute(
                session,
                tenant_id,
                AttributeIn(
                    code=item.code,
                    name_en=item.name_en,
                    name_ar=item.name_ar,
                    values=[AttributeValueIn(value_en=v.value_en, value_ar=v.value_ar) for v in item.values],
                ),
            )
            continue
        have = {v.value_en.lower() for v in existing[item.code].values}
        for value in item.values:
            if value.value_en.lower() not in have:
                await add_value(
                    session,
                    tenant_id,
                    existing[item.code].id,
                    AttributeValueIn(value_en=value.value_en, value_ar=value.value_ar),
                )


async def _apply_pack_custom_fields(
    session: AsyncSession, tenant_id: uuid.UUID, _business_id: uuid.UUID, fields: Sequence[packs.CustomField]
) -> None:
    existing = {(f.entity_type.value, f.key) for f in await list_custom_fields(session)}
    for item in fields:
        if (item.entity_type, item.key) in existing:
            continue
        await create_custom_field(
            session,
            tenant_id,
            CustomFieldIn.model_validate(
                {
                    "entity_type": item.entity_type,
                    "key": item.key,
                    "label_en": item.label_en,
                    "label_ar": item.label_ar,
                    "data_type": item.data_type,
                    "options": item.options,
                }
            ),
        )


packs.register_applier("attributes", _apply_pack_attributes)
packs.register_applier("custom_fields", _apply_pack_custom_fields)
