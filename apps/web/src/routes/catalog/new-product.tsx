import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import {
  emptyDraft,
  localName,
  toProductIn,
  variantPreview,
  type ProductDraft,
} from "@/routes/catalog/product-form";
import {
  errorCode,
  unwrap,
  useAttributes,
  useBrands,
  useCategories,
  useCustomFields,
  useUnits,
  type CustomField,
} from "@/routes/catalog/queries";

/** Inventory → New product (M1.2): units with exact factors, variants from attribute values, custom fields. */
export function NewProductPage() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const units = useUnits();
  const categories = useCategories();
  const brands = useBrands();
  const attributes = useAttributes();
  const fields = useCustomFields("product");
  const [draft, setDraft] = useState<ProductDraft>(emptyDraft());

  const create = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/products", { body: toProductIn(draft, attributes.data ?? [], fields.data ?? []) }),
      ),
    onSuccess: async (product) => {
      await queryClient.invalidateQueries({ queryKey: ["products"] });
      await navigate({ to: "/inventory/products/$productId", params: { productId: product.id } });
    },
  });

  if (units.isPending || attributes.isPending || fields.isPending) return <p>{t("common.loading")}</p>;
  const set = (patch: Partial<ProductDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const unitOptions = units.data ?? [];
  const extraCodes = draft.units.map((u) => u.unit).filter(Boolean);
  const preview = variantPreview(attributes.data ?? [], draft.chosen);

  return (
    <form
      className="flex max-w-3xl flex-col gap-6"
      onSubmit={(e) => {
        e.preventDefault();
        create.mutate();
      }}
    >
      <h1 className="text-2xl font-bold">{t("catalog.newProduct")}</h1>
      {create.isError && (
        <p role="alert" className="text-destructive">
          {t(`catalog.errors.${errorCode(create.error)}`, { defaultValue: t("catalog.errors.generic") })}
        </p>
      )}

      <section className="grid gap-4 sm:grid-cols-2">
        <Field label={t("catalog.nameEn")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              required
              value={draft.nameEn}
              onChange={(e) => set({ nameEn: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("catalog.nameAr")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="rtl"
              required
              value={draft.nameAr}
              onChange={(e) => set({ nameAr: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("catalog.sku")} hint={t("catalog.skuHint")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              required
              value={draft.sku}
              onChange={(e) => set({ sku: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("catalog.productType")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={draft.productType}
              onChange={(e) => set({ productType: e.target.value as ProductDraft["productType"] })}
            >
              {(["stock", "service", "non_stock"] as const).map((type) => (
                <option key={type} value={type}>
                  {t(`catalog.types.${type}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("catalog.category")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={draft.categoryId}
              onChange={(e) => set({ categoryId: e.target.value })}
            >
              <option value="">{t("catalog.none")}</option>
              {(categories.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.path_en}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("catalog.brand")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={draft.brandId}
              onChange={(e) => set({ brandId: e.target.value })}
            >
              <option value="">{t("catalog.none")}</option>
              {(brands.data ?? []).map((b) => (
                <option key={b.id} value={b.id}>
                  {localName(b, i18n.language)}
                </option>
              ))}
            </select>
          )}
        </Field>
      </section>

      <section className="flex flex-col gap-3 rounded-lg border p-4" aria-label={t("catalog.unitsTitle")}>
        <h2 className="font-semibold">{t("catalog.unitsTitle")}</h2>
        <Field label={t("catalog.baseUnit")} hint={t("catalog.baseUnitHint")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={draft.baseUnit}
              onChange={(e) =>
                set({ baseUnit: e.target.value, purchaseUnit: e.target.value, salesUnit: e.target.value })
              }
            >
              {unitOptions.map((u) => (
                <option key={u.code} value={u.code}>
                  {localName(u, i18n.language)} ({u.code})
                </option>
              ))}
            </select>
          )}
        </Field>
        {draft.units.map((row, index) => (
          <div key={index} className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_auto]">
            <Field label={t("catalog.otherUnit")}>
              {(p) => (
                <select
                  {...p}
                  className={inputClass}
                  value={row.unit}
                  onChange={(e) =>
                    set({
                      units: draft.units.map((r, i) => (i === index ? { ...r, unit: e.target.value } : r)),
                    })
                  }
                >
                  <option value="">—</option>
                  {unitOptions
                    .filter((u) => u.code !== draft.baseUnit)
                    .map((u) => (
                      <option key={u.code} value={u.code}>
                        {localName(u, i18n.language)} ({u.code})
                      </option>
                    ))}
                </select>
              )}
            </Field>
            <Field
              label={t("catalog.factor", { unit: row.unit || "…", base: draft.baseUnit })}
              hint={t("catalog.factorHint")}
            >
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  dir="ltr"
                  inputMode="decimal"
                  value={row.factor}
                  onChange={(e) =>
                    set({
                      units: draft.units.map((r, i) => (i === index ? { ...r, factor: e.target.value } : r)),
                    })
                  }
                />
              )}
            </Field>
            <Button
              type="button"
              variant="ghost"
              onClick={() => set({ units: draft.units.filter((_, i) => i !== index) })}
            >
              {t("catalog.remove")}
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          className="self-start"
          onClick={() => set({ units: [...draft.units, { unit: "", factor: "" }] })}
        >
          {t("catalog.addUnit")}
        </Button>
        <div className="grid gap-3 sm:grid-cols-2">
          {(["purchaseUnit", "salesUnit"] as const).map((key) => (
            <Field key={key} label={t(`catalog.${key}`)}>
              {(p) => (
                <select
                  {...p}
                  className={inputClass}
                  value={draft[key]}
                  onChange={(e) => set({ [key]: e.target.value })}
                >
                  {[draft.baseUnit, ...extraCodes].map((code) => (
                    <option key={code} value={code}>
                      {code}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          ))}
        </div>
      </section>

      {(attributes.data ?? []).length > 0 && (
        <section
          className="flex flex-col gap-3 rounded-lg border p-4"
          aria-label={t("catalog.variantsTitle")}
        >
          <h2 className="font-semibold">{t("catalog.variantsTitle")}</h2>
          <p className="text-sm text-muted-foreground">{t("catalog.variantsHint")}</p>
          {(attributes.data ?? []).map((attribute) => (
            <fieldset key={attribute.id} className="flex flex-wrap gap-3 text-sm">
              <legend className="mb-1 font-medium">{localName(attribute, i18n.language)}</legend>
              {attribute.values.length === 0 && (
                <span className="text-muted-foreground">{t("catalog.noValues")}</span>
              )}
              {attribute.values.map((value) => {
                const chosen = draft.chosen[attribute.code] ?? [];
                const checked = chosen.includes(value.value_en);
                return (
                  <label key={value.id} className="flex items-center gap-1">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        set({
                          chosen: {
                            ...draft.chosen,
                            [attribute.code]: checked
                              ? chosen.filter((v) => v !== value.value_en)
                              : [...chosen, value.value_en],
                          },
                        })
                      }
                    />
                    {i18n.language === "ar" ? value.value_ar : value.value_en}
                  </label>
                );
              })}
            </fieldset>
          ))}
          <p className="text-sm" data-testid="variant-preview">
            {preview.length === 0
              ? t("catalog.oneVariant")
              : t("catalog.variantCount", { count: preview.length })}
          </p>
        </section>
      )}

      {(fields.data ?? []).length > 0 && (
        <section
          className="grid gap-4 rounded-lg border p-4 sm:grid-cols-2"
          aria-label={t("catalog.customFields")}
        >
          <h2 className="font-semibold sm:col-span-2">{t("catalog.customFields")}</h2>
          {(fields.data ?? []).map((field) => (
            <CustomFieldInput
              key={field.id}
              field={field}
              value={draft.custom[field.key]}
              onChange={(value) => set({ custom: { ...draft.custom, [field.key]: value } })}
            />
          ))}
        </section>
      )}

      <Button type="submit" className="self-start" disabled={create.isPending}>
        {t("catalog.create")}
      </Button>
    </form>
  );
}

function CustomFieldInput({
  field,
  value,
  onChange,
}: {
  field: CustomField;
  value: string | boolean | string[] | undefined;
  onChange: (value: string | boolean | string[]) => void;
}) {
  const { i18n } = useTranslation();
  const label = (i18n.language === "ar" ? field.label_ar : field.label_en) + (field.required ? " *" : "");
  if (field.data_type === "bool") {
    return (
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
        {label}
      </label>
    );
  }
  return (
    <Field label={label}>
      {(p) =>
        field.data_type === "select" ? (
          <select
            {...p}
            className={inputClass}
            value={(value as string) ?? ""}
            onChange={(e) => onChange(e.target.value)}
          >
            <option value="">—</option>
            {(field.options ?? []).map((o) => (
              <option key={o.value} value={o.value}>
                {i18n.language === "ar" ? o.label_ar : o.label_en}
              </option>
            ))}
          </select>
        ) : field.data_type === "multiselect" ? (
          <select
            {...p}
            multiple
            className={`${inputClass} h-24`}
            value={(value as string[]) ?? []}
            onChange={(e) => onChange(Array.from(e.target.selectedOptions, (o) => o.value))}
          >
            {(field.options ?? []).map((o) => (
              <option key={o.value} value={o.value}>
                {i18n.language === "ar" ? o.label_ar : o.label_en}
              </option>
            ))}
          </select>
        ) : (
          <input
            {...p}
            className={inputClass}
            type={field.data_type === "date" ? "date" : "text"}
            inputMode={field.data_type === "number" || field.data_type === "decimal" ? "decimal" : undefined}
            value={(value as string) ?? ""}
            onChange={(e) => onChange(e.target.value)}
          />
        )
      }
    </Field>
  );
}
