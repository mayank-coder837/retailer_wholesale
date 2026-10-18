import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { shopApi } from "@/test/shop-api";

const WAREHOUSES = [
  {
    id: "w1",
    business_id: "b1",
    branch_id: "br1",
    code: "MAIN",
    name_en: "Main warehouse",
    name_ar: "الرئيسي",
    is_default: true,
  },
  {
    id: "w2",
    business_id: "b1",
    branch_id: "br1",
    code: "W2",
    name_en: "Back store",
    name_ar: "الخلفي",
    is_default: false,
  },
];

const BALANCES = [
  {
    variant_id: "v1",
    sku: "SOAP",
    name_en: "Soap",
    name_ar: "صابون",
    base_unit: "pcs",
    warehouse_id: "w1",
    batch_id: null,
    batch_no: null,
    expiry_date: null,
    qty: "36.000000",
    value: "54.0000",
  },
  {
    variant_id: "v2",
    sku: "SHOE",
    name_en: "Shoe",
    name_ar: "حذاء",
    base_unit: "pcs",
    warehouse_id: "w2",
    batch_id: null,
    batch_no: null,
    expiry_date: null,
    qty: "-10.000000",
    value: "-600.0000",
  },
];

const PRODUCT_SUMMARY = {
  id: "p1",
  sku: "SOAP",
  name_en: "Soap",
  name_ar: "صابون",
  product_type: "stock",
  base_unit: "pcs",
  category_id: null,
  brand_id: null,
  variant_count: 1,
  archived_at: null,
};

const unit = (code: string, factor: string, isBase: boolean) => ({
  unit: code,
  name_en: code,
  name_ar: code,
  symbol: code,
  decimals: 0,
  factor_to_base: factor,
  is_base: isBase,
  is_purchase_default: false,
  is_sales_default: false,
});

const PRODUCT = {
  ...PRODUCT_SUMMARY,
  description: null,
  track_batch: false,
  track_expiry: false,
  track_serial: false,
  custom_fields: {},
  units: [unit("pcs", "1.000000", true), unit("carton", "24.000000", false)],
  attributes: [],
  variants: [{ id: "v1", sku: "SOAP", values: [], custom_fields: {}, archived_at: null }],
  barcodes: [],
};

const POSTED = {
  id: "d1",
  doc_type: "stock_adjustment",
  status: "posted",
  number: "SA-2026-00001",
  doc_date: "2026-10-18",
  business_id: "b1",
  branch_id: "br1",
  warehouse_id: "w1",
  to_warehouse_id: null,
  reason: "count",
  reference: null,
  narration: null,
  total: "72.0000",
  posted_at: "2026-10-18T10:00:00Z",
  lines: [
    {
      id: "l1",
      line_no: 1,
      variant_id: "v1",
      sku: "SOAP",
      name_en: "Soap",
      name_ar: "صابون",
      unit: "carton",
      qty: "2.000000",
      qty_base: "48.000000",
      base_unit: "pcs",
      direction: "in",
      unit_cost: "36.000000",
      batch_id: null,
      batch_no: null,
      expiry_date: null,
      description: null,
      value: "72.0000",
    },
  ],
  warnings: [{ code: "negative_stock", variant_id: "v1", warehouse_id: "w1", qty_after: "-3.000000" }],
};

function storeApi(overrides = {}) {
  return shopApi({
    signedIn: true,
    "GET /businesses/b1/branches": {
      status: 200,
      body: [{ id: "br1", business_id: "b1", code: "HO", name_en: "Head office", name_ar: "الرئيسي" }],
    },
    "GET /branches/br1/warehouses": { status: 200, body: WAREHOUSES },
    "GET /stock/balances": { status: 200, body: BALANCES },
    "GET /stock/valuation": {
      status: 200,
      body: {
        business_id: "b1",
        stock_value: "-546.0000",
        inventory_account: "-546.0000",
        difference: "0.0000",
      },
    },
    "GET /stock/negative": { status: 200, body: [BALANCES[1]] },
    "GET /products": { status: 200, body: [PRODUCT_SUMMARY] },
    "GET /products/p1": { status: 200, body: PRODUCT },
    "POST /stock-documents": { status: 201, body: POSTED },
    "GET /stock-documents/d1": { status: 200, body: { ...POSTED, warnings: [] } },
    ...overrides,
  });
}

