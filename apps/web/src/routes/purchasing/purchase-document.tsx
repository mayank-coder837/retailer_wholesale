import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { CodeTag } from "@/components/code-tag";
import { CorrectionPanel } from "@/components/correction-panel";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { accountName, money, useAccounts, useBranches } from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { qty, useWarehouses, warehouseName } from "@/routes/inventory/queries";
import { useParty } from "@/routes/parties/queries";
import { BackToPurchases } from "@/routes/purchasing/purchases";
import { NEXT_STEPS, PURCHASE_KEYS, useOpenLines, usePurchaseDoc } from "@/routes/purchasing/queries";
import { useBusiness } from "@/shell/use-business";

/** Purchases → one document (M1.7c): its lines and totals; a draft is posted or deleted; a posted one leads to
 * the next step (receive, bill, return) and can be cancelled or reversed. */
export function PurchaseDocumentPage() {
  const { t, i18n } = useTranslation();
  const { docId } = useParams({ strict: false }) as { docId: string };
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);
  const accounts = useAccounts(current?.id);
  const doc = usePurchaseDoc(docId);
  const posted = doc.data?.status === "posted";
  const open = useOpenLines(posted ? docId : "");
  const supplier = useParty(doc.data?.party_id ?? "");
  const [lockReason, setLockReason] = useState<string | null>(null);

  const refresh = async () => {
    for (const key of PURCHASE_KEYS) await queryClient.invalidateQueries({ queryKey: [key] });
  };
  const post = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/purchase-documents/{doc_id}/post", {
          params: { path: { doc_id: docId } },
          body: { override_lock_reason: lockReason?.trim() || null },
        }),
      ),
    onSuccess: refresh,
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean };
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
    },
  });
  const remove = useMutation({
    mutationFn: async () => {
      const result = await api.DELETE("/purchase-documents/{doc_id}", {
        params: { path: { doc_id: docId } },
      });
      if (result.error !== undefined) throw result.error;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["purchase-docs"] });
      await navigate({ to: "/purchases" });
    },
  });

  if (doc.isPending) return <p>{t("common.loading")}</p>;
  if (doc.isError)
    return (
      <p role="alert" className="text-destructive">
        {t(`purchasing.errors.${errorCode(doc.error)}`, { defaultValue: t("purchasing.errors.generic") })}
      </p>
    );
  const d = doc.data;
  const failure = post.error ?? remove.error;
  const byId = new Map((accounts.data ?? []).map((a) => [a.id, a]));
  const steps = posted && (open.data?.length ?? 0) > 0 ? (NEXT_STEPS[d.doc_type] ?? []) : [];
  // A receipt carries no VAT (it comes with the bill): no VAT columns to show.
  const showVat = d.doc_type !== "goods_receipt";
  const supplierName = supplier.data
    ? i18n.language === "ar"
      ? supplier.data.name_ar
      : supplier.data.name_en
    : "";

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <BackToPurchases />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold leading-9">
          {t(`purchasing.types.${d.doc_type}`)}{" "}
          <span className="font-mono" dir="ltr">
            {d.number ?? ""}
          </span>
        </h1>
        <div className="flex gap-2 text-sm">
          {d.progress && (
            <span className="rounded-full border px-3 py-1">{t(`purchasing.progress.${d.progress}`)}</span>
          )}
          <span className="rounded-full bg-muted px-3 py-1">{t(`accounts.status.${d.status}`)}</span>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        <dt className="text-muted-foreground">{t("purchasing.doc.date")}</dt>
        <dd>
          <span dir="ltr">{d.doc_date}</span>
        </dd>
        <dt className="text-muted-foreground">{t("purchasing.doc.supplier")}</dt>
        <dd>
          {d.party_id && (
            <Link
              to="/parties/$partyId"
              params={{ partyId: d.party_id }}
              className="underline-offset-2 hover:underline"
            >
              {supplierName}
            </Link>
          )}
        </dd>
        {d.warehouse_id && (
          <>
            <dt className="text-muted-foreground">{t("inventory.stock.warehouse")}</dt>
            <dd>{warehouseName(warehouses.data, d.warehouse_id, i18n.language)}</dd>
          </>
        )}
        {d.supplier_ref && (
          <>
            <dt className="text-muted-foreground">{t("purchasing.doc.supplierRef")}</dt>
            <dd>
              <span dir="ltr">{d.supplier_ref}</span>
            </dd>
          </>
        )}
        {d.due_date && (
          <>
            <dt className="text-muted-foreground">{t("purchasing.doc.dueDate")}</dt>
            <dd>
              <span dir="ltr">{d.due_date}</span>
            </dd>
          </>
        )}
        {d.reference && (
          <>
            <dt className="text-muted-foreground">{t("accounts.voucher.reference")}</dt>
            <dd>{d.reference}</dd>
          </>
        )}
      </dl>
      {d.narration && <p>{d.narration}</p>}

      {d.lines.length > 0 && (
        <table className="w-full text-sm" aria-label={t("purchasing.doc.lines")}>
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("inventory.doc.item")}</th>
              <th className="py-2 text-right font-medium">{t("inventory.stock.qty")}</th>
              <th className="py-2 text-right font-medium">{t("purchasing.doc.price")}</th>
              <th className="py-2 text-right font-medium">{t("purchasing.doc.net")}</th>
              {showVat && (
                <>
                  <th className="py-2 text-right font-medium">{t("purchasing.doc.vat")}</th>
                  <th className="py-2 text-right font-medium">{t("purchasing.doc.total")}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y">
            {d.lines.map((l) => {
              const account = l.account_id ? byId.get(l.account_id) : undefined;
              return (
                <tr key={l.id}>
                  <td className="py-1.5">
                    {l.variant_id ? (
                      <>
                        <CodeTag>{l.sku}</CodeTag>
                        {i18n.language === "ar" ? l.name_ar : l.name_en}
                      </>
                    ) : (
                      <>
                        {account && <CodeTag>{account.code}</CodeTag>}
                        {account ? accountName(account, i18n.language) : ""}
                      </>
                    )}
                    {l.batch_no && (
                      <span className="ms-2 text-xs text-muted-foreground" dir="ltr">
                        {l.batch_no}
                      </span>
                    )}
                    {l.description && <span className="ms-2 text-muted-foreground">— {l.description}</span>}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    <span dir="ltr">
                      {qty(l.qty)} {l.unit ?? ""}
                    </span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    <span dir="ltr">{l.unit_price ? qty(l.unit_price) : ""}</span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    <span dir="ltr">{money(l.net_amount)}</span>
                  </td>
                  {showVat && (
                    <>
                      <td className="py-1.5 text-right tabular-nums">
                        <span dir="ltr">
                          {money(l.vat_amount)}
                          {Number(l.vat_amount) !== 0 && (
                            <span className="ms-1 text-xs text-muted-foreground">{qty(l.vat_rate)}%</span>
                          )}
                        </span>
                      </td>
                      <td className="py-1.5 text-right tabular-nums">
                        <span dir="ltr">{money(l.line_total)}</span>
                      </td>
                    </>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <dl
        className="ms-auto grid w-full max-w-xs grid-cols-2 gap-1 rounded-lg border p-3 text-sm"
        aria-label={t("purchasing.doc.totals")}
      >
        <dt>{t("purchasing.doc.subtotal")}</dt>
        <dd className="text-right tabular-nums">
          <span dir="ltr">{money(d.subtotal)}</span>
        </dd>
        {showVat && (
          <>
            <dt>{t("purchasing.doc.vat")}</dt>
            <dd className="text-right tabular-nums">
              <span dir="ltr">{money(d.tax_total)}</span>
            </dd>
          </>
        )}
        <dt className="font-semibold">{t("purchasing.doc.total")}</dt>
        <dd className="text-right font-semibold tabular-nums">
          <span dir="ltr">{money(d.total)}</span>
        </dd>
      </dl>

      {steps.length > 0 && (
        <div className="flex flex-wrap gap-2" role="group" aria-label={t("purchasing.nextSteps")}>
          {steps.map((kind) => (
            <Button key={kind} asChild>
              <Link to="/purchases/new/$kind" params={{ kind }} search={{ from: d.id }}>
                {t(`purchasing.next.${d.doc_type}.${kind}`)}
              </Link>
            </Button>
          ))}
        </div>
      )}

      {d.status === "draft" ? (
        <div className="flex flex-col gap-3">
          {lockReason !== null && (
            <Field label={t("accounts.voucher.lockReason")} hint={t("accounts.voucher.lockHint")}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  value={lockReason}
                  onChange={(e) => setLockReason(e.target.value)}
                />
              )}
            </Field>
          )}
          {failure && !(errorCode(failure) === "period_locked" && lockReason !== null) && (
            <p role="alert" className="text-destructive">
              {t(`purchasing.errors.${errorCode(failure)}`, {
                defaultValue: t(`inventory.errors.${errorCode(failure)}`, {
                  defaultValue: t(`accounts.errors.${errorCode(failure)}`, {
                    defaultValue: t("purchasing.errors.generic"),
                  }),
                }),
              })}
            </p>
          )}
          <div className="flex gap-2">
            <Button
              disabled={post.isPending || (lockReason !== null && !lockReason.trim())}
              onClick={() => post.mutate()}
            >
              {lockReason !== null ? t("accounts.voucher.postLocked") : t("accounts.voucher.post")}
            </Button>
            <Button variant="outline" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t("accounts.voucher.deleteDraft")}
            </Button>
          </div>
        </div>
      ) : (
        <CorrectionPanel docId={d.id} />
      )}
    </div>
  );
}
