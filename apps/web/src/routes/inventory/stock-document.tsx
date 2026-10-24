import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { money, useBranches } from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { InventoryNav } from "@/routes/inventory/inventory-nav";
import {
  qty,
  useStockDoc,
  useWarehouses,
  warehouseName,
  warningsKey,
  type StockWarnings,
} from "@/routes/inventory/queries";
import { useBusiness } from "@/shell/use-business";
import { CodeTag } from "@/components/code-tag";

/** Inventory → one stock document (M1.6): its lines and what they moved; a draft can be posted or deleted. */
export function StockDocumentPage() {
  const { t, i18n } = useTranslation();
  const { docId } = useParams({ strict: false }) as { docId: string };
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);
  const doc = useStockDoc(docId);
  const [lockReason, setLockReason] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<StockWarnings>(
    () => queryClient.getQueryData<StockWarnings>(warningsKey(docId)) ?? [],
  );

  const post = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/stock-documents/{doc_id}/post", {
          params: { path: { doc_id: docId } },
          body: { override_lock_reason: lockReason?.trim() || null },
        }),
      ),
    onSuccess: async (posted) => {
      setWarnings(posted.warnings ?? []);
      for (const key of ["stock-doc", "stock-docs", "stock-balances", "stock-negative", "stock-valuation"]) {
        await queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean };
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
    },
  });
  const remove = useMutation({
    mutationFn: async () => {
      const result = await api.DELETE("/stock-documents/{doc_id}", { params: { path: { doc_id: docId } } });
      if (result.error !== undefined) throw result.error;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["stock-docs"] });
      await navigate({ to: "/inventory/documents" });
    },
  });

  if (doc.isPending) return <p>{t("common.loading")}</p>;
  if (doc.isError)
    return (
      <p role="alert" className="text-destructive">
        {t(`inventory.errors.${errorCode(doc.error)}`, { defaultValue: t("inventory.errors.generic") })}
      </p>
    );
  const d = doc.data;
  const failure = post.error ?? remove.error;
  const where = (id: string | null) => (id ? warehouseName(warehouses.data, id, i18n.language) : "");

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold leading-9">
          {t(`inventory.docTypes.${d.doc_type}`)}{" "}
          <span className="font-mono" dir="ltr">
            {d.number ?? ""}
          </span>
        </h1>
        <span className="rounded-full bg-muted px-3 py-1 text-sm">{t(`accounts.status.${d.status}`)}</span>
      </div>
      <InventoryNav />
      {warnings.length > 0 && (
        <ul role="status" className="rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-sm">
          {warnings.map((w, i) => (
            <li key={i}>
              {t(`inventory.warnings.${w.code}`, {
                warehouse: where(w.warehouse_id),
                qty: w.qty_after ? qty(w.qty_after) : "",
              })}
            </li>
          ))}
        </ul>
      )}
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        <dt className="text-muted-foreground">{t("inventory.doc.date")}</dt>
        <dd dir="ltr">{d.doc_date}</dd>
        <dt className="text-muted-foreground">
          {t(d.doc_type === "stock_transfer" ? "inventory.doc.from" : "inventory.stock.warehouse")}
        </dt>
        <dd>{where(d.warehouse_id)}</dd>
        {d.to_warehouse_id && (
          <>
            <dt className="text-muted-foreground">{t("inventory.doc.to")}</dt>
            <dd>{where(d.to_warehouse_id)}</dd>
          </>
        )}
        {d.reason && (
          <>
            <dt className="text-muted-foreground">{t("inventory.doc.reason")}</dt>
            <dd>{d.reason}</dd>
          </>
        )}
        {d.status !== "draft" && (
          <>
            <dt className="text-muted-foreground">{t("inventory.stock.value")}</dt>
            <dd className="tabular-nums" dir="ltr">
              {money(d.total)}
            </dd>
          </>
        )}
      </dl>
      {d.narration && <p>{d.narration}</p>}
      <table className="w-full text-sm" aria-label={t("inventory.doc.lines")}>
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 text-start font-medium">{t("inventory.doc.item")}</th>
            {d.doc_type !== "stock_transfer" && (
              <th className="py-2 text-start font-medium">{t("inventory.doc.direction")}</th>
            )}
            <th className="py-2 text-right font-medium">{t("inventory.stock.qty")}</th>
            <th className="py-2 text-start font-medium">{t("inventory.stock.batch")}</th>
            <th className="py-2 text-right font-medium">{t("inventory.stock.value")}</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {d.lines.map((l) => (
            <tr key={l.id}>
              <td className="py-1.5">
                <CodeTag>{l.sku}</CodeTag>
                {i18n.language === "ar" ? l.name_ar : l.name_en}
              </td>
              {d.doc_type !== "stock_transfer" && (
                <td className="py-1.5">
                  {t(
                    `inventory.doc.${d.doc_type === "item_transfer" ? (l.direction === "in" ? "into" : "outOf") : l.direction}`,
                  )}
                </td>
              )}
              <td className="py-1.5 text-right tabular-nums">
                <span dir="ltr">
                  {qty(l.qty)} {l.unit}
                  {l.unit !== l.base_unit && (
                    <span className="ms-1 text-xs text-muted-foreground">
                      ({qty(l.qty_base)} {l.base_unit})
                    </span>
                  )}
                </span>
              </td>
              <td className="py-1.5">
                <span dir="ltr">{l.batch_no ?? ""}</span>
              </td>
              <td className="py-1.5 text-right tabular-nums">
                <span dir="ltr">{l.value === null || l.value === undefined ? "" : money(l.value)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.status === "draft" && (
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
              {t(`inventory.errors.${errorCode(failure)}`, {
                defaultValue: t(`accounts.errors.${errorCode(failure)}`, {
                  defaultValue: t("inventory.errors.generic"),
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
      )}
    </div>
  );
}
