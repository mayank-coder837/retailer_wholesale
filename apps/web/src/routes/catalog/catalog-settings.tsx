import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { localName } from "@/routes/catalog/product-form";
import {
  errorCode,
  unwrap,
  useAttributes,
  useBrands,
  useCategories,
  useCustomFields,
  useUnits,
} from "@/routes/catalog/queries";

const TABS = ["units", "categories", "brands", "attributes", "fields"] as const;
type Tab = (typeof TABS)[number];

/** Settings → Catalog (M1.2): units, categories, brands, attributes and custom fields. */
export function CatalogSettingsPage() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("units");
  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("catalog.setup")}</h1>
      <div role="tablist" className="flex flex-wrap gap-1 border-b pb-2 text-sm">
        {TABS.map((name) => (
          <button
            key={name}
            role="tab"
            type="button"
            aria-selected={tab === name}
            className={cn(
              "rounded-md px-3 py-1.5 hover:bg-muted",
              tab === name && "bg-secondary font-semibold",
            )}
            onClick={() => setTab(name)}
          >
            {t(`catalog.tabs.${name}`)}
          </button>
        ))}
      </div>
      {tab === "units" && <UnitsTab />}
      {tab === "categories" && <CategoriesTab />}
      {tab === "brands" && <BrandsTab />}
      {tab === "attributes" && <AttributesTab />}
      {tab === "fields" && <FieldsTab />}
    </div>
  );
}

/** A small "add" form: names in English and Arabic plus extra fields; shows the server's error code. */
function AddForm({
  title,
  onSubmit,
  pending,
  error,
  children,
}: {
  title: string;
  onSubmit: () => void;
  pending: boolean;
  error: unknown;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <form
      aria-label={title}
      className="flex flex-col gap-3 rounded-lg border p-4"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <h2 className="font-semibold">{title}</h2>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {t(`catalog.errors.${errorCode(error)}`, { defaultValue: t("catalog.errors.generic") })}
        </p>
      ) : null}
      <Button type="submit" className="self-start" disabled={pending}>
        {t("catalog.add")}
      </Button>
    </form>
  );
}

function NameFields({
  en,
  ar,
  onEn,
  onAr,
}: {
  en: string;
  ar: string;
  onEn: (v: string) => void;
  onAr: (v: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <Field label={t("catalog.nameEn")}>
        {(p) => (
          <input {...p} className={inputClass} required value={en} onChange={(e) => onEn(e.target.value)} />
        )}
      </Field>
      <Field label={t("catalog.nameAr")}>
        {(p) => (
          <input
            {...p}
            className={inputClass}
            dir="rtl"
            required
            value={ar}
            onChange={(e) => onAr(e.target.value)}
          />
        )}
      </Field>
    </>
  );
}

function useAdd<T>(key: string, send: () => Promise<T>, reset: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: send,
    onSuccess: async () => {
      reset();
      await queryClient.invalidateQueries({ queryKey: [key] });
    },
  });
}

