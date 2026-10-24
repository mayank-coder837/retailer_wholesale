import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import {
  AMOUNT,
  EMIRATES,
  partyName,
  roleLabel,
  useCustomerGroups,
  useParty,
  type CreditCheck,
  type Party,
  type PartyPatch,
} from "@/routes/parties/queries";
import { showMoney, usePriceLists } from "@/routes/pricing/queries";
import { PartyStatement } from "@/routes/accounts/statement";
import { useBusiness } from "@/shell/use-business";
import { cn } from "@/lib/utils";

/** Customers & suppliers → one party (M1.4): details, addresses, contacts, this business's terms, credit. */
export function PartyPage() {
  const { t, i18n } = useTranslation();
  const { partyId } = useParams({ strict: false }) as { partyId: string };
  const party = useParty(partyId);
  const { current } = useBusiness();

  if (party.isPending) return <p>{t("common.loading")}</p>;
  if (party.isError) return <ErrorText error={party.error} />;
  const p = party.data;

  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold leading-9">{partyName(p, i18n.language)}</h1>
        <p className="text-sm text-muted-foreground">
          <span dir="ltr" className="font-mono">
            {p.code}
          </span>{" "}
          · {roleLabel(t, p)} · {t(`parties.kinds.${p.kind}`)}
          {p.trn ? (
            <>
              {" "}
              · {t("parties.trn")}{" "}
              <span dir="ltr" className="font-mono">
                {p.trn}
              </span>
            </>
          ) : null}
          {p.archived_at ? ` · ${t("parties.archived")}` : ""}
        </p>
      </div>
      <Details party={p} />
      <Addresses party={p} />
      <Contacts party={p} />
      {current && p.is_customer && (
        <CustomerTerms
          key={`c-${current.id}`}
          party={p}
          businessId={current.id}
          currency={current.base_currency}
        />
      )}
      {current && p.is_supplier && (
        <SupplierTerms key={`s-${current.id}`} party={p} businessId={current.id} />
      )}
      {current && p.is_customer && (
        <CreditCheckPanel key={`k-${current.id}`} party={p} businessId={current.id} />
      )}
      {current && <PartyStatement key={`l-${current.id}`} businessId={current.id} partyId={p.id} />}
    </div>
  );
}

function ErrorText({ error }: { error: unknown }) {
  const { t } = useTranslation();
  return (
    <p role="alert" className="text-sm text-destructive">
      {t(`parties.errors.${errorCode(error)}`, { defaultValue: t("parties.errors.generic") })}
    </p>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3" aria-label={title}>
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

/** Saving any part of a party returns the whole party: put it straight into the cache. */
function usePartyCache(partyId: string) {
  const queryClient = useQueryClient();
  return async (updated: Party) => {
    queryClient.setQueryData(["party", partyId], updated);
    await queryClient.invalidateQueries({ queryKey: ["parties"] });
  };
}

function Details({ party }: { party: Party }) {
  const { t } = useTranslation();
  const api = useApi();
  const cache = usePartyCache(party.id);
  const initial = {
    en: party.name_en,
    ar: party.name_ar,
    trn: party.trn ?? "",
    phone: party.phone ?? "",
    whatsapp: party.whatsapp ?? "",
    email: party.email ?? "",
    customer: party.is_customer,
    supplier: party.is_supplier,
  };
  const [form, setForm] = useState(initial);
  const [editing, setEditing] = useState(false);
  const set = (patch: Partial<typeof initial>) => setForm((f) => ({ ...f, ...patch }));
  const save = useMutation({
    mutationFn: async (body: PartyPatch) =>
      unwrap(await api.PATCH("/parties/{party_id}", { params: { path: { party_id: party.id } }, body })),
    onSuccess: async (updated) => {
      setEditing(false);
      await cache(updated);
    },
  });
  const trnBad = form.trn !== "" && !/^\d{15}$/.test(form.trn);

  if (!editing) {
    return (
      <Section title={t("parties.details")}>
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">{t("parties.phone")}</dt>
          <dd dir="ltr">{party.phone ?? "—"}</dd>
          <dt className="text-muted-foreground">{t("parties.whatsapp")}</dt>
          <dd dir="ltr">{party.whatsapp ?? "—"}</dd>
          <dt className="text-muted-foreground">{t("parties.email")}</dt>
          <dd dir="ltr">{party.email ?? "—"}</dd>
        </dl>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setForm(initial);
              setEditing(true);
            }}
          >
            {t("parties.edit")}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={save.isPending}
            onClick={() => save.mutate({ archived: !party.archived_at })}
          >
            {party.archived_at ? t("parties.restore") : t("parties.archive")}
          </Button>
        </div>
        {save.isError && <ErrorText error={save.error} />}
      </Section>
    );
  }
  return (
    <Section title={t("parties.details")}>
      <form
        className="grid gap-4 sm:grid-cols-2"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (trnBad || !form.en.trim() || !form.ar.trim() || !(form.customer || form.supplier)) return;
          save.mutate({
            name_en: form.en.trim(),
            name_ar: form.ar.trim(),
            trn: form.trn || null,
            phone: form.phone.trim() || null,
            whatsapp: form.whatsapp.trim() || null,
            email: form.email.trim() || null,
            is_customer: form.customer,
            is_supplier: form.supplier,
          });
        }}
      >
        <Field label={t("parties.nameEn")}>
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
        <Field label={t("parties.nameAr")}>
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
        <Field label={t("parties.trn")} error={trnBad ? t("parties.errors.trn") : null}>
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
              value={form.email}
              onChange={(e) => set({ email: e.target.value })}
            />
          )}
        </Field>
        <div className="flex gap-5 text-sm sm:col-span-2">
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
        <div className="flex gap-2 sm:col-span-2">
          <Button type="submit" disabled={save.isPending}>
            {t("parties.save")}
          </Button>
          <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
            {t("parties.cancel")}
          </Button>
        </div>
      </form>
      {save.isError && <ErrorText error={save.error} />}
    </Section>
  );
}

