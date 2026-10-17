import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { allocationsOf, ON_ACCOUNT } from "@/routes/accounts/bill-wise-model";
import { fromFils, money, toFils, vatFils } from "@/routes/accounts/queries";
import { shopApi } from "@/test/shop-api";

const account = (
  id: string,
  code: string,
  name_en: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> => ({
  id,
  business_id: "b1",
  code,
  name_en,
  name_ar: `ح ${name_en}`,
  parent_id: null,
  is_group: false,
  nature: "asset",
  account_type: "current_asset",
  allow_manual_posting: true,
  is_system: true,
  archived_at: null,
  roles: [],
  balance: "0.0000",
  ...extra,
});

const ACCOUNTS = [
  account("g1", "1000", "Assets", { is_group: true, account_type: null, balance: "-150.0000" }),
  account("cash", "1110", "Cash", { parent_id: "g1", account_type: "cash", balance: "850.0000" }),
  account("ar", "1200", "Accounts receivable", {
    parent_id: "g1",
    account_type: "receivable",
    roles: ["ar_control"],
    balance: "-1000.0000",
  }),
  account("g6", "6000", "Operating expenses", { is_group: true, account_type: null, nature: "expense" }),
  account("rent", "6100", "Rent", { parent_id: "g6", nature: "expense", account_type: "expense" }),
  account("misc", "4800", "Other income", { nature: "income", account_type: "other_income" }),
];

const PARTIES = [
  {
    id: "pa1",
    code: "C0001",
    name_en: "Ali Traders",
    name_ar: "علي",
    kind: "company",
    trn: null,
    is_customer: true,
    is_supplier: false,
    phone: null,
    archived_at: null,
  },
];

const BILL = {
  id: "bill1",
  party_id: "pa1",
  account_id: "ar",
  reference: "INV-1",
  bill_date: "2026-10-01",
  due_date: null,
  direction: "debit",
  amount: "1000.0000",
  amount_open: "1000.0000",
  is_advance: false,
  document_id: "d0",
};

const POSTED = {
  id: "v1",
  doc_type: "receipt",
  status: "posted",
  number: "RV-2026-00001",
  doc_date: "2026-10-18",
  business_id: "b1",
  branch_id: "br1",
  reference: null,
  narration: null,
  account_id: "cash",
  party_id: "pa1",
  total: "400.0000",
  posted_at: "2026-10-18T10:00:00Z",
  vat_total: "0.0000",
  allocations: [],
  lines: [],
  journal: [
    {
      line_no: 1,
      account_id: "ar",
      account_code: "1200",
      account_name_en: "Accounts receivable",
      account_name_ar: "ذمم",
      party_id: "pa1",
      debit: "0.0000",
      credit: "400.0000",
      description: null,
    },
    {
      line_no: 2,
      account_id: "cash",
      account_code: "1110",
      account_name_en: "Cash",
      account_name_ar: "الصندوق",
      party_id: null,
      debit: "400.0000",
      credit: "0.0000",
      description: null,
    },
  ],
};

function booksApi(overrides = {}) {
  return shopApi({
    signedIn: true,
    "GET /accounts": { status: 200, body: ACCOUNTS },
    "GET /account-roles": { status: 200, body: [{ role: "ar_control", account_id: "ar" }] },
    "GET /businesses/b1/branches": {
      status: 200,
      body: [{ id: "br1", business_id: "b1", code: "HO", name_en: "Head office", name_ar: "الرئيسي" }],
    },
    "GET /parties": { status: 200, body: PARTIES },
    "GET /bills": { status: 200, body: [BILL] },
    "POST /vouchers": { status: 201, body: POSTED },
    "GET /vouchers/v1": { status: 200, body: POSTED },
    ...overrides,
  });
}

describe("money helpers", () => {
  it("work in fils, never in floating point", () => {
    expect(toFils("1049.97")).toBe(104997);
    expect(toFils("10.5")).toBe(1050);
    expect(fromFils(104997)).toBe("1049.97");
    expect(fromFils(-5)).toBe("-0.05");
    expect(money("-1234567.8900")).toBe("-1,234,567.89");
    expect(money("0.0000")).toBe("0.00");
    expect(vatFils(1010, 5)).toBe(51); // 0.505 → 0.51, half up (A3)
    expect(vatFils(10, 5)).toBe(1);
  });

  it("send what is not settled against bills on account", () => {
    const choice = { ...ON_ACCOUNT, mode: "against_ref" as const, against: { bill1: "300" } };
    expect(allocationsOf(choice, 40000)).toEqual([
      { type: "against_ref", amount: "300.00", bill_id: "bill1" },
      { type: "on_account", amount: "100.00" },
    ]);
    expect(allocationsOf({ ...choice, against: { bill1: "500" } }, 40000)).toBe(
      "accounts.billWise.overAllocated",
    );
    expect(allocationsOf({ ...ON_ACCOUNT, mode: "new_ref" }, 100)).toBe("accounts.billWise.needReference");
    expect(allocationsOf(ON_ACCOUNT, 100)).toEqual([]);
  });
});

describe("Accounts screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("shows the chart with groups and debit / credit balances", async () => {
    window.location.hash = "#/accounts";
    const { api } = booksApi();
    render(<App api={api} />);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("1000Assets");
    expect(rows[1]).toHaveTextContent("150.00Cr");
    expect(rows[2]).toHaveTextContent("1110Cash");
    expect(rows[2]).toHaveTextContent("850.00Dr");
    expect(rows[3]).toHaveTextContent("Customers (receivable)");

    await setLanguage("ar");
    expect(await screen.findByText("ح Cash")).toBeInTheDocument();
  });

  it("takes a receipt against the customer's invoice and opens the posted voucher", async () => {
    window.location.hash = "#/vouchers/receipt";
    const { api, calls } = booksApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByRole("heading", { name: "Receipt voucher" });
    await waitFor(() => expect(screen.getByLabelText("Received into")).toHaveValue("cash"));
    const party = await screen.findByLabelText("Customer or supplier");
    await within(party).findByRole("option", { name: "C0001 Ali Traders" });
    await user.selectOptions(party, "pa1");
    await user.type(screen.getByLabelText("Amount"), "400");
    await user.click(screen.getByRole("radio", { name: "Against bills" }));
    await user.click(await screen.findByRole("button", { name: "Fill oldest first" }));
    expect(screen.getByLabelText("Settle INV-1")).toHaveValue("400.00");
    await user.click(screen.getByRole("button", { name: "Post" }));

    expect(await screen.findByText("RV-2026-00001")).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.path === "/vouchers");
    expect(post?.query).toBe("?post=true");
    expect(post?.body).toMatchObject({
      doc_type: "receipt",
      business_id: "b1",
      branch_id: "br1",
      account_id: "cash",
      lines: [
        {
          account_id: null,
          party_id: "pa1",
          amount: "400.00",
          vat_rate: "0",
          allocations: [{ type: "against_ref", amount: "400.00", bill_id: "bill1" }],
        },
      ],
    });
    expect(screen.getByRole("table")).toHaveTextContent("1110Cash400.00");
  });

  it("will not post an unbalanced journal and shows the difference", async () => {
    window.location.hash = "#/vouchers/journal";
    const { api, calls } = booksApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    await screen.findByRole("heading", { name: "Journal voucher" });
    const accounts = await screen.findAllByLabelText("Account");
    await waitFor(() => expect(within(accounts[0]!).getAllByRole("option").length).toBeGreaterThan(1));
    await user.selectOptions(accounts[0]!, "rent");
    await user.selectOptions(accounts[1]!, "cash");
    await user.type(screen.getAllByLabelText("Dr")[0]!, "100");
    await user.type(screen.getAllByLabelText("Cr")[1]!, "99.99");
    expect(screen.getByLabelText("Totals")).toHaveTextContent("Difference0.01");
    await user.click(screen.getByRole("button", { name: "Post" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Debits and credits must be equal.");
    expect(calls.some((c) => c.method === "POST" && c.path === "/vouchers")).toBe(false);

    await user.clear(screen.getAllByLabelText("Cr")[1]!);
    await user.type(screen.getAllByLabelText("Cr")[1]!, "100");
    await user.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/vouchers")).toBe(true));
    const saved = calls.find((c) => c.path === "/vouchers")!;
    expect(saved.query).toBe("?post=false");
    expect(saved.body).toMatchObject({
      doc_type: "journal",
      lines: [
        { account_id: "rent", debit: "100.00" },
        { account_id: "cash", credit: "100.00" },
      ],
    });
  });

  it("adds VAT per line on an expense voucher", async () => {
    window.location.hash = "#/vouchers/expense";
    const { api } = booksApi();
    const user = userEvent.setup();
    render(<App api={api} />);

    const accountSelect = await screen.findByLabelText("Account");
    await waitFor(() =>
      expect(within(accountSelect).getByRole("option", { name: "6100 Rent" })).toBeTruthy(),
    );
    expect(within(accountSelect).queryByRole("option", { name: "1110 Cash" })).toBeNull();
    await user.selectOptions(accountSelect, "rent");
    await user.type(screen.getByLabelText("Amount"), "200");
    await user.selectOptions(screen.getByLabelText("VAT"), "5");
    const totals = screen.getByLabelText("Totals");
    expect(totals).toHaveTextContent("Net200.00");
    expect(totals).toHaveTextContent("VAT10.00");
    expect(totals).toHaveTextContent("Total210.00");
  });

  it("asks why before posting into a locked month, then posts the draft with the reason", async () => {
    window.location.hash = "#/vouchers/receipt";
    let attempts = 0;
    const { api, calls } = booksApi({
      "POST /vouchers": () => {
        attempts += 1;
        return attempts === 1
          ? { status: 409, body: { code: "period_locked", may_override: true } }
          : { status: 201, body: { ...POSTED, id: "d1", status: "draft", number: null, journal: [] } };
      },
      "POST /vouchers/d1/post": { status: 200, body: { ...POSTED, id: "v1" } },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    const party = await screen.findByLabelText("Customer or supplier");
    await within(party).findByRole("option", { name: "C0001 Ali Traders" });
    await user.selectOptions(party, "pa1");
    await user.type(screen.getByLabelText("Amount"), "50");
    await user.click(screen.getByRole("button", { name: "Post" }));
    const reason = await screen.findByLabelText("Why post into a locked month?");
    await user.type(reason, "late bank slip");
    await user.click(screen.getByRole("button", { name: "Post into the locked month" }));

    expect(await screen.findByText("RV-2026-00001")).toBeInTheDocument();
    const override = calls.find((c) => c.path === "/vouchers/d1/post");
    expect(override?.body).toEqual({ override_lock_reason: "late bank slip" });
  });

  it("locks a month", async () => {
    window.location.hash = "#/accounts/periods";
    const period = {
      id: "p10",
      period_no: 10,
      start_date: "2026-10-01",
      end_date: "2026-10-31",
      status: "open",
      locked_at: null,
      locked_by: null,
    };
    const { api, calls } = booksApi({
      "GET /fiscal-years": {
        status: 200,
        body: [
          {
            id: "y1",
            business_id: "b1",
            code: "2026",
            start_date: "2026-01-01",
            end_date: "2026-12-31",
            status: "open",
            periods: [period],
          },
        ],
      },
      "PUT /fiscal-periods/p10/lock": { status: 200, body: { ...period, status: "locked" } },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "Fiscal year 2026" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Lock October 2026" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "PUT" && c.path === "/fiscal-periods/p10/lock")).toBe(true),
    );
  });

  it("shows the trial balance with equal totals", async () => {
    window.location.hash = "#/accounts/trial-balance";
    const { api } = booksApi({
      "GET /trial-balance": {
        status: 200,
        body: {
          business_id: "b1",
          as_of: "2026-10-18",
          rows: [
            {
              account_id: "cash",
              code: "1110",
              name_en: "Cash",
              name_ar: "الصندوق",
              nature: "asset",
              debit: "190.0000",
              credit: "0.0000",
            },
            {
              account_id: "misc",
              code: "4800",
              name_en: "Other income",
              name_ar: "إيرادات",
              nature: "income",
              debit: "0.0000",
              credit: "190.0000",
            },
          ],
          total_debit: "190.0000",
          total_credit: "190.0000",
        },
      },
    });
    render(<App api={api} />);

    const table = await screen.findByRole("table", { name: "Trial balance" });
    const rows = within(table).getAllByRole("row");
    expect(rows.at(-1)).toHaveTextContent("Total190.00190.00");
  });
});
