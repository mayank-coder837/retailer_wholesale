import { expect, test, type APIRequestContext } from "@playwright/test";

// M1.8 acceptance, against the real API and database: quotation → order → part delivery (no prices) → invoice at
// the order's price; the invoice is sent, cancelled with the sent warning, and the books agree.
// Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";

async function seller(request: APIRequestContext) {
  const email = `seller-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.ae`;
  const signup = await request.post(`${API}/auth/signup`, {
    data: {
      email,
      password: PASSWORD,
      full_name: "Seller Owner",
      tenant_name: "Seller Trading",
      business: {
        legal_name_en: "Seller Trading LLC",
        legal_name_ar: "شركة البائع",
        vat_registered: true, // sales charge VAT only for a registered business (M1.10)
        trn: "100000000000003",
      },
      head_office: { code: "HO", name_en: "Head office", name_ar: "الرئيسي", emirate: "dubai" },
      client: "web",
    },
  });
  expect(signup.ok()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await signup.json()).access_token}` };
  const get = async (path: string) => (await request.get(`${API}${path}`, { headers })).json();
  const business = (await get("/businesses"))[0].id as string;
  const branch = (await get(`/businesses/${business}/branches`))[0].id as string;
  const warehouse = (await get(`/branches/${branch}/warehouses`))[0].id as string;
  const customer = await request.post(`${API}/parties`, {
    headers,
    data: { name_en: "Gulf Fabrics LLC", name_ar: "أقمشة الخليج", is_customer: true },
  });
  const product = await request.post(`${API}/products`, {
    headers,
    data: { sku: "FAB", name_en: "Poplin fabric", name_ar: "قماش بوبلين", base_unit: "m" },
  });
  const variant = (await product.json()).variants[0].id as string;
  const stock = await request.post(`${API}/stock-documents?post=true`, {
    headers,
    data: {
      doc_type: "stock_adjustment",
      business_id: business,
      branch_id: branch,
      doc_date: new Date().toISOString().slice(0, 10),
      warehouse_id: warehouse,
      lines: [{ variant_id: variant, unit: "m", qty: "100", direction: "in", unit_cost: "6" }],
    },
  });
  expect(stock.ok()).toBeTruthy();
  return { email, customer: (await customer.json()).id as string };
}

test("quotation → order → delivery → invoice, sent, cancelled; the books agree", async ({ page, request }) => {
  const { email, customer } = await seller(request);

  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Seller Owner" })).toBeVisible();

  // The quotation: 20 m at 10, numbered when saved.
  await page.getByRole("link", { name: "Sales", exact: true }).click();
  await page.getByRole("link", { name: "New quotation" }).click();
  await page.getByLabel("Customer", { exact: true }).selectOption(customer);
  await page.getByLabel("Item", { exact: true }).selectOption({ label: "FAB Poplin fabric" });
  await expect(page.getByLabel("Unit", { exact: true })).toHaveValue("m");
  await page.getByLabel("Quantity", { exact: true }).fill("20");
  await page.getByLabel("Price per unit").fill("10");
  await expect(page.getByLabel("Totals")).toContainText("Total210.00");
  await page.getByRole("button", { name: "Save quotation" }).click();
  await expect(page.getByRole("heading", { name: /Quotation QT-\d{4}-00001/ })).toBeVisible();

  // Ordered, then 15 m delivered (no prices on the delivery note).
  await page.getByRole("link", { name: "Make the order" }).click();
  await page.getByRole("button", { name: "Post" }).click();
  await expect(page.getByRole("heading", { name: /Sales order SO-\d{4}-00001/ })).toBeVisible();
  await page.getByRole("link", { name: "Deliver goods" }).click();
  await expect(page.getByLabel("Price per unit")).toHaveCount(0);
  await page.getByLabel("Quantity", { exact: true }).fill("15");
  await page.getByRole("button", { name: "Post" }).click();
  await expect(page.getByRole("heading", { name: /Delivery note DN-\d{4}-00001/ })).toBeVisible();
  await expect(page.getByText("Not invoiced", { exact: true })).toBeVisible();

  // Invoiced at the order's price.
  await page.getByRole("link", { name: "Invoice the delivery" }).click();
  await expect(page.getByLabel("Price per unit")).toHaveValue("10");
  await page.getByRole("button", { name: "Post" }).click();
  await expect(page.getByRole("heading", { name: /Sales invoice INV-\d{4}-00001/ })).toBeVisible();
  await expect(page.getByLabel("Totals")).toContainText("Total157.50");

  // Sent to the customer, then cancelled: the panel warns first.
  await page.getByRole("button", { name: "Mark as sent" }).click();
  await expect(page.getByText("Sent", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByText(/This document was printed or sent to the customer/)).toBeVisible();
  await page.getByLabel("Reason", { exact: true }).fill("Wrong customer");
  await page.getByRole("button", { name: "Cancel the document" }).click();
  await expect(page.getByText(/Cancelled on/)).toBeVisible();

  // Nothing is owed now: ageing is empty, and the trial balance balances.
  await page.goto("/#/accounts/ageing");
  await expect(page.getByText("Nothing is open.")).toBeVisible();
  await page.goto("/#/accounts/trial-balance");
  const total = page.getByRole("table", { name: "Trial balance" }).getByRole("row").last();
  await expect(total).toContainText("Total600.00600.00");
});
