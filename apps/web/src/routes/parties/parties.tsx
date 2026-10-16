import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { errorCode } from "@/routes/catalog/queries";
import { partyName, roleLabel, useParties, type Role } from "@/routes/parties/queries";

const FILTERS: (Role | null)[] = [null, "customer", "supplier"];

/** Customers & suppliers (M1.4): one list, searched by name, code, TRN or phone. */
export function PartiesPage() {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<Role | null>(null);
  const parties = useParties(query.trim(), role);

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">{t("parties.title")}</h1>
        <Button asChild>
          <Link to="/parties/new">{t("parties.new")}</Link>
        </Button>
      </div>
      <div className="flex flex-wrap gap-3">
        <input
          className={inputClass + " min-w-60 flex-1"}
          type="search"
          aria-label={t("parties.search")}
          placeholder={t("parties.search")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div role="group" aria-label={t("parties.show")} className="flex gap-1">
          {FILTERS.map((f) => (
            <Button
              key={f ?? "all"}
              variant={role === f ? "default" : "outline"}
              aria-pressed={role === f}
              onClick={() => setRole(f)}
            >
              {t(`parties.filter.${f ?? "all"}`)}
            </Button>
          ))}
        </div>
      </div>
      {parties.isPending && <p>{t("common.loading")}</p>}
      {parties.isError && (
        <p role="alert" className="text-destructive">
          {t(`parties.errors.${errorCode(parties.error)}`, { defaultValue: t("parties.errors.generic") })}
        </p>
      )}
      {parties.isSuccess && parties.data.length === 0 && (
        <p className="text-muted-foreground">{query ? t("parties.noMatch") : t("parties.none")}</p>
      )}
      {parties.isSuccess && parties.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("parties.code")}</th>
              <th className="py-2 text-start font-medium">{t("parties.name")}</th>
              <th className="py-2 text-start font-medium">{t("parties.roles")}</th>
              <th className="py-2 text-start font-medium">{t("parties.trn")}</th>
              <th className="py-2 text-start font-medium">{t("parties.phone")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {parties.data.map((p) => (
              <tr key={p.id}>
                <td className="py-2 font-mono" dir="ltr">
                  {p.code}
                </td>
                <td className="py-2">
                  <Link
                    to="/parties/$partyId"
                    params={{ partyId: p.id }}
                    className="underline-offset-2 hover:underline"
                  >
                    {partyName(p, i18n.language)}
                  </Link>
                </td>
                <td className="py-2">{roleLabel(t, p)}</td>
                <td className="py-2 font-mono" dir="ltr">
                  {p.trn ?? ""}
                </td>
                <td className="py-2" dir="ltr">
                  {p.phone ?? ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
