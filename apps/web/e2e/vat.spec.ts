import { expect, test } from "@playwright/test";

// M1.10d acceptance, against the real API and database: a posted invoice lands in Box 1b (Dubai) of this month's
// VAT return, the figure opens that invoice, and the invoice prints as a tax invoice in English and Arabic.
// Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";
const SHOTS = process.env.SCREENSHOTS; // a folder: save the screens for a look

test("an invoice in the VAT return, opened from its box and printed", async ({ page, request }) => {
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
  const variant = (await product.json()).variants[0].id as string;
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
  const invoice = await request.post(`${API}/sales-documents?post=true`, {
    headers,
    data: {
      doc_type: "sales_invoice",
      business_id: business,
      branch_id: branch,
      doc_date: today,
      party_id: (await customer.json()).id,
      warehouse_id: warehouse,
      lines: [{ variant_id: variant, unit: "m", qty: "20", unit_price: "10", tax_code: "SR" }],
    },
  });
  expect(invoice.ok()).toBeTruthy();
  const number = (await invoice.json()).number as string;

  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, VAT Owner" })).toBeVisible();

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
  await expect(printed.getByText("Tax Invoice", { exact: false }).first()).toBeVisible();
  await expect(printed.getByText(number).first()).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/print-en.png`, fullPage: true });
  await page.getByRole("group", { name: "Language of the printed document" }).getByText("العربية").click();
  await expect(printed.getByText("فاتورة ضريبية").first()).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/print-ar.png`, fullPage: true });
});
