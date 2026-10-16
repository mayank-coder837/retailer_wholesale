"""Pricing business rules (M1.3): price lists, promotions, unit prices and the price quote.

The quote gathers what each step of the engine can find — last sale, customer and group lists, the business's
standard list, promotions, the unit's base and minimum price — and lets the pure engine decide. Products are
read through the catalog module's service, never its tables.
"""

import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.modules.catalog import products as catalog
from app.modules.catalog import service as units
from app.modules.catalog.schemas import ProductOut
from app.modules.pricing import engine
from app.modules.pricing.models import PriceList, PriceListItem, Promotion, PromotionScope
from app.modules.pricing.schemas import (
    PriceListIn,
    PriceListItemIn,
    PriceListItemOut,
    PriceListOut,
    PriceListPatch,
    PromotionIn,
    PromotionOut,
    PromotionPatch,
    QuoteIn,
    QuoteOut,
    UnitPricesIn,
)
from app.modules.tenancy import service as tenancy

DUBAI = ZoneInfo("Asia/Dubai")


class PriceListNotFoundError(NotFoundError):
    code = "price_list_not_found"
    title = "There is no such price list"


class PromotionNotFoundError(NotFoundError):
    code = "promotion_not_found"
    title = "There is no such promotion"


class DuplicatePriceListError(ConflictError):
    code = "duplicate_price_list"
    title = "A price list with this name, or a standard list, already exists for this business"


class InvalidPricingError(UnprocessableError):
    code = "invalid_pricing"
    title = "The price details are not valid"


def _valid_on(row: PriceList | Promotion, day: date) -> bool:
    return (
        row.archived_at is None
        and (row.valid_from is None or row.valid_from <= day)
        and (row.valid_to is None or row.valid_to >= day)
    )


# --- unit prices -------------------------------------------------------------------------------------------


async def set_unit_prices(
    session: AsyncSession, product_id: uuid.UUID, unit: str, data: UnitPricesIn
) -> ProductOut:
    if data.sale_price is not None and data.min_price is not None and data.min_price > data.sale_price:
        raise InvalidPricingError("The minimum price is above the sale price.", field="min_price")
    try:
        return await catalog.set_unit_prices(
            session, product_id, unit, sale_price=data.sale_price, min_price=data.min_price
        )
    except catalog.InvalidProductError as exc:
        raise InvalidPricingError(str(exc), field="unit") from exc


# --- price lists -------------------------------------------------------------------------------------------


async def _flush_unique(session: AsyncSession) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise DuplicatePriceListError() from exc


async def get_price_list(session: AsyncSession, price_list_id: uuid.UUID) -> PriceList:
    row = await session.get(PriceList, price_list_id)
    if row is None:
        raise PriceListNotFoundError()
    return row


async def list_price_lists(session: AsyncSession, business_id: uuid.UUID | None = None) -> list[PriceListOut]:
    query = select(PriceList).order_by(PriceList.is_standard.desc(), PriceList.name_en)
    if business_id is not None:
        query = query.where(PriceList.business_id == business_id)
    return [PriceListOut.model_validate(r) for r in await session.scalars(query)]


async def create_price_list(session: AsyncSession, tenant_id: uuid.UUID, data: PriceListIn) -> PriceListOut:
    await tenancy.get_business(session, data.business_id)
    row = PriceList(id=new_id(), tenant_id=tenant_id, **data.model_dump())
    session.add(row)
    await _flush_unique(session)
    return PriceListOut.model_validate(row)


async def update_price_list(
    session: AsyncSession, price_list_id: uuid.UUID, data: PriceListPatch
) -> PriceListOut:
    row = await get_price_list(session, price_list_id)
    for field in ("name_en", "name_ar", "is_standard"):
        if (value := getattr(data, field)) is not None:
            setattr(row, field, value)
    for field in ("valid_from", "valid_to"):
        if field in data.model_fields_set:
            setattr(row, field, getattr(data, field))
    if row.valid_from and row.valid_to and row.valid_to < row.valid_from:
        raise InvalidPricingError("valid_to is before valid_from.", field="valid_to")
    if data.archived is not None:
        row.archived_at = datetime.now(UTC) if data.archived else None
    await _flush_unique(session)
    return PriceListOut.model_validate(row)


