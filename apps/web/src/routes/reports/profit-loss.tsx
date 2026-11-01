import { useState } from "react";
import { useTranslation } from "react-i18next";

import { todayIso } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { presetPeriod, useProfitLoss, type Period, type Preset } from "@/routes/reports/queries";
import { PeriodPicker, ReportsNav, ResultRow, SectionRows } from "@/routes/reports/report-parts";
import { useBusiness } from "@/shell/use-business";

/** Reports → profit and loss (M1.11b): sales − cost of goods sold = gross profit; + other income − expenses. */
export function ProfitLossPage() {
  const { t } = useTranslation();
  const { current } = useBusiness();
  const [preset, setPreset] = useState<Preset>("this_month");
  const [custom, setCustom] = useState<Period>({ from: "", to: "" });
  const period: Period | null =
    preset === "custom"
      ? custom.from && custom.to
        ? custom
        : null
      : presetPeriod(preset, todayIso(), current?.fiscal_year_start_month ?? 1);
  const pl = useProfitLoss(current?.id, period);
  const net = pl.data?.net_profit ?? "0";
  const loss = net.trim().startsWith("-");

  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("reports.title")}</h1>
      <ReportsNav />
      <PeriodPicker preset={preset} custom={custom} onPreset={setPreset} onCustom={setCustom} />
      {!period && <p className="text-muted-foreground">{t("reports.pickDates")}</p>}
      {period && pl.isPending && <p>{t("common.loading")}</p>}
      {pl.isError && (
        <p role="alert" className="text-destructive">
          {t(`reports.errors.${errorCode(pl.error)}`, { defaultValue: t("reports.errors.generic") })}
        </p>
      )}
      {period && pl.data && (
        <>
          <p className="text-sm text-muted-foreground">
            {t("reports.periodShown", { from: pl.data.date_from, to: pl.data.date_to })}
          </p>
          <table className="w-full text-sm" aria-label={t("reports.nav.profitLoss")}>
            <tbody>
              {pl.data.sections
                .map((s) => (
                  <SectionRows
                    key={s.key}
                    title={t(`reports.pl.${s.key}`)}
                    rows={s.rows}
                    total={s.total}
                    totalLabel={t(`reports.pl.total.${s.key}`)}
                    emptyLabel={t("reports.nothing")}
                  />
                ))
                .flatMap((section, i) =>
                  i === 1
                    ? [
                        section,
                        <ResultRow
                          key="gross"
                          label={t("reports.pl.grossProfit")}
                          value={pl.data.gross_profit}
                        />,
                      ]
                    : [section],
                )}
              <ResultRow
                label={loss ? t("reports.pl.netLoss") : t("reports.pl.netProfit")}
                value={net}
                final
              />
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
