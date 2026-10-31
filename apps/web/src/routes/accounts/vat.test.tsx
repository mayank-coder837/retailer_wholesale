import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { periodEnding, recentMonths, sumAmounts } from "@/routes/accounts/vat-queries";
import { shopApi } from "@/test/shop-api";

const box = (code: string, kind: string, amount = "0.0000", vat = "0.0000") => ({
  box: code,
  label_en: `Label ${code}`,
  label_ar: `خانة ${code}`,
  kind,
  amount,
  vat,
  adjustment: "0.0000",
});

const RETURN = (payable: string) => ({
  business_id: "b1",
  date_from: "2026-07-01",
  date_to: "2026-09-30",
  status: "draft",
  boxes: [
    box("1a", "amount_vat"),
    box("1b", "amount_vat", "10000.0000", "500.0000"),
    box("2", "manual"),
    box("4", "amount", "300.0000"),
    box("8", "total", "10300.0000", "500.0000"),
    box("9", "amount_vat", "2000.0000", "100.0000"),
    box("11", "total", "2000.0000", "100.0000"),
    box("12", "total", "0.0000", "500.0000"),
    box("13", "total", "0.0000", "100.0000"),
    box("14", "total", "0.0000", payable),
  ],
  payable,
});

const LINES = [
  {
    document_id: "inv1",
    number: "INV-2026-00001",
    doc_type: "sales_invoice",
    doc_date: "2026-08-03",
    party_id: "c1",
    party_name_en: "Ali Traders",
    party_name_ar: "علي",
    amount: "10500.0000",
    vat: "525.0000",
  },
  {
    document_id: "ret1",
    number: "SRN-2026-00001",
    doc_type: "sales_return",
    doc_date: "2026-09-10",
    party_id: "c1",
    party_name_en: "Ali Traders",
    party_name_ar: "علي",
    amount: "-500.0000",
    vat: "-25.0000",
  },
];

describe("VAT period helpers", () => {
  it("names a quarter by the month it ends in, whatever the stagger", () => {
    expect(periodEnding("2026-09", 3)).toEqual({ from: "2026-07-01", to: "2026-09-30" });
    expect(periodEnding("2026-04", 3)).toEqual({ from: "2026-02-01", to: "2026-04-30" });
    expect(periodEnding("2026-02", 3)).toEqual({ from: "2025-12-01", to: "2026-02-28" });
    expect(periodEnding("2028-02", 1)).toEqual({ from: "2028-02-01", to: "2028-02-29" });
  });

  it("offers finished months, latest first", () => {
    expect(recentMonths("2026-01-15", 3)).toEqual(["2025-12", "2025-11", "2025-10"]);
  });

  it("adds 4-decimal amounts exactly, negatives included", () => {
    expect(sumAmounts(["10500.0000", "-500.0000"])).toBe("10000.0000");
    expect(sumAmounts(["0.1000", "0.2000"])).toBe("0.3000");
    expect(sumAmounts(["-0.0050", "0.0010"])).toBe("-0.0040");
    expect(sumAmounts([])).toBe("0.0000");
  });
});

