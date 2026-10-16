"""Units of measure (M1.1) and catalog (M1.2) endpoints. All tenant-wide master data."""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.catalog import products, service, setup
from app.modules.catalog.models import CustomFieldEntity
from app.modules.catalog.schemas import (
    AttributeIn,
    AttributeOut,
    AttributeValueIn,
    AttributeValueOut,
    BarcodeIn,
    BarcodeLookupOut,
    BrandIn,
    BrandOut,
    BrandPatch,
    CategoryIn,
    CategoryOut,
    CategoryPatch,
    ConversionOut,
    CustomFieldIn,
    CustomFieldOut,
    CustomFieldPatch,
    ProductIn,
    ProductOut,
    ProductPatch,
    ProductSummary,
    ProductUnitIn,
    ProductUnitPatch,
    UnitIn,
    UnitOut,
    UnitPatch,
    VariantIn,
    VariantPatch,
)
from app.modules.identity.authz import require

router = APIRouter()

UnitView = Annotated[PermissionSet, require("unit.view")]
UnitCreate = Annotated[PermissionSet, require("unit.create")]
UnitEdit = Annotated[PermissionSet, require("unit.edit")]
ProductView = Annotated[PermissionSet, require("product.view")]
ProductCreate = Annotated[PermissionSet, require("product.create")]
ProductEdit = Annotated[PermissionSet, require("product.edit")]
TenantEdit = Annotated[PermissionSet, require("tenant.edit")]
Created = status.HTTP_201_CREATED


@router.get("/units", operation_id="list_units", tags=["units"])
async def list_units(
    perms: UnitView, session: TenantSessionDep, include_archived: bool = False
) -> list[UnitOut]:
    perms.require_held("unit.view")
    return await service.list_units(session, include_archived=include_archived)


@router.post("/units", operation_id="create_unit", status_code=status.HTTP_201_CREATED, tags=["units"])
async def create_unit(
    body: UnitIn, perms: UnitCreate, session: TenantSessionDep, principal: PrincipalDep
) -> UnitOut:
    perms.require("unit.create")  # tenant-wide: units are shared by every business of the tenant
    return await service.create_unit(session, principal.tenant_id, body)


@router.get("/units/convert", operation_id="convert_units", tags=["units"])
async def convert_units(
    perms: UnitView,
    session: TenantSessionDep,
    qty: Annotated[Decimal, Query(max_digits=19, decimal_places=6)],
    from_unit: Annotated[str, Query(alias="from", max_length=20)],
    to_unit: Annotated[str, Query(alias="to", max_length=20)],
) -> ConversionOut:
    perms.require_held("unit.view")
    return await service.convert(session, qty, from_unit, to_unit)


@router.get("/units/{unit_id}", operation_id="get_unit", tags=["units"])
async def get_unit(unit_id: uuid.UUID, perms: UnitView, session: TenantSessionDep) -> UnitOut:
    perms.require_held("unit.view")
    return await service.get_unit(session, unit_id)


@router.patch("/units/{unit_id}", operation_id="update_unit", tags=["units"])
async def update_unit(
    unit_id: uuid.UUID, body: UnitPatch, perms: UnitEdit, session: TenantSessionDep
) -> UnitOut:
    perms.require("unit.edit")
    return await service.update_unit(session, unit_id, body)


# --- categories and brands (M1.2) ---------------------------------------------------------------------------


@router.get("/categories", operation_id="list_categories", tags=["catalog"])
async def list_categories(
    perms: ProductView, session: TenantSessionDep, include_archived: bool = False
) -> list[CategoryOut]:
    perms.require_held("product.view")
    return await setup.list_categories(session, include_archived=include_archived)


@router.post("/categories", operation_id="create_category", status_code=Created, tags=["catalog"])
async def create_category(
    body: CategoryIn, perms: ProductCreate, session: TenantSessionDep, principal: PrincipalDep
) -> CategoryOut:
    perms.require("product.create")
    return await setup.create_category(session, principal.tenant_id, body)


