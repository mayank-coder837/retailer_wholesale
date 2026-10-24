import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { money } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { useParties } from "@/routes/parties/queries";
import {
  PURCHASE_DOC_TYPES,
  usePurchaseDocs,
  type PurchaseDocSummary,
  type PurchaseDocType,
} from "@/routes/purchasing/queries";
import { useBusiness } from "@/shell/use-business";

const STATUSES: (PurchaseDocSummary["status"] | null)[] = [null, "draft", "posted", "cancelled", "reversed"];
/** A return always starts from the bill or receipt it returns, so it has no button here. */
const STARTABLE: PurchaseDocType[] = ["purchase_order", "goods_receipt", "purchase_bill"];

/** The way back from a purchase document or its entry form. */
export function BackToPurchases() {
  const { t } = useTranslation();
  return (
    <Link to="/purchases" className="text-sm text-muted-foreground underline-offset-2 hover:underline">
      {t("purchasing.back")}
    </Link>
  );
}

/** Purchases (M1.7c): orders, goods receipts, supplier bills and returns, newest first. */
export function PurchasesPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const [docType, setDocType] = useState<PurchaseDocType | null>(null);
  const [status, setStatus] = useState<PurchaseDocSummary["status"] | null>(null);
  const docs = usePurchaseDocs(current?.id, { docType, status });
  const suppliers = useParties("", "supplier");
  const supplierName = (id: string | null) => {
    const s = suppliers.data?.find((p) => p.id === id);
    return s ? (i18n.language === "ar" ? s.name_ar : s.name_en) : "";
  };

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("purchasing.title")}</h1>
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("purchasing.new")}>
        {STARTABLE.map((kind) => (
          <Button key={kind} asChild variant="outline">
            <Link to="/purchases/new/$kind" params={{ kind }}>
              {t(`purchasing.newTypes.${kind}`)}
            </Link>
          </Button>
        ))}
      </div>
      <p className="text-sm text-muted-foreground">{t("purchasing.flowHint")}</p>
      <nav aria-label={t("purchasing.type")} className="flex flex-wrap gap-1 border-b pb-2 text-sm">
        {[null, ...PURCHASE_DOC_TYPES].map((kind) => (
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
            {kind ? t(`purchasing.plural.${kind}`) : t("purchasing.all")}
          </button>
        ))}
      </nav>
      <div role="group" aria-label={t("purchasing.status")} className="flex flex-wrap gap-1">
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
          {t(`purchasing.errors.${errorCode(docs.error)}`, { defaultValue: t("purchasing.errors.generic") })}
        </p>
      )}
      {docs.data?.length === 0 && <p className="text-muted-foreground">{t("purchasing.none")}</p>}
      {docs.data && docs.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("purchasing.doc.date")}</th>
              <th className="py-2 text-start font-medium">{t("purchasing.doc.number")}</th>
              <th className="py-2 text-start font-medium">{t("purchasing.type")}</th>
              <th className="py-2 text-start font-medium">{t("purchasing.doc.supplier")}</th>
              <th className="py-2 text-start font-medium">{t("purchasing.status")}</th>
              <th className="py-2 text-right font-medium">{t("purchasing.doc.total")}</th>
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
                    to="/purchases/$docId"
                    params={{ docId: d.id }}
                    className="underline-offset-2 hover:underline"
                  >
                    <span dir="ltr">{d.number ?? t("accounts.status.draft")}</span>
                  </Link>
                </td>
                <td className="py-1.5">{t(`purchasing.types.${d.doc_type}`)}</td>
                <td className="py-1.5">{supplierName(d.party_id)}</td>
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
