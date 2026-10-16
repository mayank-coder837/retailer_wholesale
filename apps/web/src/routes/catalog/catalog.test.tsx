import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { toProductIn, variantPreview, emptyDraft } from "@/routes/catalog/product-form";
import { shopApi } from "@/test/shop-api";

const unit = (
  code: string,
  family: string,
  factor: string,
  decimals: number,
  en: string,
  ar: string,
  base = false,
) => ({
  id: `u-${code}`,
  family,
  code,
  name_en: en,
  name_ar: ar,
  symbol: code,
  decimals,
  factor_to_family_base: factor,
  is_family_base: base,
  is_standard: true,
  archived_at: null,
});

const UNITS = [
  unit("pcs", "count", "1.000000", 0, "Piece", "قطعة", true),
  unit("roll", "count", "1.000000", 0, "Roll", "رول"),
  unit("m", "length", "1.000000", 2, "Metre", "متر", true),
];

const ATTRIBUTES = [
  {
    id: "a-color",
    code: "color",
    name_en: "Colour",
    name_ar: "اللون",
    values: [
      { id: "v-red", value_en: "Red", value_ar: "أحمر", sort: 0 },
      { id: "v-blue", value_en: "Blue", value_ar: "أزرق", sort: 1 },
    ],
  },
  {
    id: "a-width",
    code: "width",
    name_en: "Width",
    name_ar: "العرض",
    values: [{ id: "v-44", value_en: "44 in", value_ar: "44 بوصة", sort: 0 }],
  },
];

const GSM = {
  id: "f-gsm",
  entity_type: "product" as const,
  key: "gsm",
  label_en: "GSM",
  label_ar: "الوزن",
  data_type: "decimal" as const,
  options: null,
  required: false,
  archived_at: null,
};

const variant = (id: string, sku: string, values: [string, string, string][]) => ({
  id,
  sku,
  values: values.map(([attribute, value_en, value_ar]) => ({ attribute, value_en, value_ar })),
  custom_fields: {},
  archived_at: null,
});

const FABRIC = {
  id: "p1",
  sku: "FAB-001",
  name_en: "Cotton poplin",
  name_ar: "بوبلين قطن",
  description: null,
  product_type: "stock",
  category_id: null,
  brand_id: null,
  base_unit: "m",
  track_batch: false,
  track_expiry: false,
  track_serial: false,
  custom_fields: { gsm: "120.5" },
  archived_at: null,
  units: [
    {
      unit: "m",
      name_en: "Metre",
      name_ar: "متر",
      symbol: "m",
      decimals: 2,
      factor_to_base: "1.000000",
      is_base: true,
      is_purchase_default: false,
      is_sales_default: true,
    },
    {
      unit: "roll",
      name_en: "Roll",
      name_ar: "رول",
      symbol: "roll",
      decimals: 0,
      factor_to_base: "50.000000",
      is_base: false,
      is_purchase_default: true,
      is_sales_default: false,
    },
  ],
  attributes: ["color", "width"],
  variants: [
    variant("var-red", "FAB-001-RED-44IN", [
      ["color", "Red", "أحمر"],
      ["width", "44 in", "44 بوصة"],
    ]),
    variant("var-blue", "FAB-001-BLUE-44IN", [
      ["color", "Blue", "أزرق"],
      ["width", "44 in", "44 بوصة"],
    ]),
  ],
  barcodes: [] as { id: string; code: string; kind: string; unit: string; variant_id: string }[],
};

function catalogApi(overrides = {}) {
  return shopApi({
    signedIn: true,
    "GET /units": { status: 200, body: UNITS },
    "GET /attributes": { status: 200, body: ATTRIBUTES },
    "GET /custom-fields": { status: 200, body: [GSM] },
    "GET /categories": { status: 200, body: [] },
    "GET /brands": { status: 200, body: [] },
    "GET /products": {
      status: 200,
      body: [
        {
          id: "p1",
          sku: "FAB-001",
          name_en: "Cotton poplin",
          name_ar: "بوبلين قطن",
          product_type: "stock",
          base_unit: "m",
          category_id: null,
          brand_id: null,
          variant_count: 2,
          archived_at: null,
        },
      ],
    },
    "GET /products/p1": { status: 200, body: FABRIC },
    "POST /products": { status: 201, body: FABRIC },
    ...overrides,
  });
}

