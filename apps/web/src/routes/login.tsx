import { Link, Navigate, useNavigate } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import product from "@smartbiz/config";

import { useSession, type TenantChoice } from "@/auth/use-session";
import { Field, inputClass } from "@/components/field";
import { LanguageSwitch } from "@/components/language-switch";
import { Button } from "@/components/ui/button";
import { ServerStatus } from "@/shell/server-status";

export function LoginPage() {
  const { t } = useTranslation();
  const session = useSession();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [choices, setChoices] = useState<TenantChoice[] | null>(null);
  const [busy, setBusy] = useState(false);

  if (session.status === "signed_in" && !busy) return <Navigate to="/dashboard" />;

  async function submit(tenantId?: string) {
    setBusy(true);
    setError(null);
    const result = await session.login(email, password, tenantId);
    if (result.ok) {
      await navigate({ to: "/dashboard" });
      return;
    }
    setBusy(false);
    if (result.code === "tenant_selection_required" && result.memberships) {
      setChoices(result.memberships);
    } else {
      setError(t(`login.errors.${result.code}`, { defaultValue: t("login.errors.unknown") }));
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void submit();
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-6 px-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold">{product.name}</p>
          <h1 className="text-2xl font-bold">{t("login.title")}</h1>
        </div>
        <LanguageSwitch />
      </div>
      <p className="text-muted-foreground">{t("login.tagline")}</p>
      {choices ? (
        <section className="flex flex-col gap-2" aria-label={t("login.chooseAccount")}>
          <p className="text-sm text-muted-foreground">{t("login.chooseAccount")}</p>
          {choices.map((c) => (
            <Button
              key={c.tenant_id}
              variant="outline"
              disabled={busy}
              onClick={() => void submit(c.tenant_id)}
            >
              {c.tenant_name}
            </Button>
          ))}
        </section>
      ) : (
        <form className="flex flex-col gap-3" onSubmit={onSubmit}>
          <Field label={t("login.email")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("login.password")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            )}
          </Field>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" disabled={busy}>
            {t("login.submit")}
          </Button>
        </form>
      )}
      <p className="text-sm text-muted-foreground">
        {t("login.noAccount", { product: product.name })}{" "}
        <Link to="/signup" className="underline">
          {t("login.createAccount")}
        </Link>
      </p>
      <ServerStatus />
    </main>
  );
}
