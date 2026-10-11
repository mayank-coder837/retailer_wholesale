import { expect, test, type Page } from "@playwright/test";

// M0.5 acceptance, against the real API and database: a new user completes onboarding and lands on the
// dashboard, in English and in Arabic. Needs the API at VITE_API_URL (default http://localhost:8000).

const unique = () => `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;

interface Labels {
  next: string;
  fullName: string;
  email: string;
  password: string;
  tenantName: string;
  legalEn: string;
  legalAr: string;
  emirate: string;
  vat: string;
  trn: string;
  submit: string;
  welcome: (name: string) => string;
  logout: string;
  login: string;
}

const EN: Labels = {
  next: "Next",
  fullName: "Your name",
  email: "Email",
  password: "Password (at least 10 characters)",
  tenantName: "Account name (usually your trade name)",
  legalEn: "Legal name in English",
  legalAr: "Legal name in Arabic",
  emirate: "Emirate",
  vat: "Registered for VAT",
  trn: "Tax registration number (TRN)",
  submit: "Create account",
  welcome: (name) => `Welcome, ${name}`,
  logout: "Log out",
  login: "Log in",
};

const AR: Labels = {
  next: "التالي",
  fullName: "اسمك",
  email: "البريد الإلكتروني",
  password: "كلمة المرور (10 أحرف على الأقل)",
  tenantName: "اسم الحساب (عادةً الاسم التجاري)",
  legalEn: "الاسم القانوني بالإنجليزية",
  legalAr: "الاسم القانوني بالعربية",
  emirate: "الإمارة",
  vat: "مسجّل في ضريبة القيمة المضافة",
  trn: "رقم التسجيل الضريبي (TRN)",
  submit: "إنشاء الحساب",
  welcome: (name) => `مرحبًا، ${name}`,
  logout: "تسجيل الخروج",
  login: "تسجيل الدخول",
};

async function completeWizard(page: Page, l: Labels, name: string, industry: RegExp) {
  await page.getByLabel(l.fullName).fill(name);
  await page.getByLabel(l.email, { exact: true }).fill(`owner-${unique()}@e2e.ae`);
  await page.getByLabel(l.password).fill("correct horse battery");
  await page.getByLabel(l.tenantName).fill("Al Noor");
  await page.getByRole("button", { name: l.next }).click();

  await page.getByLabel(l.legalEn).fill("Al Noor Trading LLC");
  await page.getByLabel(l.legalAr).fill("شركة النور للتجارة");
  await page.getByRole("button", { name: l.next }).click();

  await page.getByLabel(l.emirate).selectOption("sharjah");
  await page.getByRole("button", { name: l.next }).click();

  await page.getByLabel(l.vat).check();
  await page.getByLabel(l.trn).fill("100123456700003");
  await page.getByRole("button", { name: l.next }).click();

  await page.getByRole("radio", { name: industry }).check();
  await page.getByRole("button", { name: l.submit }).click();
}

test("a new trader onboards in English and stays signed in after a reload", async ({ page }) => {
  await page.goto("/#/signup");

  await completeWizard(page, EN, "Fatima Owner", /Textile and garments/);

  await expect(page.getByRole("heading", { name: EN.welcome("Fatima Owner") })).toBeVisible();
  await expect(page.getByTestId(/^kpi-/)).toHaveCount(6);
  await expect(page.getByLabel("Business")).toHaveCount(1);

  await page.reload(); // the HttpOnly refresh cookie restores the session
  await expect(page.getByRole("heading", { name: EN.welcome("Fatima Owner") })).toBeVisible();

  await page.getByRole("button", { name: EN.logout }).click();
  await expect(page.getByRole("heading", { name: EN.login })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: EN.login })).toBeVisible();
});

test("a new trader onboards in Arabic, right to left", async ({ page }) => {
  await page.goto("/#/signup");
  await page.getByRole("button", { name: "Language" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");

  await completeWizard(page, AR, "سالم", /البقالة والسلع الاستهلاكية/);

  await expect(page.getByRole("heading", { name: AR.welcome("سالم") })).toBeVisible();
  await expect(page.getByRole("link", { name: "لوحة التحكم" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
});
