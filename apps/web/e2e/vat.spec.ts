import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

// M1.10d acceptance, against the real API and database: a posted invoice lands in Box 1b (Dubai) of this month's
// VAT return, the figure opens that invoice, and the invoice prints as a tax invoice in English and Arabic; an
// invoice typed with VAT included posts the VAT the screen showed.
// Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";
const SHOTS = process.env.SCREENSHOTS; // a folder: save the screens for a look

/** A VAT-registered trader in Dubai with a customer and 100 m of fabric in stock. */
async function trader(request: APIRequestContext) {
  const email = `vat-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.ae`;
  const signup = await request.post(`${API}/auth/signup`, {
    data: {
      email,
      password: PASSWORD,
      full_name: "VAT Owner",
      tenant_name: "VAT Trading",
      business: {
        legal_name_en: "VAT Trading LLC",
        legal_name_ar: "شركة الضريبة",
        vat_registered: true,
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
  const today = new Date().toISOString().slice(0, 10);
  const customer = await request.post(`${API}/parties`, {
    headers,
    data: { name_en: "Gulf Fabrics LLC", name_ar: "أقمشة الخليج", is_customer: true },
  });
  const product = await request.post(`${API}/products`, {
    headers,
    data: { sku: "FAB", name_en: "Poplin fabric", name_ar: "قماش بوبلين", base_unit: "m" },
  });
  const productBody = await product.json();
  const variant = productBody.variants[0].id as string;
  const productId = productBody.id as string;
  const stock = await request.post(`${API}/stock-documents?post=true`, {
    headers,
    data: {
      doc_type: "stock_adjustment",
      business_id: business,
      branch_id: branch,
      doc_date: today,
      warehouse_id: warehouse,
      lines: [{ variant_id: variant, unit: "m", qty: "100", direction: "in", unit_cost: "6" }],
    },
  });
  expect(stock.ok()).toBeTruthy();
  const customerId = (await customer.json()).id as string;
  return { email, headers, business, branch, warehouse, today, customerId, variant, productId };
}

async function logIn(page: Page, email: string) {
  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, VAT Owner" })).toBeVisible();
}

test("an invoice in the VAT return, opened from its box and printed", async ({ page, request }) => {
  const { email, headers, business, branch, warehouse, today, customerId, variant } = await trader(request);
  const invoice = await request.post(`${API}/sales-documents?post=true`, {
    headers,
    data: {
      doc_type: "sales_invoice",
      business_id: business,
      branch_id: branch,
      doc_date: today,
      party_id: customerId,
      warehouse_id: warehouse,
      lines: [{ variant_id: variant, unit: "m", qty: "20", unit_price: "10", tax_code: "SR" }],
    },
  });
  expect(invoice.ok()).toBeTruthy();
  const number = (await invoice.json()).number as string;

  await logIn(page, email);

  // This month, by dates: 200.00 at 5% in Dubai.
  await page.getByRole("link", { name: "Accounts", exact: true }).click();
  await page.getByRole("link", { name: "VAT return" }).click();
  await page.getByRole("button", { name: "Other dates" }).click();
  await page.getByLabel("From").fill(`${today.slice(0, 8)}01`);
  await page.getByLabel("To").fill(today);
  await expect(page.getByText("VAT payable to the FTA")).toBeVisible();
  await expect(page.getByText("AED 10.00")).toBeVisible();
  const dubai = page.getByRole("row", { name: /^1b/ });
  await expect(dubai).toContainText("200.00");
  await expect(dubai).toContainText("10.00");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/vat-return-en.png`, fullPage: true });

  await dubai.getByRole("button", { name: "Show the documents behind box 1b" }).last().click();
  const docs = page.getByRole("table", { name: "Documents in box 1b" });
  await expect(docs).toContainText(number);
  await expect(docs).toContainText("Total of 1 document200.0010.00");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/vat-drill-en.png`, fullPage: true });

  await page.getByRole("button", { name: "Language" }).click();
  await expect(page.getByText("ضريبة مستحقة الدفع للهيئة")).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/vat-return-ar.png`, fullPage: true });
  await page.getByRole("button", { name: "اللغة" }).click();

  // The invoice from its box, then its print preview.
  await docs.getByRole("link", { name: number }).click();
  await page.getByRole("link", { name: "Print / Save as PDF" }).click();
  const printed = page.frameLocator('iframe[title="Print preview"]');
  const print = page.getByRole("button", { name: "Print / Save as PDF" });
  await expect(printed.locator('html[dir="ltr"]')).toBeAttached();
  await expect(printed.getByText("Tax Invoice", { exact: false }).first()).toBeVisible();
  await expect(printed.getByText(number).first()).toBeVisible();
  await expect(print).toBeEnabled();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/print-en.png`, fullPage: true });
  // Both languages show both titles: the page's direction tells which one arrived.
  await page.getByRole("group", { name: "Language of the printed document" }).getByText("العربية").click();
  await expect(printed.locator('html[dir="rtl"]')).toBeAttached();
  await expect(printed.getByText(number).first()).toBeVisible();
  await expect(print).toBeEnabled();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/print-ar.png`, fullPage: true });
});

test("an invoice typed with VAT included posts the VAT the screen showed", async ({ page, request }) => {
  const { email } = await trader(request);
  await logIn(page, email);

  await page.getByRole("link", { name: "Sales", exact: true }).click();
  await page.getByRole("link", { name: "New invoice" }).click();
  await page.getByLabel("Customer", { exact: true }).selectOption({ label: "C0001 Gulf Fabrics LLC" });
  await page.getByLabel("Item", { exact: true }).selectOption({ label: "FAB Poplin fabric" });
  await expect(page.getByLabel("Unit", { exact: true })).toHaveValue("m");
  await page.getByLabel("Quantity", { exact: true }).fill("3");
  await page.getByLabel("Price per unit").fill("10.50");
  await expect(page.getByLabel("Tax", { exact: true })).toHaveValue("SR");
  await page.getByLabel("Prices").selectOption("vat_inclusive");
  // 31.50 including VAT: VAT 1.50, net 30.00 (the API's own example).
  await expect(page.getByLabel("Totals")).toContainText("Subtotal30.00VAT1.50Total31.50");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/entry-inclusive-en.png`, fullPage: true });
  await page.getByRole("button", { name: "Post" }).click();

  await expect(page.getByRole("heading", { name: /Sales invoice INV-\d{4}-00001/ })).toBeVisible();
  await expect(page.getByLabel("Totals")).toContainText("Subtotal30.00VAT1.50Total31.50");

  await page.getByRole("button", { name: "Language" }).click();
  await page.getByRole("link", { name: "المبيعات", exact: true }).click();
  await page.getByRole("link", { name: "فاتورة جديدة" }).click();
  await expect(page.getByLabel("الأسعار")).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/entry-ar.png`, fullPage: true });
});

test("an expense under the reverse charge: VAT shown, not paid; item and customer codes", async ({
  page,
  request,
}) => {
  const { email, customerId, productId } = await trader(request);
  await logIn(page, email);

  await page.goto("/#/vouchers/expense");
  await expect(page.getByRole("heading", { name: "Expense voucher" })).toBeVisible();
  await page.getByLabel("Account", { exact: true }).selectOption({ label: "6100 Rent" });
  await page.getByLabel("Amount", { exact: true }).fill("200");
  await page.getByLabel("Tax", { exact: true }).selectOption("RC-S");
  await expect(page.getByLabel("Totals")).toContainText(
    "Net200.00VAT10.00Reverse charge (you account for it)-10.00Total200.00",
  );
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/voucher-rc-en.png`, fullPage: true });
  await page.getByRole("button", { name: "Post" }).click();
  await expect(page.getByRole("heading", { name: /Expense voucher EV-\d{4}-00001/ })).toBeVisible();
  // The cash paid is the net: the VAT goes in as input and out as output.
  const journal = page.getByRole("table");
  await expect(journal).toContainText("200.00");
  await expect(journal).toContainText("10.00");

  await page.goto(`/#/inventory/products/${productId}`);
  const tax = page.getByRole("region", { name: "Tax" });
  await tax.getByLabel("Tax when sold").selectOption("EX");
  await tax.getByRole("button", { name: "Save tax" }).click();
  await expect(tax.getByText("Saved")).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/product-tax-en.png`, fullPage: true });

  await page.goto(`/#/parties/${customerId}`);
  const details = page.getByRole("region", { name: "Details" });
  await details.getByRole("button", { name: "Edit", exact: true }).click();
  await details.getByLabel("Tax on sales to them").selectOption("ZR");
  await details.getByRole("button", { name: "Save", exact: true }).click();
  await expect(details.getByRole("definition").filter({ hasText: "Zero rated" })).toBeVisible();
  await page.getByRole("button", { name: "Language" }).click();
  await expect(page.getByText("الضريبة على المبيعات له")).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/party-tax-ar.png`, fullPage: true });

  // A new invoice to this customer now defaults to zero rated: the customer's code beats the item's.
  await page.getByRole("button", { name: "اللغة" }).click();
  await page.goto("/#/sales/new/sales_invoice");
  await page.getByLabel("Customer", { exact: true }).selectOption(customerId);
  await page.getByLabel("Item", { exact: true }).selectOption({ label: "FAB Poplin fabric" });
  await expect(page.getByLabel("Tax", { exact: true })).toHaveValue("ZR");
});
