import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import product from "@smartbiz/config";

import { useApi } from "@/api/use-api";
import { useSession } from "@/auth/use-session";
import { Field, inputClass } from "@/components/field";
import { LanguageSwitch } from "@/components/language-switch";
import { Button } from "@/components/ui/button";

import {
  CURRENCIES,
  EMIRATES,
  EMPTY,
  STEPS,
  signupPayload,
  validateStep,
  type Emirate,
  type Errors,
  type WizardData,
} from "@/routes/signup-form";

export function SignupPage() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const session = useSession();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [data, setData] = useState<WizardData>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const packs = useQuery({
    queryKey: ["industry-packs"],
    queryFn: async () => (await api.GET("/industry-packs")).data ?? [],
  });

  const set = <K extends keyof WizardData>(key: K, value: WizardData[K]) =>
    setData((current) => ({ ...current, [key]: value }));
  const err = (key: keyof WizardData) => (errors[key] ? t(errors[key]) : null);
  const months = Array.from({ length: 12 }, (_, i) =>
    new Intl.DateTimeFormat(i18n.language, { month: "long" }).format(new Date(2026, i, 1)),
  );

  async function submit() {
    setBusy(true);
    setFormError(null);
    const result = await session.signup(signupPayload(data, i18n.language));
    if (!result.ok) {
      setBusy(false);
      if (result.code === "email_taken") {
        setStep(0);
        setErrors({ email: "signup.errors.email_taken" });
      } else {
        setFormError(t("signup.errors.unknown"));
      }
      return;
    }
    if (data.industry) {
      const businesses = (await api.GET("/businesses")).data ?? [];
      const first = businesses[0];
      if (first) {
        await api.POST("/businesses/{business_id}/industry-packs", {
          params: { path: { business_id: first.id } },
          body: { code: data.industry },
        });
      }
    }
    await navigate({ to: "/dashboard" });
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const stepErrors = validateStep(step, data);
    setErrors(stepErrors);
    if (Object.keys(stepErrors).length) return;
    if (step < STEPS.length - 1) setStep(step + 1);
    else void submit();
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-lg flex-col justify-center gap-6 px-4 py-10">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold">{t("signup.title", { product: product.name })}</h1>
        <LanguageSwitch />
      </div>
      <ol className="flex gap-1" aria-label={t("signup.stepOf", { step: step + 1, total: STEPS.length })}>
        {STEPS.map((s, i) => (
          <li
            key={s}
            aria-current={i === step ? "step" : undefined}
            className={`h-1.5 flex-1 rounded-full ${i <= step ? "bg-primary" : "bg-muted"}`}
          />
        ))}
      </ol>
      <form className="flex flex-col gap-4" onSubmit={onSubmit} noValidate>
        <div>
          <p className="text-sm text-muted-foreground">
            {t("signup.stepOf", { step: step + 1, total: STEPS.length })}
          </p>
          <h2 className="text-xl font-semibold">{t(`signup.steps.${STEPS[step]}`)}</h2>
        </div>

        {step === 0 && (
          <>
            <Field label={t("signup.fields.fullName")} error={err("fullName")}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  autoComplete="name"
                  value={data.fullName}
                  onChange={(e) => set("fullName", e.target.value)}
                />
              )}
            </Field>
            <Field label={t("signup.fields.email")} error={err("email")}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  type="email"
                  autoComplete="username"
                  value={data.email}
                  onChange={(e) => set("email", e.target.value)}
                />
              )}
            </Field>
            <Field label={t("signup.fields.password")} error={err("password")}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  type="password"
                  autoComplete="new-password"
                  value={data.password}
                  onChange={(e) => set("password", e.target.value)}
                />
              )}
            </Field>
            <Field label={t("signup.fields.tenantName")} error={err("tenantName")}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  autoComplete="organization"
                  value={data.tenantName}
                  onChange={(e) => set("tenantName", e.target.value)}
                />
              )}
            </Field>
          </>
        )}

        {step === 1 && (
          <>
            <Field label={t("signup.fields.legalNameEn")} error={err("legalNameEn")}>
              {(p) => (
                <input
                  {...p}
                  dir="ltr"
                  className={inputClass}
                  value={data.legalNameEn}
                  onChange={(e) => set("legalNameEn", e.target.value)}
                />
              )}
            </Field>
            <Field label={t("signup.fields.legalNameAr")} error={err("legalNameAr")}>
              {(p) => (
                <input
                  {...p}
                  dir="rtl"
                  lang="ar"
                  className={inputClass}
                  value={data.legalNameAr}
                  onChange={(e) => set("legalNameAr", e.target.value)}
                />
              )}
            </Field>
            <Field label={`${t("signup.fields.tradeLicense")} (${t("common.optional")})`}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  value={data.tradeLicense}
                  onChange={(e) => set("tradeLicense", e.target.value)}
                />
              )}
            </Field>
          </>
        )}

        {step === 2 && (
          <>
            <Field label={t("signup.fields.branchNameEn")} error={err("branchNameEn")}>
              {(p) => (
                <input
                  {...p}
                  dir="ltr"
                  className={inputClass}
                  value={data.branchNameEn}
                  onChange={(e) => set("branchNameEn", e.target.value)}
                />
              )}
            </Field>
            <Field label={t("signup.fields.branchNameAr")} error={err("branchNameAr")}>
              {(p) => (
                <input
                  {...p}
                  dir="rtl"
                  lang="ar"
                  className={inputClass}
                  value={data.branchNameAr}
                  onChange={(e) => set("branchNameAr", e.target.value)}
                />
              )}
            </Field>
            <Field
              label={t("signup.fields.emirate")}
              hint={t("signup.fields.emirateHint")}
              error={err("emirate")}
            >
              {(p) => (
                <select
                  {...p}
                  className={inputClass}
                  value={data.emirate}
                  onChange={(e) => set("emirate", e.target.value as Emirate)}
                >
                  <option value="" disabled>
                    —
                  </option>
                  {EMIRATES.map((e) => (
                    <option key={e} value={e}>
                      {t(`emirates.${e}`)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          </>
        )}

        {step === 3 && (
          <>
            <label className="flex items-center gap-2 text-sm font-medium">
              <input
                type="checkbox"
                className="size-4"
                checked={data.vatRegistered}
                onChange={(e) => set("vatRegistered", e.target.checked)}
              />
              {t("signup.fields.vatRegistered")}
            </label>
            {data.vatRegistered && (
              <Field label={t("signup.fields.trn")} error={err("trn")}>
                {(p) => (
                  <input
                    {...p}
                    dir="ltr"
                    inputMode="numeric"
                    maxLength={15}
                    className={inputClass}
                    value={data.trn}
                    onChange={(e) => set("trn", e.target.value.replace(/\D/g, ""))}
                  />
                )}
              </Field>
            )}
            <Field label={t("signup.fields.currency")}>
              {(p) => (
                <select
                  {...p}
                  className={inputClass}
                  value={data.currency}
                  onChange={(e) => set("currency", e.target.value)}
                >
                  {CURRENCIES.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label={t("signup.fields.fiscalStart")}>
              {(p) => (
                <select
                  {...p}
                  className={inputClass}
                  value={data.fiscalStart}
                  onChange={(e) => set("fiscalStart", Number(e.target.value))}
                >
                  {months.map((m, i) => (
                    <option key={m} value={i + 1}>
                      {m}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          </>
        )}

        {step === 4 && (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">{t("signup.fields.industry")}</legend>
            <p className="mb-2 text-sm text-muted-foreground">{t("signup.fields.industryHint")}</p>
            {[...(packs.data ?? []), null].map((pack) => (
              <label
                key={pack?.code ?? "none"}
                className="flex items-start gap-3 rounded-md border p-3 text-sm has-[:checked]:border-primary"
              >
                <input
                  type="radio"
                  name="industry"
                  className="mt-0.5"
                  checked={data.industry === (pack?.code ?? "")}
                  onChange={() => set("industry", pack?.code ?? "")}
                />
                <span>
                  <span className="block font-medium">
                    {pack
                      ? i18n.language === "ar"
                        ? pack.name_ar
                        : pack.name_en
                      : t("signup.fields.noIndustry")}
                  </span>
                  {pack && (
                    <span className="text-muted-foreground">
                      {i18n.language === "ar" ? pack.description_ar : pack.description}
                    </span>
                  )}
                </span>
              </label>
            ))}
          </fieldset>
        )}

        {formError && (
          <p role="alert" className="text-sm text-destructive">
            {formError}
          </p>
        )}
        <div className="flex justify-between gap-2">
          {step > 0 ? (
            <Button type="button" variant="outline" onClick={() => setStep(step - 1)} disabled={busy}>
              {t("common.back")}
            </Button>
          ) : (
            <span />
          )}
          <Button type="submit" disabled={busy}>
            {busy ? t("signup.creating") : step === STEPS.length - 1 ? t("signup.submit") : t("common.next")}
          </Button>
        </div>
      </form>
      <p className="text-sm text-muted-foreground">
        {t("signup.haveAccount")}{" "}
        <Link to="/login" className="underline">
          {t("login.title")}
        </Link>
      </p>
    </main>
  );
}
