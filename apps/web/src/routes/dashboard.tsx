import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { useSession } from "@/auth/use-session";
import { useBusiness } from "@/shell/use-business";

/** The owner's daily view from concept v2. Figures arrive with the Phase 1 ledgers; the layout is final. */
const KPIS = ["salesToday", "receivables", "afterPdc", "chequesDue", "cashBank", "lowStock"] as const;

export function DashboardPage() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const { tenantId } = useSession();
  const { current } = useBusiness();
  const me = useQuery({
    queryKey: ["me", tenantId],
    queryFn: async () => (await api.GET("/me")).data ?? null,
  });
  const businessName = current
    ? i18n.language === "ar"
      ? current.legal_name_ar
      : current.legal_name_en
    : "";

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
            <p className="mt-2 text-3xl font-semibold tabular-nums">—</p>
            <p className="mt-1 text-xs text-muted-foreground">{t("dashboard.phase1")}</p>
          </article>
        ))}
      </section>
    </div>
  );
}
