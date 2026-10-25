"""Units of measure (M1.1) and catalog (M1.2) API shapes. Document names are in English and Arabic."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.catalog.models import (
    CODE_PATTERN,
    BarcodeKind,
    CustomFieldEntity,
    CustomFieldType,
    ProductType,
)
from app.modules.tax.fields import PurchaseTaxCode, SalesTaxCode

Name = Field(min_length=1, max_length=100)


class UnitIn(BaseModel):
    family: str = Field(pattern=CODE_PATTERN, max_length=20, description="Family code: count, length, …")
    code: str = Field(pattern=CODE_PATTERN, max_length=20)
    name_en: str = Name
    name_ar: str = Name
    symbol: str = Field(min_length=1, max_length=20)
    decimals: int = Field(
        ge=0,
        le=6,
        description="Decimal places a quantity typed in this unit may have; conversions stay exact",
    )
    factor_to_family_base: Decimal = Field(
        gt=0, max_digits=19, decimal_places=6, description="How many family base units one of this unit is"
    )


class UnitPatch(BaseModel):
    """Factor and family are fixed once a unit exists: lines converted with it must keep their meaning."""

    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    symbol: str | None = Field(default=None, min_length=1, max_length=20)
    decimals: int | None = Field(default=None, ge=0, le=6)
    archived: bool | None = None


class UnitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    family: str
    code: str
    name_en: str
    name_ar: str
    symbol: str
    decimals: int
    factor_to_family_base: Decimal
    is_family_base: bool
    is_standard: bool
    archived_at: datetime | None


class ConversionOut(BaseModel):
    qty: Decimal
    from_unit: str
    to_unit: str
    factor: Decimal
    result: Decimal  # exact: a conversion that would need rounding is refused (uom_inexact)


# --- catalog (M1.2) --------------------------------------------------------------------------------------

Sku = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class Named(BaseModel):
    name_en: str = Name
    name_ar: str = Name


class CategoryIn(Named):
    parent_id: uuid.UUID | None = None


class CategoryPatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: uuid.UUID | None = None
    move_to_top: bool = False  # parent_id null is ambiguous in a patch: say so explicitly
    archived: bool | None = None


class CategoryOut(Named):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    path_en: str  # "Fabrics / Cotton"
    archived_at: datetime | None


class BrandIn(Named):
    pass


class BrandPatch(BaseModel):
    name_en: str | None = Field(default=None, min_length=1, max_length=100)
    name_ar: str | None = Field(default=None, min_length=1, max_length=100)
    archived: bool | None = None


class BrandOut(Named):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    archived_at: datetime | None


class AttributeValueIn(BaseModel):
    value_en: str = Name
    value_ar: str = Name


class AttributeValueOut(AttributeValueIn):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sort: int


class AttributeIn(Named):
    code: str = Field(pattern=CODE_PATTERN, max_length=30)
    values: list[AttributeValueIn] = Field(default_factory=list)


class AttributeOut(Named):
    id: uuid.UUID
    code: str
    values: list[AttributeValueOut]


class CustomFieldOption(BaseModel):
    value: str = Field(min_length=1, max_length=100)
    label_en: str = Name
    label_ar: str = Name


class CustomFieldIn(BaseModel):
    entity_type: CustomFieldEntity
    key: str = Field(pattern=CODE_PATTERN, max_length=50)
    label_en: str = Name
    label_ar: str = Name
    data_type: CustomFieldType
    options: list[CustomFieldOption] | None = None
    required: bool = False


class CustomFieldPatch(BaseModel):
    """Key, entity and type are fixed: values already saved must keep their meaning."""

    label_en: str | None = Field(default=None, min_length=1, max_length=100)
    label_ar: str | None = Field(default=None, min_length=1, max_length=100)
    options: list[CustomFieldOption] | None = None
    required: bool | None = None
    archived: bool | None = None


class CustomFieldOut(CustomFieldIn):
    id: uuid.UUID
    archived_at: datetime | None


class ProductUnitIn(BaseModel):
    unit: str = Field(max_length=20, description="Unit code, e.g. roll")
    factor_to_base: Decimal | None = Field(
        default=None,
        gt=0,
        max_digits=19,
        decimal_places=6,
        description="How many base units one of this unit is (1 roll = 50 m → 50). Omit to use the standard "
        "factor when the unit is of the base unit's family.",
    )
    is_purchase_default: bool = False
    is_sales_default: bool = False


class ProductUnitPatch(BaseModel):
    """The factor is fixed once a unit is added (L3: lines converted with it keep their meaning)."""

    is_purchase_default: bool | None = None
    is_sales_default: bool | None = None


class ProductUnitOut(BaseModel):
    unit: str
    name_en: str
    name_ar: str
    symbol: str
    decimals: int
    factor_to_base: Decimal
    is_base: bool
    is_purchase_default: bool
    is_sales_default: bool
    sale_price: Decimal | None = None
    min_price: Decimal | None = None


class VariantChoice(BaseModel):
    """Which attribute a product varies by, and the values to generate variants for."""

    attribute: str = Field(max_length=30, description="Attribute code, e.g. color")
    values: list[str] = Field(min_length=1, description="Values by English name; new values are added")


class ProductIn(Named):
    sku: str = Sku
    description: str | None = Field(default=None, max_length=2000)
    product_type: ProductType = ProductType.STOCK
    category_id: uuid.UUID | None = None
    brand_id: uuid.UUID | None = None
    base_unit: str = Field(max_length=20, description="Unit code stock is kept in, e.g. m")
    units: list[ProductUnitIn] = Field(default_factory=list, description="Other units, e.g. roll = 50 m")
    attributes: list[VariantChoice] = Field(
        default_factory=list, description="Every combination becomes a variant (colour x width)"
    )
    track_batch: bool = False
    track_expiry: bool = False
    track_serial: bool = False
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    sales_tax_code: SalesTaxCode | None = Field(
        default=None, description="Default VAT on sales lines; none: SR"
    )
    purchase_tax_code: PurchaseTaxCode | None = Field(
        default=None, description="Default VAT on purchase lines; none: SR"
    )


class ProductPatch(BaseModel):
    """Base unit and SKU are fixed: stock is kept in the base unit, documents print the SKU."""

    name_en: str | None = Field(default=None, min_length=1, max_length=200)
    name_ar: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category_id: uuid.UUID | None = None
    brand_id: uuid.UUID | None = None
    track_batch: bool | None = None
    track_expiry: bool | None = None
    track_serial: bool | None = None
    custom_fields: dict[str, Any] | None = None
    sales_tax_code: SalesTaxCode | None = Field(default=None, description="Send null to go back to SR")
    purchase_tax_code: PurchaseTaxCode | None = Field(default=None, description="Send null to go back to SR")
    archived: bool | None = None


class VariantValueOut(BaseModel):
    attribute: str
    value_en: str
    value_ar: str


class BarcodeOut(BaseModel):
    id: uuid.UUID
    code: str
    kind: BarcodeKind
    unit: str
    variant_id: uuid.UUID


class VariantOut(BaseModel):
    id: uuid.UUID
    sku: str
    values: list[VariantValueOut]
    custom_fields: dict[str, Any]
    archived_at: datetime | None


class ProductOut(Named):
    id: uuid.UUID
    sku: str
    description: str | None
    product_type: ProductType
    category_id: uuid.UUID | None
    brand_id: uuid.UUID | None
    base_unit: str
    track_batch: bool
    track_expiry: bool
    track_serial: bool
    custom_fields: dict[str, Any]
    sales_tax_code: str | None
    purchase_tax_code: str | None
    archived_at: datetime | None
    units: list[ProductUnitOut]
    attributes: list[str]
    variants: list[VariantOut]
    barcodes: list[BarcodeOut]


class ProductSummary(Named):
    id: uuid.UUID
    sku: str
    product_type: ProductType
    base_unit: str
    category_id: uuid.UUID | None
    brand_id: uuid.UUID | None
    variant_count: int
    archived_at: datetime | None


class VariantIn(BaseModel):
    values: dict[str, str] = Field(description="Attribute code → value (English); one per product attribute")
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class VariantPatch(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    custom_fields: dict[str, Any] | None = None
    archived: bool | None = None


class BarcodeIn(BaseModel):
    variant_id: uuid.UUID
    unit: str = Field(max_length=20, description="One of the product's units")
    code: str | None = Field(default=None, min_length=1, max_length=48, description="Omit to generate one")
    kind: BarcodeKind = BarcodeKind.EAN13


class BarcodeLookupOut(BaseModel):
    """What a scan finds: the exact variant and unit, and how many base units one scan is."""

    code: str
    kind: BarcodeKind
    product_id: uuid.UUID
    product_sku: str
    product_name_en: str
    product_name_ar: str
    variant_id: uuid.UUID
    variant_sku: str
    values: list[VariantValueOut]
    unit: str
    factor_to_base: Decimal
    base_unit: str
