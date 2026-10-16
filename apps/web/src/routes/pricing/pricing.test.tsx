import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { showMoney, splitSource } from "@/routes/pricing/queries";
import { BUSINESSES, shopApi } from "@/test/shop-api";

const SOAP = {
  id: "p1",
  sku: "SOAP-1",
  name_en: "Soap",
  name_ar: "صابون",
  description: null,
  product_type: "stock",
  category_id: null,
  brand_id: null,
  base_unit: "pcs",
  track_batch: false,
  track_expiry: false,
  track_serial: false,
  custom_fields: {},
  archived_at: null,
  units: [
    {
      unit: "pcs",
      name_en: "Piece",
      name_ar: "قطعة",
      symbol: "pcs",
      decimals: 0,
      factor_to_base: "1.000000",
      is_base: true,
      is_purchase_default: true,
      is_sales_default: true,
      sale_price: "5.0000",
      min_price: "4.0000",
    },
  ],
  attributes: [],
  variants: [{ id: "var-1", sku: "SOAP-1", values: [], custom_fields: {}, archived_at: null }],
  barcodes: [],
};

const QUOTE = {
  price: "3.5000",
  price_source: "promotion:Eid",
  source: "promotion",
  source_name: "Eid",
  converted: false,
  min_price: "4.0000",
  below_min: true,
  may_go_below_min: false,
};

function pricingApi(overrides = {}) {
  return shopApi({
    signedIn: true,
    "GET /products/p1": { status: 200, body: SOAP },
    "GET /price-lists": { status: 200, body: [] },
    "GET /promotions": { status: 200, body: [] },
    ...overrides,
  });
}

describe("Pricing screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("saves the price and minimum of a unit as exact text", async () => {
    window.location.hash = "#/inventory/products/p1";
    const { api, calls } = pricingApi({
      "PATCH /products/p1/units/pcs/prices": { status: 200, body: SOAP },
    });
    render(<App api={api} />);

    const price = await screen.findByLabelText("Price per pcs");
    expect(price).toHaveValue("5.0000");
    await userEvent.clear(price);
    await userEvent.type(price, "5.25");
    const group = screen.getByRole("group", { name: "Price per unit" });
    await userEvent.click(within(group).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
        sale_price: "5.25",
        min_price: "4.0000",
      }),
    );
  });

  it("checks a price and explains where it came from and the minimum", async () => {
    window.location.hash = "#/inventory/products/p1";
    const { api, calls } = pricingApi({ "POST /pricing/quote": { status: 200, body: QUOTE } });
    render(<App api={api} />);

    const check = await screen.findByRole("form", { name: "Price check" });
    await userEvent.clear(within(check).getByLabelText("Quantity"));
    await userEvent.type(within(check).getByLabelText("Quantity"), "12");
    await userEvent.click(within(check).getByRole("button", { name: "Check" }));

    const result = await screen.findByTestId("quote");
    expect(result).toHaveTextContent("3.50 AED");
    expect(result).toHaveTextContent("Promotion: Eid");
    expect(result).toHaveTextContent("Below the minimum of 4.00: needs a manager.");
    expect(calls.find((c) => c.path === "/pricing/quote")?.body).toEqual({
      business_id: BUSINESSES[0]!.id,
      variant_id: "var-1",
      unit: "pcs",
      qty: "12",
    });
  });

  it("explains the price in Arabic too", async () => {
    window.location.hash = "#/inventory/products/p1";
    await setLanguage("ar");
    const { api } = pricingApi({
      "POST /pricing/quote": {
        status: 200,
        body: {
          ...QUOTE,
          price_source: "quantity_break:Standard",
          source: "quantity_break",
          source_name: "Standard",
          below_min: false,
        },
      },
    });
    render(<App api={api} />);

    const check = await screen.findByRole("form", { name: "فحص السعر" });
    await userEvent.click(within(check).getByRole("button", { name: "فحص" }));

    expect(await screen.findByTestId("quote")).toHaveTextContent("قائمة الأسعار: Standard");
  });

  it("adds a standard price list for the business in pricing settings", async () => {
    window.location.hash = "#/settings/pricing";
    const { api, calls } = pricingApi({
      "POST /price-lists": {
        status: 201,
        body: {
          id: "l1",
          business_id: BUSINESSES[0]!.id,
          name_en: "Standard",
          name_ar: "القياسية",
          currency: "AED",
          is_standard: true,
          valid_from: null,
          valid_to: null,
          archived_at: null,
        },
      },
    });
    render(<App api={api} />);

    const form = await screen.findByRole("form", { name: "Add a price list" });
    await userEvent.type(within(form).getByLabelText("Name (English)"), "Standard");
    await userEvent.type(within(form).getByLabelText("Name (Arabic)"), "القياسية");
    await userEvent.click(within(form).getByLabelText(/Standard list of this business/));
    await userEvent.click(within(form).getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/price-lists")?.body).toEqual({
        business_id: BUSINESSES[0]!.id,
        currency: "AED",
        name_en: "Standard",
        name_ar: "القياسية",
        is_standard: true,
        valid_from: null,
        valid_to: null,
      }),
    );
  });
});

describe("pricing helpers", () => {
  it("shows money with at least two decimals and never rounds", () => {
    expect(showMoney("3.0000")).toBe("3.00");
    expect(showMoney("2.4500")).toBe("2.45");
    expect(showMoney("1.2345")).toBe("1.2345");
    expect(showMoney(null)).toBe("—");
  });

  it("splits the stored price source", () => {
    expect(splitSource("last_price:INV-2026-00412")).toEqual({
      source: "last_price",
      name: "INV-2026-00412",
    });
    expect(splitSource("base")).toEqual({ source: "base", name: "" });
  });
});
