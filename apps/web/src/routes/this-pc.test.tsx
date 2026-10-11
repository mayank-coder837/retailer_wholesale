import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app";
import type { DesktopBridge, RuntimeInfo } from "@/config/runtime";
import { initI18n, setLanguage } from "@/i18n";
import { shopApi } from "@/test/shop-api";

const INFO: RuntimeInfo = {
  serverName: "Deira main PC",
  lan: true,
  apiPort: 8710,
  version: "0.1.0",
  previousVersion: null,
  addresses: ["192.168.1.20"],
  fingerprint: "ab".repeat(32),
};

const BACKUPS = {
  keep: 14,
  backups: [
    { name: "smartbiz-20261011-020000.dump", size_bytes: 5_242_880, created_at: "2026-10-10T22:00:00Z" },
  ],
};

function bridge(role: "main" | "office", info: RuntimeInfo = INFO) {
  const runtime = {
    info: vi.fn(async () => info),
    setLan: vi.fn(async () => ({ ok: false as const, code: "uac_cancelled" })),
    openPairing: vi.fn(async () => ({
      ok: true as const,
      code: "K7PQ2MXA",
      expiresAt: "2026-10-11T10:10:00Z",
      addresses: ["192.168.1.20"],
      port: 8710,
    })),
    closePairing: vi.fn(async () => ({ ok: true as const })),
    restore: vi.fn(async () => ({ ok: true as const })),
  };
  const disconnect = vi.fn(async () => ({ ok: true as const }));
  const session = {
    getRefreshToken: vi.fn(async () => "stored"),
    setRefreshToken: vi.fn(async () => undefined),
    clear: vi.fn(async () => undefined),
  };
  const value: DesktopBridge = {
    apiUrl: "http://api.test",
    isDesktop: true,
    role,
    session,
    ...(role === "main" ? { runtime } : { disconnect }),
  };
  window.smartbiz = value;
  return { runtime, disconnect };
}

describe("Settings → This PC", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
    window.location.hash = "#/settings/this-pc";
  });

  afterEach(() => {
    delete window.smartbiz;
    vi.restoreAllMocks();
  });

  it("shows the main PC's server, pairing code and backups", async () => {
    const { runtime } = bridge("main");
    const { api, calls } = shopApi({ signedIn: true, "GET /system/backups": { status: 200, body: BACKUPS } });
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "This PC" })).toBeInTheDocument();
    expect(await screen.findByText("Deira main PC")).toBeInTheDocument();
    expect(screen.getByText("On: office PCs can connect to this PC.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "This PC" })).toHaveAttribute("aria-current", "page");

    await userEvent.click(screen.getByRole("button", { name: "Show pairing code" }));
    expect(await screen.findByTestId("pairing-code")).toHaveTextContent("K7PQ2MXA");
    expect(screen.getByText("192.168.1.20:8710")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Stop pairing" }));
    expect(runtime.closePairing).toHaveBeenCalled();

    const backups = await screen.findByRole("region", { name: "Backups" });
    expect(within(backups).getByText(/5 MB/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/system/backups")?.authorization).toMatch(/^Bearer /);
  });

  it("restores a backup only after the person confirms", async () => {
    const { runtime } = bridge("main");
    const { api } = shopApi({ signedIn: true, "GET /system/backups": { status: 200, body: BACKUPS } });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<App api={api} />);

    const restore = await screen.findByRole("button", { name: "Restore" });
    await userEvent.click(restore);
    expect(runtime.restore).not.toHaveBeenCalled();
    await userEvent.click(restore);
    expect(runtime.restore).toHaveBeenCalledWith("smartbiz-20261011-020000.dump");
    expect(confirm.mock.calls[0]?.[0]).toMatch(/Everything entered after it will be lost/);
  });

  it("backs up now and reports a refused administrator prompt", async () => {
    const { runtime } = bridge("main");
    const { api } = shopApi({
      signedIn: true,
      "GET /system/backups": { status: 200, body: BACKUPS },
      "POST /system/backups": { status: 201, body: BACKUPS.backups[0] },
    });
    render(<App api={api} />);

    await userEvent.click(await screen.findByRole("button", { name: "Back up now" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Backup saved: smartbiz-20261011-020000.dump",
    );

    await userEvent.click(screen.getByRole("button", { name: "Turn off" }));
    expect(runtime.setLan).toHaveBeenCalledWith(false);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Windows administrator approval was not given.",
    );
  });

  it("asks for office network access before pairing, in Arabic too", async () => {
    bridge("main", { ...INFO, lan: false });
    await setLanguage("ar");
    const { api } = shopApi({
      signedIn: true,
      "GET /system/backups": { status: 503, body: { code: "backups_unavailable" } },
    });
    render(<App api={api} />);

    expect(await screen.findByRole("heading", { name: "هذا الحاسوب" })).toBeInTheDocument();
    expect(await screen.findAllByText("فعّل الوصول عبر شبكة المكتب أولاً.")).not.toHaveLength(0);
    expect(screen.queryByRole("button", { name: "عرض رمز الربط" })).toBeNull();
    expect(await screen.findByText("لا يحتفظ هذا الخادم بنسخ احتياطية محلية.")).toBeInTheDocument();
  });

  it("lets an office PC disconnect from the main PC", async () => {
    const { disconnect } = bridge("office");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { api } = shopApi({ signedIn: true });
    render(<App api={api} />);

    await userEvent.click(await screen.findByRole("button", { name: "Disconnect from the main PC" }));
    expect(disconnect).toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "Backups" })).toBeNull();
  });

  it("hides This PC outside the desktop app", async () => {
    window.location.hash = "#/dashboard";
    const { api } = shopApi({ signedIn: true });
    render(<App api={api} />);

    await screen.findByRole("heading", { name: "Welcome, Fatima Owner" });
    expect(screen.queryByRole("link", { name: "This PC" })).toBeNull();
  });
});
