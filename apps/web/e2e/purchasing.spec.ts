import { expect, test, type APIRequestContext } from "@playwright/test";

// M1.7 acceptance, against the real API and database: an order is received in part, the receipt is billed,
// the bill is cancelled (its number freed and taken again) and the new bill is reversed by a debit note.
// Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";

async function buyer(request: APIRequestContext) {
  const email = `buyer-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.ae`;
  const signup = await request.post(`${API}/auth/signup`, {
    data: {
      email,
      password: PASSWORD,
      full_name: "Buyer Owner",
      tenant_name: "Buyer Trading",
      business: { legal_name_en: "Buyer Trading LLC", legal_name_ar: "شركة المشتري" },
      head_office: { code: "HO", name_en: "Head office", name_ar: "الرئيسي", emirate: "dubai" },
      client: "web",
    },
  });
  expect(signup.ok()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await signup.json()).access_token}` };
  const supplier = await request.post(`${API}/parties`, {
    headers,
    data: { name_en: "Sharjah Mills", name_ar: "مصانع الشارقة", is_supplier: true },
  });
  expect(supplier.ok()).toBeTruthy();
  const product = await request.post(`${API}/products`, {
    headers,
    data: {
      sku: "FAB",
      name_en: "Poplin fabric",
      name_ar: "قماش بوبلين",
      base_unit: "m",
      units: [{ unit: "roll", factor_to_base: "50" }],
    },
  });
  expect(product.ok()).toBeTruthy();
  return { email, supplier: (await supplier.json()).id as string };
}

test("order → part receipt → bill, then cancel, reuse the number and reverse", async ({ page, request }) => {
  const { email, supplier } = await buyer(request);

  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Buyer Owner" })).toBeVisible();

  // The order: 2 rolls at 380.
  await page.getByRole("link", { name: "Purchases" }).click();
  await page.getByRole("link", { name: "New order" }).click();
  await page.getByLabel("Supplier", { exact: true }).selectOption(supplier);
  await page.getByLabel("Item", { exact: true }).selectOption({ label: "FAB Poplin fabric" });
  await expect(page.getByLabel("Unit", { exact: true })).toHaveValue("m");
  await page.getByLabel("Unit", { exact: true }).selectOption("roll");
  await page.getByLabel("Quantity", { exact: true }).fill("2");
  await page.getByLabel("Price per unit").fill("380");
  await expect(page.getByLabel("Totals")).toContainText("Total798.00");
  await page.getByRole("button", { name: "Post" }).click();
  await expect(page.getByRole("heading", { name: /Purchase order PO-\d{4}-00001/ })).toBeVisible();

  // One roll arrives.
  await page.getByRole("link", { name: "Receive goods" }).click();
  await expect(page.getByText("Still open: 2 roll")).toBeVisible();
  await page.getByLabel("Quantity", { exact: true }).fill("1");
  await page.getByRole("button", { name: "Post" }).click();
  await expect(page.getByRole("heading", { name: /Goods received GRN-\d{4}-00001/ })).toBeVisible();
  await expect(page.getByText("Open", { exact: true })).toBeVisible(); // still to bill

  // The supplier bills it at 400: the bill, then cancelled.
  await page.getByRole("link", { name: "Enter the bill" }).click();
  await page.getByLabel("Supplier's bill no.").fill("SM-1");
  await page.getByLabel("Price per unit").fill("400");
  await page.getByRole("button", { name: "Post" }).click();
  const billHeading = page.getByRole("heading", { name: /Supplier bill PB-\d{4}-00001/ });
  await expect(billHeading).toBeVisible();
  await page.getByRole("button", { name: "Cancel" }).click();
  await page.getByLabel("Reason", { exact: true }).fill("Wrong price");
  await page.getByRole("button", { name: "Cancel the document" }).click();
  await expect(page.getByText(/Cancelled on/)).toBeVisible();

  // Billed again, taking the freed number; then reversed by a debit note.
  await page.goto("/#/purchases");
  await page.getByRole("link", { name: /GRN-\d{4}-00001/ }).click();
  await page.getByRole("link", { name: "Enter the bill" }).click();
  await page.getByLabel("Number").selectOption("1"); // reuse the freed PB-…-00001
  await page.getByLabel("Supplier's bill no.").fill("SM-2");
  await page.getByRole("button", { name: "Post" }).click();
  await expect(billHeading).toBeVisible();
  await expect(page.getByText("Reissued with the number of the cancelled")).toBeVisible();
  await page.getByRole("button", { name: "Reverse" }).click();
  await page.getByLabel("Reason", { exact: true }).fill("Supplier withdrew it");
  await page.getByRole("button", { name: "Reverse the document" }).click();
  await expect(page.getByText(/Reversed by/)).toBeVisible();
  await page.getByRole("link", { name: /DBN-\d{4}-00001/ }).click();
  await expect(page.getByRole("heading", { name: /Debit note DBN-\d{4}-00001/ })).toBeVisible();
  await expect(page.getByText(/This reverses/)).toBeVisible();

  // The books still agree: nothing billed is left, and the trial balance balances.
  await page.goto("/#/accounts/trial-balance");
  const total = page.getByRole("table", { name: "Trial balance" }).getByRole("row").last();
  await expect(total).toContainText("Total380.00380.00");
});
