import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { fakeApi } from "@/test/fake-api";

const TOKEN = {
  access_token: "tok-123",
  token_type: "bearer",
  expires_in: 900,
  tenant_id: "t1",
  membership_id: "m1",
};

const PERMISSIONS = [
  { code: "sales_invoice.view", resource: "sales_invoice", action: "view" },
  { code: "sales_invoice.post", resource: "sales_invoice", action: "post" },
  { code: "receipt.view", resource: "receipt", action: "view" },
  { code: "receipt.post", resource: "receipt", action: "post" },
  { code: "price.override_min", resource: "price", action: "override_min" },
];

const ROLES = [
  {
    id: "r-owner",
    system_key: "owner",
    name_en: "Owner",
    name_ar: "المالك",
    description: null,
    permissions: PERMISSIONS.map((p) => p.code),
    editable: false,
  },
  {
    id: "r-sales",
    system_key: "salesperson",
    name_en: "Salesperson",
    name_ar: "مندوب مبيعات",
    description: null,
    permissions: ["receipt.view", "sales_invoice.post", "sales_invoice.view"],
    editable: true,
  },
];

function shopApi(overrides = {}) {
  return fakeApi({
    "GET /health": { status: 200, body: { status: "ok", product: "SmartBiz OS", version: "0.1.0" } },
    "POST /auth/login": { status: 200, body: TOKEN },
    "GET /roles": { status: 200, body: ROLES },
    "GET /permissions": { status: 200, body: PERMISSIONS },
    "PATCH /roles/r-sales": (body) => ({ status: 200, body: { ...ROLES[1], ...(body as object) } }),
    ...overrides,
  });
}

async function logIn() {
  await userEvent.type(await screen.findByLabelText("Email"), "owner@shop.ae");
  await userEvent.type(screen.getByLabelText("Password"), "correct horse battery");
  await userEvent.click(screen.getByRole("button", { name: "Log in" }));
}

describe("Login and roles screen", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
    window.location.hash = "#/login";
  });

  it("logs in and sends the token with later requests", async () => {
    const { api, calls } = shopApi();
    render(<App api={api} />);

    await logIn();

    expect(await screen.findByRole("heading", { name: "Roles and permissions" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/roles")?.authorization).toBe("Bearer tok-123");
  });

  it("asks which account to open when the user has several", async () => {
    const memberships = [
      { tenant_id: "t1", tenant_name: "First Co" },
      { tenant_id: "t2", tenant_name: "Second Co" },
    ];
    const { api, calls } = shopApi({
      "POST /auth/login": (body: unknown) =>
        (body as { tenant_id?: string }).tenant_id
          ? { status: 200, body: TOKEN }
          : { status: 409, body: { code: "tenant_selection_required", memberships } },
    });
    render(<App api={api} />);

    await logIn();
    await userEvent.click(await screen.findByRole("button", { name: "Second Co" }));

    expect(await screen.findByRole("heading", { name: "Roles and permissions" })).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/auth/login").at(-1)?.body).toMatchObject({ tenant_id: "t2" });
  });

  it("shows a translated error for a wrong password", async () => {
    const { api } = shopApi({ "POST /auth/login": { status: 401, body: { code: "invalid_credentials" } } });
    render(<App api={api} />);

    await logIn();

    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
  });

  it("shows the Owner role read-only", async () => {
    const { api } = shopApi();
    render(<App api={api} />);
    await logIn();

    expect(await screen.findByText(/always has every permission/)).toBeInTheDocument();
    expect(screen.getByLabelText("Sales invoices — Post")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
  });

  it("edits a role's permissions and saves them", async () => {
    const { api, calls } = shopApi();
    render(<App api={api} />);
    await logIn();

    await userEvent.click(await screen.findByRole("button", { name: "Salesperson" }));
    expect(screen.getByLabelText("Sales invoices — Post")).toBeChecked();
    const receiptPost = screen.getByLabelText("Receipt vouchers — Post");
    expect(receiptPost).not.toBeChecked();

    await userEvent.click(receiptPost);
    await userEvent.click(screen.getByLabelText("Sell below the minimum price"));
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Saved.");
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      permissions: [
        "price.override_min",
        "receipt.post",
        "receipt.view",
        "sales_invoice.post",
        "sales_invoice.view",
      ],
    });
  });

  it("explains when the user may not view roles", async () => {
    const { api } = shopApi({ "GET /roles": { status: 403, body: { code: "permission_denied" } } });
    render(<App api={api} />);
    await logIn();

    expect(await screen.findByRole("alert")).toHaveTextContent("You do not have permission to view roles.");
  });

  it("labels the matrix in Arabic", async () => {
    const { api } = shopApi();
    render(<App api={api} />);
    await logIn();
    await screen.findByRole("heading", { name: "Roles and permissions" });

    await setLanguage("ar");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "الأدوار والصلاحيات" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("columnheader", { name: "ترحيل" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "فواتير المبيعات" })).toBeInTheDocument();
  });
});
