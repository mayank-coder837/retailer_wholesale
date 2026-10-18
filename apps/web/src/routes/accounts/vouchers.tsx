import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import {
  money,
  useVouchers,
  VOUCHER_TYPES,
  type VoucherSummary,
  type VoucherType,
} from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { useBusiness } from "@/shell/use-business";

const STATUSES: (VoucherSummary["status"] | null)[] = [null, "draft", "posted"];

/** Accounts → vouchers (M1.5): newest first, filtered by type and status; a button per voucher type. */
export function VouchersPage() {
  const { t } = useTranslation();
  const { current } = useBusiness();
  const [docType, setDocType] = useState<VoucherType | null>(null);
  const [status, setStatus] = useState<VoucherSummary["status"] | null>(null);
  const vouchers = useVouchers(current?.id, { docType, status });

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold">{t("accounts.title")}</h1>
      <AccountsNav />
      <div className="flex flex-wrap gap-2" aria-label={t("accounts.vouchers.new")} role="group">
        {VOUCHER_TYPES.map((kind) => (
          <Button key={kind} asChild variant="outline" size="sm">
            <Link to="/vouchers/$kind" params={{ kind }}>
              {t(`accounts.voucherTypes.${kind}`)}
            </Link>
          </Button>
        ))}
      </div>
      <div className="flex flex-wrap gap-3">
        <select
          className="h-9 rounded-md border bg-background px-3 text-sm"
          aria-label={t("accounts.vouchers.type")}
          value={docType ?? ""}
          onChange={(e) => setDocType((e.target.value || null) as VoucherType | null)}
        >
          <option value="">{t("accounts.vouchers.allTypes")}</option>
          {VOUCHER_TYPES.map((kind) => (
            <option key={kind} value={kind}>
              {t(`accounts.voucherTypes.${kind}`)}
            </option>
          ))}
        </select>
        <div role="group" aria-label={t("accounts.vouchers.status")} className="flex gap-1">
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
      {vouchers.isPending && <p>{t("common.loading")}</p>}
      {vouchers.isError && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(vouchers.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      {vouchers.data && vouchers.data.length === 0 && (
        <p className="text-muted-foreground">{t("accounts.vouchers.none")}</p>
      )}
      {vouchers.data && vouchers.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("accounts.voucher.date")}</th>
              <th className="py-2 text-start font-medium">{t("accounts.vouchers.number")}</th>
              <th className="py-2 text-start font-medium">{t("accounts.vouchers.type")}</th>
              <th className="py-2 text-start font-medium">{t("accounts.voucher.narration")}</th>
              <th className="py-2 text-end font-medium">{t("accounts.voucher.total")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {vouchers.data.map((v) => (
              <tr key={v.id}>
                <td className="py-1.5" dir="ltr">
                  {v.doc_date}
                </td>
                <td className="py-1.5 font-mono">
                  <Link
                    to="/accounts/vouchers/$voucherId"
                    params={{ voucherId: v.id }}
                    className="underline-offset-2 hover:underline"
                  >
                    {v.number ?? t("accounts.status.draft")}
                  </Link>
                </td>
                <td className="py-1.5">{t(`accounts.voucherTypes.${v.doc_type}`)}</td>
                <td className="py-1.5">{v.narration ?? v.reference ?? ""}</td>
                <td className="py-1.5 text-end tabular-nums" dir="ltr">
                  {money(v.total)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
