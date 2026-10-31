import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { besideField, Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { localName } from "@/routes/catalog/product-form";
import { errorCode, unwrap, useProduct, type Product } from "@/routes/catalog/queries";
import { ProductPricing } from "@/routes/pricing/product-pricing";
import { TaxCodeField } from "@/routes/tax/tax-fields";

/** Inventory → product (M1.2): units, variants and barcodes. */
export function ProductPage() {
  const { t, i18n } = useTranslation();
  const { productId } = useParams({ strict: false }) as { productId: string };
  const product = useProduct(productId);

  if (product.isPending) return <p>{t("common.loading")}</p>;
  if (product.isError) {
    return (
      <p role="alert" className="text-destructive">
        {t(`catalog.errors.${errorCode(product.error)}`, { defaultValue: t("catalog.errors.generic") })}
      </p>
    );
  }
  const p = product.data;
  const variantLabel = (v: Product["variants"][number]) =>
    v.values.map((x) => (i18n.language === "ar" ? x.value_ar : x.value_en)).join(" · ") ||
    t("catalog.defaultVariant");

  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold leading-9">{localName(p, i18n.language)}</h1>
        <p className="text-sm text-muted-foreground">
          <span dir="ltr" className="font-mono">
            {p.sku}
          </span>{" "}
          · {t(`catalog.types.${p.product_type}`)} · {t("catalog.stockIn", { unit: p.base_unit })}
        </p>
      </div>

      <section className="flex flex-col gap-2" aria-label={t("catalog.unitsTitle")}>
        <h2 className="text-lg font-semibold">{t("catalog.unitsTitle")}</h2>
        <ul className="divide-y rounded-md border text-sm">
          {p.units.map((u) => (
            <li key={u.unit} className="flex flex-wrap justify-between gap-2 px-3 py-2">
              <span>
                {localName(u, i18n.language)} ({u.unit})
              </span>
              <span dir="ltr" className="text-muted-foreground">
                {u.is_base
                  ? t("catalog.base")
                  : `1 ${u.unit} = ${trimZeros(u.factor_to_base)} ${p.base_unit}`}
                {u.is_purchase_default ? ` · ${t("catalog.buyIn")}` : ""}
                {u.is_sales_default ? ` · ${t("catalog.sellIn")}` : ""}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col gap-2" aria-label={t("catalog.variantsTitle")}>
        <h2 className="text-lg font-semibold">{t("catalog.variantsTitle")}</h2>
        <ul className="divide-y rounded-md border text-sm">
          {p.variants.map((v) => (
            <li key={v.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
              <span>{variantLabel(v)}</span>
              <span dir="ltr" className="font-mono text-muted-foreground">
                {v.sku}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <ProductTax product={p} />
      <Barcodes product={p} variantLabel={variantLabel} />
      <ProductPricing product={p} variantLabel={variantLabel} />
    </div>
  );
}

/** The item's own VAT codes (M1.10): the default on its lines after the customer's or supplier's. */
function ProductTax({ product }: { product: Product }) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const saved = { sales: product.sales_tax_code ?? "", purchase: product.purchase_tax_code ?? "" };
  const [form, setForm] = useState(saved);
  const changed = form.sales !== saved.sales || form.purchase !== saved.purchase;
  const save = useMutation({
    mutationFn: async () => {
      // Only what changed: a code saved earlier is never sent back to be checked again.
      const body: { sales_tax_code?: string | null; purchase_tax_code?: string | null } = {};
      if (form.sales !== saved.sales) body.sales_tax_code = form.sales || null;
      if (form.purchase !== saved.purchase) body.purchase_tax_code = form.purchase || null;
      return unwrap(
        await api.PATCH("/products/{product_id}", { params: { path: { product_id: product.id } }, body }),
      );
    },
    onSuccess: (updated) => queryClient.setQueryData(["product", product.id], updated),
  });
  return (
    <section className="flex flex-col gap-2" aria-label={t("tax.productTitle")}>
      <h2 className="text-lg font-semibold">{t("tax.productTitle")}</h2>
      <div className="flex flex-wrap items-end gap-3">
        <TaxCodeField
          side="sales"
          label={t("tax.salesCode")}
          empty={t("tax.standardDefault")}
          className="w-64"
          stock={false}
          value={form.sales}
          onChange={(code) => setForm({ ...form, sales: code })}
        />
        <TaxCodeField
          side="purchase"
          label={t("tax.purchaseCode")}
          empty={t("tax.standardDefault")}
          className="w-64"
          stock={product.product_type === "stock"}
          value={form.purchase}
          onChange={(code) => setForm({ ...form, purchase: code })}
        />
        <Button disabled={!changed || save.isPending} onClick={() => save.mutate()}>
          {t("tax.save")}
        </Button>
        {save.isSuccess && !changed && (
          <span role="status" className="pb-2 text-sm text-muted-foreground">
            {t("tax.saved")}
          </span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{t("tax.productHint")}</p>
      {save.isError && (
        <p role="alert" className="text-sm text-destructive">
          {t(`catalog.errors.${errorCode(save.error)}`, { defaultValue: t("catalog.errors.generic") })}
        </p>
      )}
    </section>
  );
}

/** 50.000000 → 50; 0.914400 → 0.9144 (display only: the stored factor stays exact). */
function trimZeros(value: string): string {
  return value.includes(".") ? value.replace(/\.?0+$/, "") : value;
}

function Barcodes({
  product,
  variantLabel,
}: {
  product: Product;
  variantLabel: (v: Product["variants"][number]) => string;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const [variantId, setVariantId] = useState(product.variants[0]?.id ?? "");
  const [unit, setUnit] = useState(product.base_unit);
  const [code, setCode] = useState("");
  const refresh = (updated: Product) => queryClient.setQueryData(["product", product.id], updated);

  const add = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/products/{product_id}/barcodes", {
          params: { path: { product_id: product.id } },
          body: { variant_id: variantId, unit, code: code.trim() || null, kind: "ean13" },
        }),
      ),
    onSuccess: (updated) => {
      setCode("");
      refresh(updated);
    },
  });
  const remove = useMutation({
    mutationFn: async (barcodeId: string) =>
      unwrap(await api.DELETE("/barcodes/{barcode_id}", { params: { path: { barcode_id: barcodeId } } })),
    onSuccess: refresh,
  });
  const variants = new Map(product.variants.map((v) => [v.id, v]));

  return (
    <section className="flex flex-col gap-3" aria-label={t("catalog.barcodes")}>
      <h2 className="text-lg font-semibold">{t("catalog.barcodes")}</h2>
      {product.barcodes.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("catalog.noBarcodes")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {product.barcodes.map((b) => (
            <li key={b.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span dir="ltr" className="font-mono">
                {b.code}
              </span>
              <span className="text-muted-foreground">
                {variants.get(b.variant_id) ? variantLabel(variants.get(b.variant_id)!) : ""} · {b.unit}
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={remove.isPending}
                onClick={() => remove.mutate(b.id)}
              >
                {t("catalog.remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="grid items-start gap-3 sm:grid-cols-[1fr_auto_1fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <Field label={t("catalog.variant")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={variantId}
              onChange={(e) => setVariantId(e.target.value)}
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
            <select {...p} className={inputClass} value={unit} onChange={(e) => setUnit(e.target.value)}>
              {product.units.map((u) => (
                <option key={u.unit} value={u.unit}>
                  {u.unit}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("catalog.barcode")} hint={t("catalog.barcodeHint")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          )}
        </Field>
        <Button type="submit" className={besideField} disabled={add.isPending}>
          {code.trim() ? t("catalog.addBarcode") : t("catalog.generateBarcode")}
        </Button>
      </form>
      {(add.isError || remove.isError) && (
        <p role="alert" className="text-sm text-destructive">
          {t(`catalog.errors.${errorCode(add.error ?? remove.error)}`, {
            defaultValue: t("catalog.errors.generic"),
          })}
        </p>
      )}
    </section>
  );
}