function UnitsTab() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const units = useUnits();
  const empty = { family: "count", code: "", en: "", ar: "", symbol: "", decimals: "0", factor: "1" };
  const [form, setForm] = useState(empty);
  const add = useAdd(
    "units",
    async () =>
      unwrap(
        await api.POST("/units", {
          body: {
            family: form.family,
            code: form.code.trim(),
            name_en: form.en,
            name_ar: form.ar,
            symbol: form.symbol || form.code,
            decimals: Number.parseInt(form.decimals, 10),
            factor_to_family_base: form.factor,
          },
        }),
      ),
    () => setForm(empty),
  );
  const families = [...new Set((units.data ?? []).map((u) => u.family))];
  return (
    <div className="flex flex-col gap-4">
      <table className="w-full text-sm">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 text-start font-medium">{t("catalog.unit")}</th>
            <th className="py-2 text-start font-medium">{t("catalog.family")}</th>
            <th className="py-2 text-right font-medium">{t("catalog.factorToFamilyBase")}</th>
            <th className="py-2 text-right font-medium">{t("catalog.decimals")}</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {(units.data ?? []).map((u) => (
            <tr key={u.id}>
              <td className="py-1.5">
                {localName(u, i18n.language)} <span className="text-muted-foreground">({u.code})</span>
              </td>
              <td className="py-1.5">{t(`catalog.families.${u.family}`, { defaultValue: u.family })}</td>
              <td className="py-1.5 text-right tabular-nums">
                <span dir="ltr">{u.factor_to_family_base}</span>
              </td>
              <td className="py-1.5 text-right tabular-nums">{u.decimals}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <AddForm
        title={t("catalog.addUnitTitle")}
        onSubmit={() => add.mutate()}
        pending={add.isPending}
        error={add.error}
      >
        <Field label={t("catalog.family")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.family}
              onChange={(e) => setForm({ ...form, family: e.target.value })}
            >
              {families.map((f) => (
                <option key={f} value={f}>
                  {t(`catalog.families.${f}`, { defaultValue: f })}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("catalog.code")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              required
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
            />
          )}
        </Field>
        <NameFields
          en={form.en}
          ar={form.ar}
          onEn={(en) => setForm({ ...form, en })}
          onAr={(ar) => setForm({ ...form, ar })}
        />
        <Field label={t("catalog.decimals")} hint={t("catalog.decimalsHint")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              type="number"
              min={0}
              max={6}
              value={form.decimals}
              onChange={(e) => setForm({ ...form, decimals: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("catalog.factorToFamilyBase")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              inputMode="decimal"
              value={form.factor}
              onChange={(e) => setForm({ ...form, factor: e.target.value })}
            />
          )}
        </Field>
      </AddForm>
    </div>
  );
}

function CategoriesTab() {
  const { t } = useTranslation();
  const api = useApi();
  const categories = useCategories();
  const [form, setForm] = useState({ en: "", ar: "", parent: "" });
  const add = useAdd(
    "categories",
    async () =>
      unwrap(
        await api.POST("/categories", {
          body: { name_en: form.en, name_ar: form.ar, parent_id: form.parent || null },
        }),
      ),
    () => setForm({ en: "", ar: "", parent: "" }),
  );
  return (
    <div className="flex flex-col gap-4">
      {(categories.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("catalog.noneYet")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {(categories.data ?? []).map((c) => (
            <li key={c.id} className="px-3 py-2">
              {c.path_en} <span className="text-muted-foreground">· {c.name_ar}</span>
            </li>
          ))}
        </ul>
      )}
      <AddForm
        title={t("catalog.addCategory")}
        onSubmit={() => add.mutate()}
        pending={add.isPending}
        error={add.error}
      >
        <NameFields
          en={form.en}
          ar={form.ar}
          onEn={(en) => setForm({ ...form, en })}
          onAr={(ar) => setForm({ ...form, ar })}
        />
        <Field label={t("catalog.parent")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.parent}
              onChange={(e) => setForm({ ...form, parent: e.target.value })}
            >
              <option value="">{t("catalog.topLevel")}</option>
              {(categories.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.path_en}
                </option>
              ))}
            </select>
          )}
        </Field>
      </AddForm>
    </div>
  );
}

function BrandsTab() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const brands = useBrands();
  const [form, setForm] = useState({ en: "", ar: "" });
  const add = useAdd(
    "brands",
    async () => unwrap(await api.POST("/brands", { body: { name_en: form.en, name_ar: form.ar } })),
    () => setForm({ en: "", ar: "" }),
  );
  return (
    <div className="flex flex-col gap-4">
      {(brands.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("catalog.noneYet")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {(brands.data ?? []).map((b) => (
            <li key={b.id} className="px-3 py-2">
              {localName(b, i18n.language)}
            </li>
          ))}
        </ul>
      )}
      <AddForm
        title={t("catalog.addBrand")}
        onSubmit={() => add.mutate()}
        pending={add.isPending}
        error={add.error}
      >
        <NameFields
          en={form.en}
          ar={form.ar}
          onEn={(en) => setForm({ ...form, en })}
          onAr={(ar) => setForm({ ...form, ar })}
        />
      </AddForm>
    </div>
  );
}

