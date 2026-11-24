import { expect, test } from "@playwright/test";

// M1.12d acceptance, against the real API and database: a customers file goes through the import page (upload,
// match, check, import), and an opening trial balance imported through the API shows "Everything ties" on the
// opening balances check. Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";

test("customers are imported through the page and the opening balances tie", async ({ page, request }) => {
  const email = `imports-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.ae`;
  const signup = await request.post(`${API}/auth/signup`, {
    data: {
      email,
      password: PASSWORD,
      full_name: "Import Owner",
      tenant_name: "Import Trading",
      business: { legal_name_en: "Import Trading LLC", legal_name_ar: "شركة الاستيراد" },
      head_office: { code: "HO", name_en: "Head office", name_ar: "الرئيسي", emirate: "dubai" },
      client: "web",
    },
  });
  expect(signup.ok()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await signup.json()).access_token}` };
  const business = (await (await request.get(`${API}/businesses`, { headers })).json())[0].id as string;

  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Import Owner" })).toBeVisible();

  // Customers, through the page.
  await page.getByRole("link", { name: "Import data" }).click();
  await page.getByRole("button", { name: "Choose Customers and suppliers" }).click();
  await page.getByLabel("File", { exact: true }).setInputFiles({
    name: "customers.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("Party Name,TRN\nGulf Fabrics LLC,100123456700003\nAl Noor Stores,\n"),
  });
  await page.getByRole("button", { name: "Upload and read the file" }).click();
  await expect(page.getByRole("heading", { name: "Match the columns" })).toBeVisible();
  await expect(page.getByLabel("Name", { exact: true })).toHaveValue("0"); // found by its heading
  await page.getByRole("button", { name: "Check the file" }).click();
  await expect(page.getByRole("status").filter({ hasText: "all or nothing" })).toBeVisible();
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(page.getByText("Imported.")).toBeVisible();
  const parties = await (await request.get(`${API}/parties?q=Gulf`, { headers })).json();
  expect(parties).toHaveLength(1);

  // An opening trial balance through the API, then the check page.
  const upload = await request.post(`${API}/imports`, {
    headers,
    multipart: {
      business_id: business,
      kind: "opening_balances",
      file: {
        name: "tb.csv",
        mimeType: "text/csv",
        buffer: Buffer.from("Account,Debit,Credit\n1110,100,\n3100,,100\n"),
      },
    },
  });
  expect(upload.ok(), await upload.text()).toBeTruthy();
  const batch = await upload.json();
  const today = new Date().toISOString().slice(0, 10);
  const checked = await request.post(`${API}/imports/${batch.id}/check`, {
    headers,
    data: { mapping: batch.suggested_mapping, options: { opening_date: today } },
  });
  expect(checked.ok(), await checked.text()).toBeTruthy();
  expect((await request.post(`${API}/imports/${batch.id}/commit`, { headers })).ok()).toBeTruthy();

  await page.goto("/#/settings/import/reconciliation");
  await expect(page.getByRole("status")).toContainText("Everything ties.");
  await expect(page.getByRole("table", { name: "Opening balances check" })).toContainText("100.00");
  await page.getByRole("button", { name: "Language" }).first().click();
  await expect(page.getByRole("heading", { name: "فحص الأرصدة الافتتاحية" })).toBeVisible();
});
