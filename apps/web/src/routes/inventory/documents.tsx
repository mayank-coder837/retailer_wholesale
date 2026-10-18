import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { money } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { InventoryNav } from "@/routes/inventory/inventory-nav";
import {
  STOCK_DOC_TYPES,
  useStockDocs,
  type StockDocSummary,
  type StockDocType,
} from "@/routes/inventory/queries";
import { useBusiness } from "@/shell/use-business";

const STATUSES: (StockDocSummary["status"] | null)[] = [null, "draft", "posted"];

/** Inventory → stock documents (M1.6): adjustments, transfers and Item Transfers, newest first. */
export function StockDocumentsPage() {
  const { t } = useTranslation();
  const { current } = useBusiness();
  const [docType, setDocType] = useState<StockDocType | null>(null);
  const [status, setStatus] = useState<StockDocSummary["status"] | null>(null);
  const docs = useStockDocs(current?.id, { docType, status });

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold">{t("inventory.title")}</h1>
      <InventoryNav />
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("inventory.documents.new")}>
        {STOCK_DOC_TYPES.map((kind) => (
          <Button key={kind} asChild variant="outline" size="sm">
            <Link to="/inventory/documents/new/$kind" params={{ kind }}>
              {t(`inventory.docTypes.${kind}`)}
            </Link>
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap gap-3">
        <select
          className="h-9 rounded-md border bg-background px-3 text-sm"
          aria-label={t("inventory.documents.type")}
          value={docType ?? ""}
          onChange={(e) => setDocType((e.target.value || null) as StockDocType | null)}
        >
          <option value="">{t("inventory.documents.allTypes")}</option>
          {STOCK_DOC_TYPES.map((kind) => (
            <option key={kind} value={kind}>
              {t(`inventory.docTypes.${kind}`)}
            </option>
          ))}
        </select>
        <div role="group" aria-label={t("inventory.documents.status")} className="flex gap-1">
          {STATUSES.map((s) => (
            <Button
              key={s ?? "all"}
              size="sm"
              variant={status === s ? "default" : "outline"}
              aria-pressed={status === s}
              onClick={() => setStatus(s)}
            >
              {t(`accounts.status.${s ?? "all"}`)}
            </Button>
          ))}
        </div>
      </div>
      {docs.isPending && <p>{t("common.loading")}</p>}
      {docs.isError && (
        <p role="alert" className="text-destructive">
          {t(`inventory.errors.${errorCode(docs.error)}`, { defaultValue: t("inventory.errors.generic") })}
        </p>
      )}
      {docs.data?.length === 0 && <p className="text-muted-foreground">{t("inventory.documents.none")}</p>}
      {docs.data && docs.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("inventory.doc.date")}</th>
              <th className="py-2 text-start font-medium">{t("inventory.documents.number")}</th>
              <th className="py-2 text-start font-medium">{t("inventory.documents.type")}</th>
              <th className="py-2 text-start font-medium">{t("inventory.doc.reason")}</th>
              <th className="py-2 text-end font-medium">{t("inventory.stock.value")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {docs.data.map((d) => (
              <tr key={d.id}>
                <td className="py-1.5" dir="ltr">
                  {d.doc_date}
                </td>
                <td className="py-1.5 font-mono">
                  <Link
                    to="/inventory/documents/$docId"
                    params={{ docId: d.id }}
                    className="underline-offset-2 hover:underline"
                  >
                    {d.number ?? t("accounts.status.draft")}
                  </Link>
                </td>
                <td className="py-1.5">{t(`inventory.docTypes.${d.doc_type}`)}</td>
                <td className="py-1.5">{d.reason ?? d.narration ?? ""}</td>
                <td className="py-1.5 text-end tabular-nums" dir="ltr">
                  {money(d.total)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
