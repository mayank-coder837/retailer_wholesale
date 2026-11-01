import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { presetPeriod } from "@/routes/reports/queries";
import { shopApi } from "@/test/shop-api";

interface Row {
  account_id: string | null;
  key: string | null;
  code: string | null;
  name_en: string;
  name_ar: string;
  amount: string;
}

const row = (code: string, amount: string, name = `Account ${code}`): Row => ({
  account_id: `a${code}`,
  key: null,
  code,
  name_en: name,
  name_ar: `حساب ${code}`,
  amount,
});
const section = (key: string, rows: ReturnType<typeof row>[], total: string) => ({ key, rows, total });

const PL = (net = "2300.0000") => ({
  business_id: "b1",
  date_from: "2026-11-01",
  date_to: "2026-11-30",
  sections: [
    section("sales", [row("4100", "5000.0000", "Sales")], "5000.0000"),
    section("cogs", [row("5100", "2000.0000", "Cost of goods sold")], "2000.0000"),
    section("other_income", [], "0.0000"),
    section("expenses", [row("6100", "700.0000", "Rent")], "700.0000"),
  ],
  gross_profit: "3000.0000",
  net_profit: net,
});

const BS = (balanced = true) => ({
  business_id: "b1",
  as_of: "2026-11-30",
  fiscal_year_start: "2026-01-01",
  assets: [
    section("current_assets", [row("1110", "12300.0000", "Cash")], "12300.0000"),
    section("fixed_assets", [], "0.0000"),
  ],
  liabilities: [
    section("current_liabilities", [], "0.0000"),
    section("non_current_liabilities", [], "0.0000"),
  ],
  equity: section(
    "equity",
    [
      row("3100", "10000.0000", "Capital"),
      {
        account_id: null,
        key: "profit_year",
        code: null,
        name_en: "Profit for the year to date",
        name_ar: "صافي الربح للسنة حتى تاريخه",
        amount: "2300.0000",
      },
    ],
    "12300.0000",
  ),
  total_assets: "12300.0000",
  total_liabilities: "0.0000",
  total_equity: "12300.0000",
  balanced,
});

const DAY_BOOK = (truncated = false) => ({
  business_id: "b1",
  date_from: "2026-11-01",
  date_to: "2026-11-30",
  entries: [
    {
      entry_id: "e1",
      entry_date: "2026-11-02",
      number: "EV-2026-00001",
      narration: "November rent",
      document_id: "v1",
      doc_type: "expense",
      lines: [
        {
          account_id: "a6100",
          code: "6100",
          name_en: "Rent",
          name_ar: "إيجار",
          party_id: null,
          party_name_en: null,
          party_name_ar: null,
          description: null,
          debit: "700.0000",
          credit: "0.0000",
        },
        {
          account_id: "a1110",
          code: "1110",
          name_en: "Cash",
          name_ar: "نقد",
          party_id: null,
          party_name_en: null,
          party_name_ar: null,
          description: null,
          debit: "0.0000",
          credit: "700.0000",
        },
      ],
    },
  ],
  total_debit: "700.0000",
  total_credit: "700.0000",
  truncated,
});

describe("report periods", () => {
  it("names this month, last month and the fiscal year to date", () => {
    expect(presetPeriod("this_month", "2026-11-17", 1)).toEqual({ from: "2026-11-01", to: "2026-11-17" });
    expect(presetPeriod("last_month", "2026-01-10", 1)).toEqual({ from: "2025-12-01", to: "2025-12-31" });
    expect(presetPeriod("this_year", "2026-11-17", 1)).toEqual({ from: "2026-01-01", to: "2026-11-17" });
    // A fiscal year from April: in February it started last April.
    expect(presetPeriod("this_year", "2027-02-03", 4)).toEqual({ from: "2026-04-01", to: "2027-02-03" });
    expect(presetPeriod("this_year", "2026-04-01", 4)).toEqual({ from: "2026-04-01", to: "2026-04-01" });
  });
});

