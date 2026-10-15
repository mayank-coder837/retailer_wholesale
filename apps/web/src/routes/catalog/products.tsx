import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { localName } from "@/routes/catalog/product-form";
import { useProducts } from "@/routes/catalog/queries";

/** Inventory → Products (M1.2): search by name, SKU or a scanned barcode. */
export function ProductsPage() {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const products = useProducts(query.trim());

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">{t("catalog.products")}</h1>
        <div className="flex gap-2">
          <Button asChild variant="outline">
            <Link to="/settings/catalog">{t("catalog.setup")}</Link>
          </Button>
          <Button asChild>
            <Link to="/inventory/products/new">{t("catalog.newProduct")}</Link>
          </Button>
        </div>
      </div>
      <input
        className={inputClass}
        type="search"
        aria-label={t("catalog.search")}
        placeholder={t("catalog.search")}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {products.isPending && <p>{t("common.loading")}</p>}
      {products.isError && (
        <p role="alert" className="text-destructive">
          {t("catalog.errors.forbidden")}
        </p>
      )}
      {products.isSuccess && products.data.length === 0 && (
        <p className="text-muted-foreground">{query ? t("catalog.noMatch") : t("catalog.noProducts")}</p>
      )}
      {products.isSuccess && products.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-start text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("catalog.sku")}</th>
              <th className="py-2 text-start font-medium">{t("catalog.name")}</th>
              <th className="py-2 text-start font-medium">{t("catalog.baseUnit")}</th>
              <th className="py-2 text-end font-medium">{t("catalog.variants")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {products.data.map((p) => (
              <tr key={p.id}>
                <td className="py-2 font-mono" dir="ltr">
                  {p.sku}
                </td>
                <td className="py-2">
                  <Link
                    to="/inventory/products/$productId"
                    params={{ productId: p.id }}
                    className="underline-offset-2 hover:underline"
                  >
                    {localName(p, i18n.language)}
                  </Link>
                </td>
                <td className="py-2" dir="ltr">
                  {p.base_unit}
                </td>
                <td className="py-2 text-end">{p.variant_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
