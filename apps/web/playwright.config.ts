import { defineConfig, devices } from "@playwright/test";

/**
 * Two projects:
 * - smoke: no API needed (signed-out pages);
 * - full: the onboarding, accounting and inventory acceptance flows against the real API (VITE_API_URL, default http://localhost:8000).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    { name: "smoke", testMatch: /smoke\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    {
      name: "full",
      testMatch: /(onboarding|accounts|inventory|purchasing|sales)\.spec\.ts/,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