describe("Reports screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("lists every report by what it answers", async () => {
    window.location.hash = "#/reports";
    const { api } = shopApi({ signedIn: true });
    render(<App api={api} />);
    const accounts = await screen.findByRole("region", { name: "Accounts" });
    expect(within(accounts).getByRole("link", { name: "Trial balance" })).toHaveAttribute(
      "href",
      "/#/accounts/trial-balance",
    );
    expect(
      within(screen.getByRole("region", { name: "VAT" })).getByRole("link", { name: "VAT return" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Stock" })).toHaveTextContent("Negative stock");
  });

  it("shows profit and loss with gross and net profit", async () => {
    window.location.hash = "#/reports/profit-loss";
    const { api, calls } = shopApi({
      signedIn: true,
      "GET /reports/profit-loss": { status: 200, body: PL() },
    });
    render(<App api={api} />);
    const table = await screen.findByRole("table", { name: "Profit and loss" });
    const line = (text: string) => within(table).getByText(text).closest("tr")!;
    const sales = within(table).getByRole("link", { name: "Sales" }).closest("tr")!;
    expect(sales).toHaveTextContent("4100Sales5,000.00");
    expect(line("Total sales")).toHaveTextContent("Total sales5,000.00");
    expect(line("Gross profit")).toHaveTextContent("Gross profit3,000.00");
    expect(line("Net profit")).toHaveTextContent("Net profit2,300.00");
    expect(within(table).getByText("Nothing in this period.")).toBeInTheDocument(); // no other income
    expect(within(line("Rent")).getByRole("link", { name: "Rent" })).toHaveAttribute(
      "href",
      "/#/accounts/a6100",
    );
    expect(calls.find((c) => c.path === "/reports/profit-loss")?.query).toMatch(/from=\d{4}-\d{2}-01&to=/);
  });

  it("says net loss when the period lost money, and takes other dates", async () => {
    window.location.hash = "#/reports/profit-loss";
    const { api, calls } = shopApi({
      signedIn: true,
      "GET /reports/profit-loss": { status: 200, body: PL("-150.0000") },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    expect(await screen.findByText("Net loss")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Other dates" }));
    expect(screen.getByText("Choose the first and last day of the period.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("From"), "2026-01-01");
    await user.type(screen.getByLabelText("To"), "2026-03-31");
    await waitFor(() =>
      expect(calls.some((c) => c.query.includes("from=2026-01-01&to=2026-03-31"))).toBe(true),
    );
  });

  it("shows the balance sheet with the profit not yet closed", async () => {
    window.location.hash = "#/reports/balance-sheet";
    const { api } = shopApi({ signedIn: true, "GET /reports/balance-sheet": { status: 200, body: BS() } });
    render(<App api={api} />);
    const table = await screen.findByRole("table", { name: "Balance sheet" });
    const line = (text: string) => within(table).getByText(text).closest("tr")!;
    expect(line("Total assets")).toHaveTextContent("12,300.00");
    expect(line("Profit for the year to date")).toHaveTextContent("not yet closed into equity2,300.00");
    expect(line("Total liabilities and equity")).toHaveTextContent("12,300.00");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/This fiscal year started on 2026-01-01/)).toBeInTheDocument();
  });

  it("warns if the balance sheet does not balance", async () => {
    window.location.hash = "#/reports/balance-sheet";
    const { api } = shopApi({
      signedIn: true,
      "GET /reports/balance-sheet": { status: 200, body: BS(false) },
    });
    render(<App api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Assets do not equal liabilities and equity.");
  });

  it("shows the day book entry by entry, each opening its document", async () => {
    window.location.hash = "#/reports/day-book";
    const { api } = shopApi({
      signedIn: true,
      "GET /reports/day-book": { status: 200, body: DAY_BOOK(true) },
    });
    render(<App api={api} />);
    const table = await screen.findByRole("table", { name: "Day book" });
    expect(within(table).getByRole("link", { name: "EV-2026-00001" })).toHaveAttribute(
      "href",
      "/#/accounts/vouchers/v1",
    );
    expect(table).toHaveTextContent("2026-11-02Expense voucherEV-2026-00001November rent");
    expect(table).toHaveTextContent("6100Rent700.00");
    expect(table).toHaveTextContent("Total of 1 entry700.00700.00");
    expect(screen.getByRole("status")).toHaveTextContent("Showing the first 1 entries");
  });

  it("works in Arabic", async () => {
    await setLanguage("ar");
    window.location.hash = "#/reports/profit-loss";
    const { api } = shopApi({ signedIn: true, "GET /reports/profit-loss": { status: 200, body: PL() } });
    render(<App api={api} />);
    expect(await screen.findByText("صافي الربح")).toBeInTheDocument();
    expect(screen.getByText("مجمل الربح")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "الميزانية العمومية" })).toBeInTheDocument();
  });
});
