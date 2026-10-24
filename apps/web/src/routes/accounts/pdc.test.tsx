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

const BANK = {
  id: "bank1",
  business_id: "b1",
  code: "1150",
  name_en: "Bank",
  name_ar: "البنك",
  parent_id: null,
  account_type: "bank",
  nature: "asset",
  is_group: false,
  is_system: false,
  archived_at: null,
};

const BILL = {
  id: "bill1",
  party_id: "c1",
  account_id: "ar",
  document_id: "d1",
  reference: "INV-2026-00001",
  bill_date: "2026-10-01",
  due_date: "2026-10-31",
  direction: "debit",
  amount: "1000.0000",
  amount_open: "1000.0000",
  is_advance: false,
};

const pdc = (over: Record<string, unknown> = {}) => ({
  id: "p1",
  business_id: "b1",
  branch_id: "br1",
  direction: "received",
  party_id: "c1",
  party_name_en: "Ali Traders",
  party_name_ar: "علي",
  cheque_no: "000101",
  bank_name: "Emirates NBD",
  cheque_date: "2026-11-25",
  amount: "1000.0000",
  currency: "AED",
  status: "in_hand",
  deposit_account_id: null,
  deposited_on: null,
  cleared_on: null,
  bounced_on: null,
  bounce_reason: null,
  voucher_id: null,
  voucher_number: null,
  charge_voucher_id: null,
  notes: null,
  intended_bills: [
    { bill_id: "bill1", reference: "INV-2026-00001", amount: "1000.0000", amount_open: "1000.0000" },
  ],
  ...over,
});

function chequesApi(overrides = {}) {
  return shopApi({
    signedIn: true,
    "GET /businesses/b1/branches": {
      status: 200,
      body: [{ id: "br1", business_id: "b1", code: "HO", name_en: "Head office", name_ar: "الرئيسي" }],
    },
    "GET /parties": { status: 200, body: [CUSTOMER] },
    "GET /accounts": { status: 200, body: [BANK] },
    "GET /bills": { status: 200, body: [BILL] },
    ...overrides,
  });
}

describe("PDC screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("shows the register by maturity month with each month's total", async () => {
    window.location.hash = "#/accounts/cheques";
    const { api } = chequesApi({
      "GET /pdcs": {
        status: 200,
        body: [
          pdc(),
          pdc({ id: "p2", cheque_no: "000102", cheque_date: "2026-12-25", amount: "500.0000" }),
          pdc({ id: "p3", cheque_no: "000103", cheque_date: "2026-12-26", amount: "250.5000" }),
          pdc({ id: "p4", cheque_no: "000104", status: "cleared" }),
        ],
      },
    });
    render(<App api={api} />);

    const november = await screen.findByRole("table", { name: "November 2026" });
    expect(within(november).getAllByRole("row")[1]).toHaveTextContent(
      "2026-11-25000101Ali TradersEmirates NBDIn hand1,000.00",
    );
    const december = screen.getByRole("table", { name: "December 2026" });
    expect(december).toHaveTextContent("2 cheques, 750.50");
    expect(screen.queryByText("000104")).toBeNull(); // cleared: not in "not cleared yet"
  });

  it("records a cheque received against an open invoice", async () => {
    window.location.hash = "#/accounts/cheques/new?direction=received";
    const { api, calls } = chequesApi({
      "POST /pdcs": { status: 201, body: pdc() },
      "GET /pdcs/p1": { status: 200, body: pdc() },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    const customer = await screen.findByLabelText("Customer", { selector: "select" });
    await within(customer).findByRole("option", { name: "C0001 Ali Traders" });
    await user.selectOptions(customer, "c1");
    await user.type(screen.getByLabelText("Cheque no."), "000101");
    await user.type(screen.getByLabelText("Bank"), "Emirates NBD");
    await user.clear(screen.getByLabelText("Cheque date"));
    await user.type(screen.getByLabelText("Cheque date"), "2026-11-25");
    await user.type(screen.getByLabelText("Amount"), "1000");
    await user.type(await screen.findByLabelText("Amount for INV-2026-00001"), "1000");
    await user.click(screen.getByRole("button", { name: "Save cheque" }));

    expect(await screen.findByRole("button", { name: "Deposit" })).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST" && c.path === "/pdcs")?.body).toEqual({
      business_id: "b1",
      branch_id: "br1",
      direction: "received",
      party_id: "c1",
      cheque_no: "000101",
      bank_name: "Emirates NBD",
      cheque_date: "2026-11-25",
      amount: "1000",
      deposit_account_id: null,
      intended_bills: [{ bill_id: "bill1", amount: "1000" }],
      notes: null,
    });
  });

  it("clears a cheque into the bank and links the receipt it posted", async () => {
    window.location.hash = "#/accounts/cheques/p1";
    let cleared = false;
    const done = pdc({
      status: "cleared",
      deposit_account_id: "bank1",
      cleared_on: "2026-11-26",
      voucher_id: "v1",
      voucher_number: "RV-2026-00001",
    });
    const { api, calls } = chequesApi({
      "GET /pdcs/p1": () => ({ status: 200, body: cleared ? done : pdc() }),
      "POST /pdcs/p1/clear": () => {
        cleared = true;
        return { status: 200, body: done };
      },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    await user.click(await screen.findByRole("button", { name: "Cleared by the bank" }));
    const form = screen.getByRole("form", { name: "Cleared by the bank" });
    const confirm = within(form).getByRole("button", { name: "Post and mark cleared" });
    expect(confirm).toBeDisabled(); // not deposited: the bank account is needed
    const bank = within(form).getByLabelText("Bank account");
    await within(bank).findByRole("option", { name: "1150 Bank" });
    await user.selectOptions(bank, "bank1");
    await user.clear(within(form).getByLabelText("Cleared on"));
    await user.type(within(form).getByLabelText("Cleared on"), "2026-11-26");
    await user.click(confirm);

    expect(await screen.findByRole("link", { name: "RV-2026-00001" })).toBeInTheDocument();
    expect(screen.getByText("Cleared", { selector: "span" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/pdcs/p1/clear")?.body).toEqual({
      date: "2026-11-26",
      bank_account_id: "bank1",
      override_lock_reason: null,
    });
    expect(screen.queryByRole("button", { name: "Deposit" })).toBeNull();
  });

  it("offers a bank charge on a bounce only once the cheque went through a bank", async () => {
    window.location.hash = "#/accounts/cheques/p1";
    const deposited = pdc({ status: "deposited", deposit_account_id: "bank1", deposited_on: "2026-11-25" });
    const { api, calls } = chequesApi({
      "GET /pdcs/p1": { status: 200, body: deposited },
      "POST /pdcs/p1/bounce": { status: 200, body: { ...deposited, status: "bounced" } },
    });
    const user = userEvent.setup();
    render(<App api={api} />);

    expect(await screen.findByRole("button", { name: "Cleared by the bank" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Return to customer" })).toBeNull(); // deposited: too late
    await user.click(screen.getByRole("button", { name: "Bounced" }));
    const form = screen.getByRole("form", { name: "Bounced" });
    await user.type(within(form).getByLabelText("Reason"), "insufficient funds");
    await user.type(within(form).getByLabelText("Bank charge"), "25");
    await user.click(within(form).getByRole("button", { name: "Mark bounced" }));

    await waitFor(() =>
      expect(calls.find((c) => c.path === "/pdcs/p1/bounce")?.body).toMatchObject({
        reason: "insufficient funds",
        charge: "25",
      }),
    );
  });
});
