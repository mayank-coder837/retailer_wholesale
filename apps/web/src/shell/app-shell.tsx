import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate, Outlet, useNavigate } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import product from "@smartbiz/config";

import { useApi } from "@/api/use-api";
import { useSession } from "@/auth/use-session";
import { inputClass } from "@/components/field";
import { LanguageSwitch } from "@/components/language-switch";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ServerStatus } from "@/shell/server-status";
import { useDesktopShortcuts } from "@/shell/shortcuts";
import { BusinessContext, rememberedBusinessKey, type Business } from "@/shell/use-business";

const NAV = [
  { to: "/dashboard", key: "dashboard" },
  { to: "/sales", key: "sales" },
  { to: "/purchases", key: "purchases" },
  { to: "/parties", key: "parties" },
  { to: "/inventory", key: "inventory" },
  { to: "/accounts", key: "accounts" },
  { to: "/reports", key: "reports" },
] as const;

const SETTINGS = [
  { to: "/settings/roles", key: "roles" },
  { to: "/settings/catalog", key: "catalog" },
  { to: "/settings/pricing", key: "pricing" },
] as const;

/** A sidebar entry; on a phone, choosing one folds the menu away. */
function MenuLink({ to, onPick, children }: { to: string; onPick: () => void; children: ReactNode }) {
  return (
    <Link
      to={to}
      onClick={onPick}
      className="rounded-md px-3 py-2 hover:bg-muted"
      activeProps={{ className: "bg-secondary font-semibold", "aria-current": "page" }}
    >
      {children}
    </Link>
  );
}

function readRemembered(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

/** The signed-in layout: guard, sidebar, top bar (business and account switchers), and the page. */
export function AppShell() {
  const { t } = useTranslation();
  const { status } = useSession();
  if (status === "loading") {
    return <p className="p-10 text-sm text-muted-foreground">{t("common.loading")}</p>;
  }
  if (status === "signed_out") return <Navigate to="/login" />;
  return <SignedInShell />;
}

function SignedInShell() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const session = useSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  useDesktopShortcuts();
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = () => setMenuOpen(false);

  const me = useQuery({
    queryKey: ["me", session.tenantId],
    queryFn: async () => {
      const { data, error } = await api.GET("/me");
      if (error || !data) throw error ?? new Error("me");
      return data;
    },
  });
  const businesses = useQuery({
    queryKey: ["businesses", session.tenantId],
    queryFn: async () => {
      const { data, error } = await api.GET("/businesses");
      if (error || !data) throw error ?? new Error("businesses");
      return data;
    },
  });

  // The choice is remembered per account; switching account reads that account's remembered business.
  const storageKey = rememberedBusinessKey(session.tenantId ?? "none");
  const [choice, setChoice] = useState<{ key: string; id: string } | null>(null);
  const selectedId = choice?.key === storageKey ? choice.id : readRemembered(storageKey);

  const list: Business[] = businesses.data ?? [];
  const businessValue = {
    businesses: list,
    current: list.find((b) => b.id === selectedId) ?? list[0] ?? null,
    select(id: string) {
      setChoice({ key: storageKey, id });
      try {
        localStorage.setItem(storageKey, id);
      } catch {
        // Not remembered; the choice still applies now.
      }
    },
  };

  async function switchAccount(tenantId: string) {
    const result = await session.switchTenant(tenantId);
    if (result.ok) {
      queryClient.clear();
      await navigate({ to: "/dashboard" });
    }
  }

  async function logout() {
    await session.logout();
    queryClient.clear();
    await navigate({ to: "/login" });
  }

  const businessName = (b: Business) => (i18n.language === "ar" ? b.legal_name_ar : b.legal_name_en);
  const memberships = me.data?.memberships ?? [];

  return (
    <BusinessContext.Provider value={businessValue}>
      <div className="flex min-h-screen flex-col md:flex-row">
        <div className="flex items-center justify-between border-b bg-muted/40 px-4 py-3 md:hidden">
          <Link to="/dashboard" className="text-lg font-bold">
            {product.name}
          </Link>
          <Button
            variant="outline"
            size="sm"
            aria-expanded={menuOpen}
            aria-controls="app-menu"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? t("nav.closeMenu") : t("nav.openMenu")}
          </Button>
        </div>
        <aside
          id="app-menu"
          className={cn(
            menuOpen ? "flex" : "hidden",
            "shrink-0 flex-col gap-4 border-b bg-muted/40 p-4",
            "md:sticky md:top-0 md:flex md:h-screen md:w-60 md:overflow-y-auto md:border-e md:border-b-0",
          )}
        >
          <Link to="/dashboard" className="hidden text-lg font-bold md:block">
            {product.name}
          </Link>
          <nav aria-label={t("nav.main")} className="flex flex-col gap-1 text-sm">
            {NAV.map((item) => (
              <MenuLink key={item.to} to={item.to} onPick={closeMenu}>
                {t(`nav.${item.key}`)}
              </MenuLink>
            ))}
          </nav>
          {/* Settings sit at the bottom, above the server status: daily work stays on top. */}
          <div className="mt-auto flex flex-col gap-4">
            <nav aria-label={t("nav.settings")} className="flex flex-col gap-1 text-sm">
              <p className="px-3 text-xs font-medium uppercase text-muted-foreground">{t("nav.settings")}</p>
              {SETTINGS.map((item) => (
                <MenuLink key={item.to} to={item.to} onPick={closeMenu}>
                  {t(`nav.${item.key}`)}
                </MenuLink>
              ))}
              {window.smartbiz?.role && (
                <MenuLink to="/settings/this-pc" onPick={closeMenu}>
                  {t("nav.thisPc")}
                </MenuLink>
              )}
            </nav>
            <ServerStatus />
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
            {businessValue.businesses.length > 0 && (
              <label className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">{t("shell.business")}</span>
                <select
                  className={cn(inputClass, "w-auto")}
                  value={businessValue.current?.id ?? ""}
                  onChange={(e) => businessValue.select(e.target.value)}
                >
                  {businessValue.businesses.map((b) => (
                    <option key={b.id} value={b.id}>
                      {businessName(b)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {memberships.length > 1 && (
              <label className="flex items-center gap-2 text-sm">
                <span className="text-muted-foreground">{t("shell.account")}</span>
                <select
                  className={cn(inputClass, "w-auto")}
                  value={session.tenantId ?? ""}
                  onChange={(e) => void switchAccount(e.target.value)}
                >
                  {memberships.map((m) => (
                    <option key={m.tenant_id} value={m.tenant_id}>
                      {m.tenant_name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <div className="ms-auto flex items-center gap-3">
              <span className="text-sm" data-testid="user-name">
                {me.data?.user.full_name}
              </span>
              <LanguageSwitch />
              <Button variant="outline" size="sm" onClick={() => void logout()}>
                {t("shell.logout")}
              </Button>
            </div>
          </header>
          <main className="flex-1 p-4 md:p-6">
            <Outlet />
          </main>
        </div>
      </div>
    </BusinessContext.Provider>
  );
}
