import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { useSession } from "@/auth/use-session";
import { money } from "@/routes/accounts/queries";
import { useBusiness } from "@/shell/use-business";

/** The owner's daily view from concept v2: six figures for today, read from the ledgers (P2.1). */
const KPIS = ["salesToday", "receivables", "afterPdc", "chequesDue", "cashBank", "lowStock"] as const;
type Kpi = (typeof KPIS)[number];

export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const { tenantId } = useSession();
  const { current } = useBusiness();
  const me = useQuery({
    queryKey: ["me", tenantId],
    queryFn: async () => (await api.GET("/me")).data ?? null,
  });
  const figures = useQuery({
    queryKey: ["dashboard", tenantId, current?.id],
    enabled: !!current,
    // Someone without report access simply sees dashes, not an error page.
    queryFn: async () =>
      (await api.GET("/reports/dashboard", { params: { query: { business_id: current!.id } } })).data ?? null,
  });
  const businessName = current
    ? i18n.language === "ar"
      ? current.legal_name_ar
      : current.legal_name_en
    : "";
  const d = figures.data;

  function value(kpi: Kpi): string {
    if (!d) return "—";
    switch (kpi) {
      case "salesToday":
        return money(d.sales_today);
      case "receivables":
        return money(d.receivables);
      case "afterPdc":
        return money(d.after_pdc);
      case "chequesDue":
        return money(d.cheques_due_amount);
      case "cashBank":
        return money(d.cash_bank);
      case "lowStock":
        return String(d.low_stock_items);
    }
  }

  function note(kpi: Kpi): string | null {
    if (!d) return figures.isPending ? null : t("dashboard.unavailable");
    if (kpi === "chequesDue") return t("dashboard.chequeCount", { count: d.cheques_due_count });
    if (kpi === "lowStock") return t("dashboard.lowStockHint");
    return null;
  }

  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-2xl font-bold leading-9">
          {t("dashboard.welcome", { name: me.data?.user.full_name ?? "" })}
        </h1>
        <p className="text-muted-foreground">
          {businessName && `${businessName} · `}
          {t("dashboard.subtitle")}
        </p>
      </header>
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-label={t("dashboard.subtitle")}>
        {KPIS.map((kpi) => (
          <article key={kpi} className="rounded-lg border p-4" data-testid={`kpi-${kpi}`}>
            <h2 className="text-sm text-muted-foreground">{t(`dashboard.kpi.${kpi}`)}</h2>
            <p className="mt-2 text-3xl font-semibold tabular-nums">{value(kpi)}</p>
            <p className="mt-1 text-xs text-muted-foreground">{note(kpi)}</p>
          </article>
        ))}
      </section>
    </div>
  );
}