describe("VAT return screen", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("shows the boxes by part and says what is payable", async () => {
    window.location.hash = "#/accounts/vat-return";
    const { api, calls } = shopApi({
      signedIn: true,
      "GET /vat-returns/draft": { status: 200, body: RETURN("400.0000") },
    });
    render(<App api={api} />);

    expect(await screen.findByText("VAT payable to the FTA")).toBeInTheDocument();
    expect(screen.getByText("AED 400.00")).toBeInTheDocument();
    const query = calls.find((c) => c.path === "/vat-returns/draft")!.query;
    expect(query).toMatch(/business_id=b1/);
    expect(query).toMatch(/from=\d{4}-\d{2}-01/);

    const table = screen.getByRole("table", { name: "VAT return" });
    const row = (label: string) => within(table).getByText(label).closest("tr")!;
    expect(row("Label 1b")).toHaveTextContent("1bLabel 1b10,000.00500.00");
    expect(row("Label 2")).toHaveTextContent("Entered by hand when filing (later)——");
    expect(row("Label 4")).toHaveTextContent("4Label 4300.00"); // no VAT column for zero-rated
    expect(row("Label 14")).toHaveTextContent("14Label 14400.00");
    expect(within(table).getByText("VAT on expenses and all other inputs")).toBeInTheDocument();
    // Totals are calculated, not documents: nothing to open.
    expect(within(row("Label 8")).queryByRole("button")).toBeNull();
    // Neither is a zero: no documents behind it.
    expect(within(row("Label 1a")).queryByRole("button")).toBeNull();
    expect(within(row("Label 4")).getAllByRole("button")).toHaveLength(1);
  });

  it("says refundable when input VAT is larger", async () => {
    window.location.hash = "#/accounts/vat-return";
    const { api } = shopApi({
      signedIn: true,
      "GET /vat-returns/draft": { status: 200, body: RETURN("-250.0000") },
    });
    render(<App api={api} />);
    expect(await screen.findByText("VAT refundable by the FTA")).toBeInTheDocument();
    expect(screen.getByText("AED 250.00")).toBeInTheDocument();
  });

  it("opens the documents behind a figure, and they add up", async () => {
    window.location.hash = "#/accounts/vat-return";
    const { api, calls } = shopApi({
      signedIn: true,
      "GET /vat-returns/draft": { status: 200, body: RETURN("400.0000") },
      "GET /vat-returns/draft/lines": { status: 200, body: LINES },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    const buttons = await screen.findAllByRole("button", { name: "Show the documents behind box 1b" });
    await user.click(buttons[1]!);
    const docs = await screen.findByRole("table", { name: "Documents in box 1b" });
    expect(calls.find((c) => c.path === "/vat-returns/draft/lines")!.query).toMatch(/box=1b/);
    const rows = within(docs).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("2026-08-03Sales invoiceINV-2026-00001Ali Traders10,500.00525.00");
    expect(within(rows[1]!).getByRole("link", { name: "INV-2026-00001" })).toHaveAttribute(
      "href",
      "/#/sales/inv1",
    );
    expect(rows[3]).toHaveTextContent("Total of 2 documents10,000.00500.00");

    await user.click(screen.getAllByRole("button", { name: "Show the documents behind box 1b" })[0]!);
    await waitFor(() => expect(screen.queryByRole("table", { name: "Documents in box 1b" })).toBeNull());
  });

  it("asks for both dates before showing other periods", async () => {
    window.location.hash = "#/accounts/vat-return";
    const { api, calls } = shopApi({
      signedIn: true,
      "GET /vat-returns/draft": { status: 200, body: RETURN("400.0000") },
    });
    const user = userEvent.setup();
    render(<App api={api} />);
    await screen.findByText("VAT payable to the FTA");
    await user.click(screen.getByRole("button", { name: "Other dates" }));
    expect(screen.getByText("Choose the first and last day of the period.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("From"), "2026-01-01");
    await user.type(screen.getByLabelText("To"), "2026-01-31");
    await waitFor(() =>
      expect(
        calls.some((c) => c.query.includes("from=2026-01-01") && c.query.includes("to=2026-01-31")),
      ).toBe(true),
    );
  });

  it("is in Arabic with the boxes' Arabic labels", async () => {
    await setLanguage("ar");
    window.location.hash = "#/accounts/vat-return";
    const { api } = shopApi({
      signedIn: true,
      "GET /vat-returns/draft": { status: 200, body: RETURN("400.0000") },
    });
    render(<App api={api} />);
    expect(await screen.findByText("ضريبة مستحقة الدفع للهيئة")).toBeInTheDocument();
    expect(screen.getByText("خانة 1b")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "إقرار الضريبة" })).toBeInTheDocument();
  });
});
