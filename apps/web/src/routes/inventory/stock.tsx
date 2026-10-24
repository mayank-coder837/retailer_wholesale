import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { inputClass } from "@/components/field";
import { money, useBranches } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { InventoryNav } from "@/routes/inventory/inventory-nav";
import {
  qty,
  useBalances,
  useNegativeStock,
  useValuation,
  useWarehouses,
  type Balance,
  warehouseName,
  type Warehouse,
} from "@/routes/inventory/queries";
import { useBusiness } from "@/shell/use-business";
import { CodeTag } from "@/components/code-tag";
import { cn } from "@/lib/utils";

/** Quantity and value per item, warehouse and batch, each linking to the item's stock ledger. */
export function BalanceTable({ rows, warehouses }: { rows: Balance[]; warehouses: Warehouse[] | undefined }) {
  const { t, i18n } = useTranslation();
  return (
    <table className="w-full text-sm">
      <thead className="text-muted-foreground">
        <tr>
          <th className="py-2 text-start font-medium">{t("inventory.stock.item")}</th>
          <th className="py-2 text-start font-medium">{t("inventory.stock.warehouse")}</th>
          <th className="py-2 text-start font-medium">{t("inventory.stock.batch")}</th>
          <th className="py-2 text-right font-medium">{t("inventory.stock.qty")}</th>
          <th className="py-2 text-right font-medium">{t("inventory.stock.value")}</th>
        </tr>
      </thead>
      <tbody className="divide-y">
        {rows.map((r) => (
          <tr key={`${r.variant_id}-${r.warehouse_id}-${r.batch_id ?? ""}`}>
            <td className="py-1.5">
              <CodeTag>{r.sku}</CodeTag>
              <Link
                to="/inventory/items/$variantId"
                params={{ variantId: r.variant_id }}
                className="underline-offset-2 hover:underline"
              >
                {i18n.language === "ar" ? r.name_ar : r.name_en}
              </Link>
            </td>
            <td className="py-1.5">{warehouseName(warehouses, r.warehouse_id, i18n.language)}</td>
            <td className="py-1.5">
              <span dir="ltr">
                {r.batch_no ?? ""}
                {r.expiry_date && <span className="ms-2 text-xs text-muted-foreground">{r.expiry_date}</span>}
              </span>
            </td>
            <td
              className={
                "py-1.5 text-right tabular-nums" + (r.qty.startsWith("-") ? " text-destructive" : "")
              }
            >
              <span dir="ltr">
                {qty(r.qty)} {r.base_unit}
              </span>
            </td>
            <td className="py-1.5 text-right tabular-nums">
              <span dir="ltr">{money(r.value)}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Inventory → stock (M1.6): what is on hand, where, and what it is worth; the valuation check on top. */
export function StockPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);
  const [query, setQuery] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const balances = useBalances(current?.id, { q: query.trim(), warehouseId });
  const valuation = useValuation(current?.id);

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("inventory.title")}</h1>
      <InventoryNav />
      {valuation.data && (
        <section
          aria-label={t("inventory.valuation.title")}
          className="flex flex-wrap items-center gap-x-6 gap-y-1 rounded-lg border p-3 text-sm"
        >
          <span>
            {t("inventory.valuation.stock")}{" "}
            <strong dir="ltr" className="tabular-nums">
              {money(valuation.data.stock_value)}
            </strong>
          </span>
          <span>
            {t("inventory.valuation.account")}{" "}
            <strong dir="ltr" className="tabular-nums">
              {money(valuation.data.inventory_account)}
            </strong>
          </span>
          {valuation.data.difference === "0.0000" ? (
            <span className="text-muted-foreground">{t("inventory.valuation.agree")}</span>
          ) : (
            <span role="alert" className="text-destructive">
              {t("inventory.valuation.differ", { amount: money(valuation.data.difference) })}
            </span>
          )}
        </section>
      )}
      <div className="flex flex-wrap gap-3">
        <input
          className={cn(inputClass, "min-w-60 flex-1")}
          type="search"
          aria-label={t("inventory.stock.search")}
          placeholder={t("inventory.stock.search")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          className="h-9 rounded-md border bg-background px-3 text-sm"
          aria-label={t("inventory.stock.warehouse")}
          value={warehouseId}
          onChange={(e) => setWarehouseId(e.target.value)}
        >
          <option value="">{t("inventory.stock.allWarehouses")}</option>
          {warehouses.data?.map((w) => (
            <option key={w.id} value={w.id}>
              {i18n.language === "ar" ? w.name_ar : w.name_en}
            </option>
          ))}
        </select>
      </div>
      {balances.isPending && <p>{t("common.loading")}</p>}
      {balances.isError && (
        <p role="alert" className="text-destructive">
          {t(`inventory.errors.${errorCode(balances.error)}`, {
            defaultValue: t("inventory.errors.generic"),
          })}
        </p>
      )}
      {balances.data?.length === 0 && (
        <p className="text-muted-foreground">
          {query ? t("inventory.stock.noMatch") : t("inventory.stock.none")}
        </p>
      )}
      {balances.data && balances.data.length > 0 && (
        <BalanceTable rows={balances.data} warehouses={warehouses.data} />
      )}
    </div>
  );
}

/** Inventory → negative stock (M1.6): items sold or moved below zero, to fix with an Item Transfer or a count. */
export function NegativeStockPage() {
  const { t } = useTranslation();
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);
  const negative = useNegativeStock(current?.id);

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("inventory.title")}</h1>
      <InventoryNav />
      <p className="text-sm text-muted-foreground">{t("inventory.negative.hint")}</p>
      {negative.isPending && <p>{t("common.loading")}</p>}
      {negative.isError && (
        <p role="alert" className="text-destructive">
          {t(`inventory.errors.${errorCode(negative.error)}`, {
            defaultValue: t("inventory.errors.generic"),
          })}
        </p>
      )}
      {negative.data?.length === 0 && <p className="text-muted-foreground">{t("inventory.negative.none")}</p>}
      {negative.data && negative.data.length > 0 && (
        <BalanceTable rows={negative.data} warehouses={warehouses.data} />
      )}
    </div>
  );
}
