import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { money, useBranches } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { InventoryNav } from "@/routes/inventory/inventory-nav";
import { qty, useBalances, useItemLedger, useWarehouses, warehouseName } from "@/routes/inventory/queries";
import { useBusiness } from "@/shell/use-business";

/** Inventory → one item's stock ledger (M1.6): every move with the running quantity and value. */
export function ItemLedgerPage() {
  const { t, i18n } = useTranslation();
  const { variantId } = useParams({ strict: false }) as { variantId: string };
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);
  const [warehouseId, setWarehouseId] = useState("");
  const ledger = useItemLedger(current?.id, variantId, warehouseId);
  const item = useBalances(current?.id, { variantId }).data?.[0];

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold">
        {item ? (
          <>
            <span className="me-2 font-mono text-muted-foreground" dir="ltr">
              {item.sku}
            </span>
            {i18n.language === "ar" ? item.name_ar : item.name_en}
          </>
        ) : (
          t("inventory.ledger.title")
        )}
      </h1>
      <InventoryNav />
      <select
        className="h-9 w-64 rounded-md border bg-background px-3 text-sm"
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
      {ledger.isPending && <p>{t("common.loading")}</p>}
      {ledger.isError && (
        <p role="alert" className="text-destructive">
          {t(`inventory.errors.${errorCode(ledger.error)}`, { defaultValue: t("inventory.errors.generic") })}
        </p>
      )}
      {ledger.data && (
        <>
          <p className="text-sm">
            {t("inventory.ledger.average")}{" "}
            <strong dir="ltr" className="tabular-nums">
              {ledger.data.avg_cost ?? "—"}
            </strong>
          </p>
          <table className="w-full text-sm" aria-label={t("inventory.ledger.title")}>
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 text-start font-medium">{t("inventory.ledger.date")}</th>
                <th className="py-2 text-start font-medium">{t("inventory.ledger.document")}</th>
                <th className="py-2 text-start font-medium">{t("inventory.ledger.move")}</th>
                <th className="py-2 text-start font-medium">{t("inventory.stock.warehouse")}</th>
                <th className="py-2 text-end font-medium">{t("inventory.stock.qty")}</th>
                <th className="py-2 text-end font-medium">{t("inventory.stock.value")}</th>
                <th className="py-2 text-end font-medium">{t("inventory.ledger.balance")}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {ledger.data.lines.map((line, i) => (
                <tr key={i}>
                  <td className="py-1.5" dir="ltr">
                    {line.move_date}
                  </td>
                  <td className="py-1.5 font-mono" dir="ltr">
                    {line.document_id ? (
                      <Link
                        to="/inventory/documents/$docId"
                        params={{ docId: line.document_id }}
                        className="underline-offset-2 hover:underline"
                      >
                        {line.number}
                      </Link>
                    ) : (
                      line.number
                    )}
                  </td>
                  <td className="py-1.5">{t(`inventory.moves.${line.move_type}`)}</td>
                  <td className="py-1.5">
                    {warehouseName(warehouses.data, line.warehouse_id, i18n.language)}
                  </td>
                  <td className="py-1.5 text-end tabular-nums" dir="ltr">
                    {/^-?0*(\.0*)?$/.test(line.qty) ? "" : qty(line.qty)}
                  </td>
                  <td className="py-1.5 text-end tabular-nums" dir="ltr">
                    {money(line.value)}
                  </td>
                  <td className="py-1.5 text-end tabular-nums" dir="ltr">
                    {qty(line.qty_balance)} · {money(line.value_balance)}
                  </td>
                </tr>
              ))}
              <tr className="font-semibold">
                <td className="py-1.5" colSpan={6}>
                  {t("inventory.ledger.closing")}
                </td>
                <td className="py-1.5 text-end tabular-nums" dir="ltr">
                  {qty(ledger.data.closing_qty)} · {money(ledger.data.closing_value)}
                </td>
              </tr>
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
