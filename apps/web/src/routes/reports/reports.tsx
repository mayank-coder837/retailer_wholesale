import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { ReportsNav } from "@/routes/reports/report-parts";

/** Every report, grouped by what it answers; the statements live here, the others in their own areas. */
const GROUPS: { key: string; reports: { key: string; to: string }[] }[] = [
  {
    key: "accounts",
    reports: [
      { key: "profitLoss", to: "/reports/profit-loss" },
      { key: "balanceSheet", to: "/reports/balance-sheet" },
      { key: "trialBalance", to: "/accounts/trial-balance" },
      { key: "dayBook", to: "/reports/day-book" },
    ],
  },
  {
    key: "parties",
    reports: [
      { key: "ageing", to: "/accounts/ageing" },
      { key: "statements", to: "/parties" },
      { key: "cheques", to: "/accounts/cheques" },
    ],
  },
  {
    key: "stock",
    reports: [
      { key: "stock", to: "/inventory/stock" },
      { key: "negativeStock", to: "/inventory/negative" },
    ],
  },
  { key: "vat", reports: [{ key: "vatReturn", to: "/accounts/vat-return" }] },
];

/** Reports (M1.11b): the list of every report. */
export function ReportsPage() {
  const { t } = useTranslation();
  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("reports.title")}</h1>
      <ReportsNav />
      <div className="grid gap-8 sm:grid-cols-2">
        {GROUPS.map((g) => (
          <section key={g.key} aria-label={t(`reports.groups.${g.key}`)} className="flex flex-col gap-2">
            <h2 className="text-lg font-semibold">{t(`reports.groups.${g.key}`)}</h2>
            <ul className="flex flex-col divide-y">
              {g.reports.map((r) => (
                <li key={r.key} className="py-2">
                  <Link to={r.to} className="font-medium underline-offset-2 hover:underline">
                    {t(`reports.list.${r.key}.title`)}
                  </Link>
                  <p className="text-sm text-muted-foreground">{t(`reports.list.${r.key}.what`)}</p>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}
