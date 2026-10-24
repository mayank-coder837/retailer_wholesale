import { expect, test, type APIRequestContext } from "@playwright/test";

// M1.9 acceptance, against the real API and database: a customer's post-dated cheque is recorded against an
// invoice, stays out of the books (statement and ageing show it beside the balance), then clears into the bank,
// posting the receipt that settles the invoice. Needs the API at VITE_API_URL (default http://localhost:8000).

const API = process.env.VITE_API_URL ?? "http://localhost:8000";
const PASSWORD = "correct horse battery";

async function shopWithInvoice(request: APIRequestContext) {
  const email = `cheques-${Date.now()}-${Math.floor(Math.random() * 1e6)}@e2e.ae`;
  const signup = await request.post(`${API}/auth/signup`, {
    data: {
      email,
      password: PASSWORD,
      full_name: "Cheque Owner",
      tenant_name: "Cheque Trading",
      business: { legal_name_en: "Cheque Trading LLC", legal_name_ar: "شركة الشيكات" },
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
  const customer = (
    await (
      await request.post(`${API}/parties`, {
        headers,
        data: { name_en: "Gulf Fabrics LLC", name_ar: "أقمشة الخليج", is_customer: true },
      })
    ).json()
  ).id as string;
  // An opening invoice of 1,000 for the customer (a journal, as in the accounting acceptance test).
  const invoice = await request.post(`${API}/vouchers?post=true`, {
    headers,
    data: {
      doc_type: "journal",
      business_id: business,
      branch_id: branch,
      doc_date: new Date().toISOString().slice(0, 10),
      lines: [
        {
          account_id: account("1200"),
          party_id: customer,
          debit: "1000",
          allocations: [{ type: "new_ref", amount: "1000", reference: "INV-OPEN-1" }],
        },
        { account_id: account("4100"), credit: "1000" },
      ],
    },
  });
  expect(invoice.ok()).toBeTruthy();
  return { email, customer };
}

test("a cheque is a memo until it clears; clearing posts the receipt and settles the invoice", async ({
  page,
  request,
}) => {
  const { email, customer } = await shopWithInvoice(request);

  await page.goto("/#/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("heading", { name: "Welcome, Cheque Owner" })).toBeVisible();

  await page.getByRole("link", { name: "Accounts", exact: true }).click();
  await page.getByRole("link", { name: "Cheques (PDC)" }).click();
  await page.getByRole("link", { name: "Record a cheque received" }).click();
  await page.getByLabel("Customer", { exact: true }).selectOption(customer);
  await page.getByLabel("Cheque no.").fill("000777");
  await page.getByLabel("Bank", { exact: true }).fill("Emirates NBD");
  await page.getByLabel("Amount", { exact: true }).fill("1000");
  await page.getByLabel("Amount for INV-OPEN-1").fill("1000");
  await page.getByRole("button", { name: "Save cheque" }).click();
  await expect(page.getByRole("heading", { name: "Cheques received 000777" })).toBeVisible();
  await expect(page.getByText("In hand", { exact: true })).toBeVisible();

  // Not in the books: the customer still owes 1,000, with the cheque beside it.
  await page.getByRole("link", { name: "Ageing" }).click();
  await expect(page.getByRole("table", { name: "Ageing" })).toContainText(
    "Gulf Fabrics LLC1,000.001,000.001,000.00",
  );

  await page.getByRole("link", { name: "Cheques (PDC)" }).click();
  await page.getByRole("link", { name: "000777" }).click();
  await page.getByRole("button", { name: "Cleared by the bank" }).click();
  await page.getByLabel("Bank account").selectOption({ label: "1150 Bank" });
  await page.getByRole("button", { name: "Post and mark cleared" }).click();
  await expect(page.getByText("Cleared", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: /RV-\d{4}-00001/ }).click();
  await expect(page.getByRole("heading", { name: /Receipt voucher RV-\d{4}-00001/ })).toBeVisible();

  await page.getByRole("link", { name: "Ageing" }).click();
  await expect(page.getByText("Nothing is open.")).toBeVisible();
});