function Addresses({ party }: { party: Party }) {
  const { t } = useTranslation();
  const api = useApi();
  const cache = usePartyCache(party.id);
  const empty = {
    type: "billing" as "billing" | "shipping",
    line1: "",
    city: "",
    emirate: "",
    isDefault: false,
  };
  const [form, setForm] = useState(empty);
  const path = { params: { path: { party_id: party.id } } };
  const add = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/parties/{party_id}/addresses", {
          ...path,
          body: {
            type: form.type,
            line1: form.line1.trim(),
            city: form.city.trim() || null,
            emirate: (form.emirate || null) as (typeof EMIRATES)[number] | null,
            country: "AE",
            is_default: form.isDefault,
          },
        }),
      ),
    onSuccess: async (updated) => {
      setForm(empty);
      await cache(updated);
    },
  });
  const remove = useMutation({
    mutationFn: async (addressId: string) =>
      unwrap(
        await api.DELETE("/parties/{party_id}/addresses/{address_id}", {
          params: { path: { party_id: party.id, address_id: addressId } },
        }),
      ),
    onSuccess: cache,
  });
  const ready = form.line1.trim() !== "" && form.emirate !== "";

  return (
    <Section title={t("parties.addresses")}>
      {party.addresses.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("parties.noAddresses")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {party.addresses.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span>
                {[a.line1, a.line2, a.city, a.emirate ? t(`emirates.${a.emirate}`) : null, a.country]
                  .filter(Boolean)
                  .join(", ")}
              </span>
              <span className="text-muted-foreground">
                {t(`parties.addressTypes.${a.type}`)}
                {a.is_default ? ` · ${t("parties.default")}` : ""}
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={remove.isPending}
                onClick={() => remove.mutate(a.id)}
              >
                {t("parties.remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="grid items-end gap-3 sm:grid-cols-[auto_2fr_1fr_1fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) add.mutate();
        }}
      >
        <Field label={t("parties.addressType")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.type}
              onChange={(e) => setForm({ ...form, type: e.target.value as typeof form.type })}
            >
              <option value="billing">{t("parties.addressTypes.billing")}</option>
              <option value="shipping">{t("parties.addressTypes.shipping")}</option>
            </select>
          )}
        </Field>
        <Field label={t("parties.line1")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={form.line1}
              onChange={(e) => setForm({ ...form, line1: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.city")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={form.city}
              onChange={(e) => setForm({ ...form, city: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.emirate")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.emirate}
              onChange={(e) => setForm({ ...form, emirate: e.target.value })}
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
        <Button type="submit" disabled={!ready || add.isPending}>
          {t("parties.addAddress")}
        </Button>
        <label className="flex items-center gap-2 text-sm sm:col-span-5">
          <input
            type="checkbox"
            checked={form.isDefault}
            onChange={(e) => setForm({ ...form, isDefault: e.target.checked })}
          />
          {t("parties.makeDefault")}
        </label>
      </form>
      {(add.isError || remove.isError) && <ErrorText error={add.error ?? remove.error} />}
    </Section>
  );
}

function Contacts({ party }: { party: Party }) {
  const { t } = useTranslation();
  const api = useApi();
  const cache = usePartyCache(party.id);
  const empty = { name: "", role: "", phone: "", email: "" };
  const [form, setForm] = useState(empty);
  const add = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/parties/{party_id}/contacts", {
          params: { path: { party_id: party.id } },
          body: {
            name: form.name.trim(),
            role: form.role.trim() || null,
            phone: form.phone.trim() || null,
            email: form.email.trim() || null,
          },
        }),
      ),
    onSuccess: async (updated) => {
      setForm(empty);
      await cache(updated);
    },
  });
  const remove = useMutation({
    mutationFn: async (contactId: string) =>
      unwrap(
        await api.DELETE("/parties/{party_id}/contacts/{contact_id}", {
          params: { path: { party_id: party.id, contact_id: contactId } },
        }),
      ),
    onSuccess: cache,
  });

  return (
    <Section title={t("parties.contacts")}>
      {party.contacts.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("parties.noContacts")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {party.contacts.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span>
                {c.name}
                {c.role ? ` · ${c.role}` : ""}
              </span>
              <span dir="ltr" className="text-muted-foreground">
                {[c.phone, c.email].filter(Boolean).join(" · ")}
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={remove.isPending}
                onClick={() => remove.mutate(c.id)}
              >
                {t("parties.remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_1fr_1fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          if (form.name.trim()) add.mutate();
        }}
      >
        <Field label={t("parties.contactName")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.contactRole")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.phone")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.email")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
          )}
        </Field>
        <Button type="submit" disabled={!form.name.trim() || add.isPending}>
          {t("parties.addContact")}
        </Button>
      </form>
      {(add.isError || remove.isError) && <ErrorText error={add.error ?? remove.error} />}
    </Section>
  );
}

function CustomerTerms({
  party,
  businessId,
  currency,
}: {
  party: Party;
  businessId: string;
  currency: string;
}) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const cache = usePartyCache(party.id);
  const groups = useCustomerGroups(businessId);
  const lists = usePriceLists(businessId);
  const saved = party.customer_profiles.find((c) => c.business_id === businessId);
  const initial = {
    group: saved?.customer_group_id ?? "",
    list: saved?.price_list_id ?? "",
    limit: saved?.credit_limit ? showMoney(saved.credit_limit) : "",
    days: saved?.credit_days?.toString() ?? "",
    policy: (saved?.credit_policy ?? "warn") as "warn" | "block",
  };
  const [form, setForm] = useState(initial);
  const [done, setDone] = useState(false);
  const limitBad = form.limit !== "" && !AMOUNT.test(form.limit);
  const daysBad = form.days !== "" && !/^\d{1,4}$/.test(form.days);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PUT("/parties/{party_id}/customer-profile", {
          params: { path: { party_id: party.id } },
          body: {
            business_id: businessId,
            customer_group_id: form.group || null,
            price_list_id: form.list || null,
            credit_limit: form.limit || null,
            credit_days: form.days === "" ? null : Number(form.days),
            credit_policy: form.policy,
          },
        }),
      ),
    onSuccess: async (updated) => {
      setDone(true);
      await cache(updated);
    },
  });
  const name = (x: { name_en: string; name_ar: string }) => (i18n.language === "ar" ? x.name_ar : x.name_en);

  return (
    <Section title={t("parties.customerTerms")}>
      <form
        className="grid items-start gap-4 sm:grid-cols-2"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          setDone(false);
          if (!limitBad && !daysBad) save.mutate();
        }}
      >
        <Field label={t("parties.group")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.group}
              onChange={(e) => setForm({ ...form, group: e.target.value })}
            >
              <option value="">{t("parties.noGroup")}</option>
              {(groups.data ?? []).map((g) => (
                <option key={g.id} value={g.id}>
                  {name(g)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("parties.priceList")} hint={t("parties.priceListHint")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.list}
              onChange={(e) => setForm({ ...form, list: e.target.value })}
            >
              <option value="">{t("parties.noList")}</option>
              {(lists.data ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {name(l)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field
          label={`${t("parties.creditLimit")} (${currency})`}
          hint={t("parties.creditLimitHint")}
          error={limitBad ? t("parties.errors.amount") : null}
        >
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              inputMode="decimal"
              value={form.limit}
              onChange={(e) => setForm({ ...form, limit: e.target.value.trim() })}
            />
          )}
        </Field>
        <Field
          label={t("parties.creditDays")}
          hint={t("parties.creditDaysHint")}
          error={daysBad ? t("parties.errors.days") : null}
        >
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              inputMode="numeric"
              value={form.days}
              onChange={(e) => setForm({ ...form, days: e.target.value.trim() })}
            />
          )}
        </Field>
        <Field label={t("parties.creditPolicy")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.policy}
              onChange={(e) => setForm({ ...form, policy: e.target.value as "warn" | "block" })}
            >
              <option value="warn">{t("parties.policies.warn")}</option>
              <option value="block">{t("parties.policies.block")}</option>
            </select>
          )}
        </Field>
        <div className="flex items-center gap-3 sm:col-span-2">
          <Button type="submit" disabled={save.isPending}>
            {t("parties.saveTerms")}
          </Button>
          {done && <span className="text-sm text-muted-foreground">{t("parties.saved")}</span>}
        </div>
      </form>
      {save.isError && <ErrorText error={save.error} />}
    </Section>
  );
}

