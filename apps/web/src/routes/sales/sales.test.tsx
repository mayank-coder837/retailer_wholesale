import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { shopApi } from "@/test/shop-api";

const CUSTOMER = {
  id: "c1",
  code: "C0001",
  name_en: "Ali Traders",
  name_ar: "علي",
  is_customer: true,
  is_supplier: false,
  archived_at: null,
};

const WAREHOUSE = {
  id: "w1",
  business_id: "b1",
  branch_id: "br1",
  code: "MAIN",
  name_en: "Main warehouse",
  name_ar: "الرئيسي",
  is_default: true,
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

const PRODUCT_SUMMARY = {
  id: "p1",
  sku: "FAB",
  name_en: "Fabric",
  name_ar: "قماش",
  product_type: "stock",
  base_unit: "pcs",
  category_id: null,
  brand_id: null,
  variant_count: 1,
  archived_at: null,
};

const PRODUCT = {
  ...PRODUCT_SUMMARY,
  description: null,
  track_batch: false,
  track_expiry: false,
  track_serial: false,
  custom_fields: {},
  units: [unit("pcs", "1.000000", true)],
  attributes: [],
  variants: [{ id: "v1", sku: "FAB", values: [], custom_fields: {}, archived_at: null }],
  barcodes: [],
};

const line = (over: Record<string, unknown> = {}) => ({
  id: "l1",
  line_no: 1,
  variant_id: "v1",
  sku: "FAB",
  name_en: "Fabric",
  name_ar: "قماش",
  unit: "pcs",
  qty: "10.000000",
  qty_base: "10.000000",
  base_unit: "pcs",
  unit_price: "12.000000",
  price_source: null,
  below_min: false,
  net_amount: "120.0000",
  vat_rate: "5",
  vat_amount: "6.0000",
  line_total: "126.0000",
  source_line_id: null,
  batch_id: null,
  description: null,
  cost: null,
  ...over,
});

const doc = (over: Record<string, unknown> = {}) => ({
  id: "inv1",
  doc_type: "sales_invoice",
  status: "posted",
  number: "INV-2026-00001",
  doc_date: "2026-10-25",
  business_id: "b1",
  branch_id: "br1",
  party_id: "c1",
  warehouse_id: "w1",
  cash_account_id: null,
  valid_until: null,
  due_date: "2026-11-24",
  reference: null,
  narration: null,
  subtotal: "120.0000",
  tax_total: "6.0000",
  round_off: "0.0000",
  total: "126.0000",
  posted_at: "2026-10-25T10:00:00Z",
  lines: [line()],
  progress: null,
  warnings: [],
  ...over,
});

const state = (over: Record<string, unknown> = {}) => ({
  id: "inv1",
  doc_type: "sales_invoice",
  status: "posted",
  number: "INV-2026-00001",
  doc_date: "2026-10-25",
  cancel_reason: null,
  cancelled_at: null,
  sent_at: null,
  reversal_id: null,
  reversal_doc_type: null,
  reversal_number: null,
  reversal_date: null,
  reversal_of: null,
  reissue_of: null,
  ...over,
});

const QUOTE = {
  price: "12.0000",
  price_source: "last_price:INV-2026-00007",
  source: "last_price",
  source_name: "INV-2026-00007",
  converted: false,
  min_price: "9.0000",
  below_min: false,
  may_go_below_min: false,
};

function sellerApi(overrides = {}) {
  return shopApi({
    signedIn: true,
    "GET /businesses/b1/branches": {
      status: 200,
      body: [{ id: "br1", business_id: "b1", code: "HO", name_en: "Head office", name_ar: "الرئيسي" }],
    },
    "GET /branches/br1/warehouses": { status: 200, body: [WAREHOUSE] },
    "GET /parties": { status: 200, body: [CUSTOMER] },
    "GET /parties/c1": { status: 200, body: CUSTOMER },
    "GET /accounts": { status: 200, body: [] },
    "GET /products": { status: 200, body: [PRODUCT_SUMMARY] },
    "GET /products/p1": { status: 200, body: PRODUCT },
    "POST /pricing/quote": { status: 200, body: QUOTE },
    "GET /parties/c1/sales-history": { status: 200, body: [] },
    "GET /documents/released-numbers": { status: 200, body: [] },
    "GET /sales-documents/inv1": { status: 200, body: doc() },
    "GET /sales-documents/inv1/open-lines": { status: 200, body: [] },
    "GET /documents/inv1": { status: 200, body: state() },
    ...overrides,
  });
}

async function chooseCustomerAndItem(user: ReturnType<typeof userEvent.setup>) {
  const customer = await screen.findByLabelText("Customer", { selector: "select" });
  await within(customer).findByRole("option", { name: "C0001 Ali Traders" });
  await user.selectOptions(customer, "c1");
  const item = screen.getByLabelText("Item", { selector: "select" });
  await within(item).findByRole("option", { name: "FAB Fabric" });
  await user.selectOptions(item, "p1");
  await waitFor(() => expect(screen.getByLabelText("Unit")).toHaveValue("pcs"));
}

describe("Sales screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("lists sales documents with their customer", async () => {
    window.location.hash = "#/sales";
    const summary = { ...doc(), lines: undefined, progress: undefined, warnings: undefined };
    const { api } = sellerApi({ "GET /sales-documents": { status: 200, body: [summary] } });
    render(<App api={api} />);

    const table = await screen.findByRole("table");
    await waitFor(() =>
      expect(within(table).getAllByRole("row")[1]).toHaveTextContent(
        "2026-10-25INV-2026-00001Sales invoiceAli TradersPosted126.00",
      ),
    );
    expect(screen.queryByRole("link", { name: /return/i })).toBeNull();
  });

  it("suggests the last price to this customer and posts it with its source", async () => {
    window.location.hash = "#/sales/new/sales_invoice";
    const { api, calls } = sellerApi({ "POST /sales-documents": { status: 201, body: doc() } });
    const user = userEvent.setup();
    render(<App api={api} />);

    await chooseCustomerAndItem(user);
    await user.type(screen.getByLabelText("Quantity"), "10");
    await waitFor(() => expect(screen.getByLabelText("Price per unit")).toHaveValue("12"));
    expect(screen.getByText("Last price to this customer (INV-2026-00007)")).toBeInTheDocument();
    expect(screen.getByLabelText("Totals")).toHaveTextContent("Subtotal120.00VAT6.00Total126.00");
    await user.click(screen.getByRole("button", { name: "Post" }));

    await screen.findByRole("heading", { name: /INV-2026-00001/ });
    const post = calls.find((c) => c.method === "POST" && c.path === "/sales-documents");
    expect(post?.body).toMatchObject({
      doc_type: "sales_invoice",
      party_id: "c1",
      warehouse_id: "w1",
      cash_account_id: null,
      lines: [
        {
          variant_id: "v1",
          unit: "pcs",
          qty: "10",
          unit_price: "12",
          price_source: "last_price:INV-2026-00007",
        },
      ],
    });
  });

  it("warns below the minimum price and keeps a typed price as manual", async () => {
    window.location.hash = "#/sales/new/sales_order";
    const { api } = sellerApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    await chooseCustomerAndItem(user);
    await user.type(screen.getByLabelText("Quantity"), "1");
    const price = screen.getByLabelText("Price per unit");
    await waitFor(() => expect(price).toHaveValue("12"));
    await user.clear(price);
    await user.type(price, "8");
    expect(screen.getByText("Below the minimum price (9)")).toBeInTheDocument();
  });

  it("adds an item from the customer's history at its last price", async () => {
    window.location.hash = "#/sales/new/sales_invoice";
    const history = [
      {
        doc_date: "2026-10-01",
        document_id: "old",
        number: "INV-2026-00007",
        variant_id: "v1",
        sku: "FAB",
        name_en: "Fabric",
        name_ar: "قماش",
        qty: "3.000000",
        unit: "pcs",
        unit_price: "11.500000",
      },
    ];
    const { api, calls } = sellerApi({
      "GET /parties/c1/sales-history": { status: 200, body: history },
      "POST /sales-documents": { status: 201, body: doc() },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    const customer = await screen.findByLabelText("Customer", { selector: "select" });
    await within(customer).findByRole("option", { name: "C0001 Ali Traders" });
    await user.selectOptions(customer, "c1");
    const panel = await screen.findByRole("region", { name: "What this customer bought" });
    await user.click(within(panel).getByRole("button", { name: "Add FAB" }));
    expect(screen.getByLabelText("Price per unit")).toHaveValue("11.5");
    await user.click(screen.getByRole("button", { name: "Post" }));

    await screen.findByRole("heading", { name: /INV-2026-00001/ });
    const post = calls.find((c) => c.method === "POST" && c.path === "/sales-documents");
    expect(post?.body).toMatchObject({
      lines: [
        {
          variant_id: "v1",
          unit: "pcs",
          qty: "1",
          unit_price: "11.5",
          price_source: "last_price:INV-2026-00007",
        },
      ],
    });
  });

  it("lets someone allowed post past a credit block with a reason", async () => {
    window.location.hash = "#/sales/new/sales_order";
    const order = doc({ id: "so1", doc_type: "sales_order", number: "SO-2026-00001" });
    let drafts = 0;
    const { api, calls } = sellerApi({
      "POST /sales-documents": (body: unknown) => {
        drafts += 1;
        // The first try posts at once and is blocked; the second saves the draft the override posts.
        if ((body as { lines: unknown[] }) && drafts === 1)
          return {
            status: 409,
            body: {
              code: "credit_blocked",
              may_override: true,
              reasons: ["over_limit"],
              exposure: "420.0000",
              credit_limit: "300.0000",
              available: "-120.0000",
            },
          };
        return { status: 201, body: { ...order, status: "draft", number: null } };
      },
      "POST /sales-documents/so1/post": {
        status: 200,
        body: { ...order, warnings: [{ code: "credit_overridden", reasons: ["over_limit"] }] },
      },
      "GET /sales-documents/so1": { status: 200, body: order },
      "GET /sales-documents/so1/open-lines": { status: 200, body: [] },
      "GET /documents/so1": { status: 200, body: state({ id: "so1", doc_type: "sales_order" }) },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await chooseCustomerAndItem(user);
    await user.type(screen.getByLabelText("Quantity"), "10");
    await waitFor(() => expect(screen.getByLabelText("Price per unit")).toHaveValue("12"));
    await user.click(screen.getByRole("button", { name: "Post" }));
    expect(await screen.findByText(/This customer is blocked/)).toBeInTheDocument();
    expect(screen.getByText("Owed with this document: 420.00 · limit 300.00")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Why let it through?"), "pays Thursday");
    await user.click(screen.getByRole("button", { name: "Post anyway" }));

    expect(await screen.findByText(/Posted past the credit block/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/sales-documents/so1/post")?.body).toEqual({
      override_lock_reason: null,
      credit_override_reason: "pays Thursday",
      reuse_number: null,
    });
  });

  it("marks a posted invoice as sent", async () => {
    window.location.hash = "#/sales/inv1";
    let sent = false;
    const { api, calls } = sellerApi({
      "GET /documents/inv1": () => ({
        status: 200,
        body: state(sent ? { sent_at: "2026-10-25T11:00:00Z" } : {}),
      }),
      "POST /documents/inv1/sent": () => {
        sent = true;
        return { status: 200, body: state({ sent_at: "2026-10-25T11:00:00Z" }) };
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.click(await screen.findByRole("button", { name: "Mark as sent" }));
    expect(await screen.findByText("Sent")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/documents/inv1/sent")).toBe(true);
    expect(screen.queryByRole("button", { name: "Mark as sent" })).toBeNull();
  });
});

describe("Printing a sales document", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("leads from a posted invoice to its print preview, in English or Arabic", async () => {
    window.location.hash = "#/sales/inv1";
    const { api, calls } = sellerApi({
      "GET /documents/inv1/print": { status: 200, body: "<html><body><h1>TAX INVOICE</h1></body></html>" },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.click(await screen.findByRole("link", { name: "Print / Save as PDF" }));
    const frame = await screen.findByTitle("Print preview");
    expect(frame.getAttribute("srcdoc")).toContain("TAX INVOICE");
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin allow-modals"); // no scripts
    const print = calls.filter((c) => c.path === "/documents/inv1/print");
    expect(print[0]?.query).toBe("?lang=en");
    expect(print[0]?.authorization).toMatch(/^Bearer /);

    await user.click(screen.getByRole("button", { name: "العربية" }));
    await waitFor(() =>
      expect(calls.filter((c) => c.path === "/documents/inv1/print").map((c) => c.query)).toContain(
        "?lang=ar",
      ),
    );
  });

  it("offers no print on a draft", async () => {
    window.location.hash = "#/sales/inv1";
    const { api } = sellerApi({
      "GET /sales-documents/inv1": { status: 200, body: doc({ status: "draft", number: null }) },
    });
    render(<App api={api} />);
    await screen.findByRole("button", { name: "Post" });
    expect(screen.queryByRole("link", { name: "Print / Save as PDF" })).toBeNull();
  });

  it("explains a refusal", async () => {
    window.location.hash = "#/sales/inv1/print";
    const { api } = sellerApi({
      "GET /documents/inv1/print": { status: 403, body: { code: "permission_denied" } },
    });
    render(<App api={api} />);
    expect(
      await screen.findByText("You don't have permission to print or export this document."),
    ).toBeInTheDocument();
  });
});
