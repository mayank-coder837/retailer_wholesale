import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Field, inputClass } from "@/components/field";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import { accountName, money, useTrialBalance } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { useBusiness } from "@/shell/use-business";
import { CodeTag } from "@/components/code-tag";

/** Accounts → trial balance (M1.5): every account's balance on a day; the two columns are always equal. */
export function TrialBalancePage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const [asOf, setAsOf] = useState("");
  const tb = useTrialBalance(current?.id, asOf);

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("accounts.title")}</h1>
      <AccountsNav />
      <Field label={t("accounts.tb.asOf")} hint={t("accounts.tb.asOfHint")} className="w-56">
        {(p) => (
          <input
            {...p}
            type="date"
            className={inputClass}
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
          />
        )}
      </Field>
      {tb.isPending && <p>{t("common.loading")}</p>}
      {tb.isError && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(tb.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      {tb.data && tb.data.rows.length === 0 && (
        <p className="text-muted-foreground">{t("accounts.tb.empty")}</p>
      )}
      {tb.data && tb.data.rows.length > 0 && (
        <table className="w-full text-sm" aria-label={t("accounts.nav.trialBalance")}>
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("accounts.chart.account")}</th>
              <th className="py-2 text-right font-medium">{t("accounts.dr")}</th>
              <th className="py-2 text-right font-medium">{t("accounts.cr")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {tb.data.rows.map((r) => (
              <tr key={r.account_id}>
                <td className="py-1.5">
                  <CodeTag>{r.code}</CodeTag>
                  <Link
                    to="/accounts/$accountId"
                    params={{ accountId: r.account_id }}
                    className="underline-offset-2 hover:underline"
                  >
                    {accountName(r, i18n.language)}
                  </Link>
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  <span dir="ltr">{r.debit === "0.0000" ? "" : money(r.debit)}</span>
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  <span dir="ltr">{r.credit === "0.0000" ? "" : money(r.credit)}</span>
                </td>
              </tr>
            ))}
            <tr className="font-semibold">
              <td className="py-1.5">{t("accounts.tb.total")}</td>
              <td className="py-1.5 text-right tabular-nums">
                <span dir="ltr">{money(tb.data.total_debit)}</span>
              </td>
              <td className="py-1.5 text-right tabular-nums">
                <span dir="ltr">{money(tb.data.total_credit)}</span>
              </td>
            </tr>
          </tbody>
        </table>
      )}
    </div>
  );
}
