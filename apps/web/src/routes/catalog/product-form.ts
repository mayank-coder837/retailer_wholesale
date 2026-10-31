import type { Attribute, CustomField, ProductIn } from "@/routes/catalog/queries";

export interface UnitRow {
  unit: string;
  factor: string; // as typed: exact decimals travel as text, never as floats
}

export interface ProductDraft {
  sku: string;
  nameEn: string;
  nameAr: string;
  productType: "stock" | "service" | "non_stock";
  categoryId: string;
  brandId: string;
  baseUnit: string;
  units: UnitRow[];
  purchaseUnit: string; // a unit code: the base unit or one of `units`
  salesUnit: string;
  chosen: Record<string, string[]>; // attribute code → English values
  salesTaxCode: string; // empty: standard rated (M1.10)
  purchaseTaxCode: string;
  custom: Record<string, string | boolean | string[]>;
}

export function emptyDraft(baseUnit = "pcs"): ProductDraft {
  return {
    sku: "",
    nameEn: "",
    nameAr: "",
    productType: "stock",
    categoryId: "",
    brandId: "",
    baseUnit,
    units: [],
    purchaseUnit: baseUnit,
    salesUnit: baseUnit,
    chosen: {},
    custom: {},
    salesTaxCode: "",
    purchaseTaxCode: "",
  };
}

/** Every combination of the chosen values, in attribute order: what the server will create as variants. */
export function variantPreview(attributes: Attribute[], chosen: Record<string, string[]>): string[][] {
  const lists = attributes.filter((a) => (chosen[a.code] ?? []).length > 0).map((a) => chosen[a.code] ?? []);
  if (lists.length === 0) return [];
  return lists.reduce<string[][]>(
    (combos, values) => combos.flatMap((c) => values.map((v) => [...c, v])),
    [[]],
  );
}

function customValue(field: CustomField, raw: string | boolean | string[] | undefined): unknown {
  if (raw === undefined || raw === "" || (Array.isArray(raw) && raw.length === 0)) return undefined;
  if (field.data_type === "number" && typeof raw === "string") return Number.parseInt(raw, 10);
  return raw; // decimals stay text so they arrive exact
}

/** The POST /products body. */
export function toProductIn(draft: ProductDraft, attributes: Attribute[], fields: CustomField[]): ProductIn {
  const custom: Record<string, unknown> = {};
  for (const field of fields) {
    const value = customValue(field, draft.custom[field.key]);
    if (value !== undefined) custom[field.key] = value;
  }
  return {
    sku: draft.sku.trim(),
    name_en: draft.nameEn.trim(),
    name_ar: draft.nameAr.trim(),
    product_type: draft.productType,
    category_id: draft.categoryId || null,
    brand_id: draft.brandId || null,
    base_unit: draft.baseUnit,
    units: draft.units
      .filter((u) => u.unit && u.unit !== draft.baseUnit)
      .map((u) => ({
        unit: u.unit,
        factor_to_base: u.factor.trim() || null,
        is_purchase_default: draft.purchaseUnit === u.unit,
        is_sales_default: draft.salesUnit === u.unit,
      })),
    attributes: attributes
      .filter((a) => (draft.chosen[a.code] ?? []).length > 0)
      .map((a) => ({ attribute: a.code, values: draft.chosen[a.code] ?? [] })),
    track_batch: false,
    track_expiry: false,
    track_serial: false,
    custom_fields: custom,
    sales_tax_code: draft.salesTaxCode || null,
    purchase_tax_code: draft.purchaseTaxCode || null,
  };
}

/** Name in the interface language, falling back to English. */
export function localName(item: { name_en: string; name_ar: string }, language: string): string {
  return language === "ar" ? item.name_ar || item.name_en : item.name_en;
}
