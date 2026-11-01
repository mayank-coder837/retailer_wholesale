import { expect, test } from "@playwright/test";

// M1.11 acceptance, against the real API and database: a small month (capital in, a cash sale, rent paid) gives a
// profit and loss whose net profit is the balance sheet's profit for the year, a balanced sheet, and a day book
// with every entry. Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";
const SHOTS = process.env.SCREENSHOTS; // a folder: save the screens for a look

test("profit and loss, balance sheet and day book agree", async ({ page, request }) => {
  const email = `reports-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.ae`;
  const signup = await request.post(`${API}/auth/signup`, {
    data: {
      email,
      password: PASSWORD,
      full_name: "Report Owner",
      tenant_name: "Report Trading",
      business: { legal_name_en: "Report Trading LLC", legal_name_ar: "شركة التقارير" },
      head_office: { code: "HO", name_en: "Head office", name_ar: "الرئيسي", emirate: "dubai" },
      client: "web",
    },
  });
  expect(signup.ok()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await signup.json()).access_token}` };
  const get = async (path: string) => (await request.get(`${API}${path}`, { headers })).json();
  const business = (await get("/businesses"))[0].id as string;
  const branch = (await get(`/businesses/${business}/branches`))[0].id as string;
  const accounts = (await get(`/accounts?business_id=${business}`)) as { id: string; code: string }[];
  const account = (code: string) => accounts.find((a) => a.code === code)!.id;
  const today = new Date().toISOString().slice(0, 10);
  const post = async (body: Record<string, unknown>) => {
    const r = await request.post(`${API}/vouchers?post=true`, {
      headers,
      data: { business_id: business, branch_id: branch, doc_date: today, ...body },
    });
    expect(r.ok(), await r.text()).toBeTruthy();
  };
  const journal = (dr: string, cr: string, amount: string) =>
    post({
      doc_type: "journal",
      lines: [
        { account_id: account(dr), debit: amount },
        { account_id: account(cr), credit: amount },
      ],
    });
  await journal("1110", "3100", "10000"); // capital in
  await journal("1110", "4100", "5000"); // a cash sale
  await post({
    doc_type: "expense",
    account_id: account("1110"),
    lines: [{ account_id: account("6100"), amount: "1000" }],
  });

  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Report Owner" })).toBeVisible();

  await page.getByRole("link", { name: "Reports", exact: true }).click();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/reports-en.png`, fullPage: true });
  await page.getByRole("region", { name: "Accounts" }).getByRole("link", { name: "Profit and loss" }).click();
  const pl = page.getByRole("table", { name: "Profit and loss" });
  await expect(pl.getByRole("row", { name: /^Gross profit/ })).toContainText("5,000.00");
  await expect(pl.getByRole("row", { name: /^Net profit/ })).toContainText("4,000.00");
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/profit-loss-en.png`, fullPage: true });

  await page.getByRole("link", { name: "Balance sheet" }).first().click();
  const bs = page.getByRole("table", { name: "Balance sheet" });
  await expect(bs.getByRole("row", { name: /Profit for the year to date/ })).toContainText("4,000.00");
  await expect(bs.getByRole("row", { name: /^Total assets/ })).toContainText("14,000.00");
  await expect(bs.getByRole("row", { name: /^Total liabilities and equity/ })).toContainText("14,000.00");
  await expect(page.getByRole("alert")).toHaveCount(0);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/balance-sheet-en.png`, fullPage: true });

  await page.getByRole("link", { name: "Day book" }).first().click();
  const book = page.getByRole("table", { name: "Day book" });
  await expect(book).toContainText("Total of 3 entries16,000.0016,000.00");

  await page.getByRole("button", { name: "Language" }).click();
  await expect(page.getByRole("table", { name: "دفتر اليومية" })).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/day-book-ar.png`, fullPage: true });
  await page.getByRole("link", { name: "الميزانية العمومية" }).first().click();
  await expect(page.getByRole("table", { name: "الميزانية العمومية" })).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/balance-sheet-ar.png`, fullPage: true });
});