function SupplierTerms({ party, businessId }: { party: Party; businessId: string }) {
  const { t } = useTranslation();
  const api = useApi();
  const cache = usePartyCache(party.id);
  const saved = party.supplier_profiles.find((s) => s.business_id === businessId);
  const [days, setDays] = useState(saved?.payment_terms_days?.toString() ?? "");
  const [done, setDone] = useState(false);
  const bad = days !== "" && !/^\d{1,4}$/.test(days);
  const save = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PUT("/parties/{party_id}/supplier-profile", {
          params: { path: { party_id: party.id } },
          body: { business_id: businessId, payment_terms_days: days === "" ? null : Number(days) },
        }),
      ),
    onSuccess: async (updated) => {
      setDone(true);
      await cache(updated);
    },
  });
  return (
    <Section title={t("parties.supplierTerms")}>
      <form
        className="flex flex-wrap items-end gap-3"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          setDone(false);
          if (!bad) save.mutate();
        }}
      >
        <Field label={t("parties.paymentDays")} error={bad ? t("parties.errors.days") : null}>
          {(p) => (
            <input
              {...p}
              className={cn(inputClass, "w-32")}
              dir="ltr"
              inputMode="numeric"
              value={days}
              onChange={(e) => setDays(e.target.value.trim())}
            />
          )}
        </Field>
        <Button type="submit" disabled={save.isPending}>
          {t("parties.saveTerms")}
        </Button>
        {done && <span className="text-sm text-muted-foreground">{t("parties.saved")}</span>}
      </form>
      {save.isError && <ErrorText error={save.error} />}
    </Section>
  );
}

