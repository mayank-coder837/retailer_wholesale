import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { shopApi } from "@/test/shop-api";

const field = (key: string, en: string, ar: string, required = false) => ({
  key,
  label_en: en,
  label_ar: ar,
  required,
  example: "",
  hint_en: "",
  hint_ar: "",
});

const KINDS = [
  {
    key: "parties",
    label_en: "Customers and suppliers",
    label_ar: "العملاء والموردون",
    description_en: "Names, TRN, contact details, credit terms.",
    description_ar: "الأسماء والرقم الضريبي وبيانات الاتصال.",
    order: 20,
    fields: [field("name_en", "Name", "الاسم", true), field("trn", "TRN", "الرقم الضريبي")],
    options: [
      {
        key: "default_type",
        label_en: "When the file does not say, these are",
        label_ar: "عندما لا يحدد الملف، هؤلاء",
        values: ["customer", "supplier", "both"],
        default: "customer",
        required: false,
        kind: "choice",
      },
    ],
    permissions: [],
    undoable: false,
  },
  {
    key: "opening_balances",
    label_en: "Opening trial balance",
    label_ar: "ميزان المراجعة الافتتاحي",
    description_en: "Each account's balance on the day the old books stop.",
    description_ar: "رصيد كل حساب في اليوم الذي تتوقف فيه الدفاتر القديمة.",
    order: 40,
    fields: [
      field("account", "Account", "الحساب", true),
      field("debit", "Debit", "مدين"),
      field("credit", "Credit", "دائن"),
    ],
    options: [
      {
        key: "opening_date",
        label_en: "Opening date",
        label_ar: "تاريخ الافتتاح",
        values: [],
        default: "",
        required: true,
        kind: "date",
      },
      {
        key: "branch",
        label_en: "Branch",
        label_ar: "الفرع",
        values: [],
        default: "",
        required: false,
        kind: "branch",
      },
    ],
    permissions: ["journal.create", "journal.post"],
    undoable: true,
  },
];

const batch = (extra: Record<string, unknown> = {}) => ({
  id: "batch-1",
  business_id: "b1",
  kind: "parties",
  status: "uploaded",
  filename: "customers.csv",
  size: 120,
  sha256: "0".repeat(64),
  headers: ["Party Name", "TRN"],
  row_count: 3,
  mapping: {},
  options: {},
  counts: {},
  result: {},
  created_at: "2026-11-15T10:00:00Z",
  committed_at: null,
  suggested_mapping: { name_en: 0, trn: 1 },
  notes: [],
  sample: [["Gulf Fabrics LLC", "100123456700003"]],
  duplicate_of: null,
  ...extra,
});

const CHECKED = {
  batch_id: "batch-1",
  total: 3,
  new: 1,
  exists: 0,
  skipped: 0,
  errors: 2,
  truncated: false,
  summary: {},
  rows: [
    {
      number: 3,
      status: "error",
      label: "Short TRN Co",
      problems: [{ field: "trn", code: "invalid", message: "String should match pattern" }],
      note: null,
      note_code: null,
    },
    {
      number: 4,
      status: "error",
      label: "Twice Co",
      problems: [{ field: null, code: "duplicate_in_file", message: "The same party is on row 2." }],
      note: null,
      note_code: null,
    },
    { number: 2, status: "new", label: "Gulf Fabrics LLC", problems: [], note: null, note_code: null },
  ],
};
const CLEAN = {
  ...CHECKED,
  errors: 0,
  new: 3,
  rows: [{ number: 2, status: "new", label: "Gulf Fabrics LLC", problems: [], note: null, note_code: null }],
};

const base = {
  signedIn: true,
  "GET /imports/kinds": { status: 200, body: KINDS },
  "GET /imports": { status: 200, body: [] },
};