@router.patch("/categories/{category_id}", operation_id="update_category", tags=["catalog"])
async def update_category(
    category_id: uuid.UUID, body: CategoryPatch, perms: ProductEdit, session: TenantSessionDep
) -> CategoryOut:
    perms.require("product.edit")
    return await setup.update_category(session, category_id, body)


@router.get("/brands", operation_id="list_brands", tags=["catalog"])
async def list_brands(
    perms: ProductView, session: TenantSessionDep, include_archived: bool = False
) -> list[BrandOut]:
    perms.require_held("product.view")
    return await setup.list_brands(session, include_archived=include_archived)


@router.post("/brands", operation_id="create_brand", status_code=Created, tags=["catalog"])
async def create_brand(
    body: BrandIn, perms: ProductCreate, session: TenantSessionDep, principal: PrincipalDep
) -> BrandOut:
    perms.require("product.create")
    return await setup.create_brand(session, principal.tenant_id, body)


@router.patch("/brands/{brand_id}", operation_id="update_brand", tags=["catalog"])
async def update_brand(
    brand_id: uuid.UUID, body: BrandPatch, perms: ProductEdit, session: TenantSessionDep
) -> BrandOut:
    perms.require("product.edit")
    return await setup.update_brand(session, brand_id, body)


# --- attributes and custom fields ---------------------------------------------------------------------------


@router.get("/attributes", operation_id="list_attributes", tags=["catalog"])
async def list_attributes(perms: ProductView, session: TenantSessionDep) -> list[AttributeOut]:
    perms.require_held("product.view")
    return await setup.list_attributes(session)


@router.post("/attributes", operation_id="create_attribute", status_code=Created, tags=["catalog"])
async def create_attribute(
    body: AttributeIn, perms: ProductCreate, session: TenantSessionDep, principal: PrincipalDep
) -> AttributeOut:
    perms.require("product.create")
    return await setup.create_attribute(session, principal.tenant_id, body)


