import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import { CodeTag } from "@/components/code-tag";
import { DocLink } from "@/components/correction-panel";
import { todayIso } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { presetPeriod, useDayBook, type Period, type Preset } from "@/routes/reports/queries";
import { Amount, PeriodPicker, ReportsNav } from "@/routes/reports/report-parts";
import { useBusiness } from "@/shell/use-business";

function docTypeName(t: TFunction, docType: string | null): string {
  if (!docType) return t("reports.dayBook.entry");
  return t(`sales.types.${docType}`, {
    defaultValue: t(`purchasing.types.${docType}`, {
      defaultValue: t(`accounts.voucherTypes.${docType}`, {
        defaultValue: t(`inventory.types.${docType}`, { defaultValue: docType }),
      }),
    }),
  });
}

/** Reports → day book (M1.11b): every posted entry in the period, with its lines, oldest first. */
export function DayBookPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const [preset, setPreset] = useState<Preset>("this_month");
  const [custom, setCustom] = useState<Period>({ from: "", to: "" });
  const period: Period | null =
    preset === "custom"
      ? custom.from && custom.to
        ? custom
        : null
      : presetPeriod(preset, todayIso(), current?.fiscal_year_start_month ?? 1);
  const book = useDayBook(current?.id, period);
  const local = (en: string | null, ar: string | null) => (i18n.language === "ar" ? ar : en) ?? "";

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("reports.title")}</h1>
      <ReportsNav />
      <PeriodPicker preset={preset} custom={custom} onPreset={setPreset} onCustom={setCustom} />
      {!period && <p className="text-muted-foreground">{t("reports.pickDates")}</p>}
      {period && book.isPending && <p>{t("common.loading")}</p>}
      {book.isError && (
        <p role="alert" className="text-destructive">
          {t(`reports.errors.${errorCode(book.error)}`, { defaultValue: t("reports.errors.generic") })}
        </p>
      )}
      {period && book.data && book.data.entries.length === 0 && (
        <p className="text-muted-foreground">{t("reports.dayBook.none")}</p>
      )}
      {period && book.data && book.data.entries.length > 0 && (
        <>
          {book.data.truncated && (
            <p role="status" className="rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-sm">
              {t("reports.dayBook.truncated", { count: book.data.entries.length })}
            </p>
          )}
          <table className="w-full text-sm" aria-label={t("reports.nav.dayBook")}>
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 text-start font-medium">{t("reports.dayBook.account")}</th>
                <th className="py-2 text-start font-medium">{t("reports.dayBook.party")}</th>
                <th className="py-2 text-right font-medium">{t("accounts.dr")}</th>
                <th className="py-2 text-right font-medium">{t("accounts.cr")}</th>
              </tr>
            </thead>
            {book.data.entries.map((e) => (
              <tbody key={e.entry_id} className="border-t">
                <tr className="bg-muted/40">
                  <th colSpan={4} scope="rowgroup" className="py-1.5 text-start font-medium">
                    {/* Gaps, not margins: the date and number run left to right inside an Arabic line. */}
                    <span className="flex flex-wrap items-baseline gap-x-3">
                      <span dir="ltr" className="tabular-nums">
                        {e.entry_date}
                      </span>
                      <span className="flex items-baseline gap-x-2">
                        {docTypeName(t, e.doc_type)}
                        {e.document_id ? (
                          <DocLink
                            doc={{ id: e.document_id, doc_type: e.doc_type ?? "", number: e.number }}
                          />
                        ) : (
                          <span className="font-mono" dir="ltr">
                            {e.number ?? ""}
                          </span>
                        )}
                      </span>
                      {e.narration && (
                        <span className="font-normal text-muted-foreground">{e.narration}</span>
                      )}
                    </span>
                  </th>
                </tr>
                {e.lines.map((l, i) => (
                  <Fragment key={i}>
                    <tr>
                      <td className="py-1 ps-4">
                        <CodeTag>{l.code}</CodeTag>
                        {local(l.name_en, l.name_ar)}
                      </td>
                      <td className="py-1">{local(l.party_name_en, l.party_name_ar)}</td>
                      <td className="py-1 text-right">
                        {Number(l.debit) !== 0 && <Amount value={l.debit} />}
                      </td>
                      <td className="py-1 text-right">
                        {Number(l.credit) !== 0 && <Amount value={l.credit} />}
                      </td>
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            ))}
            <tbody className="border-t-2">
              <tr className="font-semibold">
                <td className="py-2" colSpan={2}>
                  {t("reports.dayBook.total", { count: book.data.entries.length })}
                </td>
                <td className="py-2 text-right">
                  <Amount value={book.data.total_debit} />
                </td>
                <td className="py-2 text-right">
                  <Amount value={book.data.total_credit} />
                </td>
              </tr>
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