async function chooseSoap(user: ReturnType<typeof userEvent.setup>) {
  const item = await screen.findByLabelText("Item", { selector: "select" });
  await within(item).findByRole("option", { name: "SOAP Soap" });
  await user.selectOptions(item, "p1");
  await waitFor(() => expect(screen.getByLabelText("Unit")).toHaveValue("pcs"));
}

describe("Inventory screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("shows stock per warehouse and that the books agree", async () => {
    window.location.hash = "#/inventory/stock";
    const { api } = storeApi();
    render(<App api={api} />);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    await waitFor(() => expect(rows[1]).toHaveTextContent("SOAPSoapMain warehouse36 pcs54.00"));
    expect(rows[2]).toHaveTextContent("SHOEShoeBack store-10 pcs-600.00");
    expect(await screen.findByText("The stock ledger and the books agree.")).toBeInTheDocument();
  });

  it("brings stock in by the carton and shows the warning the post returned", async () => {
    window.location.hash = "#/inventory/documents/new/stock_adjustment";
    const { api, calls } = storeApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "Stock adjustment" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText("Warehouse")).toHaveValue("w1"));
    await chooseSoap(user);
    await user.selectOptions(screen.getByLabelText("Unit"), "carton");
    await user.type(screen.getByLabelText("Quantity"), "2");
    await user.type(screen.getByLabelText("Cost per unit"), "36");
    await user.type(screen.getByLabelText("Reason"), "count");
    await user.click(screen.getByRole("button", { name: "Post" }));

    expect(await screen.findByText("SA-2026-00001")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Stock in Main warehouse is now -3: below zero.");
    expect(screen.getByRole("table", { name: "Items" })).toHaveTextContent("2 carton(48 pcs)");
    const post = calls.find((c) => c.method === "POST" && c.path === "/stock-documents");
    expect(post?.query).toBe("?post=true");
    expect(post?.body).toMatchObject({
      doc_type: "stock_adjustment",
      branch_id: "br1",
      warehouse_id: "w1",
      reason: "count",
      lines: [{ variant_id: "v1", unit: "carton", qty: "2", direction: "in", unit_cost: "36" }],
    });
  });

  it("transfers to the other warehouse", async () => {
    window.location.hash = "#/inventory/documents/new/stock_transfer";
    const { api, calls } = storeApi({
      "POST /stock-documents": { status: 201, body: { ...POSTED, doc_type: "stock_transfer", warnings: [] } },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await waitFor(() => expect(screen.getByLabelText("To warehouse")).toHaveValue("w2"));
    expect(screen.queryByLabelText("In or out")).toBeNull();
    await chooseSoap(user);
    await user.type(screen.getByLabelText("Quantity"), "4");
    await user.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() =>
      expect(calls.some((c) => c.path === "/stock-documents" && c.method === "POST")).toBe(true),
    );
    const saved = calls.find((c) => c.path === "/stock-documents" && c.method === "POST")!;
    expect(saved.query).toBe("?post=false");
    expect(saved.body).toMatchObject({
      doc_type: "stock_transfer",
      warehouse_id: "w1",
      to_warehouse_id: "w2",
      lines: [{ variant_id: "v1", unit: "pcs", qty: "4" }],
    });
  });

  it("will not save an Item Transfer with only one side", async () => {
    window.location.hash = "#/inventory/documents/new/item_transfer";
    const { api, calls } = storeApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "Item Transfer" })).toBeInTheDocument();
    const remove = await screen.findByRole("button", { name: "Remove line 2" });
    await user.click(remove);
    await chooseSoap(user);
    await user.type(screen.getByLabelText("Quantity"), "1");
    await user.click(screen.getByRole("button", { name: "Post" }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "An Item Transfer takes stock out of one item and puts it into another.",
    );
    expect(calls.some((c) => c.path === "/stock-documents" && c.method === "POST")).toBe(false);
  });

  it("lists negative stock", async () => {
    window.location.hash = "#/inventory/negative";
    const { api } = storeApi();
    render(<App api={api} />);

    const table = await screen.findByRole("table");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(table).toHaveTextContent("SHOE");
  });
});
