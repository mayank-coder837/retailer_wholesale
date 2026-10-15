/** Onboarding wizard data, per-step validation and the sign-up payload (kept apart from the component). */

export const EMIRATES = [
  "abu_dhabi",
  "dubai",
  "sharjah",
  "ajman",
  "umm_al_quwain",
  "ras_al_khaimah",
  "fujairah",
] as const;
export type Emirate = (typeof EMIRATES)[number];
export const CURRENCIES = ["AED", "SAR", "OMR", "QAR", "KWD", "BHD", "USD", "EUR", "GBP", "INR", "PKR"];
export const STEPS = ["account", "business", "office", "vat", "industry"] as const;

export interface WizardData {
  fullName: string;
  email: string;
  password: string;
  tenantName: string;
  legalNameEn: string;
  legalNameAr: string;
  tradeLicense: string;
  branchNameEn: string;
  branchNameAr: string;
  emirate: Emirate | "";
  vatRegistered: boolean;
  trn: string;
  currency: string;
  fiscalStart: number;
  industry: string;
}

export const EMPTY: WizardData = {
  fullName: "",
  email: "",
  password: "",
  tenantName: "",
  legalNameEn: "",
  legalNameAr: "",
  tradeLicense: "",
  branchNameEn: "Head office",
  branchNameAr: "المكتب الرئيسي",
  emirate: "",
  vatRegistered: false,
  trn: "",
  currency: "AED",
  fiscalStart: 1,
  industry: "",
};

export type Errors = Partial<Record<keyof WizardData, string>>;

/** Returns i18n keys of the errors on one step (empty = the step is valid). */
export function validateStep(step: number, d: WizardData): Errors {
  const errors: Errors = {};
  const required = (key: keyof WizardData) => {
    if (!String(d[key]).trim()) errors[key] = "signup.errors.required";
  };
  if (step === 0) {
    required("fullName");
    required("tenantName");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(d.email)) errors.email = "signup.errors.email";
    if (d.password.length < 10) errors.password = "signup.errors.password";
  } else if (step === 1) {
    required("legalNameEn");
    required("legalNameAr");
  } else if (step === 2) {
    required("branchNameEn");
    required("branchNameAr");
    required("emirate");
  } else if (step === 3 && d.vatRegistered && !/^\d{15}$/.test(d.trn)) {
    errors.trn = "signup.errors.trn";
  }
  return errors;
}

export function signupPayload(d: WizardData, locale: string) {
  return {
    email: d.email.trim(),
    password: d.password,
    full_name: d.fullName.trim(),
    locale: locale === "ar" ? "ar" : "en",
    tenant_name: d.tenantName.trim(),
    business: {
      legal_name_en: d.legalNameEn.trim(),
      legal_name_ar: d.legalNameAr.trim(),
      trade_license_no: d.tradeLicense.trim() || null,
      trn: d.vatRegistered ? d.trn : null,
      vat_registered: d.vatRegistered,
      base_currency: d.currency,
      fiscal_year_start_month: d.fiscalStart,
      time_zone: "Asia/Dubai",
    },
    head_office: {
      code: "HO",
      name_en: d.branchNameEn.trim(),
      name_ar: d.branchNameAr.trim(),
      emirate: d.emirate as Emirate,
      is_head_office: true,
    },
  };
}
