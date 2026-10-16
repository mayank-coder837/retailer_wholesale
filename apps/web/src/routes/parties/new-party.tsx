import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { EMPTY_PARTY, partyFormErrors, type PartyForm } from "@/routes/parties/party-form";
import { EMIRATES, type AddressIn, type Emirate, type PartyIn } from "@/routes/parties/queries";

/** Customers & suppliers → new (M1.4). The code is numbered by the server (C0001, S0001, P0001). */
export function NewPartyPage() {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const [form, setForm] = useState(EMPTY_PARTY);
  const [tried, setTried] = useState(false);
  const errors = partyFormErrors(form);
  const set = (patch: Partial<PartyForm>) => setForm((f) => ({ ...f, ...patch }));
  const shown = (key: keyof PartyForm) => (tried && errors[key] ? t(errors[key]) : null);

  const save = useMutation({
    mutationFn: async () => {
      const addresses: AddressIn[] = form.line1.trim()
        ? [
            {
              type: "billing",
              line1: form.line1.trim(),
              city: form.city.trim() || null,
              emirate: form.emirate || null,
              country: "AE",
              is_default: true,
            },
          ]
        : [];
      const body: PartyIn = {
        name_en: form.en.trim(),
        name_ar: form.ar.trim(),
        kind: form.kind,
        is_customer: form.customer,
        is_supplier: form.supplier,
        trn: form.trn || null,
        phone: form.phone.trim() || null,
        whatsapp: form.whatsapp.trim() || null,
        email: form.email.trim() || null,
        default_currency: "AED",
        addresses,
      };
      return unwrap(await api.POST("/parties", { body }));
    },
    onSuccess: (party) => navigate({ to: "/parties/$partyId", params: { partyId: party.id } }),
  });

  return (
    <form
      className="flex max-w-3xl flex-col gap-5"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (Object.keys(errors).length === 0) save.mutate();
      }}
    >
      <h1 className="text-2xl font-bold">{t("parties.new")}</h1>

      <fieldset className="flex flex-col gap-2 text-sm">
        <legend className="mb-1 font-medium">{t("parties.roles")}</legend>
        <div className="flex gap-5">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.customer}
              onChange={(e) => set({ customer: e.target.checked })}
            />
            {t("parties.customer")}
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={form.supplier}
              onChange={(e) => set({ supplier: e.target.checked })}
            />
            {t("parties.supplier")}
          </label>
        </div>
        {shown("customer") && <p className="text-destructive">{shown("customer")}</p>}
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("parties.nameEn")} error={shown("en")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              value={form.en}
              onChange={(e) => set({ en: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.nameAr")} error={shown("ar")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="rtl"
              value={form.ar}
              onChange={(e) => set({ ar: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.kind")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.kind}
              onChange={(e) => set({ kind: e.target.value as typeof form.kind })}
            >
              <option value="company">{t("parties.kinds.company")}</option>
              <option value="individual">{t("parties.kinds.individual")}</option>
            </select>
          )}
        </Field>
        <Field label={t("parties.trn")} hint={t("parties.trnHint")} error={shown("trn")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              inputMode="numeric"
              maxLength={15}
              value={form.trn}
              onChange={(e) => set({ trn: e.target.value.replace(/\D/g, "") })}
            />
          )}
        </Field>
        <Field label={t("parties.phone")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              type="tel"
              value={form.phone}
              onChange={(e) => set({ phone: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.whatsapp")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              type="tel"
              value={form.whatsapp}
              onChange={(e) => set({ whatsapp: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.email")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              type="email"
              value={form.email}
              onChange={(e) => set({ email: e.target.value })}
            />
          )}
        </Field>
      </div>

      <fieldset className="grid gap-4 sm:grid-cols-3">
        <legend className="mb-2 text-sm font-medium">
          {t("parties.billingAddress")} ({t("common.optional")})
        </legend>
        <Field label={t("parties.line1")} className="sm:col-span-3">
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={form.line1}
              onChange={(e) => set({ line1: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.city")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={form.city}
              onChange={(e) => set({ city: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.emirate")} error={shown("emirate")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.emirate}
              onChange={(e) => set({ emirate: e.target.value as Emirate | "" })}
            >
              <option value="">—</option>
              {EMIRATES.map((em) => (
                <option key={em} value={em}>
                  {t(`emirates.${em}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
      </fieldset>

      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t(`parties.errors.${errorCode(save.error)}`, { defaultValue: t("parties.errors.generic") })}
        </p>
      )}
      <div>
        <Button type="submit" disabled={save.isPending}>
          {t("parties.create")}
        </Button>
      </div>
    </form>
  );
}
