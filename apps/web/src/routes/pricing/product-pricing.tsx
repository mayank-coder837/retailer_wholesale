import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { errorCode, unwrap, type Product } from "@/routes/catalog/queries";
import { showMoney, splitSource, usePriceLists, type Quote } from "@/routes/pricing/queries";
import { useBusiness } from "@/shell/use-business";

/** Product page → Pricing (M1.3): base and minimum price per unit, list prices, promotions, a price check. */
export function ProductPricing({
  product,
  variantLabel,
}: {
  product: Product;
  variantLabel: (v: Product["variants"][number]) => string;
}) {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-4" aria-label={t("pricing.title")}>
      <h2 className="text-lg font-semibold">{t("pricing.title")}</h2>
      <UnitPrices product={product} />
      <ListPrice product={product} variantLabel={variantLabel} />
      <NewPromotion product={product} variantLabel={variantLabel} />
      <PriceCheck product={product} variantLabel={variantLabel} />
    </section>
  );
}

function Problem({ error }: { error: unknown }) {
  const { t } = useTranslation();
  if (!error) return null;
  return (
    <p role="alert" className="text-sm text-destructive">
      {t(`pricing.errors.${errorCode(error)}`, {
        defaultValue: t(`catalog.errors.${errorCode(error)}`, { defaultValue: t("catalog.errors.generic") }),
      })}
    </p>
  );
}

function UnitPrices({ product }: { product: Product }) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const [edits, setEdits] = useState<Record<string, { sale: string; min: string }>>({});
  const save = useMutation({
    mutationFn: async (unit: string) => {
      const row = product.units.find((u) => u.unit === unit)!;
      const edit = edits[unit] ?? { sale: row.sale_price ?? "", min: row.min_price ?? "" };
      return unwrap(
        await api.PATCH("/products/{product_id}/units/{unit_code}/prices", {
          params: { path: { product_id: product.id, unit_code: unit } },
          body: { sale_price: edit.sale.trim() || null, min_price: edit.min.trim() || null },
        }),
      );
    },
    onSuccess: (updated) => queryClient.setQueryData(["product", product.id], updated),
  });
  return (
    <div
      className="flex flex-col gap-2 rounded-lg border p-4"
      role="group"
      aria-label={t("pricing.unitPrices")}
    >
      <h3 className="font-semibold">{t("pricing.unitPrices")}</h3>
      <p className="text-xs text-muted-foreground">{t("pricing.vatExclusive")}</p>
      {product.units.map((u) => {
        const edit = edits[u.unit] ?? { sale: u.sale_price ?? "", min: u.min_price ?? "" };
        const set = (patch: Partial<typeof edit>) => setEdits({ ...edits, [u.unit]: { ...edit, ...patch } });
        return (
          <div key={u.unit} className="grid items-end gap-3 sm:grid-cols-[6rem_1fr_1fr_auto]">
            <span className="pb-2 text-sm font-medium" dir="ltr">
              {u.unit}
            </span>
            <Field label={t("pricing.salePrice", { unit: u.unit })}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  dir="ltr"
                  inputMode="decimal"
                  value={edit.sale}
                  onChange={(e) => set({ sale: e.target.value })}
                />
              )}
            </Field>
            <Field label={t("pricing.minPrice", { unit: u.unit })}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  dir="ltr"
                  inputMode="decimal"
                  value={edit.min}
                  onChange={(e) => set({ min: e.target.value })}
                />
              )}
            </Field>
            <Button
              type="button"
              variant="outline"
              disabled={save.isPending}
              onClick={() => save.mutate(u.unit)}
            >
              {t("pricing.save")}
            </Button>
          </div>
        );
      })}
      <Problem error={save.error} />
    </div>
  );
}

