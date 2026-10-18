import { expect, test, type APIRequestContext } from "@playwright/test";

// M1.5 acceptance, against the real API and database: a receipt settles an open invoice bill-wise, the
// voucher shows what it posted, the customer's statement and the trial balance agree.
// Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";

async function books(request: APIRequestContext) {
  const email = `books-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.ae`;
  const signup = await request.post(`${API}/auth/signup`, {
    data: {
      email,
      password: PASSWORD,
      full_name: "Books Owner",
      tenant_name: "Books Trading",
      business: { legal_name_en: "Books Trading LLC", legal_name_ar: "شركة الدفاتر" },
      head_office: { code: "HO", name_en: "Head office", name_ar: "الرئيسي", emirate: "dubai" },
      client: "web",
    },
  });
  expect(signup.ok()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await signup.json()).access_token}` };
  const get = async (path: string) => (await request.get(`${API}${path}`, { headers })).json();
  const business = (await get("/businesses"))[0].id as string;
  const branch = (await get(`/businesses/${business}/branches`))[0].id as string;
  const accounts = Object.fromEntries(
    ((await get(`/accounts?business_id=${business}`)) as { code: string; id: string }[]).map((a) => [
      a.code,
      a.id,
    ]),
  );
  const customer = await request.post(`${API}/parties`, {
    headers,
    data: { name_en: "Ali Traders", name_ar: "علي للتجارة", is_customer: true },
  });
  const party = (await customer.json()).id as string;
  // An opening invoice of 1,000 (sales invoices arrive in M1.8): Dr customer (bill INV-1) / Cr sales.
  const invoice = await request.post(`${API}/vouchers?post=true`, {
    headers,
    data: {
      doc_type: "journal",
      business_id: business,
      branch_id: branch,
      doc_date: new Date().toISOString().slice(0, 10),
      lines: [
        {
          account_id: accounts["1200"],
          party_id: party,
          debit: "1000",
          allocations: [{ type: "new_ref", amount: "1000", reference: "INV-1" }],
        },
        { account_id: accounts["4100"], credit: "1000" },
      ],
    },
  });
  expect(invoice.ok()).toBeTruthy();
  return { email, party };
}

test("a receipt settles an invoice bill-wise and the books agree", async ({ page, request }) => {
  const { email, party } = await books(request);

  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Books Owner" })).toBeVisible();

  await page.goto("/#/vouchers/receipt");
  await expect(page.getByRole("heading", { name: "Receipt voucher" })).toBeVisible();
  await page.getByLabel("Customer or supplier", { exact: true }).selectOption(party);
  await page.getByLabel("Amount", { exact: true }).fill("400");
  await page.getByRole("radio", { name: "Against bills" }).click();
  await page.getByRole("button", { name: "Fill oldest first" }).click();
  await expect(page.getByLabel("Settle INV-1")).toHaveValue("400.00");
  await page.getByRole("button", { name: "Post" }).click();

  // The voucher page: its number and the journal it posted.
  await expect(page.getByRole("heading", { name: /Receipt voucher RV-\d{4}-00001/ })).toBeVisible();
  const journal = page.getByRole("table");
  await expect(journal).toContainText("1200");
  await expect(journal).toContainText("1110");
  await expect(journal).toContainText("400.00");

  // The customer's statement: 600 still open on INV-1.
  await page.goto(`/#/parties/${party}`);
  const statement = page.getByRole("region", { name: "Statement" });
  await expect(statement).toContainText("600.00");
  await expect(page.getByRole("table", { name: "Open bills" })).toContainText("INV-1");

  // The trial balance balances.
  await page.goto("/#/accounts/trial-balance");
  const tb = page.getByRole("table", { name: "Trial balance" });
  await expect(tb.getByRole("row").last()).toContainText("Total1,000.001,000.00");
});

test("the chart of accounts opens in Arabic, right to left", async ({ page, request }) => {
  const { email } = await books(request);
  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Books Owner" })).toBeVisible();

  await page.getByRole("button", { name: "Language" }).click();
  await page.goto("/#/accounts");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("link", { name: "المبيعات" })).toBeVisible();
  await expect(page.getByRole("link", { name: "دليل الحسابات" })).toBeVisible();
});
