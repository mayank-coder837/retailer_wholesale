import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import product from "@smartbiz/config";

import { useApi } from "@/api/use-api";
import { useSession } from "@/auth/use-session";
import { LanguageSwitch } from "@/components/language-switch";
import { cn } from "@/lib/utils";

export function HomePage() {
  const { t } = useTranslation();
  const api = useApi();
  const { accessToken } = useSession();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, error } = await api.GET("/health");
      if (error || !data) throw new Error("API not reachable");
      return data;
    },
    retry: false,
  });

  const status = health.isPending ? "checking" : health.isSuccess ? "online" : "offline";
  const statusText = {
    checking: t("home.apiChecking"),
    online: t("home.apiOnline"),
    offline: t("home.apiOffline"),
  }[status];

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col gap-6 px-4 py-10">
      <header className="flex items-center justify-between gap-4">
        <span className="text-sm font-semibold">{product.name}</span>
        <LanguageSwitch />
      </header>
      <section className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold">{t("home.welcome", { product: product.name })}</h1>
        <p className="text-muted-foreground">{t("home.tagline")}</p>
      </section>
      <p className="flex items-center gap-2 text-sm" data-testid="api-status" data-status={status}>
        <span
          aria-hidden
          className={cn(
            "inline-block size-2 rounded-full",
            status === "online" && "bg-success",
            status === "offline" && "bg-destructive",
            status === "checking" && "bg-muted-foreground",
          )}
        />
        {t("home.apiStatus")}: {statusText}
      </p>
      <nav className="flex gap-4 text-sm">
        <Link to={accessToken ? "/settings/roles" : "/login"} className="underline">
          {accessToken ? t("roles.title") : t("login.title")}
        </Link>
      </nav>
    </main>
  );
}
