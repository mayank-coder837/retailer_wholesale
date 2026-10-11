import { expect, test } from "@playwright/test";

import product from "@smartbiz/config" with { type: "json" };

test("home renders in English, left to right, with the product name", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle(product.name);
  await expect(page.getByRole("heading", { name: `Welcome to ${product.name}` })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
});

test("switching to Arabic renders right to left and is remembered", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "Language" }).click();

  await expect(page.getByRole("heading", { name: `مرحبًا بك في ${product.name}` })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
});