async function toMatchStep(api: ReturnType<typeof shopApi>["api"]) {
  const user = userEvent.setup();
  window.location.hash = "#/settings/import";
  render(<App api={api} />);
  await user.click(await screen.findByRole("button", { name: /Choose Customers and suppliers/ }));
  await user.upload(
    screen.getByLabelText("File"),
    new File(["Party Name,TRN\nGulf,1"], "customers.csv", { type: "text/csv" }),
  );
  await user.click(screen.getByRole("button", { name: "Upload and read the file" }));
  await screen.findByRole("heading", { name: "Match the columns" });
  return user;
}

describe("Import data", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("lists the kinds in order, and recent imports with Undo for an opening import", async () => {
    window.location.hash = "#/settings/import";
    const recent = batch({
      id: "old-1",
      kind: "opening_balances",
      status: "committed",
      filename: "tb.csv",
      committed_at: "2026-11-14T10:00:00Z",
    });
    const { api, calls } = shopApi({
      ...base,
      "GET /imports": { status: 200, body: [recent] },
      "POST /imports/old-1/undo": { status: 200, body: { ...recent, status: "undone" } },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    expect(await screen.findByText("Customers and suppliers")).toBeInTheDocument();
    expect(screen.getByText("Opening trial balance")).toBeInTheDocument();
    const recentTable = await screen.findByRole("table");
    expect(within(recentTable).getByText("tb.csv")).toBeInTheDocument();
    expect(within(recentTable).getByText("Imported")).toBeInTheDocument();
    expect(within(recentTable).getByRole("link", { name: "Opening balances check" })).toHaveAttribute(
      "href",
      "/#/settings/import/reconciliation",
    );
    await user.click(within(recentTable).getByRole("button", { name: "Undo" }));
    expect(screen.getByText("Undo this import?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Yes, undo" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/imports/old-1/undo")).toBe(true));
  });

  it("uploads a file, matches its columns, and lists the wrong rows in words", async () => {
    const { api, calls } = shopApi({
      ...base,
      "POST /imports": { status: 201, body: batch() },
      "POST /imports/batch-1/check": { status: 200, body: CHECKED },
    });
    const user = await toMatchStep(api);
    const upload = calls.find((c) => c.method === "POST" && c.path === "/imports");
    expect(upload?.body).toMatchObject({
      business_id: "b1",
      kind: "parties",
      file: expect.stringContaining("[file "),
    });

    // The suggestion is already chosen; one example value shows what the column holds.
    expect(screen.getByLabelText("Name")).toHaveValue("0");
    expect(screen.getByLabelText("TRN")).toHaveValue("1");
    expect(screen.getByText("Gulf Fabrics LLC")).toBeInTheDocument();
    expect(screen.getByLabelText("When the file does not say, these are")).toHaveValue("customer");

    await user.click(screen.getByRole("button", { name: "Check the file" }));
    const table = await screen.findByRole("table", { name: "Rows of the file" });
    const check = calls.find((c) => c.path === "/imports/batch-1/check");
    expect(check?.body).toEqual({
      mapping: { name_en: 0, trn: 1 },
      options: { default_type: "customer" },
    });
    const wrong = within(table).getByText("Short TRN Co").closest("tr")!;
    expect(wrong).toHaveTextContent("3");
    expect(wrong).toHaveTextContent("TRN: Not valid.");
    expect(within(table).getByText("Twice Co").closest("tr")).toHaveTextContent("Appears twice in the file.");
    expect(screen.getByRole("status")).toHaveTextContent("Fix the file and upload it again");
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Change the columns" }));
    expect(await screen.findByRole("heading", { name: "Match the columns" })).toBeInTheDocument();
  });

  it("imports when the file is clean and says what was made", async () => {
    const { api, calls } = shopApi({
      ...base,
      "POST /imports": { status: 201, body: batch() },
      "POST /imports/batch-1/check": { status: 200, body: CLEAN },
      "POST /imports/batch-1/commit": {
        status: 200,
        body: batch({
          status: "committed",
          result: { counts: { created: 3, customers: 3, suppliers: 0, skipped: 0 } },
        }),
      },
    });
    const user = await toMatchStep(api);
    await user.click(screen.getByRole("button", { name: "Check the file" }));
    const importButton = await screen.findByRole("button", { name: "Import" });
    expect(importButton).toBeEnabled();
    expect(screen.getByRole("status")).toHaveTextContent("all or nothing");
    await user.click(importButton);
    expect(await screen.findByText("Imported.")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/imports/batch-1/commit")).toBe(true);
    expect(screen.getByText("Created").nextSibling).toHaveTextContent("3");
    expect(screen.getByRole("link", { name: "Customers and suppliers" })).toHaveAttribute(
      "href",
      "/#/parties",
    );
    await user.click(screen.getByRole("button", { name: "Import another file" }));
    expect(await screen.findByText(/Best order/)).toBeInTheDocument();
  });

  it("warns about a file already imported and explains what the reader found", async () => {
    const { api } = shopApi({
      ...base,
      "POST /imports": {
        status: 201,
        body: batch({
          duplicate_of: "old-1",
          notes: [
            { code: "debit_negative", message: "English fallback", count: 0 },
            { code: "addresses_left_out", message: "x", count: 2 },
          ],
        }),
      },
    });
    await toMatchStep(api);
    expect(screen.getByText(/This exact file was already imported/)).toBeInTheDocument();
    expect(screen.getByText(/Debit balances are written with a minus sign/)).toBeInTheDocument();
    expect(screen.getByText(/2 address\(es\) were left out/)).toBeInTheDocument();
  });

  it("names a problem with the whole file, such as a missing fiscal year", async () => {
    const { api } = shopApi({
      ...base,
      "POST /imports": { status: 201, body: batch({ kind: "parties" }) },
      "POST /imports/batch-1/check": {
        status: 422,
        body: { code: "opening_no_fiscal_year", title: "x", status: 422 },
      },
    });
    const user = await toMatchStep(api);
    await user.click(screen.getByRole("button", { name: "Check the file" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "There is no fiscal year for the opening date",
    );
  });

  it("asks for the opening date and branch of an opening import", async () => {
    window.location.hash = "#/settings/import";
    const { api } = shopApi({
      ...base,
      "GET /businesses/b1/branches": {
        status: 200,
        body: [
          { id: "br1", code: "HO", name_en: "Head office", name_ar: "المكتب الرئيسي", emirate: "dubai" },
        ],
      },
      "POST /imports": {
        status: 201,
        body: batch({
          kind: "opening_balances",
          headers: ["Account", "Debit"],
          suggested_mapping: { account: 0, debit: 1, credit: null },
        }),
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    await user.click(await screen.findByRole("button", { name: /Choose Opening trial balance/ }));
    await user.upload(screen.getByLabelText("File"), new File(["a"], "tb.csv", { type: "text/csv" }));
    await user.click(screen.getByRole("button", { name: "Upload and read the file" }));
    const check = await screen.findByRole("button", { name: "Check the file" });
    expect(check).toBeDisabled(); // an opening date is needed
    await user.type(screen.getByLabelText("Opening date"), "2026-03-31");
    expect(check).toBeEnabled();
    expect(screen.getByLabelText("Branch")).toHaveValue("");
    expect(await screen.findByRole("option", { name: "Head office" })).toBeInTheDocument();
    expect(screen.getByLabelText("Credit")).toHaveValue("");
  });

  it("works in Arabic", async () => {
    await setLanguage("ar");
    window.location.hash = "#/settings/import";
    const { api } = shopApi({
      ...base,
      "POST /imports": { status: 201, body: batch() },
      "POST /imports/batch-1/check": { status: 200, body: CHECKED },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    expect(await screen.findByRole("heading", { name: "استيراد البيانات" })).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /اختر العملاء والموردون/ }));
    await user.upload(screen.getByLabelText("الملف"), new File(["a"], "c.csv", { type: "text/csv" }));
    await user.click(screen.getByRole("button", { name: "ارفع الملف واقرأه" }));
    await user.click(await screen.findByRole("button", { name: "افحص الملف" }));
    const table = await screen.findByRole("table", { name: "صفوف الملف" });
    expect(within(table).getByText("Short TRN Co").closest("tr")).toHaveTextContent(
      "الرقم الضريبي: غير صالح.",
    );
    expect(within(table).getByText("Twice Co").closest("tr")).toHaveTextContent("يظهر مرتين في الملف.");
  });
});

describe("Opening balances check", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  const row = (code: string, name: string, file: string, books: string, control = false) => ({
    account_id: `a${code}`,
    code,
    name_en: name,
    name_ar: name,
    control,
    file_debit: "0.0000",
    file_credit: "0.0000",
    file_balance: file,
    books_balance: books,
    difference: String(Number(books) - Number(file)),
  });
  const recon = (rows: ReturnType<typeof row>[], equity: string, ties: boolean) => ({
    business_id: "b1",
    imported: true,
    batch_id: "batch-1",
    opening_date: "2026-03-31",
    rows,
    file_debit: "79450.0000",
    file_credit: "79450.0000",
    books_debit: "79450.0000",
    books_credit: "79450.0000",
    equity_code: "3900",
    equity_balance: equity,
    ties,
  });

  it("says everything ties, with opening balance equity at zero", async () => {
    window.location.hash = "#/settings/import/reconciliation";
    const { api } = shopApi({
      signedIn: true,
      "GET /imports/reconciliation": {
        status: 200,
        body: recon(
          [
            row("1110", "Cash", "20000.0000", "20000.0000"),
            row("1200", "Accounts receivable", "8000.0000", "8000.0000", true),
          ],
          "0.0000",
          true,
        ),
      },
    });
    render(<App api={api} />);
    expect(await screen.findByRole("status")).toHaveTextContent("Everything ties.");
    const table = screen.getByRole("table", { name: "Opening balances check" });
    expect(within(table).getByText("Cash").closest("tr")).toHaveTextContent("20,000.00");
    expect(within(table).getByText("Accounts receivable").closest("tr")).toHaveTextContent(
      "from bills / stock",
    );
    expect(within(table).getByText("Opening balance equity").closest("tr")).toHaveTextContent(
      "Zero: all ties",
    );
    expect(screen.queryByText(/A difference on receivables/)).toBeNull();
  });

  it("shows exactly where the books differ from the file", async () => {
    window.location.hash = "#/settings/import/reconciliation";
    const { api } = shopApi({
      signedIn: true,
      "GET /imports/reconciliation": {
        status: 200,
        body: recon([row("1200", "Accounts receivable", "9000.0000", "8000.0000", true)], "1000.0000", false),
      },
    });
    render(<App api={api} />);
    expect(await screen.findByRole("status")).toHaveTextContent("Differences found.");
    const table = screen.getByRole("table", { name: "Opening balances check" });
    expect(within(table).getByText("Accounts receivable").closest("tr")).toHaveTextContent("-1,000.00");
    expect(within(table).getByText("Opening balance equity").closest("tr")).toHaveTextContent("Not zero");
    expect(screen.getByText(/A difference on receivables/)).toBeInTheDocument();
  });

  it("says when nothing has been imported yet, and works in Arabic", async () => {
    await setLanguage("ar");
    window.location.hash = "#/settings/import/reconciliation";
    const { api } = shopApi({
      signedIn: true,
      "GET /imports/reconciliation": {
        status: 200,
        body: { ...recon([], "0.0000", false), imported: false, batch_id: null, opening_date: null },
      },
    });
    render(<App api={api} />);
    expect(await screen.findByText(/لم يُستورد ميزان مراجعة افتتاحي بعد/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "استورد واحدا" })).toHaveAttribute("href", "/#/settings/import");
  });
});
