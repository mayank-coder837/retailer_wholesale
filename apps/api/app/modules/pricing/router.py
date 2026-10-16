"""Pricing endpoints (M1.3): unit prices, price lists and their items, promotions, the price quote.

Price lists and promotions belong to a business, so permissions are checked for that business.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, status

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.catalog.schemas import ProductOut
from app.modules.identity.authz import require
from app.modules.pricing import service
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

router = APIRouter(tags=["pricing"])
Created = status.HTTP_201_CREATED

ListView = Annotated[PermissionSet, require("price_list.view")]
ListCreate = Annotated[PermissionSet, require("price_list.create")]
ListEdit = Annotated[PermissionSet, require("price_list.edit")]
PromotionView = Annotated[PermissionSet, require("promotion.view")]
PromotionCreate = Annotated[PermissionSet, require("promotion.create")]
PromotionEdit = Annotated[PermissionSet, require("promotion.edit")]


@router.patch("/products/{product_id}/units/{unit_code}/prices", operation_id="set_unit_prices")
async def set_unit_prices(
    product_id: uuid.UUID, unit_code: str, body: UnitPricesIn, perms: ListEdit, session: TenantSessionDep
) -> ProductOut:
    perms.require("price_list.edit")  # base and minimum prices are tenant-wide, like the product
    return await service.set_unit_prices(session, product_id, unit_code, body)


@router.get("/price-lists", operation_id="list_price_lists")
async def list_price_lists(
    perms: ListView, session: TenantSessionDep, business_id: uuid.UUID | None = None
) -> list[PriceListOut]:
    return [
        p
        for p in await service.list_price_lists(session, business_id)
        if perms.allows("price_list.view", p.business_id)
    ]


@router.post("/price-lists", operation_id="create_price_list", status_code=Created)
async def create_price_list(
    body: PriceListIn, perms: ListCreate, session: TenantSessionDep, principal: PrincipalDep
) -> PriceListOut:
    perms.require("price_list.create", body.business_id)
    return await service.create_price_list(session, principal.tenant_id, body)


@router.patch("/price-lists/{price_list_id}", operation_id="update_price_list")
async def update_price_list(
    price_list_id: uuid.UUID, body: PriceListPatch, perms: ListEdit, session: TenantSessionDep
) -> PriceListOut:
    perms.require("price_list.edit", (await service.get_price_list(session, price_list_id)).business_id)
    return await service.update_price_list(session, price_list_id, body)


@router.get("/price-lists/{price_list_id}/items", operation_id="list_price_list_items")
async def list_price_list_items(
    price_list_id: uuid.UUID, perms: ListView, session: TenantSessionDep
) -> list[PriceListItemOut]:
    perms.require("price_list.view", (await service.get_price_list(session, price_list_id)).business_id)
    return await service.list_items(session, price_list_id)


@router.put("/price-lists/{price_list_id}/items", operation_id="put_price_list_item")
async def put_price_list_item(
    price_list_id: uuid.UUID,
    body: PriceListItemIn,
    perms: ListEdit,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> list[PriceListItemOut]:
    perms.require("price_list.edit", (await service.get_price_list(session, price_list_id)).business_id)
    return await service.put_item(session, principal.tenant_id, price_list_id, body)


@router.delete("/price-lists/{price_list_id}/items/{item_id}", operation_id="delete_price_list_item")
async def delete_price_list_item(
    price_list_id: uuid.UUID, item_id: uuid.UUID, perms: ListEdit, session: TenantSessionDep
) -> list[PriceListItemOut]:
    perms.require("price_list.edit", (await service.get_price_list(session, price_list_id)).business_id)
    return await service.delete_item(session, price_list_id, item_id)


@router.get("/promotions", operation_id="list_promotions")
async def list_promotions(
    perms: PromotionView, session: TenantSessionDep, business_id: uuid.UUID | None = None
) -> list[PromotionOut]:
    return [
        p
        for p in await service.list_promotions(session, business_id)
        if perms.allows("promotion.view", p.business_id)
    ]


@router.post("/promotions", operation_id="create_promotion", status_code=Created)
async def create_promotion(
    body: PromotionIn, perms: PromotionCreate, session: TenantSessionDep, principal: PrincipalDep
) -> PromotionOut:
    perms.require("promotion.create", body.business_id)
    return await service.create_promotion(session, principal.tenant_id, body)


@router.patch("/promotions/{promotion_id}", operation_id="update_promotion")
async def update_promotion(
    promotion_id: uuid.UUID, body: PromotionPatch, perms: PromotionEdit, session: TenantSessionDep
) -> PromotionOut:
    promotions = {p.id: p for p in await service.list_promotions(session)}
    if promotion_id in promotions:
        perms.require("promotion.edit", promotions[promotion_id].business_id)
    return await service.update_promotion(session, promotion_id, body)


@router.post("/pricing/quote", operation_id="quote_price")
async def quote_price(body: QuoteIn, perms: ListView, session: TenantSessionDep) -> QuoteOut:
    """The price to propose for an item, where it came from, and the minimum-price check."""
    perms.require("price_list.view", body.business_id)
    may_override = perms.allows("price.override_min", body.business_id)
    return await service.quote(session, body, may_override_min=may_override)
