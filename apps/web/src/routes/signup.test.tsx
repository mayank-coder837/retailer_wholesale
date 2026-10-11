import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { App } from "@/app";
import { initI18n, setLanguage } from "@/i18n";
import { EMPTY, signupPayload, validateStep } from "@/routes/signup-form";
import { shopApi, TOKEN } from "@/test/shop-api";

const PACKS = [
  {
    code: "textile",
    version: 1,
    name_en: "Textile and garments",
    name_ar: "الأقمشة والملابس",
    description: "Fabric by the roll.",
    description_ar: "أقمشة بالرول.",
    sections: ["settings"],
  },
];

function wizardApi(overrides = {}) {
  return shopApi({
    "GET /industry-packs": { status: 200, body: PACKS },
    "POST /auth/signup": { status: 201, body: TOKEN },
    "POST /businesses/b1/industry-packs": { status: 200, body: { changed: true } },
    ...overrides,
  });
}

const next = () => userEvent.click(screen.getByRole("button", { name: "Next" }));

async function fillAccount(email = "fatima@alnoor.ae") {
  await userEvent.type(await screen.findByLabelText("Your name"), "Fatima Owner");
  await userEvent.type(screen.getByLabelText("Email"), email);
  await userEvent.type(screen.getByLabelText("Password (at least 10 characters)"), "correct horse battery");
  await userEvent.type(screen.getByLabelText("Account name (usually your trade name)"), "Al Noor");
  await next();
}

async function fillToVat() {
  await fillAccount();
  await userEvent.type(await screen.findByLabelText("Legal name in English"), "Al Noor Trading LLC");
  await userEvent.type(screen.getByLabelText("Legal name in Arabic"), "شركة النور للتجارة");
  await next();
  await userEvent.selectOptions(await screen.findByLabelText("Emirate"), "sharjah");
  await next();
  await screen.findByLabelText("Registered for VAT");
}

describe("Wizard rules", () => {
  it("requires the account fields and a 10-character password", () => {
    expect(validateStep(0, EMPTY)).toEqual({
      fullName: "signup.errors.required",
      tenantName: "signup.errors.required",
      email: "signup.errors.email",
      password: "signup.errors.password",
    });
  });

  it("requires a 15-digit TRN only when VAT-registered", () => {
    expect(validateStep(3, { ...EMPTY, vatRegistered: false, trn: "" })).toEqual({});
    expect(validateStep(3, { ...EMPTY, vatRegistered: true, trn: "1234" })).toEqual({
      trn: "signup.errors.trn",
    });
    expect(validateStep(3, { ...EMPTY, vatRegistered: true, trn: "100123456700003" })).toEqual({});
  });

  it("builds the sign-up payload, dropping the TRN when not registered", () => {
    const payload = signupPayload(
      { ...EMPTY, email: " a@b.ae ", trn: "100123456700003", vatRegistered: false, emirate: "dubai" },
      "ar",
    );
    expect(payload.email).toBe("a@b.ae");
    expect(payload.locale).toBe("ar");
    expect(payload.business.trn).toBeNull();
    expect(payload.head_office).toMatchObject({ code: "HO", emirate: "dubai", is_head_office: true });
  });
});

describe("Onboarding wizard", () => {
  beforeAll(async () => {
    await initI18n();
  });

  beforeEach(async () => {
    await setLanguage("en");
    window.location.hash = "#/signup";
  });

  it("walks through five steps, signs up, applies the pack and opens the dashboard", async () => {
    const { api, calls } = wizardApi();
    render(<App api={api} />);

    await fillToVat();
    await userEvent.click(screen.getByLabelText("Registered for VAT"));
    await userEvent.type(screen.getByLabelText("Tax registration number (TRN)"), "100123456700003");
    await next();
    await userEvent.click(await screen.findByRole("radio", { name: /Textile and garments/ }));
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Welcome, Fatima Owner" })).toBeInTheDocument();
    const signup = calls.find((c) => c.path === "/auth/signup")?.body;
    expect(signup).toMatchObject({
      email: "fatima@alnoor.ae",
      tenant_name: "Al Noor",
      client: "web",
      business: { legal_name_ar: "شركة النور للتجارة", vat_registered: true, trn: "100123456700003" },
      head_office: { emirate: "sharjah", is_head_office: true },
    });
    expect(calls.find((c) => c.path === "/businesses/b1/industry-packs")?.body).toEqual({ code: "textile" });
  });

  it("will not move on until the step is valid", async () => {
    const { api } = wizardApi();
    render(<App api={api} />);
    await screen.findByLabelText("Your name");

    await next();

    expect(screen.getAllByText("This is required.")).toHaveLength(2);
    expect(screen.getByText("Use at least 10 characters.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your account" })).toBeInTheDocument();
  });

  it("asks for a valid TRN when VAT-registered", async () => {
    const { api } = wizardApi();
    render(<App api={api} />);
    await fillToVat();

    await userEvent.click(screen.getByLabelText("Registered for VAT"));
    await userEvent.type(screen.getByLabelText("Tax registration number (TRN)"), "12ab3");
    await next();

    expect(screen.getByText("The TRN has 15 digits.")).toBeInTheDocument();
  });

  it("returns to the first step when the email is taken", async () => {
    const { api } = wizardApi({ "POST /auth/signup": { status: 409, body: { code: "email_taken" } } });
    render(<App api={api} />);
    await fillToVat();
    await next();
    await userEvent.click(await screen.findByRole("button", { name: "Create account" }));

    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your account" })).toBeInTheDocument();
  });
});
