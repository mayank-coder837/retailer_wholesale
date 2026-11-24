import { useTranslation } from "react-i18next";

import { money } from "@/routes/accounts/queries";
import { label, type CheckResult, type ImportKind } from "@/routes/imports/queries";

/** Amounts a kind adds up from the file; shown as money. The rest of the summary is shown as written. */
const MONEY = [
  "file_debit",
  "file_credit",
  "receivable",
  "receivable_advances",
  "payable",
  "payable_advances",
  "value",
];
const PLAIN = ["accounts_posted", "lines", "warehouses", "opening_date"];

interface Control {
  code: string;
  name_en: string;
  name_ar: string;
  debit: string;
  credit: string;
}

/** The result of checking a file: counts, what it adds up to, and every row with its problems in words. */
export function CheckResults({ kind, result }: { kind: ImportKind; result: CheckResult }) {
  const { t, i18n } = useTranslation();
  const summary = result.summary as Record<string, unknown>;
  const fields = new Map(kind.fields.map((f) => [f.key, label(f, i18n.language)]));
  const controls = (summary.controls as Control[] | undefined) ?? [];
  const equity = Number(summary.equity_amount ?? 0) !== 0;

  return (
    <section aria-label={t("import.check.result")} className="flex flex-col gap-4">
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
        {(["total", "new", "exists", "skipped", "errors"] as const).map((key) => (
          <div key={key} className="rounded-md border px-3 py-2">
            <dt className="text-muted-foreground">{t(`import.counts.${key}`)}</dt>
            <dd
              className={
                key === "errors" && result.errors > 0
                  ? "text-lg font-bold text-destructive"
                  : "text-lg font-bold"
              }
            >
              {result[key]}
            </dd>
          </div>
        ))}
      </dl>

      <dl className="flex flex-col gap-1 text-sm">
        {PLAIN.filter((k) => summary[k] !== undefined).map((k) => (
          <div key={k} className="flex gap-2">
            <dt className="text-muted-foreground">{t(`import.summary.${k}`)}</dt>
            <dd dir="ltr">{String(summary[k])}</dd>
          </div>
        ))}
        {MONEY.filter((k) => summary[k] !== undefined).map((k) => (
          <div key={k} className="flex gap-2">
            <dt className="text-muted-foreground">{t(`import.summary.${k}`)}</dt>
            <dd dir="ltr" className="tabular-nums">
              {money(String(summary[k]))}
            </dd>
          </div>
        ))}
        {equity && (
          <div className="flex gap-2">
            <dt className="text-muted-foreground">{t("import.summary.equity")}</dt>
            <dd dir="ltr" className="tabular-nums">
              {t(`import.summary.side.${String(summary.equity_side)}`)} {money(String(summary.equity_amount))}
            </dd>
          </div>
        )}
      </dl>

      {controls.length > 0 && (
        <div className="flex flex-col gap-1 text-sm">
          <h3 className="font-medium">{t("import.summary.controls")}</h3>
          <p className="text-muted-foreground">{t("import.summary.controlsHint")}</p>
          <ul className="divide-y rounded-md border">
            {controls.map((c) => (
              <li key={c.code} className="flex justify-between gap-3 px-3 py-1.5">
                <span>
                  <span className="font-mono" dir="ltr">
                    {c.code}
                  </span>{" "}
                  {i18n.language.startsWith("ar") ? c.name_ar : c.name_en}
                </span>
                <span dir="ltr" className="tabular-nums">
                  {Number(c.debit) !== 0
                    ? `${t("import.summary.side.debit")} ${money(c.debit)}`
                    : `${t("import.summary.side.credit")} ${money(c.credit)}`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <table className="w-full text-sm" aria-label={t("import.check.rows")}>
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-2 text-start font-medium">{t("import.check.row")}</th>
            <th className="py-2 text-start font-medium">{t("import.check.item")}</th>
            <th className="py-2 text-start font-medium">{t("import.check.state")}</th>
            <th className="py-2 text-start font-medium">{t("import.check.problem")}</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {result.rows.map((r) => (
            <tr key={r.number} className="align-top">
              <td className="py-1.5 tabular-nums" dir="ltr">
                {r.number}
              </td>
              <td className="py-1.5">{r.label}</td>
              <td className={r.status === "error" ? "py-1.5 font-medium text-destructive" : "py-1.5"}>
                {t(`import.rowStatus.${r.status}`)}
              </td>
              <td className="py-1.5">
                {r.problems.length > 0 ? (
                  <ul className="flex flex-col gap-0.5">
                    {r.problems.map((p, i) => (
                      <li key={i}>
                        {p.field && fields.get(p.field) ? `${fields.get(p.field)}: ` : ""}
                        {t(`import.problems.${p.code}`, { defaultValue: p.message })}
                      </li>
                    ))}
                  </ul>
                ) : r.note ? (
                  <span className="text-muted-foreground">
                    {t(`import.notes.${r.note_code ?? ""}`, { defaultValue: r.note })}
                  </span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {result.truncated && <p className="text-sm text-muted-foreground">{t("import.check.truncated")}</p>}
    </section>
  );
}