function CreditCheckPanel({ party, businessId }: { party: Party; businessId: string }) {
  const { t } = useTranslation();
  const api = useApi();
  const [amount, setAmount] = useState("");
  const check = useMutation({
    mutationFn: async (): Promise<CreditCheck> =>
      unwrap(
        await api.POST("/parties/{party_id}/credit-check", {
          params: { path: { party_id: party.id } },
          body: { business_id: businessId, amount },
        }),
      ),
  });
  const r = check.data;
  const tone = r?.decision === "block" ? "text-destructive" : r?.decision === "warn" ? "text-amber-700" : "";

  return (
    <Section title={t("parties.creditCheck")}>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (AMOUNT.test(amount)) check.mutate();
        }}
      >
        <Field label={t("parties.saleAmount")}>
          {(p) => (
            <input
              {...p}
              className={cn(inputClass, "w-40")}
              dir="ltr"
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value.trim())}
            />
          )}
        </Field>
        <Button type="submit" variant="outline" disabled={!AMOUNT.test(amount) || check.isPending}>
          {t("parties.checkCredit")}
        </Button>
      </form>
      {r && (
        <div className="flex flex-col gap-1 rounded-md border p-3 text-sm" data-testid="credit-result">
          <p className={`font-semibold ${tone}`}>
            {t(`parties.decisions.${r.decision}`)}
            {r.reasons.length > 0 && ` — ${r.reasons.map((x) => t(`parties.reasons.${x}`)).join(", ")}`}
          </p>
          <p>
            {t("parties.exposure")}: <span dir="ltr">{showMoney(r.exposure)}</span>
            {r.credit_limit !== null && (
              <>
                {" "}
                · {t("parties.creditLimit")}: <span dir="ltr">{showMoney(r.credit_limit)}</span> ·{" "}
                {t("parties.available")}: <span dir="ltr">{showMoney(r.available)}</span>
              </>
            )}
          </p>
          <p className="text-muted-foreground">
            {t("parties.pdcInHand")}: <span dir="ltr">{showMoney(r.pdc_in_hand)}</span> —{" "}
            {t("parties.pdcNote")}
          </p>
          {r.decision === "block" && (
            <p className="text-muted-foreground">
              {r.may_override ? t("parties.mayOverride") : t("parties.mayNotOverride")}
            </p>
          )}
        </div>
      )}
      {check.isError && <ErrorText error={check.error} />}
    </Section>
  );
}
