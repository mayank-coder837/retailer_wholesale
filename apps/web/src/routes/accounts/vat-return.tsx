import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";

import { DocLink } from "@/components/correction-panel";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import { money, todayIso } from "@/routes/accounts/queries";
import {
  INPUT_BOXES,
  NET_BOXES,
  OUTPUT_BOXES,
  periodEnding,
  recentMonths,
  sumAmounts,
  useVatBoxLines,
  useVatReturn,
  type Period,
  type VatBox,
} from "@/routes/accounts/vat-queries";
import { errorCode } from "@/routes/catalog/queries";
import { useBusiness } from "@/shell/use-business";

type Length = "quarter" | "month" | "custom";
const SECTIONS = [
  ["output", OUTPUT_BOXES],
  ["input", INPUT_BOXES],
  ["net", NET_BOXES],
] as const;

/** Accounts → VAT return (M1.10d): the VAT 201 draft for a period, box by box in the FTA's order; a figure
 * opens the documents behind it. A live draft: saving and filing come later. */
export function VatReturnPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const months = recentMonths(todayIso(), 12);
  const [length, setLength] = useState<Length>("quarter");
  const [endMonth, setEndMonth] = useState(months[0] ?? "");
  const [custom, setCustom] = useState<Period>({ from: "", to: "" });
  const [open, setOpen] = useState<string | null>(null);

  const period: Period | null =
    length === "custom"
      ? custom.from && custom.to
        ? custom
        : null
      : periodEnding(endMonth, length === "quarter" ? 3 : 1);
  const vat = useVatReturn(current?.id, period);
  const monthName = (m: string) =>
    new Date(`${m}-01T00:00:00Z`).toLocaleDateString(i18n.language === "ar" ? "ar-AE" : "en-GB", {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    });
  const choose = (next: () => void) => {
    next();
    setOpen(null);
  };

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("accounts.title")}</h1>
      <AccountsNav />
      <div className="flex flex-wrap items-end gap-4">
        <div role="group" aria-label={t("vat.period")} className="flex flex-wrap gap-1">
          {(["quarter", "month", "custom"] as const).map((l) => (
            <Button
              key={l}
              variant={length === l ? "default" : "outline"}
              aria-pressed={length === l}
              onClick={() => choose(() => setLength(l))}
            >
              {t(`vat.lengths.${l}`)}
            </Button>
          ))}
        </div>
        {length === "custom" ? (
          <>
            <Field label={t("vat.from")} className="w-44">
              {(p) => (
                <input
                  {...p}
                  type="date"
                  className={inputClass}
                  value={custom.from}
                  onChange={(e) => choose(() => setCustom({ ...custom, from: e.target.value }))}
                />
              )}
            </Field>
            <Field label={t("vat.to")} className="w-44">
              {(p) => (
                <input
                  {...p}
                  type="date"
                  className={inputClass}
                  value={custom.to}
                  onChange={(e) => choose(() => setCustom({ ...custom, to: e.target.value }))}
                />
              )}
            </Field>
          </>
        ) : (
          <Field label={t("vat.endsIn")} hint={t("vat.endsInHint")} className="w-56">
            {(p) => (
              <select
                {...p}
                className={inputClass}
                value={endMonth}
                onChange={(e) => choose(() => setEndMonth(e.target.value))}
              >
                {months.map((m) => (
                  <option key={m} value={m}>
                    {monthName(m)}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
      </div>

      {!period && <p className="text-muted-foreground">{t("vat.pickDates")}</p>}
      {period && vat.isPending && <p>{t("common.loading")}</p>}
      {vat.isError && (
        <p role="alert" className="text-destructive">
          {t(`vat.errors.${errorCode(vat.error)}`, {
            defaultValue: t(`accounts.errors.${errorCode(vat.error)}`, {
              defaultValue: t("accounts.errors.generic"),
            }),
          })}
        </p>
      )}
      {period && vat.data && (
        <>
          <Outcome payable={vat.data.payable} period={period} />
          <table className="w-full text-sm" aria-label={t("vat.title")}>
            <thead className="text-muted-foreground">
              <tr>
                <th className="w-12 py-2 text-start font-medium">{t("vat.box")}</th>
                <th className="py-2 text-start font-medium">{t("vat.description")}</th>
                <th className="py-2 text-right font-medium">{t("vat.amount")}</th>
                <th className="py-2 text-right font-medium">{t("vat.vat")}</th>
              </tr>
            </thead>
            {SECTIONS.map(([section, codes]) => (
              <tbody key={section} className="divide-y border-t">
                <tr>
                  <th colSpan={4} scope="colgroup" className="pb-1 pt-5 text-start text-base font-semibold">
                    {t(`vat.sections.${section}`)}
                  </th>
                </tr>
                {codes.map((code) => {
                  const box = vat.data.boxes.find((b) => b.box === code);
                  if (!box) return null;
                  return (
                    <Fragment key={code}>
                      <BoxRow
                        box={box}
                        expanded={open === code}
                        onToggle={() => setOpen(open === code ? null : code)}
                      />
                      {open === code && (
                        <tr>
                          <td colSpan={4} className="bg-muted/40 px-3 py-3">
                            <BoxLines box={box} period={period} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            ))}
          </table>
          <p className="text-sm text-muted-foreground">{t("vat.draftNote")}</p>
        </>
      )}
    </div>
  );
}

/** Box 14 in words: what the business pays the FTA, or is owed, for the period. */
function Outcome({ payable, period }: { payable: string; period: Period }) {
  const { t } = useTranslation();
  const refundable = payable.trim().startsWith("-");
  const zero = money(payable) === "0.00";
  const key = zero ? "nil" : refundable ? "refundable" : "payable";
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-3 rounded-lg border p-4">
      <p>
        <span className="block text-sm text-muted-foreground">
          {t("vat.periodShown", { from: period.from, to: period.to })}
        </span>
        <span className="text-lg font-semibold">{t(`vat.outcome.${key}`)}</span>
      </p>
      <p className="text-2xl font-bold tabular-nums">
        <span dir="ltr">
          {t("vat.aed")} {money(refundable ? payable.trim().slice(1) : payable)}
        </span>
      </p>
    </div>
  );
}

const drillable = (box: VatBox) => box.kind === "amount_vat" || box.kind === "amount";

function BoxRow({ box, expanded, onToggle }: { box: VatBox; expanded: boolean; onToggle: () => void }) {
  const { t, i18n } = useTranslation();
  const total = box.kind === "total";
  const net = NET_BOXES.includes(box.box);
  const figure = (value: string, show: boolean) => {
    if (box.kind === "manual") return <span className="text-muted-foreground">—</span>;
    if (!show) return null;
    const text = <span dir="ltr">{money(value)}</span>;
    // A zero has no documents behind it: nothing to open.
    if (!drillable(box) || money(value) === "0.00") return text;
    return (
      <button
        type="button"
        className="tabular-nums underline decoration-dotted underline-offset-4 hover:decoration-solid"
        aria-expanded={expanded}
        aria-label={t("vat.showDocuments", { box: box.box })}
        onClick={onToggle}
      >
        {text}
      </button>
    );
  };
  return (
    <tr className={total ? "font-semibold" : undefined}>
      <td className="py-1.5 align-top tabular-nums text-muted-foreground">{box.box}</td>
      <td className="py-1.5">
        {i18n.language === "ar" ? box.label_ar : box.label_en}
        {box.kind === "manual" && (
          <span className="ms-2 text-xs text-muted-foreground">{t("vat.manualLater")}</span>
        )}
      </td>
      <td className="py-1.5 text-right tabular-nums">{figure(box.amount, !net && box.kind !== "manual")}</td>
      <td className="py-1.5 text-right tabular-nums">{figure(box.vat, box.kind !== "amount")}</td>
    </tr>
  );
}

function docTypeName(t: TFunction, docType: string): string {
  return t(`sales.types.${docType}`, {
    defaultValue: t(`purchasing.types.${docType}`, {
      defaultValue: t(`accounts.voucherTypes.${docType}`, { defaultValue: docType }),
    }),
  });
}

/** The documents behind one box figure; their total is the figure. */
function BoxLines({ box, period }: { box: VatBox; period: Period }) {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const lines = useVatBoxLines(current?.id, period, box.box);
  const withVat = box.kind === "amount_vat";
  if (lines.isPending) return <p>{t("common.loading")}</p>;
  if (lines.isError)
    return (
      <p role="alert" className="text-destructive">
        {t("accounts.errors.generic")}
      </p>
    );
  if (lines.data.length === 0) return <p className="text-muted-foreground">{t("vat.noDocuments")}</p>;
  return (
    <table className="w-full text-sm" aria-label={t("vat.documentsIn", { box: box.box })}>
      <thead className="text-muted-foreground">
        <tr>
          <th className="py-1 text-start font-medium">{t("vat.date")}</th>
          <th className="py-1 text-start font-medium">{t("vat.document")}</th>
          <th className="py-1 text-start font-medium">{t("vat.party")}</th>
          <th className="py-1 text-right font-medium">{t("vat.amount")}</th>
          {withVat && <th className="py-1 text-right font-medium">{t("vat.vat")}</th>}
        </tr>
      </thead>
      <tbody className="divide-y">
        {lines.data.map((l) => (
          <tr key={l.document_id}>
            <td className="py-1">
              <span dir="ltr">{l.doc_date}</span>
            </td>
            <td className="py-1">
              <span className="me-2">{docTypeName(t, l.doc_type)}</span>
              <DocLink doc={{ id: l.document_id, doc_type: l.doc_type, number: l.number }} />
            </td>
            <td className="py-1">{(i18n.language === "ar" ? l.party_name_ar : l.party_name_en) ?? ""}</td>
            <td className="py-1 text-right tabular-nums">
              <span dir="ltr">{money(l.amount)}</span>
            </td>
            {withVat && (
              <td className="py-1 text-right tabular-nums">
                <span dir="ltr">{money(l.vat)}</span>
              </td>
            )}
          </tr>
        ))}
        <tr className="font-semibold">
          <td className="py-1" colSpan={3}>
            {t("vat.total", { count: lines.data.length })}
          </td>
          <td className="py-1 text-right tabular-nums">
            <span dir="ltr">{money(sumAmounts(lines.data.map((l) => l.amount)))}</span>
          </td>
          {withVat && (
            <td className="py-1 text-right tabular-nums">
              <span dir="ltr">{money(sumAmounts(lines.data.map((l) => l.vat)))}</span>
            </td>
          )}
        </tr>
      </tbody>
    </table>
  );
}
