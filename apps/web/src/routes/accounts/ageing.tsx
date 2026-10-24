import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { components } from "@smartbiz/api-client";

import { Button } from "@/components/ui/button";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import { money } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { useAgeing } from "@/routes/sales/queries";
import { useBusiness } from "@/shell/use-business";

type Buckets = components["schemas"]["AgeingBuckets"];
const COLUMNS = ["not_due", "days_1_30", "days_31_60", "days_61_90", "over_90", "advances", "total"] as const;

/** Accounts → ageing (M1.8c): what customers owe (or suppliers are owed) by days past due, advances apart. */
export function AgeingPage() {
  const { t } = useTranslation();
  const { current } = useBusiness();
  const [side, setSide] = useState<"receivables" | "payables">("receivables");
  const ageing = useAgeing(current?.id, side);

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("accounts.title")}</h1>
      <AccountsNav />
      <div role="group" aria-label={t("ageing.side")} className="flex flex-wrap gap-1">
        {(["receivables", "payables"] as const).map((s) => (
          <Button
            key={s}
            variant={side === s ? "default" : "outline"}
            aria-pressed={side === s}
            onClick={() => setSide(s)}
          >
            {t(`ageing.${s}`)}
          </Button>
        ))}
      </div>
      {ageing.isPending && <p>{t("common.loading")}</p>}
      {ageing.isError && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(ageing.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      {ageing.data && (
        <>
          <p className="text-sm text-muted-foreground">{t("ageing.asOf", { date: ageing.data.as_of })}</p>
          {ageing.data.rows.length === 0 ? (
            <p className="text-muted-foreground">{t("ageing.none")}</p>
          ) : (
            <AgeingTable rows={ageing.data.rows} totals={ageing.data.totals} />
          )}
        </>
      )}
    </div>
  );
}

/** The ageing table; on a party's page it shows that one party's row. */
export function AgeingTable({
  rows,
  totals,
}: {
  rows: components["schemas"]["AgeingRowOut"][];
  totals?: Buckets;
}) {
  const { t, i18n } = useTranslation();
  const cells = (b: Buckets) =>
    COLUMNS.map((c) => (
      <td key={c} className="py-1.5 text-right tabular-nums">
        <span dir="ltr">{Number(b[c]) === 0 ? "" : money(b[c] ?? "0")}</span>
      </td>
    ));
  return (
    <table className="w-full text-sm" aria-label={t("ageing.title")}>
      <thead className="text-muted-foreground">
        <tr>
          <th className="py-2 text-start font-medium">{t("ageing.party")}</th>
          {COLUMNS.map((c) => (
            <th key={c} className="py-2 text-right font-medium">
              {t(`ageing.columns.${c}`)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y">
        {rows.map((r) => (
          <tr key={r.party_id}>
            <td className="py-1.5">
              <Link
                to="/parties/$partyId"
                params={{ partyId: r.party_id }}
                className="underline-offset-2 hover:underline"
              >
                {i18n.language === "ar" ? r.name_ar : r.name_en}
              </Link>
            </td>
            {cells(r)}
          </tr>
        ))}
        {totals && rows.length > 1 && (
          <tr className="font-semibold">
            <td className="py-1.5">{t("ageing.totals")}</td>
            {cells(totals)}
          </tr>
        )}
      </tbody>
    </table>
  );
}