async def _unit_of(
    session: AsyncSession, variant_id: uuid.UUID, unit: str
) -> tuple[catalog.VariantContext, uuid.UUID]:
    try:
        context = await catalog.variant_context(session, variant_id)
    except catalog.ProductNotFoundError as exc:
        raise InvalidPricingError("There is no such variant.", field="variant_id") from exc
    if unit not in context.units:
        raise InvalidPricingError(f"{unit} is not a unit of this product.", field="unit")
    return context, context.units[unit].unit_id


async def list_items(session: AsyncSession, price_list_id: uuid.UUID) -> list[PriceListItemOut]:
    await get_price_list(session, price_list_id)
    codes = {u.id: u.code for u in await units.list_units(session, include_archived=True)}
    rows = await session.scalars(
        select(PriceListItem)
        .where(PriceListItem.price_list_id == price_list_id)
        .order_by(PriceListItem.variant_id, PriceListItem.unit_id, PriceListItem.min_qty)
    )
    return [
        PriceListItemOut(
            id=r.id, variant_id=r.variant_id, unit=codes[r.unit_id], min_qty=r.min_qty, price=r.price
        )
        for r in rows
    ]


async def put_item(
    session: AsyncSession, tenant_id: uuid.UUID, price_list_id: uuid.UUID, data: PriceListItemIn
) -> list[PriceListItemOut]:
    """Sets a variant's price in a unit from a quantity; the same variant, unit and quantity is replaced."""
    await get_price_list(session, price_list_id)
    _, unit_id = await _unit_of(session, data.variant_id, data.unit)
    existing = (
        await session.scalars(
            select(PriceListItem).where(
                PriceListItem.price_list_id == price_list_id,
                PriceListItem.variant_id == data.variant_id,
                PriceListItem.unit_id == unit_id,
                PriceListItem.min_qty == data.min_qty,
            )
        )
    ).first()
    if existing is not None:
        existing.price = data.price
    else:
        session.add(
            PriceListItem(
                id=new_id(),
                tenant_id=tenant_id,
                price_list_id=price_list_id,
                variant_id=data.variant_id,
                unit_id=unit_id,
                min_qty=data.min_qty,
                price=data.price,
            )
        )
    await session.flush()
    return await list_items(session, price_list_id)


async def delete_item(
    session: AsyncSession, price_list_id: uuid.UUID, item_id: uuid.UUID
) -> list[PriceListItemOut]:
    row = await session.get(PriceListItem, item_id)
    if row is None or row.price_list_id != price_list_id:
        raise PriceListNotFoundError("There is no such price in this list.")
    await session.delete(row)
    await session.flush()
    return await list_items(session, price_list_id)


# --- promotions --------------------------------------------------------------------------------------------


async def list_promotions(session: AsyncSession, business_id: uuid.UUID | None = None) -> list[PromotionOut]:
    query = select(Promotion).order_by(Promotion.created_at.desc())
    if business_id is not None:
        query = query.where(Promotion.business_id == business_id)
    return [PromotionOut.model_validate(r) for r in await session.scalars(query)]


async def create_promotion(session: AsyncSession, tenant_id: uuid.UUID, data: PromotionIn) -> PromotionOut:
    await tenancy.get_business(session, data.business_id)
    if not await catalog.exists(session, data.scope.value, data.scope_id):
        raise InvalidPricingError(f"There is no such {data.scope.value}.", field="scope_id")
    row = Promotion(
        id=new_id(),
        tenant_id=tenant_id,
        business_id=data.business_id,
        name_en=data.name_en,
        name_ar=data.name_ar,
        kind=data.kind.value,
        value=data.value,
        scope=data.scope.value,
        scope_id=data.scope_id,
        min_qty=data.min_qty,
        valid_from=data.valid_from,
        valid_to=data.valid_to,
    )
    session.add(row)
    await session.flush()
    return PromotionOut.model_validate(row)


