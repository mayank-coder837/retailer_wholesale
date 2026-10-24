import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { shopApi } from "@/test/shop-api";

const SUPPLIER = {
  id: "s1",
  code: "S0001",
  name_en: "Sharjah Mills",
  name_ar: "مصانع الشارقة",
  is_customer: false,
  is_supplier: true,
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

const line = (over: Record<string, unknown> = {}) => ({
  id: "l1",
  line_no: 1,
  variant_id: "v1",
  sku: "FAB",
  name_en: "Fabric",
  name_ar: "قماش",
  account_id: null,
  unit: "roll",
  qty: "2.000000",
  qty_base: "100.000000",
  base_unit: "m",
  unit_price: "380.000000",
  net_amount: "760.0000",
  vat_rate: "5",
  vat_amount: "38.0000",
  line_total: "798.0000",
  source_line_id: null,
  batch_id: null,
  batch_no: null,
  expiry_date: null,
  description: null,
  cleared_value: null,
  ...over,
});

const doc = (over: Record<string, unknown> = {}) => ({
  id: "po1",
  doc_type: "purchase_order",
  status: "posted",
  number: "PO-2026-00001",
  doc_date: "2026-10-24",
  business_id: "b1",
  branch_id: "br1",
  party_id: "s1",
  warehouse_id: null,
  supplier_ref: null,
  due_date: null,
  reference: null,
  narration: null,
  subtotal: "760.0000",
  tax_total: "38.0000",
  total: "798.0000",
  posted_at: "2026-10-24T10:00:00Z",
  lines: [line()],
  progress: "open",
  ...over,
});

const OPEN_ORDER_LINE = {
  line_id: "l1",
  variant_id: "v1",
  sku: "FAB",
  name_en: "Fabric",
  name_ar: "قماش",
  unit: "roll",
  factor_to_base: "50.000000",
  qty_open_base: "100.000000",
  qty_open: "2.000000",
  unit_price: "380.000000",
  vat_rate: "5",
  value_open: null,
};

const state = (over: Record<string, unknown> = {}) => ({
  id: "po1",
  doc_type: "purchase_order",
  status: "posted",
  number: "PO-2026-00001",
  doc_date: "2026-10-24",
  cancel_reason: null,
  cancelled_at: null,
  reversal_id: null,
  reversal_doc_type: null,
  reversal_number: null,
  reversal_date: null,
  reversal_of: null,
  reissue_of: null,
  ...over,
});

function buyerApi(overrides = {}) {
  return shopApi({
    signedIn: true,
    "GET /businesses/b1/branches": {
      status: 200,
      body: [{ id: "br1", business_id: "b1", code: "HO", name_en: "Head office", name_ar: "الرئيسي" }],
    },
    "GET /branches/br1/warehouses": { status: 200, body: [WAREHOUSE] },
    "GET /parties": { status: 200, body: [SUPPLIER] },
    "GET /parties/s1": { status: 200, body: SUPPLIER },
    "GET /accounts": { status: 200, body: [] },
    "GET /documents/released-numbers": { status: 200, body: [] },
    "GET /purchase-documents/po1": { status: 200, body: doc() },
    "GET /purchase-documents/po1/open-lines": { status: 200, body: [OPEN_ORDER_LINE] },
    "GET /documents/po1": { status: 200, body: state() },
    ...overrides,
  });
}

describe("Purchasing screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("lists purchase documents with their supplier", async () => {
    window.location.hash = "#/purchases";
    const summary = { ...doc(), lines: undefined, progress: undefined };
    const { api } = buyerApi({ "GET /purchase-documents": { status: 200, body: [summary] } });
    render(<App api={api} />);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    await waitFor(() =>
      expect(rows[1]).toHaveTextContent("2026-10-24PO-2026-00001Purchase orderSharjah MillsPosted798.00"),
    );
    expect(screen.getByRole("link", { name: "New order" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /return/i })).toBeNull(); // a return starts from its source
  });

  it("receives an order from its open lines, without VAT on the receipt", async () => {
    window.location.hash = "#/purchases/po1";
    const receipt = doc({
      id: "grn1",
      doc_type: "goods_receipt",
      number: "GRN-2026-00001",
      warehouse_id: "w1",
    });
    const { api, calls } = buyerApi({
      "POST /purchase-documents": { status: 201, body: receipt },
      "GET /purchase-documents/grn1": { status: 200, body: receipt },
      "GET /purchase-documents/grn1/open-lines": { status: 200, body: [] },
      "GET /documents/grn1": { status: 200, body: state({ id: "grn1", doc_type: "goods_receipt" }) },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.click(await screen.findByRole("link", { name: "Receive goods" }));
    expect(await screen.findByRole("heading", { name: "Goods received" })).toBeInTheDocument();
    expect(screen.getByText("Still open: 2 roll")).toBeInTheDocument();
    expect(screen.queryByLabelText("VAT")).toBeNull();
    await user.clear(screen.getByLabelText("Quantity"));
    await user.type(screen.getByLabelText("Quantity"), "1");
    const totals = screen.getByLabelText("Totals");
    expect(totals).toHaveTextContent("Subtotal380.00VAT0.00Total380.00");
    await user.click(screen.getByRole("button", { name: "Post" }));

    await screen.findByRole("heading", { name: /GRN-2026-00001/ });
    const post = calls.find((c) => c.method === "POST" && c.path === "/purchase-documents");
    expect(post?.query).toBe("?post=true");
    expect(post?.body).toMatchObject({
      doc_type: "goods_receipt",
      party_id: "s1",
      warehouse_id: "w1",
      lines: [{ variant_id: "v1", unit: "roll", qty: "1", unit_price: "380", source_line_id: "l1" }],
    });
    expect((post?.body as { lines: Record<string, unknown>[] }).lines[0]).not.toHaveProperty("vat_rate");
  });

  it("takes a number freed by a cancelled bill when asked", async () => {
    window.location.hash = "#/purchases/new/purchase_bill?from=po1";
    const bill = doc({ id: "pb1", doc_type: "purchase_bill", number: "PB-2026-00001" });
    const { api, calls } = buyerApi({
      "GET /documents/released-numbers": {
        status: 200,
        body: [{ number: 1, formatted: "PB-2026-00001", fiscal_year: "2026", cancelled_document_id: "old" }],
      },
      "POST /purchase-documents": { status: 201, body: { ...bill, status: "draft", number: null } },
      "POST /purchase-documents/pb1/post": { status: 200, body: bill },
      "GET /purchase-documents/pb1": { status: 200, body: bill },
      "GET /purchase-documents/pb1/open-lines": { status: 200, body: [] },
      "GET /documents/pb1": { status: 200, body: state({ id: "pb1", doc_type: "purchase_bill" }) },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    const number = await screen.findByLabelText("Number");
    await user.selectOptions(number, "1");
    await user.type(screen.getByLabelText("Supplier's bill no."), "SM-1");
    await user.click(screen.getByRole("button", { name: "Post" }));

    await screen.findByRole("heading", { name: /PB-2026-00001/ });
    const draft = calls.find((c) => c.method === "POST" && c.path === "/purchase-documents");
    expect(draft?.query).toBe("");
    expect(draft?.body).toMatchObject({ doc_type: "purchase_bill", supplier_ref: "SM-1" });
    const post = calls.find((c) => c.path === "/purchase-documents/pb1/post");
    expect(post?.body).toEqual({ override_lock_reason: null, reuse_number: 1 });
  });

  it("cancels a posted document with a reason and shows it cancelled", async () => {
    window.location.hash = "#/purchases/po1";
    let cancelled = false;
    const { api, calls } = buyerApi({
      "GET /documents/po1": () => ({
        status: 200,
        body: cancelled
          ? state({
              status: "cancelled",
              cancel_reason: "Ordered twice",
              cancelled_at: "2026-10-24T12:00:00Z",
            })
          : state(),
      }),
      "POST /documents/po1/cancel": () => {
        cancelled = true;
        return { status: 200, body: state({ status: "cancelled" }) };
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("button", { name: "Reverse" })).toBeNull(); // an order is only cancelled
    const form = screen.getByRole("form", { name: "Cancel" });
    const confirm = within(form).getByRole("button", { name: "Cancel the document" });
    expect(confirm).toBeDisabled(); // a reason is needed
    await user.type(within(form).getByLabelText("Reason"), "Ordered twice");
    await user.click(confirm);

    expect(await screen.findByText(/Cancelled on 2026-10-24/)).toBeInTheDocument();
    expect(screen.getByText(/Ordered twice/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/documents/po1/cancel")?.body).toEqual({ reason: "Ordered twice" });
  });

  it("explains that a return starts from its bill or receipt", async () => {
    window.location.hash = "#/purchases/new/purchase_return";
    const { api, calls } = buyerApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "A return starts from the bill or goods receipt",
    );
    await user.click(screen.getByRole("button", { name: "Post" }));
    expect(calls.some((c) => c.path.startsWith("/purchase-documents"))).toBe(false);
  });
});
