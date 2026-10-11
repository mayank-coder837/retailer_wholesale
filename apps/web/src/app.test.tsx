import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it } from "vitest";

import { createApiClient } from "@smartbiz/api-client";
import product from "@smartbiz/config";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";

function apiReturning(status: number, body: unknown) {
  const fakeFetch = (async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })) as typeof fetch;
  return createApiClient("http://api.test", fakeFetch);
}

const healthy = () => apiReturning(200, { status: "ok", product: product.name, version: "0.1.0" });

describe("App shell", () => {
  beforeAll(async () => {
    await initI18n();
  });

  it("shows the product name from packages/config", async () => {
    await setLanguage("en");
    render(<App api={healthy()} />);

    expect(await screen.findByRole("heading", { name: `Welcome to ${product.name}` })).toBeInTheDocument();
  });

  it("reports the API as connected when /health answers", async () => {
    await setLanguage("en");
    render(<App api={healthy()} />);

    await waitFor(() => expect(screen.getByTestId("api-status")).toHaveAttribute("data-status", "online"));
  });

  it("reports the API as not reachable when /health fails", async () => {
    await setLanguage("en");
    render(<App api={apiReturning(503, { code: "database_unavailable" })} />);

    await waitFor(() => expect(screen.getByTestId("api-status")).toHaveAttribute("data-status", "offline"));
  });

  it("switches to Arabic with right-to-left layout", async () => {
    await setLanguage("en");
    render(<App api={healthy()} />);

    await userEvent.click(await screen.findByRole("button", { name: "Language" }));

    expect(await screen.findByRole("heading", { name: `مرحبًا بك في ${product.name}` })).toBeInTheDocument();
    expect(document.documentElement).toHaveAttribute("dir", "rtl");
    expect(document.documentElement).toHaveAttribute("lang", "ar");
  });
});
