import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import en from "@/i18n/en.json";
import ar from "@/i18n/ar.json";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { BUSINESSES, ME, shopApi, TOKEN } from "@/test/shop-api";

function desktopBridge(stored: string | null) {
  const session = {
    getRefreshToken: vi.fn(async () => stored),
    setRefreshToken: vi.fn(async () => undefined),
    clear: vi.fn(async () => undefined),
  };
  window.smartbiz = { apiUrl: "http://api.test", isDesktop: true, session };
  return session;
}

describe("App shell", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
    localStorage.clear();
    window.location.hash = "#/dashboard";
  });

  afterEach(() => {
    delete window.smartbiz;
  });

  it("sends signed-out users to the login page", async () => {
    const { api } = shopApi();
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
  });

  it("restores a web session with the refresh cookie and shows the dashboard", async () => {
    const { api, calls } = shopApi({ signedIn: true });
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "Welcome, Fatima Owner" })).toBeInTheDocument();
    expect(screen.getAllByTestId(/^kpi-/)).toHaveLength(6);
    const refresh = calls.filter((c) => c.path === "/auth/refresh");
    expect(refresh).toHaveLength(1);
    expect(refresh[0]?.body).toEqual({ refresh_token: null });
  });

  it("restores a desktop session from the encrypted store and saves the rotated token", async () => {
    const store = desktopBridge("stored-refresh");
    const { api, calls } = shopApi({
      "POST /auth/refresh": { status: 200, body: { ...TOKEN, refresh_token: "rotated-refresh" } },
    });
    render(<App api={api} />);

    await screen.findByRole("heading", { name: "Welcome, Fatima Owner" });
    expect(calls.find((c) => c.path === "/auth/refresh")?.body).toEqual({ refresh_token: "stored-refresh" });
    expect(store.setRefreshToken).toHaveBeenCalledWith("rotated-refresh");
  });

  it("does not call the API on desktop when nothing is stored", async () => {
    desktopBridge(null);
    const { api, calls } = shopApi();
    render(<App api={api} />);

    await screen.findByRole("heading", { name: "Log in" });
    expect(calls.some((c) => c.path === "/auth/refresh")).toBe(false);
  });

  it("logs out", async () => {
    const { api, calls } = shopApi({ signedIn: true });
    render(<App api={api} />);
    await screen.findByRole("heading", { name: "Welcome, Fatima Owner" });

    await userEvent.click(screen.getByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("heading", { name: "Log in" })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/auth/logout")).toBe(true);
  });

  it("switches business and remembers the choice", async () => {
    const { api } = shopApi({ signedIn: true, "GET /businesses": { status: 200, body: BUSINESSES } });
    render(<App api={api} />);
    await screen.findByText(/Al Noor Trading LLC/, { selector: "p" });

    await userEvent.selectOptions(screen.getByLabelText("Business"), "b2");

    expect(await screen.findByText(/Al Noor Textiles LLC/, { selector: "p" })).toBeInTheDocument();
    expect(localStorage.getItem("smartbiz.business.t1")).toBe("b2");
  });

  it("switches account when the user belongs to several", async () => {
    const me = {
      ...ME,
      memberships: [
        ...ME.memberships,
        { membership_id: "m2", tenant_id: "t2", tenant_name: "Other Co", status: "active" },
      ],
    };
    const { api, calls } = shopApi({
      signedIn: true,
      "GET /me": { status: 200, body: me },
      "POST /auth/switch-tenant": { status: 200, body: { ...TOKEN, tenant_id: "t2", membership_id: "m2" } },
    });
    render(<App api={api} />);
    await screen.findByRole("heading", { name: "Welcome, Fatima Owner" });

    await userEvent.selectOptions(await screen.findByLabelText("Account"), "t2");

    await waitFor(() => expect(calls.some((c) => c.path === "/auth/switch-tenant")).toBe(true));
    expect(calls.find((c) => c.path === "/auth/switch-tenant")?.body).toMatchObject({ tenant_id: "t2" });
  });

  it("opens the sales invoice with F8 on the desktop app", async () => {
    desktopBridge("stored");
    const { api } = shopApi({ "POST /auth/refresh": { status: 200, body: TOKEN } });
    render(<App api={api} />);
    await screen.findByRole("heading", { name: "Welcome, Fatima Owner" });

    fireEvent.keyDown(window, { key: "F8" });

    expect(await screen.findByRole("heading", { name: "Sales invoice (F8)" })).toBeInTheDocument();
  });

  it("leaves F-keys alone in a browser", async () => {
    const { api } = shopApi({ signedIn: true });
    render(<App api={api} />);
    await screen.findByRole("heading", { name: "Welcome, Fatima Owner" });

    fireEvent.keyDown(window, { key: "F8" });

    expect(screen.queryByRole("heading", { name: "Sales invoice (F8)" })).not.toBeInTheDocument();
  });

  it("navigates to Phase 1 areas from the sidebar", async () => {
    const { api } = shopApi({ signedIn: true });
    render(<App api={api} />);
    await screen.findByRole("heading", { name: "Welcome, Fatima Owner" });

    await userEvent.click(screen.getByRole("link", { name: "Inventory" }));

    expect(await screen.findByText("Inventory arrives in Phase 1.")).toBeInTheDocument();
  });

  it("is right-to-left in Arabic", async () => {
    const { api } = shopApi({ signedIn: true });
    render(<App api={api} />);
    await screen.findByRole("heading", { name: "Welcome, Fatima Owner" });

    await setLanguage("ar");

    expect(await screen.findByRole("heading", { name: "مرحبًا، Fatima Owner" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "لوحة التحكم" })).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
  });
});

describe("Translations", () => {
  it("English and Arabic have exactly the same keys", () => {
    const keys = (o: object, prefix = ""): string[] =>
      Object.entries(o).flatMap(([k, v]) =>
        v && typeof v === "object" ? keys(v as object, `${prefix}${k}.`) : [`${prefix}${k}`],
      );
    expect(keys(ar).sort()).toEqual(keys(en).sort());
  });
});
