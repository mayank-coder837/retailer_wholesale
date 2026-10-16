import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { BUSINESSES, shopApi } from "@/test/shop-api";

const B1 = BUSINESSES[0]!.id;

const SUMMARY = {
  id: "pa1",
  code: "C0001",
  name_en: "Gulf Fabrics",
  name_ar: "أقمشة الخليج",
  kind: "company",
  trn: "100123456700003",
  is_customer: true,
  is_supplier: false,
  phone: "+971501234567",
  archived_at: null,
};

const PARTY = {
  ...SUMMARY,
  whatsapp: null,
  email: null,
  default_currency: "AED",
  addresses: [],
  contacts: [],
  customer_profiles: [],
  supplier_profiles: [],
};

const PROFILE = {
  id: "cp1",
  business_id: B1,
  customer_group_id: null,
  price_list_id: null,
  credit_limit: "5000.0000",
  credit_days: 30,
  credit_policy: "block",
  salesperson_user_id: null,
};

function partiesApi(overrides = {}) {
  return shopApi({
    signedIn: true,
    "GET /parties": { status: 200, body: [SUMMARY] },
    "GET /parties/pa1": { status: 200, body: PARTY },
    "GET /customer-groups": { status: 200, body: [] },
    "GET /price-lists": { status: 200, body: [] },
    "GET /promotions": { status: 200, body: [] },
    ...overrides,
  });
}

describe("Customers & suppliers screens", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
  });

  it("lists parties and filters to suppliers", async () => {
    window.location.hash = "#/parties";
    const { api, calls } = partiesApi();
    render(<App api={api} />);

    expect(await screen.findByRole("link", { name: "Gulf Fabrics" })).toBeInTheDocument();
    expect(screen.getByText("100123456700003")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Suppliers" }));

    await waitFor(() =>
      expect(calls.some((c) => c.path === "/parties" && c.query === "?role=supplier")).toBe(true),
    );
  });

  it("checks the form before saving and sends exact details", async () => {
    window.location.hash = "#/parties/new";
    const { api, calls } = partiesApi({ "POST /parties": { status: 201, body: PARTY } });
    render(<App api={api} />);

    await userEvent.type(await screen.findByLabelText("Name (English)"), "Gulf Fabrics");
    await userEvent.type(screen.getByLabelText("Name (Arabic)"), "أقمشة الخليج");
    await userEvent.type(screen.getByLabelText("TRN"), "12345");
    await userEvent.type(screen.getByLabelText("Address"), "Deira, shop 4");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("A TRN has 15 digits.")).toBeInTheDocument();
    expect(screen.getByText("Choose the emirate.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/parties")).toBe(false);

    await userEvent.type(screen.getByLabelText("TRN"), "6700003123");
    await userEvent.selectOptions(screen.getByLabelText("Emirate"), "dubai");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/parties")?.body).toEqual({
        name_en: "Gulf Fabrics",
        name_ar: "أقمشة الخليج",
        kind: "company",
        is_customer: true,
        is_supplier: false,
        trn: "123456700003123",
        phone: null,
        whatsapp: null,
        email: null,
        default_currency: "AED",
        addresses: [
          {
            type: "billing",
            line1: "Deira, shop 4",
            city: null,
            emirate: "dubai",
            country: "AE",
            is_default: true,
          },
        ],
      }),
    );
    expect(await screen.findByRole("heading", { name: "Gulf Fabrics" })).toBeInTheDocument();
  });

  it("saves customer terms with the credit limit as exact text", async () => {
    window.location.hash = "#/parties/pa1";
    const { api, calls } = partiesApi({
      "PUT /parties/pa1/customer-profile": { status: 200, body: { ...PARTY, customer_profiles: [PROFILE] } },
    });
    render(<App api={api} />);

    const terms = await screen.findByRole("region", { name: "Customer terms in this business" });
    await userEvent.type(within(terms).getByLabelText("Credit limit (AED)"), "5000.50");
    await userEvent.type(within(terms).getByLabelText("Credit days"), "30");
    await userEvent.selectOptions(within(terms).getByLabelText("When over the limit or overdue"), "block");
    await userEvent.click(within(terms).getByRole("button", { name: "Save terms" }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        business_id: B1,
        customer_group_id: null,
        price_list_id: null,
        credit_limit: "5000.50",
        credit_days: 30,
        credit_policy: "block",
      }),
    );
    expect(await within(terms).findByText("Saved.")).toBeInTheDocument();
  });

  it("shows a blocked credit check, with cheques shown but not counted", async () => {
    window.location.hash = "#/parties/pa1";
    const { api, calls } = partiesApi({
      "POST /parties/pa1/credit-check": {
        status: 200,
        body: {
          decision: "block",
          reasons: ["over_limit"],
          credit_limit: "5000.0000",
          credit_days: 30,
          policy: "block",
          ledger_balance: "0",
          open_orders: "0",
          exposure: "6000.0000",
          available: "-1000.0000",
          pdc_in_hand: "0",
          exposure_after_pdc: "6000.0000",
          may_override: false,
        },
      },
    });
    render(<App api={api} />);

    const panel = await screen.findByRole("region", { name: "Credit check" });
    await userEvent.type(within(panel).getByLabelText("Sale amount"), "6000");
    await userEvent.click(within(panel).getByRole("button", { name: "Check" }));

    const result = await screen.findByTestId("credit-result");
    expect(result).toHaveTextContent("Blocked — over the credit limit");
    expect(result).toHaveTextContent("Available: -1000.00");
    expect(result).toHaveTextContent("they do not count until they clear");
    expect(result).toHaveTextContent("Ask someone allowed to override credit blocks.");
    expect(calls.find((c) => c.path === "/parties/pa1/credit-check")?.body).toEqual({
      business_id: B1,
      amount: "6000",
    });
  });

  it("adds a customer group in pricing settings", async () => {
    window.location.hash = "#/settings/pricing";
    const { api, calls } = partiesApi({
      "POST /customer-groups": {
        status: 201,
        body: {
          id: "g1",
          business_id: B1,
          name_en: "Wholesalers",
          name_ar: "تجار الجملة",
          price_list_id: null,
          archived_at: null,
        },
      },
    });
    render(<App api={api} />);

    const groups = await screen.findByRole("region", { name: "Customer groups" });
    await userEvent.type(within(groups).getByLabelText("Name (English)"), "Wholesalers");
    await userEvent.type(within(groups).getByLabelText("Name (Arabic)"), "تجار الجملة");
    await userEvent.click(within(groups).getByRole("button", { name: "Add group" }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST" && c.path === "/customer-groups")?.body).toEqual({
        business_id: B1,
        name_en: "Wholesalers",
        name_ar: "تجار الجملة",
        price_list_id: null,
      }),
    );
  });

  it("works in Arabic", async () => {
    window.location.hash = "#/parties";
    await setLanguage("ar");
    const { api } = partiesApi();
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "العملاء والموردون" })).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "أقمشة الخليج" })).toBeInTheDocument();
    expect(screen.getByText("عميل")).toBeInTheDocument();
  });
});
