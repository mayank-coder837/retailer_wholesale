import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { AreaNav, type AreaLink } from "@/components/area-nav";
import { CodeTag } from "@/components/code-tag";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { money } from "@/routes/accounts/queries";
import { PRESETS, type Period, type Preset, type StatementRow } from "@/routes/reports/queries";

/** The Reports area's own menu: the list of every report, then the statements built here. */
export function ReportsNav() {
  const { t } = useTranslation();
  const links: AreaLink[] = [
    { to: "/reports", label: t("reports.nav.all"), matches: (p) => p === "/reports" },
    {
      to: "/reports/profit-loss",
      label: t("reports.nav.profitLoss"),
      matches: (p) => p.startsWith("/reports/profit-loss"),
    },
    {
      to: "/reports/balance-sheet",
      label: t("reports.nav.balanceSheet"),
      matches: (p) => p.startsWith("/reports/balance-sheet"),
    },
    {
      to: "/reports/day-book",
      label: t("reports.nav.dayBook"),
      matches: (p) => p.startsWith("/reports/day-book"),
    },
  ];
  return <AreaNav label={t("reports.menu")} links={links} />;
}

/** A period: this month, last month, this fiscal year, or any dates. */
export function PeriodPicker({
  preset,
  custom,
  onPreset,
  onCustom,
}: {
  preset: Preset;
  custom: Period;
  onPreset: (p: Preset) => void;
  onCustom: (p: Period) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div role="group" aria-label={t("reports.period")} className="flex flex-wrap gap-1">
        {PRESETS.map((p) => (
          <Button
            key={p}
            variant={preset === p ? "default" : "outline"}
            aria-pressed={preset === p}
            onClick={() => onPreset(p)}
          >
            {t(`reports.presets.${p}`)}
          </Button>
        ))}
      </div>
      {preset === "custom" && (
        <>
          <Field label={t("reports.from")} className="w-44">
            {(p) => (
              <input
                {...p}
                type="date"
                className={inputClass}
                value={custom.from}
                onChange={(e) => onCustom({ ...custom, from: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("reports.to")} className="w-44">
            {(p) => (
              <input
                {...p}
                type="date"
                className={inputClass}
                value={custom.to}
                onChange={(e) => onCustom({ ...custom, to: e.target.value })}
              />
            )}
          </Field>
        </>
      )}
    </div>
  );
}

/** An amount as statements print it: grouped, two decimals, left to right in Arabic too. */
export function Amount({ value }: { value: string }) {
  return (
    <span dir="ltr" className="tabular-nums">
      {money(value)}
    </span>
  );
}

/** One statement line: an account (opens its ledger) or a calculated line. */
export function StatementLine({ row, note }: { row: StatementRow; note?: string }) {
  const { i18n } = useTranslation();
  const name = i18n.language === "ar" ? row.name_ar : row.name_en;
  return (
    <tr>
      <td className="py-1.5 ps-4">
        {row.account_id ? (
          <>
            <CodeTag>{row.code}</CodeTag>
            <Link
              to="/accounts/$accountId"
              params={{ accountId: row.account_id }}
              className="underline-offset-2 hover:underline"
            >
              {name}
            </Link>
          </>
        ) : (
          <>
            <span>{name}</span>
            {note && <span className="ms-2 text-xs text-muted-foreground">{note}</span>}
          </>
        )}
      </td>
      <td className="py-1.5 text-right">
        <Amount value={row.amount} />
      </td>
    </tr>
  );
}

/** A section heading, its lines and its total. */
export function SectionRows({
  title,
  rows,
  total,
  totalLabel,
  emptyLabel,
  note,
}: {
  title: string;
  rows: StatementRow[];
  total: string;
  totalLabel: string;
  emptyLabel: string;
  note?: string;
}) {
  return (
    <>
      <tr>
        <th colSpan={2} scope="colgroup" className="pb-1 pt-5 text-start text-base font-semibold">
          {title}
        </th>
      </tr>
      {rows.length === 0 ? (
        <tr>
          <td colSpan={2} className="py-1.5 ps-4 text-muted-foreground">
            {emptyLabel}
          </td>
        </tr>
      ) : (
        rows.map((r) => <StatementLine key={r.account_id ?? r.key} row={r} note={note} />)
      )}
      <tr className="border-t font-medium">
        <td className="py-1.5">{totalLabel}</td>
        <td className="py-1.5 text-right">
          <Amount value={total} />
        </td>
      </tr>
    </>
  );
}

/** A result line under a rule (gross profit, net profit, totals); the final one gets the accountant's double rule. */
export function ResultRow({
  label,
  value,
  final = false,
}: {
  label: string;
  value: string;
  final?: boolean;
}) {
  return (
    <tr className={final ? "text-base font-bold" : "font-semibold"}>
      <td className="py-2">{label}</td>
      <td className="py-2 text-right">
        <span
          className={
            final ? "border-b-4 border-double border-foreground pb-0.5" : "border-b border-foreground pb-0.5"
          }
        >
          <Amount value={value} />
        </span>
      </td>
    </tr>
  );
}
