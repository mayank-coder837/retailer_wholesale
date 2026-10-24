import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { useDocState } from "@/components/doc-state";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { todayIso } from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";

type DocState = components["schemas"]["CorrectedOut"];
type Linked = components["schemas"]["LinkedDocOut"];

const PURCHASE = ["purchase_order", "goods_receipt", "purchase_bill", "purchase_return"];
const STOCK = ["stock_adjustment", "stock_transfer", "item_transfer"];
/** Commitments: nothing in the books to reverse, so they are only cancelled (as the API decides). */
const CANCEL_ONLY = ["quotation", "sales_order", "proforma_invoice", "purchase_order"];

/** A link to any document's own page, by its type. */
export function DocLink({ doc }: { doc: Pick<Linked, "id" | "doc_type" | "number"> }) {
  const label = (
    <span className="font-mono" dir="ltr">
      {doc.number ?? ""}
    </span>
  );
  const className = "underline underline-offset-2";
  if (PURCHASE.includes(doc.doc_type))
    return (
      <Link to="/purchases/$docId" params={{ docId: doc.id }} className={className}>
        {label}
      </Link>
    );
  if (STOCK.includes(doc.doc_type))
    return (
      <Link to="/inventory/documents/$docId" params={{ docId: doc.id }} className={className}>
        {label}
      </Link>
    );
  return (
    <Link to="/accounts/vouchers/$voucherId" params={{ voucherId: doc.id }} className={className}>
      {label}
    </Link>
  );
}

/**
 * Cancel and Reverse on a document's page (posting rules 32 and 33): where the document stands, and the two
 * actions while it is posted. Cancel removes it as if it never happened (open period only) and frees its number;
 * Reverse writes a dated reversal with its own number.
 */
export function CorrectionPanel({ docId }: { docId: string }) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const state = useDocState(docId);
  const [mode, setMode] = useState<"cancel" | "reverse" | null>(null);
  const [reason, setReason] = useState("");
  const [date, setDate] = useState(todayIso());
  const [lockReason, setLockReason] = useState<string | null>(null);

  const act = useMutation({
    mutationFn: async () => {
      if (mode === "cancel")
        return unwrap(
          await api.POST("/documents/{doc_id}/cancel", {
            params: { path: { doc_id: docId } },
            body: { reason: reason.trim() },
          }),
        );
      return unwrap(
        await api.POST("/documents/{doc_id}/reverse", {
          params: { path: { doc_id: docId } },
          body: { reason: reason.trim(), date, override_lock_reason: lockReason?.trim() || null },
        }),
      );
    },
    onSuccess: async () => {
      setMode(null);
      setReason("");
      setLockReason(null);
      // Ledgers, stock, payables and lists all change: refresh everything on screen.
      await queryClient.invalidateQueries();
    },
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean };
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
    },
  });

  if (!state.data) return null;
  const s = state.data;
  const failure = act.isError ? errorCode(act.error) : null;
  const ready = reason.trim() !== "" && (lockReason === null || lockReason.trim() !== "");

  return (
    <section aria-label={t("corrections.title")} className="flex flex-col gap-3">
      <StateNotes state={s} />
      {s.status === "posted" && !s.reversal_of && mode === null && (
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => setMode("cancel")}>
            {t("corrections.cancel")}
          </Button>
          {!CANCEL_ONLY.includes(s.doc_type) && (
            <Button variant="outline" onClick={() => setMode("reverse")}>
              {t("corrections.reverse")}
            </Button>
          )}
        </div>
      )}
      {mode !== null && (
        <form
          className="flex flex-col gap-3 rounded-lg border p-4"
          aria-label={t(`corrections.${mode}`)}
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) act.mutate();
          }}
        >
          <h2 className="font-semibold">{t(`corrections.${mode}Title`, { number: s.number ?? "" })}</h2>
          <p className="text-sm text-muted-foreground">{t(`corrections.${mode}Hint`)}</p>
          <div className="grid items-start gap-3 sm:grid-cols-2">
            <Field label={t("corrections.reason")}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              )}
            </Field>
            {mode === "reverse" && (
              <Field label={t("corrections.date")} hint={t("corrections.dateHint")}>
                {(p) => (
                  <input
                    {...p}
                    type="date"
                    className={inputClass}
                    min={s.doc_date}
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                  />
                )}
              </Field>
            )}
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
          </div>
          {failure && !(failure === "period_locked" && lockReason !== null) && (
            <p role="alert" className="text-sm text-destructive">
              {t(`corrections.errors.${failure}`, {
                defaultValue: t(`accounts.errors.${failure}`, {
                  defaultValue: t("corrections.errors.generic"),
                }),
                documents: ((act.error as { documents?: string[] }).documents ?? []).join(", "),
              })}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" disabled={!ready || act.isPending}>
              {t(`corrections.confirm.${mode}`)}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setMode(null)}>
              {t("corrections.back")}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}

function StateNotes({ state }: { state: DocState }) {
  const { t } = useTranslation();
  const notes = [];
  if (state.status === "cancelled")
    notes.push(
      <p key="cancelled">
        {t("corrections.cancelledOn", { date: state.cancelled_at?.slice(0, 10) ?? "" })}
        {state.cancel_reason && <span className="text-muted-foreground"> — {state.cancel_reason}</span>}
      </p>,
    );
  if (state.status === "reversed" && state.reversal_id)
    notes.push(
      <p key="reversed">
        {t("corrections.reversedBy")}{" "}
        <DocLink
          doc={{
            id: state.reversal_id,
            doc_type: state.reversal_doc_type ?? "",
            number: state.reversal_number ?? null,
          }}
        />{" "}
        <span dir="ltr">({state.reversal_date})</span>
      </p>,
    );
  if (state.reversal_of)
    notes.push(
      <p key="reverses">
        {t("corrections.reverses")} <DocLink doc={state.reversal_of} />
      </p>,
    );
  if (state.reissue_of)
    notes.push(
      <p key="reissue">
        {t("corrections.reissueOf")} <DocLink doc={state.reissue_of} />
      </p>,
    );
  if (notes.length === 0) return null;
  return <div className="flex flex-col gap-1 rounded-lg border bg-muted/40 p-3 text-sm">{notes}</div>;
}