function ListPrice({
  product,
  variantLabel,
}: {
  product: Product;
  variantLabel: (v: Product["variants"][number]) => string;
}) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const { current } = useBusiness();
  const lists = usePriceLists(current?.id);
  const [form, setForm] = useState({
    list: "",
    variant: product.variants[0]?.id ?? "",
    unit: product.base_unit,
    minQty: "0",
    price: "",
  });
  const [saved, setSaved] = useState(false);
  const add = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.PUT("/price-lists/{price_list_id}/items", {
          params: { path: { price_list_id: form.list } },
          body: { variant_id: form.variant, unit: form.unit, min_qty: form.minQty || "0", price: form.price },
        }),
      ),
    onSuccess: () => {
      setSaved(true);
      setForm({ ...form, price: "" });
    },
  });
  return (
    <form
      aria-label={t("pricing.addListPrice")}
      className="flex flex-col gap-3 rounded-lg border p-4"
      onSubmit={(e) => {
        e.preventDefault();
        setSaved(false);
        add.mutate();
      }}
    >
      <h3 className="font-semibold">{t("pricing.addListPrice")}</h3>
      {(lists.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("pricing.noLists")}</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={t("pricing.list")}>
            {(p) => (
              <select
                {...p}
                className={inputClass}
                required
                value={form.list}
                onChange={(e) => setForm({ ...form, list: e.target.value })}
              >
                <option value="">—</option>
                {(lists.data ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {i18n.language === "ar" ? l.name_ar : l.name_en}
                    {l.is_standard ? ` (${t("pricing.standard")})` : ""}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("catalog.variant")}>
            {(p) => (
              <select
                {...p}
                className={inputClass}
                value={form.variant}
                onChange={(e) => setForm({ ...form, variant: e.target.value })}
              >
                {product.variants.map((v) => (
                  <option key={v.id} value={v.id}>
                    {variantLabel(v)}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("catalog.unit")}>
            {(p) => (
              <select
                {...p}
                className={inputClass}
                value={form.unit}
                onChange={(e) => setForm({ ...form, unit: e.target.value })}
              >
                {product.units.map((u) => (
                  <option key={u.unit} value={u.unit}>
                    {u.unit}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field label={t("pricing.fromQty")} hint={t("pricing.fromQtyHint")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                dir="ltr"
                inputMode="decimal"
                value={form.minQty}
                onChange={(e) => setForm({ ...form, minQty: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("pricing.price")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                dir="ltr"
                inputMode="decimal"
                required
                value={form.price}
                onChange={(e) => setForm({ ...form, price: e.target.value })}
              />
            )}
          </Field>
          <Button type="submit" className="self-end" disabled={add.isPending}>
            {t("pricing.save")}
          </Button>
        </div>
      )}
      {saved && (
        <p role="status" className="text-sm">
          {t("pricing.saved")}
        </p>
      )}
      <Problem error={add.error} />
    </form>
  );
}

function NewPromotion({
  product,
  variantLabel,
}: {
  product: Product;
  variantLabel: (v: Product["variants"][number]) => string;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const empty = { en: "", ar: "", kind: "percent", value: "", target: "product", from: "", to: "" };
  const [form, setForm] = useState(empty);
  const [saved, setSaved] = useState(false);
  const add = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/promotions", {
          body: {
            business_id: current!.id,
            name_en: form.en,
            name_ar: form.ar,
            kind: form.kind as "percent" | "amount" | "fixed_price",
            value: form.value,
            scope: form.target === "product" ? "product" : "variant",
            scope_id: form.target === "product" ? product.id : form.target,
            min_qty: "0",
            valid_from: form.from || null,
            valid_to: form.to || null,
          },
        }),
      ),
    onSuccess: async () => {
      setSaved(true);
      setForm(empty);
      await queryClient.invalidateQueries({ queryKey: ["promotions"] });
    },
  });
  return (
    <form
      aria-label={t("pricing.addPromotion")}
      className="flex flex-col gap-3 rounded-lg border p-4"
      onSubmit={(e) => {
        e.preventDefault();
        setSaved(false);
        add.mutate();
      }}
    >
      <h3 className="font-semibold">{t("pricing.addPromotion")}</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label={t("catalog.nameEn")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              required
              value={form.en}
              onChange={(e) => setForm({ ...form, en: e.target.value })}
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
              value={form.ar}
              onChange={(e) => setForm({ ...form, ar: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("pricing.kind")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value })}
            >
              {(["percent", "amount", "fixed_price"] as const).map((k) => (
                <option key={k} value={k}>
                  {t(`pricing.kinds.${k}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("pricing.value")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              inputMode="decimal"
              required
              value={form.value}
              onChange={(e) => setForm({ ...form, value: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("pricing.appliesTo")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.target}
              onChange={(e) => setForm({ ...form, target: e.target.value })}
            >
              <option value="product">{t("pricing.wholeProduct")}</option>
              {product.variants.length > 1 &&
                product.variants.map((v) => (
                  <option key={v.id} value={v.id}>
                    {variantLabel(v)}
                  </option>
                ))}
            </select>
          )}
        </Field>
        <span />
        <Field label={t("pricing.validFrom")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              type="date"
              value={form.from}
              onChange={(e) => setForm({ ...form, from: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("pricing.validTo")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              type="date"
              value={form.to}
              onChange={(e) => setForm({ ...form, to: e.target.value })}
            />
          )}
        </Field>
      </div>
      <Button type="submit" className="self-start" disabled={add.isPending || !current}>
        {t("pricing.addPromotion")}
      </Button>
      {saved && (
        <p role="status" className="text-sm">
          {t("pricing.saved")}
        </p>
      )}
      <Problem error={add.error} />
    </form>
  );
}

function PriceCheck({
  product,
  variantLabel,
}: {
  product: Product;
  variantLabel: (v: Product["variants"][number]) => string;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const { current } = useBusiness();
  const [form, setForm] = useState({
    variant: product.variants[0]?.id ?? "",
    unit: product.base_unit,
    qty: "1",
  });
  const check = useMutation({
    mutationFn: async (): Promise<Quote> =>
      unwrap(
        await api.POST("/pricing/quote", {
          body: { business_id: current!.id, variant_id: form.variant, unit: form.unit, qty: form.qty },
        }),
      ),
  });
  const result = check.data;
  const source = result ? splitSource(result.price_source) : null;
  return (
    <form
      aria-label={t("pricing.check")}
      className="flex flex-col gap-3 rounded-lg border p-4"
      onSubmit={(e) => {
        e.preventDefault();
        check.mutate();
      }}
    >
      <h3 className="font-semibold">{t("pricing.check")}</h3>
      <div className="grid items-end gap-3 sm:grid-cols-[1fr_8rem_8rem_auto]">
        <Field label={t("catalog.variant")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.variant}
              onChange={(e) => setForm({ ...form, variant: e.target.value })}
            >
              {product.variants.map((v) => (
                <option key={v.id} value={v.id}>
                  {variantLabel(v)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("catalog.unit")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.unit}
              onChange={(e) => setForm({ ...form, unit: e.target.value })}
            >
              {product.units.map((u) => (
                <option key={u.unit} value={u.unit}>
                  {u.unit}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("pricing.qty")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              inputMode="decimal"
              value={form.qty}
              onChange={(e) => setForm({ ...form, qty: e.target.value })}
            />
          )}
        </Field>
        <Button type="submit" disabled={check.isPending || !current}>
          {t("pricing.checkButton")}
        </Button>
      </div>
      {result && source && (
        <div role="status" className="text-sm" data-testid="quote">
          <p className="text-base font-semibold" dir="ltr">
            {result.price === null
              ? t("pricing.noPrice")
              : `${showMoney(result.price)} ${current?.base_currency ?? "AED"}`}
          </p>
          <p className="text-muted-foreground">
            {t(`pricing.sources.${source.source}`, { name: source.name })}
            {result.converted ? ` · ${t("pricing.converted")}` : ""}
          </p>
          {result.below_min && (
            <p className="text-destructive">
              {t(result.may_go_below_min ? "pricing.belowMinAllowed" : "pricing.belowMin", {
                min: showMoney(result.min_price),
              })}
            </p>
          )}
        </div>
      )}
      <Problem error={check.error} />
    </form>
  );
}
