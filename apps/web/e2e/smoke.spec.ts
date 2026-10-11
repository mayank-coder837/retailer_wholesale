import { expect, test } from "@playwright/test";

import product from "@smartbiz/config" with { type: "json" };

// Runs without an API: a signed-out visitor sees the login page.

test("signed-out visitors land on the login page", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle(product.name);
  await expect(page.getByRole("heading", { name: "Log in" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
});

test("the login page switches to Arabic, right to left", async ({ page }) => {
  await page.goto("/#/login");

  await page.getByRole("button", { name: "Language" }).click();

  await expect(page.getByRole("heading", { name: "تسجيل الدخول" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
});
