import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { CodeTag } from "@/components/code-tag";
import { CorrectionPanel } from "@/components/correction-panel";
import { useDocState } from "@/components/doc-state";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { accountLabel, money, useAccounts, useBranches } from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { qty, useWarehouses, warehouseName } from "@/routes/inventory/queries";
import { useParty } from "@/routes/parties/queries";
import {
  NEXT_STEPS,
  SALES_KEYS,
  salesWarningsKey,
  useSalesDoc,
  useSalesOpenLines,
  type SalesWarning,
} from "@/routes/sales/queries";
import { BackToSales } from "@/routes/sales/sales";
import { useBusiness } from "@/shell/use-business";

/** Documents with a printed form (M1.10c): the tax invoice and the tax credit note. */
const PRINTABLE = ["sales_invoice", "sales_return"];

/** Sales → one document (M1.8c): its lines and totals; a draft is posted or deleted; a posted one leads to the
 * next step, can be marked as sent, and cancelled or reversed. */
export function SalesDocumentPage() {
  const { t, i18n } = useTranslation();
  const { docId } = useParams({ strict: false }) as { docId: string };
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);
  const accounts = useAccounts(current?.id);
  const doc = useSalesDoc(docId);
  const posted = doc.data?.status === "posted";
  const open = useSalesOpenLines(posted ? docId : "");
  const state = useDocState(doc.data && doc.data.status !== "draft" ? docId : "");
  const customer = useParty(doc.data?.party_id ?? "");
  const [lockReason, setLockReason] = useState<string | null>(null);
  const [creditBlocked, setCreditBlocked] = useState<{ may_override: boolean } | null>(null);
  const [creditReason, setCreditReason] = useState("");
  const [warnings, setWarnings] = useState<SalesWarning[]>(
    () => queryClient.getQueryData<SalesWarning[]>(salesWarningsKey(docId)) ?? [],
  );

  const refresh = async () => {
    for (const key of SALES_KEYS) await queryClient.invalidateQueries({ queryKey: [key] });
  };
  const post = useMutation({
    mutationFn: async (override: boolean) =>
      unwrap(
        await api.POST("/sales-documents/{doc_id}/post", {
          params: { path: { doc_id: docId } },
          body: {
            override_lock_reason: lockReason?.trim() || null,
            credit_override_reason: override ? creditReason.trim() : null,
          },
        }),
      ),
    onSuccess: async (result) => {
      setWarnings(result.warnings ?? []);
      setCreditBlocked(null);
      await refresh();
    },
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean };
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
      if (body.code === "credit_blocked") setCreditBlocked({ may_override: !!body.may_override });
    },
  });
  const remove = useMutation({
    mutationFn: async () => {
      const result = await api.DELETE("/sales-documents/{doc_id}", { params: { path: { doc_id: docId } } });
      if (result.error !== undefined) throw result.error;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["sales-docs"] });
      await navigate({ to: "/sales" });
    },
  });
  const sent = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/documents/{doc_id}/sent", { params: { path: { doc_id: docId } } })),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["doc-state", docId] });
    },
  });

  if (doc.isPending) return <p>{t("common.loading")}</p>;
  if (doc.isError)
    return (
      <p role="alert" className="text-destructive">
        {t(`sales.errors.${errorCode(doc.error)}`, { defaultValue: t("sales.errors.generic") })}
      </p>
    );
  const d = doc.data;
  const failure = post.error ?? remove.error;
  const steps = posted && (open.data?.length ?? 0) > 0 ? (NEXT_STEPS[d.doc_type] ?? []) : [];
  const priced = d.doc_type !== "delivery_note";
  const customerName = customer.data
    ? i18n.language === "ar"
      ? customer.data.name_ar
      : customer.data.name_en
    : "";
  const cashAccount = d.cash_account_id ? accounts.data?.find((a) => a.id === d.cash_account_id) : undefined;

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <BackToSales />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold leading-9">
          {t(`sales.types.${d.doc_type}`)}{" "}
          <span className="font-mono" dir="ltr">
            {d.number ?? ""}
          </span>
        </h1>
        <div className="flex gap-2 text-sm">
          {d.progress && (
            <span className="rounded-full border px-3 py-1">{t(`sales.progress.${d.progress}`)}</span>
          )}
          {state.data?.sent_at && <span className="rounded-full border px-3 py-1">{t("sales.sent")}</span>}
          <span className="rounded-full bg-muted px-3 py-1">{t(`accounts.status.${d.status}`)}</span>
        </div>
      </div>
      {warnings.length > 0 && (
        <ul role="status" className="rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-sm">
          {warnings.map((w, i) => (
            <li key={i}>
              {t(`sales.warnings.${w.code}`, {
                reasons: w.reasons.map((r) => t(`sales.credit.reasons.${r}`, { defaultValue: r })).join(", "),
              })}
            </li>
          ))}
        </ul>
      )}
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        <dt className="text-muted-foreground">{t("sales.doc.date")}</dt>
        <dd>
          <span dir="ltr">{d.doc_date}</span>
        </dd>
        <dt className="text-muted-foreground">{t("sales.doc.customer")}</dt>
        <dd>
          {d.party_id && (
            <Link
              to="/parties/$partyId"
              params={{ partyId: d.party_id }}
              className="underline-offset-2 hover:underline"
            >
              {customerName}
            </Link>
          )}
        </dd>
        {d.warehouse_id && (
          <>
            <dt className="text-muted-foreground">{t("inventory.stock.warehouse")}</dt>
            <dd>{warehouseName(warehouses.data, d.warehouse_id, i18n.language)}</dd>
          </>
        )}
        {cashAccount && (
          <>
            <dt className="text-muted-foreground">{t("sales.doc.paidInto")}</dt>
            <dd>{accountLabel(cashAccount, i18n.language)}</dd>
          </>
        )}
        {d.due_date && (
          <>
            <dt className="text-muted-foreground">{t("sales.doc.dueDate")}</dt>
            <dd>
              <span dir="ltr">{d.due_date}</span>
            </dd>
          </>
        )}
        {d.valid_until && (
          <>
            <dt className="text-muted-foreground">{t("sales.doc.validUntil")}</dt>
            <dd>
              <span dir="ltr">{d.valid_until}</span>
            </dd>
          </>
        )}
        {d.reference && (
          <>
            <dt className="text-muted-foreground">{t("sales.doc.customerRef")}</dt>
            <dd>{d.reference}</dd>
          </>
        )}
      </dl>
      {d.narration && <p>{d.narration}</p>}

      {d.lines.length > 0 && (
        <table className="w-full text-sm" aria-label={t("sales.doc.lines")}>
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("inventory.doc.item")}</th>
              <th className="py-2 text-right font-medium">{t("inventory.stock.qty")}</th>
              {priced && (
                <>
                  <th className="py-2 text-right font-medium">{t("sales.doc.price")}</th>
                  <th className="py-2 text-right font-medium">{t("sales.doc.net")}</th>
                  <th className="py-2 text-right font-medium">{t("sales.doc.vat")}</th>
                  <th className="py-2 text-right font-medium">{t("sales.doc.total")}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody className="divide-y">
            {d.lines.map((l) => (
              <tr key={l.id}>
                <td className="py-1.5">
                  <CodeTag>{l.sku}</CodeTag>
                  {i18n.language === "ar" ? l.name_ar : l.name_en}
                  {l.below_min && (
                    <span className="ms-2 rounded border border-amber-500/60 px-1 text-xs">
                      {t("sales.belowMinFlag")}
                    </span>
                  )}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  <span dir="ltr">
                    {qty(l.qty)} {l.unit ?? ""}
                  </span>
                </td>
                {priced && (
                  <>
                    <td className="py-1.5 text-right tabular-nums">
                      <span dir="ltr">{l.unit_price ? qty(l.unit_price) : ""}</span>
                    </td>
                    <td className="py-1.5 text-right tabular-nums">
                      <span dir="ltr">{money(l.net_amount)}</span>
                    </td>
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
            ))}
          </tbody>
        </table>
      )}
      {priced && (
        <dl
          className="ms-auto grid w-full max-w-xs grid-cols-2 gap-1 rounded-lg border p-3 text-sm"
          aria-label={t("sales.doc.totals")}
        >
          <dt>{t("sales.doc.subtotal")}</dt>
          <dd className="text-right tabular-nums">
            <span dir="ltr">{money(d.subtotal)}</span>
          </dd>
          <dt>{t("sales.doc.vat")}</dt>
          <dd className="text-right tabular-nums">
            <span dir="ltr">{money(d.tax_total)}</span>
          </dd>
          {Number(d.round_off) !== 0 && (
            <>
              <dt>{t("sales.doc.roundOff")}</dt>
              <dd className="text-right tabular-nums">
                <span dir="ltr">{money(d.round_off)}</span>
              </dd>
            </>
          )}
          <dt className="font-semibold">{t("sales.doc.total")}</dt>
          <dd className="text-right font-semibold tabular-nums">
            <span dir="ltr">{money(d.total)}</span>
          </dd>
        </dl>
      )}

      {steps.length > 0 && (
        <div className="flex flex-wrap gap-2" role="group" aria-label={t("sales.nextSteps")}>
          {steps.map((kind) => (
            <Button key={kind} asChild>
              <Link to="/sales/new/$kind" params={{ kind }} search={{ from: d.id }}>
                {t(`sales.next.${d.doc_type}.${kind}`)}
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
          {creditBlocked && (
            <div
              role="alert"
              className="flex flex-col gap-3 rounded-lg border border-destructive/60 bg-destructive/5 p-4 text-sm"
            >
              <p className="font-semibold">{t("sales.credit.blocked")}</p>
              {creditBlocked.may_override ? (
                <div className="flex flex-wrap items-end gap-2">
                  <Field label={t("sales.credit.reason")} className="min-w-72 flex-1">
                    {(p) => (
                      <input
                        {...p}
                        className={inputClass}
                        value={creditReason}
                        onChange={(e) => setCreditReason(e.target.value)}
                      />
                    )}
                  </Field>
                  <Button disabled={!creditReason.trim() || post.isPending} onClick={() => post.mutate(true)}>
                    {t("sales.credit.postAnyway")}
                  </Button>
                </div>
              ) : (
                <p>{t("sales.credit.askSomeone")}</p>
              )}
            </div>
          )}
          {failure &&
            errorCode(failure) !== "credit_blocked" &&
            !(errorCode(failure) === "period_locked" && lockReason !== null) && (
              <p role="alert" className="text-destructive">
                {t(`sales.errors.${errorCode(failure)}`, {
                  defaultValue: t(`inventory.errors.${errorCode(failure)}`, {
                    defaultValue: t(`accounts.errors.${errorCode(failure)}`, {
                      defaultValue: t("sales.errors.generic"),
                    }),
                  }),
                })}
              </p>
            )}
          <div className="flex gap-2">
            <Button
              disabled={post.isPending || (lockReason !== null && !lockReason.trim())}
              onClick={() => post.mutate(false)}
            >
              {lockReason !== null ? t("accounts.voucher.postLocked") : t("accounts.voucher.post")}
            </Button>
            <Button variant="outline" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t("accounts.voucher.deleteDraft")}
            </Button>
          </div>
        </div>
      ) : (
        <>
          {posted && PRINTABLE.includes(d.doc_type) && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Button variant="outline" asChild>
                <Link to="/sales/$docId/print" params={{ docId: d.id }}>
                  {t("print.open")}
                </Link>
              </Button>
              <span className="text-muted-foreground">{t("print.openHint")}</span>
            </div>
          )}
          {posted && !state.data?.sent_at && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Button variant="outline" disabled={sent.isPending} onClick={() => sent.mutate()}>
                {t("sales.markSent")}
              </Button>
              <span className="text-muted-foreground">{t("sales.markSentHint")}</span>
            </div>
          )}
          <CorrectionPanel docId={d.id} />
        </>
      )}
    </div>
  );
}
