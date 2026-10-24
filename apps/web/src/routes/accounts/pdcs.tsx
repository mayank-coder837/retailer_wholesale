import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import {
  ISSUED_STATUSES,
  PENDING,
  RECEIVED_STATUSES,
  byMonth,
  filsToAmount,
  totalFils,
  usePdcs,
  type Pdc,
  type PdcDirection,
  type PdcStatus,
} from "@/routes/accounts/pdc-queries";
import { money, todayIso } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { useBusiness } from "@/shell/use-business";

const PENDING_FILTER = "pending";

/**
 * Accounts → cheques (M1.9b): the PDC register by maturity month — the calendar of cheques to deposit (received)
 * or to fund (issued). A memo until cleared: nothing here is in the books yet (A2).
 */
export function PdcRegisterPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const [direction, setDirection] = useState<PdcDirection>("received");
  const [filter, setFilter] = useState<PdcStatus | typeof PENDING_FILTER | "">(PENDING_FILTER);
  const status = filter === PENDING_FILTER || filter === "" ? null : filter;
  const pdcs = usePdcs(current?.id, { direction, status });
  const shown = (pdcs.data ?? []).filter((p) => filter !== PENDING_FILTER || PENDING.includes(p.status));
  const statuses = direction === "received" ? RECEIVED_STATUSES : ISSUED_STATUSES;
  const today = todayIso();

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("accounts.title")}</h1>
      <AccountsNav />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div role="group" aria-label={t("pdc.direction")} className="flex flex-wrap gap-1">
          {(["received", "issued"] as const).map((d) => (
            <Button
              key={d}
              variant={direction === d ? "default" : "outline"}
              aria-pressed={direction === d}
              onClick={() => {
                setDirection(d);
                setFilter(PENDING_FILTER);
              }}
            >
              {t(`pdc.directions.${d}`)}
            </Button>
          ))}
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-sm font-medium">
            {t("pdc.status")}
            <select
              className={cn(inputClass, "w-48")}
              value={filter}
              onChange={(e) => setFilter(e.target.value as PdcStatus | typeof PENDING_FILTER | "")}
            >
              <option value={PENDING_FILTER}>{t("pdc.pendingFilter")}</option>
              <option value="">{t("pdc.allFilter")}</option>
              {statuses.map((s) => (
                <option key={s} value={s}>
                  {t(`pdc.statuses.${s}`)}
                </option>
              ))}
            </select>
          </label>
          <Button asChild>
            <Link to="/accounts/cheques/new" search={{ direction }}>
              {t(`pdc.new.${direction}`)}
            </Link>
          </Button>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">{t("pdc.memoNote")}</p>
      {pdcs.isPending && <p>{t("common.loading")}</p>}
      {pdcs.isError && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(pdcs.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      {pdcs.data && shown.length === 0 && <p className="text-muted-foreground">{t("pdc.none")}</p>}
      {byMonth(shown).map(([month, rows]) => (
        <MonthTable
          key={month}
          month={month}
          rows={rows}
          today={today}
          language={i18n.language}
          total={filsToAmount(totalFils(rows.map((r) => r.amount)))}
        />
      ))}
    </div>
  );
}

function monthName(month: string, language: string): string {
  const [year, m] = month.split("-").map(Number);
  return new Intl.DateTimeFormat(language === "ar" ? "ar-AE" : "en-AE", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year ?? 2000, (m ?? 1) - 1, 1)));
}

function MonthTable({
  month,
  rows,
  today,
  language,
  total,
}: {
  month: string;
  rows: Pdc[];
  today: string;
  language: string;
  total: string;
}) {
  const { t } = useTranslation();
  const title = monthName(month, language);
  return (
    <table className="w-full table-fixed text-sm" aria-label={title}>
      <caption className="border-b py-2 text-start">
        <span className="font-semibold">{title}</span>
        <span className="ms-3 text-muted-foreground">
          {t("pdc.monthTotal", { count: rows.length })}{" "}
          <span dir="ltr" className="tabular-nums">
            {money(total)}
          </span>
        </span>
      </caption>
      <thead className="text-muted-foreground">
        <tr>
          <th className="w-36 py-1.5 text-start font-medium">{t("pdc.chequeDate")}</th>
          <th className="w-28 py-1.5 text-start font-medium">{t("pdc.chequeNo")}</th>
          <th className="py-1.5 text-start font-medium">{t("pdc.party")}</th>
          <th className="py-1.5 text-start font-medium">{t("pdc.bank")}</th>
          <th className="w-28 py-1.5 text-start font-medium">{t("pdc.status")}</th>
          <th className="w-32 py-1.5 text-right font-medium">{t("pdc.amount")}</th>
        </tr>
      </thead>
      <tbody className="divide-y">
        {rows.map((p) => {
          const due = PENDING.includes(p.status) && p.cheque_date <= today;
          return (
            <tr key={p.id}>
              <td className="py-1.5">
                <span dir="ltr">{p.cheque_date}</span>
                {due && (
                  <span className="ms-2 rounded bg-amber-500/15 px-1.5 text-xs text-amber-700 dark:text-amber-400">
                    {t("pdc.due")}
                  </span>
                )}
              </td>
              <td className="py-1.5 font-mono">
                <Link
                  to="/accounts/cheques/$pdcId"
                  params={{ pdcId: p.id }}
                  className="underline underline-offset-2"
                >
                  <span dir="ltr">{p.cheque_no}</span>
                </Link>
              </td>
              <td className="py-1.5">{language === "ar" ? p.party_name_ar : p.party_name_en}</td>
              <td className="py-1.5">{p.bank_name ?? ""}</td>
              <td className="py-1.5">{t(`pdc.statuses.${p.status}`)}</td>
              <td className="py-1.5 text-right tabular-nums">
                <span dir="ltr">{money(p.amount)}</span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