function AttributesTab() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const attributes = useAttributes();
  const [form, setForm] = useState({ code: "", en: "", ar: "" });
  const [value, setValue] = useState({ attribute: "", en: "", ar: "" });
  const add = useAdd(
    "attributes",
    async () =>
      unwrap(
        await api.POST("/attributes", {
          body: { code: form.code.trim(), name_en: form.en, name_ar: form.ar, values: [] },
        }),
      ),
    () => setForm({ code: "", en: "", ar: "" }),
  );
  const addValue = useAdd(
    "attributes",
    async () =>
      unwrap(
        await api.POST("/attributes/{attribute_id}/values", {
          params: { path: { attribute_id: value.attribute } },
          body: { value_en: value.en, value_ar: value.ar },
        }),
      ),
    () => setValue({ ...value, en: "", ar: "" }),
  );
  return (
    <div className="flex flex-col gap-4">
      <ul className="divide-y rounded-md border text-sm">
        {(attributes.data ?? []).map((a) => (
          <li key={a.id} className="px-3 py-2">
            <span className="font-medium">{localName(a, i18n.language)}</span>{" "}
            <span className="text-muted-foreground">
              {a.values.map((v) => (i18n.language === "ar" ? v.value_ar : v.value_en)).join(", ") ||
                t("catalog.noValues")}
            </span>
          </li>
        ))}
      </ul>
      <AddForm
        title={t("catalog.addValue")}
        onSubmit={() => addValue.mutate()}
        pending={addValue.isPending}
        error={addValue.error}
      >
        <Field label={t("catalog.attribute")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              required
              value={value.attribute}
              onChange={(e) => setValue({ ...value, attribute: e.target.value })}
            >
              <option value="">—</option>
              {(attributes.data ?? []).map((a) => (
                <option key={a.id} value={a.id}>
                  {localName(a, i18n.language)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <span />
        <NameFields
          en={value.en}
          ar={value.ar}
          onEn={(en) => setValue({ ...value, en })}
          onAr={(ar) => setValue({ ...value, ar })}
        />
      </AddForm>
      <AddForm
        title={t("catalog.addAttribute")}
        onSubmit={() => add.mutate()}
        pending={add.isPending}
        error={add.error}
      >
        <Field label={t("catalog.code")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              required
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
            />
          )}
        </Field>
        <span />
        <NameFields
          en={form.en}
          ar={form.ar}
          onEn={(en) => setForm({ ...form, en })}
          onAr={(ar) => setForm({ ...form, ar })}
        />
      </AddForm>
    </div>
  );
}

const FIELD_TYPES = ["text", "number", "decimal", "date", "bool", "select", "multiselect"] as const;

function FieldsTab() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const fields = useCustomFields();
  const empty = { entity: "product", key: "", en: "", ar: "", type: "text", options: "", required: false };
  const [form, setForm] = useState(empty);
  const add = useAdd(
    "custom-fields",
    async () =>
      unwrap(
        await api.POST("/custom-fields", {
          body: {
            entity_type: form.entity as "product" | "variant",
            key: form.key.trim(),
            label_en: form.en,
            label_ar: form.ar,
            data_type: form.type as (typeof FIELD_TYPES)[number],
            required: form.required,
            // "Cotton | قطن, Silk | حرير" → options; the English label is the stored value
            options: form.options.trim()
              ? form.options.split(",").map((o) => {
                  const [en = "", ar = ""] = o.split("|").map((s) => s.trim());
                  return { value: en, label_en: en, label_ar: ar || en };
                })
              : null,
          },
        }),
      ),
    () => setForm(empty),
  );
  return (
    <div className="flex flex-col gap-4">
      {(fields.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("catalog.noneYet")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {(fields.data ?? []).map((f) => (
            <li key={f.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
              <span>
                {i18n.language === "ar" ? f.label_ar : f.label_en}
                {f.required ? " *" : ""}
              </span>
              <span className="text-muted-foreground">
                {t(`catalog.entities.${f.entity_type}`)} · {t(`catalog.fieldTypes.${f.data_type}`)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <AddForm
        title={t("catalog.addField")}
        onSubmit={() => add.mutate()}
        pending={add.isPending}
        error={add.error}
      >
        <Field label={t("catalog.appliesTo")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.entity}
              onChange={(e) => setForm({ ...form, entity: e.target.value })}
            >
              {(["product", "variant"] as const).map((e) => (
                <option key={e} value={e}>
                  {t(`catalog.entities.${e}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("catalog.code")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              required
              value={form.key}
              onChange={(e) => setForm({ ...form, key: e.target.value })}
            />
          )}
        </Field>
        <NameFields
          en={form.en}
          ar={form.ar}
          onEn={(en) => setForm({ ...form, en })}
          onAr={(ar) => setForm({ ...form, ar })}
        />
        <Field label={t("catalog.fieldType")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.type}
              onChange={(e) => setForm({ ...form, type: e.target.value })}
            >
              {FIELD_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`catalog.fieldTypes.${type}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
        {(form.type === "select" || form.type === "multiselect") && (
          <Field label={t("catalog.options")} hint={t("catalog.optionsHint")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                value={form.options}
                onChange={(e) => setForm({ ...form, options: e.target.value })}
              />
            )}
          </Field>
        )}
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.required}
            onChange={(e) => setForm({ ...form, required: e.target.checked })}
          />
          {t("catalog.required")}
        </label>
      </AddForm>
    </div>
  );
}
