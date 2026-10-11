import { fakeApi } from "@/test/fake-api";

export const TOKEN = {
  access_token: "tok-123",
  token_type: "bearer",
  expires_in: 900,
  refresh_token: null,
  tenant_id: "t1",
  membership_id: "m1",
};

export const ME = {
  user: { id: "u1", email: "owner@alnoor.ae", full_name: "Fatima Owner", locale: "en" },
  tenant: { id: "t1", name: "Al Noor", status: "trial", default_locale: "en" },
  membership_id: "m1",
  node_id: "n1",
  memberships: [{ membership_id: "m1", tenant_id: "t1", tenant_name: "Al Noor", status: "active" }],
};

const business = (id: string, en: string, ar: string) => ({
  id,
  legal_name_en: en,
  legal_name_ar: ar,
  trade_license_no: null,
  trn: null,
  vat_registered: false,
  base_currency: "AED",
  fiscal_year_start_month: 1,
  time_zone: "Asia/Dubai",
  costing_method: "moving_average",
  negative_stock_allowed: true,
});

export const BUSINESSES = [
  business("b1", "Al Noor Trading LLC", "شركة النور للتجارة"),
  business("b2", "Al Noor Textiles LLC", "شركة النور للأقمشة"),
];

type Routes = Parameters<typeof fakeApi>[0];
type Options = { signedIn?: boolean; [route: string]: Routes[string] | boolean | undefined };

/** A shop the API knows. `signedIn` decides what the start-up silent refresh returns. */
export function shopApi({ signedIn = false, ...rest }: Options = {}) {
  const overrides = rest as Routes;
  return fakeApi({
    "GET /health": { status: 200, body: { status: "ok", product: "SmartBiz OS", version: "0.1.0" } },
    "POST /auth/refresh": signedIn
      ? { status: 200, body: TOKEN }
      : { status: 401, body: { code: "invalid_refresh_token" } },
    "POST /auth/login": { status: 200, body: TOKEN },
    "POST /auth/logout": { status: 204, body: null },
    "GET /me": { status: 200, body: ME },
    "GET /businesses": { status: 200, body: BUSINESSES.slice(0, 1) },
    ...overrides,
  });
}