describe("Catalog screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("lists products and searches by name, SKU or barcode", async () => {
    window.location.hash = "#/inventory";
    const { api, calls } = catalogApi();
    render(<App api={api} />);

    expect(await screen.findByRole("link", { name: "Cotton poplin" })).toBeInTheDocument();
    await userEvent.type(
      screen.getByRole("searchbox", { name: "Search by name, SKU or barcode" }),
      "6291000123459",
    );

    await waitFor(() =>
      expect(calls.some((c) => c.path === "/products" && c.query === "?q=6291000123459")).toBe(true),
    );
  });

  it("creates a fabric bought by the roll, with colour variants and an exact factor", async () => {
    window.location.hash = "#/inventory/products/new";
    const { api, calls } = catalogApi();
    render(<App api={api} />);

    await userEvent.type(await screen.findByLabelText("Name (English)"), "Cotton poplin");
    await userEvent.type(screen.getByLabelText("Name (Arabic)"), "بوبلين قطن");
    await userEvent.type(screen.getByLabelText("SKU"), "FAB-001");
    await userEvent.selectOptions(screen.getByLabelText("Stock unit"), "m");
    await userEvent.click(screen.getByRole("button", { name: "Add a unit" }));
    await userEvent.selectOptions(screen.getByLabelText("Other unit"), "roll");
    await userEvent.type(screen.getByLabelText("1 roll = how many m?"), "50");
    await userEvent.selectOptions(screen.getByLabelText("Usually bought in"), "roll");
    await userEvent.click(screen.getByLabelText("Red"));
    await userEvent.click(screen.getByLabelText("Blue"));
    await userEvent.click(screen.getByLabelText("44 in"));
    expect(screen.getByTestId("variant-preview")).toHaveTextContent("2 variants will be created.");
    await userEvent.type(screen.getByLabelText("GSM"), "120.5");
    await userEvent.click(screen.getByRole("button", { name: "Create product" }));

    expect(await screen.findByRole("heading", { name: "Cotton poplin" })).toBeInTheDocument();
    const body = calls.find((c) => c.method === "POST" && c.path === "/products")?.body as Record<
      string,
      unknown
    >;
    expect(body).toMatchObject({
      sku: "FAB-001",
      base_unit: "m",
      units: [{ unit: "roll", factor_to_base: "50", is_purchase_default: true, is_sales_default: false }],
      attributes: [
        { attribute: "color", values: ["Red", "Blue"] },
        { attribute: "width", values: ["44 in"] },
      ],
      custom_fields: { gsm: "120.5" },
    });
  });

  it("shows a product's units and variants, and generates a barcode for the roll", async () => {
    window.location.hash = "#/inventory/products/p1";
    const withBarcode = {
      ...FABRIC,
      barcodes: [{ id: "b1", code: "2123456789012", kind: "internal", unit: "roll", variant_id: "var-red" }],
    };
    const { api, calls } = catalogApi({ "POST /products/p1/barcodes": { status: 201, body: withBarcode } });
    render(<App api={api} />);

    const units = await screen.findByRole("region", { name: "Units" });
    expect(within(units).getByText(/1 roll = 50 m/)).toBeInTheDocument();
    expect(screen.getByText("FAB-001-RED-44IN")).toBeInTheDocument();
    // The page has several unit pickers (barcodes, list price, price check): use the barcodes one.
    const barcodes = screen.getByRole("region", { name: "Barcodes" });
    await userEvent.selectOptions(within(barcodes).getByLabelText("Unit"), "roll");
    await userEvent.click(within(barcodes).getByRole("button", { name: "Generate barcode" }));

    expect(await screen.findByText("2123456789012")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/products/p1/barcodes")?.body).toMatchObject({
      variant_id: "var-red",
      unit: "roll",
      code: null,
    });
  });

  it("shows a translated error when the barcode is taken", async () => {
    window.location.hash = "#/inventory/products/p1";
    const { api } = catalogApi({
      "POST /products/p1/barcodes": { status: 409, body: { code: "barcode_taken", title: "x" } },
    });
    render(<App api={api} />);

    await userEvent.type(await screen.findByLabelText("Barcode"), "4006381333931");
    await userEvent.click(screen.getByRole("button", { name: "Add barcode" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This barcode is already used by another product.",
    );
  });

  it("adds a brand in catalog set-up, in Arabic too", async () => {
    window.location.hash = "#/settings/catalog";
    await setLanguage("ar");
    const { api, calls } = catalogApi({
      "POST /brands": {
        status: 201,
        body: { id: "b1", name_en: "Al Noor", name_ar: "النور", archived_at: null },
      },
    });
    render(<App api={api} />);

    await userEvent.click(await screen.findByRole("tab", { name: "العلامات التجارية" }));
    const form = screen.getByRole("form", { name: "إضافة علامة تجارية" });
    await userEvent.type(within(form).getByLabelText("الاسم (بالإنجليزية)"), "Al Noor");
    await userEvent.type(within(form).getByLabelText("الاسم (بالعربية)"), "النور");
    await userEvent.click(within(form).getByRole("button", { name: "إضافة" }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/brands")?.body).toEqual({
        name_en: "Al Noor",
        name_ar: "النور",
      }),
    );
  });
});

describe("product form logic", () => {
  it("previews every combination of the chosen values", () => {
    expect(variantPreview(ATTRIBUTES, { color: ["Red", "Blue"], width: ["44 in"] })).toEqual([
      ["Red", "44 in"],
      ["Blue", "44 in"],
    ]);
    expect(variantPreview(ATTRIBUTES, {})).toEqual([]);
  });

  it("sends factors and decimals as exact text, never as floats", () => {
    const draft = {
      ...emptyDraft("m"),
      sku: " FAB-1 ",
      nameEn: "F",
      nameAr: "ف",
      units: [{ unit: "roll", factor: "0.9144" }],
    };
    const body = toProductIn(draft, ATTRIBUTES, [GSM]);
    expect(body.units).toEqual([
      { unit: "roll", factor_to_base: "0.9144", is_purchase_default: false, is_sales_default: false },
    ]);
    expect(body.sku).toBe("FAB-1");
    expect(body.custom_fields).toEqual({});
  });
});