@router.post(
    "/attributes/{attribute_id}/values",
    operation_id="add_attribute_value",
    status_code=Created,
    tags=["catalog"],
)
async def add_attribute_value(
    attribute_id: uuid.UUID,
    body: AttributeValueIn,
    perms: ProductEdit,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> AttributeValueOut:
    perms.require("product.edit")
    return await setup.add_value(session, principal.tenant_id, attribute_id, body)


@router.get("/custom-fields", operation_id="list_custom_fields", tags=["catalog"])
async def list_custom_fields(
    perms: ProductView, session: TenantSessionDep, entity_type: CustomFieldEntity | None = None
) -> list[CustomFieldOut]:
    perms.require_held("product.view")
    return await setup.list_custom_fields(session, entity_type.value if entity_type else None)


@router.post("/custom-fields", operation_id="create_custom_field", status_code=Created, tags=["catalog"])
async def create_custom_field(
    body: CustomFieldIn, perms: TenantEdit, session: TenantSessionDep, principal: PrincipalDep
) -> CustomFieldOut:
    perms.require("tenant.edit")  # account configuration, not day-to-day catalog work
    return await setup.create_custom_field(session, principal.tenant_id, body)


@router.patch("/custom-fields/{field_id}", operation_id="update_custom_field", tags=["catalog"])
async def update_custom_field(
    field_id: uuid.UUID, body: CustomFieldPatch, perms: TenantEdit, session: TenantSessionDep
) -> CustomFieldOut:
    perms.require("tenant.edit")
    return await setup.update_custom_field(session, field_id, body)


# --- products, units of a product, variants, barcodes -------------------------------------------------------


@router.get("/products", operation_id="list_products", tags=["products"])
async def list_products(
    perms: ProductView,
    session: TenantSessionDep,
    q: Annotated[str | None, Query(max_length=100, description="SKU, name, variant SKU or barcode")] = None,
    category_id: uuid.UUID | None = None,
    brand_id: uuid.UUID | None = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductSummary]:
    perms.require_held("product.view")
    return await products.list_products(
        session,
        q=q,
        category_id=category_id,
        brand_id=brand_id,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


@router.post("/products", operation_id="create_product", status_code=Created, tags=["products"])
async def create_product(
    body: ProductIn, perms: ProductCreate, session: TenantSessionDep, principal: PrincipalDep
) -> ProductOut:
    perms.require("product.create")
    return await products.create_product(session, principal.tenant_id, body)


@router.get("/products/{product_id}", operation_id="get_product", tags=["products"])
async def get_product(product_id: uuid.UUID, perms: ProductView, session: TenantSessionDep) -> ProductOut:
    perms.require_held("product.view")
    return await products.get_product(session, product_id)


@router.patch("/products/{product_id}", operation_id="update_product", tags=["products"])
async def update_product(
    product_id: uuid.UUID, body: ProductPatch, perms: ProductEdit, session: TenantSessionDep
) -> ProductOut:
    perms.require("product.edit")
    return await products.update_product(session, product_id, body)


@router.post(
    "/products/{product_id}/units", operation_id="add_product_unit", status_code=Created, tags=["products"]
)
async def add_product_unit(
    product_id: uuid.UUID, body: ProductUnitIn, perms: ProductEdit, session: TenantSessionDep
) -> ProductOut:
    perms.require("product.edit")
    return await products.add_product_unit(session, product_id, body)


@router.patch(
    "/products/{product_id}/units/{unit_code}", operation_id="update_product_unit", tags=["products"]
)
async def update_product_unit(
    product_id: uuid.UUID,
    unit_code: str,
    body: ProductUnitPatch,
    perms: ProductEdit,
    session: TenantSessionDep,
) -> ProductOut:
    perms.require("product.edit")
    return await products.update_product_unit(session, product_id, unit_code, body)


@router.delete(
    "/products/{product_id}/units/{unit_code}", operation_id="remove_product_unit", tags=["products"]
)
async def remove_product_unit(
    product_id: uuid.UUID, unit_code: str, perms: ProductEdit, session: TenantSessionDep
) -> ProductOut:
    perms.require("product.edit")
    return await products.remove_product_unit(session, product_id, unit_code)


@router.post(
    "/products/{product_id}/variants", operation_id="add_variant", status_code=Created, tags=["products"]
)
async def add_variant(
    product_id: uuid.UUID, body: VariantIn, perms: ProductEdit, session: TenantSessionDep
) -> ProductOut:
    perms.require("product.edit")
    return await products.add_variant(session, product_id, body)


@router.patch("/variants/{variant_id}", operation_id="update_variant", tags=["products"])
async def update_variant(
    variant_id: uuid.UUID, body: VariantPatch, perms: ProductEdit, session: TenantSessionDep
) -> ProductOut:
    perms.require("product.edit")
    return await products.update_variant(session, variant_id, body)


@router.post(
    "/products/{product_id}/barcodes", operation_id="add_barcode", status_code=Created, tags=["products"]
)
async def add_barcode(
    product_id: uuid.UUID, body: BarcodeIn, perms: ProductEdit, session: TenantSessionDep
) -> ProductOut:
    perms.require("product.edit")
    return await products.add_barcode(session, product_id, body)


@router.delete("/barcodes/{barcode_id}", operation_id="remove_barcode", tags=["products"])
async def remove_barcode(barcode_id: uuid.UUID, perms: ProductEdit, session: TenantSessionDep) -> ProductOut:
    perms.require("product.edit")
    return await products.remove_barcode(session, barcode_id)


@router.get("/barcodes/{code}", operation_id="lookup_barcode", tags=["products"])
async def lookup_barcode(code: str, perms: ProductView, session: TenantSessionDep) -> BarcodeLookupOut:
    """What a scan finds: product, variant, unit and how many base units one scanned item is."""
    perms.require_held("product.view")
    return await products.lookup_barcode(session, code)