async def update_promotion(
    session: AsyncSession, promotion_id: uuid.UUID, data: PromotionPatch
) -> PromotionOut:
    row = await session.get(Promotion, promotion_id)
    if row is None:
        raise PromotionNotFoundError()
    for field in ("name_en", "name_ar"):
        if (value := getattr(data, field)) is not None:
            setattr(row, field, value)
    for field in ("valid_from", "valid_to"):
        if field in data.model_fields_set:
            setattr(row, field, getattr(data, field))
    if row.valid_from and row.valid_to and row.valid_to < row.valid_from:
        raise InvalidPricingError("valid_to is before valid_from.", field="valid_to")
    if data.archived is not None:
        row.archived_at = datetime.now(UTC) if data.archived else None
    await session.flush()
    return PromotionOut.model_validate(row)


# --- the quote ---------------------------------------------------------------------------------------------


async def _list_prices(
    session: AsyncSession, price_list: PriceList | None, variant_id: uuid.UUID, unit_id: uuid.UUID, day: date
) -> engine.ListPrices | None:
    if price_list is None or not _valid_on(price_list, day):
        return None
    rows = await session.scalars(
        select(PriceListItem).where(
            PriceListItem.price_list_id == price_list.id,
            PriceListItem.variant_id == variant_id,
            PriceListItem.unit_id == unit_id,
        )
    )
    breaks = [engine.Break(r.min_qty, r.price) for r in rows]
    return engine.ListPrices(price_list.name_en, breaks) if breaks else None


async def quote(session: AsyncSession, data: QuoteIn, *, may_override_min: bool) -> QuoteOut:
    await tenancy.get_business(session, data.business_id)
    context, unit_id = await _unit_of(session, data.variant_id, data.unit)
    unit = context.units[data.unit]
    day = data.date or datetime.now(DUBAI).date()
    customer_list = group_list = None
    if data.party_id is not None:
        # Imported here: the parties service checks price lists through this module (each calls the other).
        from app.modules.parties import service as parties

        customer_id, group_id = await parties.price_lists_for(session, data.party_id, data.business_id)
        customer_list = await session.get(PriceList, customer_id) if customer_id else None
        group_list = await session.get(PriceList, group_id) if group_id else None

    standard = (
        await session.scalars(
            select(PriceList).where(
                PriceList.business_id == data.business_id, PriceList.is_standard.is_(True)
            )
        )
    ).first()
    scopes = [(PromotionScope.VARIANT, context.variant_id), (PromotionScope.PRODUCT, context.product_id)]
    if context.category_id:
        scopes.append((PromotionScope.CATEGORY, context.category_id))
    if context.brand_id:
        scopes.append((PromotionScope.BRAND, context.brand_id))
    promotions = [
        p
        for p in await session.scalars(
            select(Promotion)
            .where(
                Promotion.business_id == data.business_id,
                or_(*[(Promotion.scope == s.value) & (Promotion.scope_id == i) for s, i in scopes]),
            )
            .order_by(Promotion.created_at.desc())
        )
        if _valid_on(p, day)
    ]
    result = engine.quote(
        engine.QuoteInput(
            qty=data.qty,
            unit=data.unit,
            factor_to_base=unit.factor_to_base,
            base_price=unit.sale_price,
            min_price=unit.min_price,
            # Step 1, the last price sold to this customer, reads posted sales invoices: connected in M1.8.
            last_sale=None,
            customer_list=await _list_prices(session, customer_list, context.variant_id, unit_id, day),
            group_list=await _list_prices(session, group_list, context.variant_id, unit_id, day),
            standard_list=await _list_prices(session, standard, context.variant_id, unit_id, day),
            promotions=[
                engine.Promotion(p.name_en, engine.PromotionKind(p.kind), p.value, p.min_qty)
                for p in promotions
            ],
            may_override_min=may_override_min,
        )
    )
    return QuoteOut(
        price=result.price,
        price_source=result.price_source,
        source=result.source,
        source_name=result.source_name,
        converted=result.converted,
        min_price=result.min_price,
        below_min=result.below_min,
        may_go_below_min=result.may_go_below_min,
    )
