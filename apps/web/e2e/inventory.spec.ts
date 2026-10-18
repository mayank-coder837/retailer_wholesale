import { expect, test, type APIRequestContext } from "@playwright/test";

// M1.6 acceptance, against the real API and database: soap counted in by the carton through the screens shows on
// the stock page in pieces, the stock ledger and the books agree, and the item's ledger shows the move.
// Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";

async function store(request: APIRequestContext) {
  const email = `store-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.ae`;
  const signup = await request.post(`${API}/auth/signup`, {
    data: {
      email,
      password: PASSWORD,
      full_name: "Store Owner",
      tenant_name: "Store Trading",
      business: { legal_name_en: "Store Trading LLC", legal_name_ar: "شركة المخزن" },
      head_office: { code: "HO", name_en: "Head office", name_ar: "الرئيسي", emirate: "dubai" },
      client: "web",
    },
  });
  expect(signup.ok()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await signup.json()).access_token}` };
  const product = await request.post(`${API}/products`, {
    headers,
    data: {
      sku: "SOAP",
      name_en: "Soap",
      name_ar: "صابون",
      base_unit: "pcs",
      units: [{ unit: "carton", factor_to_base: "24" }],
    },
  });
  expect(product.ok()).toBeTruthy();
  return { email, product: await product.json() };
}

test("stock counted in by the carton shows in pieces and the books agree", async ({ page, request }) => {
  const { email, product } = await store(request);

  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Store Owner" })).toBeVisible();

  await page.goto("/#/inventory/documents/new/stock_adjustment");
  await expect(page.getByRole("heading", { name: "Stock adjustment" })).toBeVisible();
  await page.getByLabel("Item", { exact: true }).selectOption(product.id);
  await expect(page.getByLabel("Unit", { exact: true })).toHaveValue("pcs");
  await page.getByLabel("Unit", { exact: true }).selectOption("carton");
  await page.getByLabel("Quantity", { exact: true }).fill("2");
  await page.getByLabel("Cost per unit").fill("36");
  await page.getByLabel("Reason").fill("opening count");
  await page.getByRole("button", { name: "Post" }).click();

  await expect(page.getByRole("heading", { name: /Stock adjustment SA-\d{4}-00001/ })).toBeVisible();
  await expect(page.getByRole("table", { name: "Items" })).toContainText("2 carton(48 pcs)");
  await expect(page.getByRole("table", { name: "Items" })).toContainText("72.00");

  await page.goto("/#/inventory/stock");
  await expect(page.getByText("The stock ledger and the books agree.")).toBeVisible();
  const row = page.getByRole("row", { name: /SOAP/ });
  await expect(row).toContainText("48 pcs");
  await expect(row).toContainText("72.00");

  await row.getByRole("link", { name: "Soap" }).click();
  await expect(page.getByRole("heading", { name: /SOAP\s*Soap/ })).toBeVisible();
  await expect(page.getByText("Average cost:")).toContainText("1.500000");
  await expect(page.getByRole("table", { name: "Stock ledger" })).toContainText("Adjustment in");
});
