import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { money } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { useParties } from "@/routes/parties/queries";
import {
  SALES_DOC_TYPES,
  useSalesDocs,
  type SalesDocSummary,
  type SalesDocType,
} from "@/routes/sales/queries";
import { useBusiness } from "@/shell/use-business";

const STATUSES: (SalesDocSummary["status"] | null)[] = [null, "draft", "posted", "cancelled", "reversed"];
/** A return always starts from the invoice it returns, so it has no button here. */
const STARTABLE: SalesDocType[] = [
  "quotation",
  "sales_order",
  "delivery_note",
  "sales_invoice",
  "proforma_invoice",
];

/** The way back from a sales document or its entry form. */
export function BackToSales() {
  const { t } = useTranslation();
  return (
    <Link to="/sales" className="text-sm text-muted-foreground underline-offset-2 hover:underline">
      {t("sales.back")}
    </Link>
  );
}

/** Sales (M1.8c): quotations, proformas, orders, delivery notes, invoices and returns, newest first. */
export function SalesPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const [docType, setDocType] = useState<SalesDocType | null>(null);
  const [status, setStatus] = useState<SalesDocSummary["status"] | null>(null);
  const docs = useSalesDocs(current?.id, { docType, status });
  const customers = useParties("", "customer");
  const customerName = (id: string | null) => {
    const s = customers.data?.find((p) => p.id === id);
    return s ? (i18n.language === "ar" ? s.name_ar : s.name_en) : "";
  };

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("sales.title")}</h1>
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("sales.new")}>
        {STARTABLE.map((kind) => (
          <Button key={kind} asChild variant="outline">
            <Link to="/sales/new/$kind" params={{ kind }}>
              {t(`sales.newTypes.${kind}`)}
            </Link>
          </Button>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">{t("sales.flowHint")}</p>
      <nav aria-label={t("sales.type")} className="flex flex-wrap gap-1 border-b pb-2 text-sm">
        {[null, ...SALES_DOC_TYPES].map((kind) => (
          <button
            key={kind ?? "all"}
            type="button"
            aria-pressed={docType === kind}
            className={
              "rounded-md px-3 py-1.5 hover:bg-muted" +
              (docType === kind ? " bg-secondary font-semibold" : "")
            }
            onClick={() => setDocType(kind)}
          >
            {kind ? t(`sales.plural.${kind}`) : t("sales.all")}
          </button>
        ))}
      </nav>
      <div role="group" aria-label={t("sales.status")} className="flex flex-wrap gap-1">
        {STATUSES.map((s) => (
          <Button
            key={s ?? "all"}
            variant={status === s ? "default" : "outline"}
            aria-pressed={status === s}
            onClick={() => setStatus(s)}
          >
            {t(`accounts.status.${s ?? "all"}`)}
          </Button>
        ))}
      </div>
      {docs.isPending && <p>{t("common.loading")}</p>}
      {docs.isError && (
        <p role="alert" className="text-destructive">
          {t(`sales.errors.${errorCode(docs.error)}`, { defaultValue: t("sales.errors.generic") })}
        </p>
      )}
      {docs.data?.length === 0 && <p className="text-muted-foreground">{t("sales.none")}</p>}
      {docs.data && docs.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("sales.doc.date")}</th>
              <th className="py-2 text-start font-medium">{t("sales.doc.number")}</th>
              <th className="py-2 text-start font-medium">{t("sales.type")}</th>
              <th className="py-2 text-start font-medium">{t("sales.doc.customer")}</th>
              <th className="py-2 text-start font-medium">{t("sales.status")}</th>
              <th className="py-2 text-right font-medium">{t("sales.doc.total")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {docs.data.map((d) => (
              <tr key={d.id}>
                <td className="py-1.5">
                  <span dir="ltr">{d.doc_date}</span>
                </td>
                <td className="py-1.5 font-mono">
                  <Link
                    to="/sales/$docId"
                    params={{ docId: d.id }}
                    className="underline-offset-2 hover:underline"
                  >
                    <span dir="ltr">{d.number ?? t("accounts.status.draft")}</span>
                  </Link>
                </td>
                <td className="py-1.5">{t(`sales.types.${d.doc_type}`)}</td>
                <td className="py-1.5">{customerName(d.party_id)}</td>
                <td className="py-1.5">{t(`accounts.status.${d.status}`)}</td>
                <td className="py-1.5 text-right tabular-nums">
                  <span dir="ltr">{money(d.total)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
