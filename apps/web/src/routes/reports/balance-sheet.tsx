import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Field, inputClass } from "@/components/field";
import { todayIso } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { useBalanceSheet } from "@/routes/reports/queries";
import { ReportsNav, ResultRow, SectionRows } from "@/routes/reports/report-parts";
import { useBusiness } from "@/shell/use-business";

/** Reports → balance sheet (M1.11b): what the business owns and owes on a date, and the owners' share. */
export function BalanceSheetPage() {
  const { t } = useTranslation();
  const { current } = useBusiness();
  const [asOf, setAsOf] = useState(todayIso());
  const bs = useBalanceSheet(current?.id, asOf);

  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("reports.title")}</h1>
      <ReportsNav />
      <Field label={t("reports.asOf")} className="w-56">
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
      {bs.isPending && asOf && <p>{t("common.loading")}</p>}
      {bs.isError && (
        <p role="alert" className="text-destructive">
          {t(`reports.errors.${errorCode(bs.error)}`, { defaultValue: t("reports.errors.generic") })}
        </p>
      )}
      {bs.data && (
        <>
          {!bs.data.balanced && (
            <p role="alert" className="rounded-lg border border-destructive/60 bg-destructive/5 p-3 text-sm">
              {t("reports.bs.unbalanced")}
            </p>
          )}
          <table className="w-full text-sm" aria-label={t("reports.nav.balanceSheet")}>
            <tbody>
              {bs.data.assets.map((s) => (
                <SectionRows
                  key={s.key}
                  title={t(`reports.bs.${s.key}`)}
                  rows={s.rows}
                  total={s.total}
                  totalLabel={t(`reports.bs.total.${s.key}`)}
                  emptyLabel={t("reports.nothing")}
                />
              ))}
              <ResultRow label={t("reports.bs.totalAssets")} value={bs.data.total_assets} final />
              {bs.data.liabilities.map((s) => (
                <SectionRows
                  key={s.key}
                  title={t(`reports.bs.${s.key}`)}
                  rows={s.rows}
                  total={s.total}
                  totalLabel={t(`reports.bs.total.${s.key}`)}
                  emptyLabel={t("reports.nothing")}
                />
              ))}
              <SectionRows
                title={t("reports.bs.equity")}
                rows={bs.data.equity.rows}
                total={bs.data.equity.total}
                totalLabel={t("reports.bs.total.equity")}
                emptyLabel={t("reports.nothing")}
                note={t("reports.bs.notClosed")}
              />
              <ResultRow
                label={t("reports.bs.totalLiabilitiesEquity")}
                value={addAmounts(bs.data.total_liabilities, bs.data.total_equity)}
                final
              />
            </tbody>
          </table>
          <p className="text-sm text-muted-foreground">
            {t("reports.bs.yearNote", { start: bs.data.fiscal_year_start })}
          </p>
        </>
      )}
    </div>
  );
}

/** The exact sum of two 4-decimal amounts (no floats for money). */
function addAmounts(a: string, b: string): string {
  const scaled = (s: string) => {
    const negative = s.trim().startsWith("-");
    const [whole = "0", fraction = ""] = s.trim().replace(/^[-+]/, "").split(".");
    const units = BigInt(whole || "0") * 10000n + BigInt((fraction + "0000").slice(0, 4));
    return negative ? -units : units;
  };
  const sum = scaled(a) + scaled(b);
  const abs = sum < 0n ? -sum : sum;
  return `${sum < 0n ? "-" : ""}${abs / 10000n}.${String(abs % 10000n).padStart(4, "0")}`;
}
