import { useTranslation } from "react-i18next";

import { Field, inputClass } from "@/components/field";
import {
  NO_VAT_REASONS,
  PRICE_MODES,
  useTaxCodes,
  type NoVatReason,
  type PriceMode,
  type Side,
} from "@/routes/tax/tax";

/** How the document's prices are typed (A10): excluding VAT, including it, or without VAT and why. ``fixed``:
 * the document follows another one (or returns it) and keeps its mode. */
export function PriceModeFields({
  mode,
  reason,
  fixed,
  onChange,
}: {
  mode: PriceMode;
  reason: NoVatReason | null;
  fixed: boolean;
  onChange: (mode: PriceMode, reason: NoVatReason | null) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      <Field label={t("tax.prices")} hint={fixed ? t("tax.fixedHint") : undefined}>
        {(p) => (
          <select
            {...p}
            className={inputClass}
            value={mode}
            disabled={fixed}
            onChange={(e) => {
              const next = e.target.value as PriceMode;
              onChange(next, next === "no_vat" ? (reason ?? "zero_rated") : null);
            }}
          >
            {PRICE_MODES.map((m) => (
              <option key={m} value={m}>
                {t(`tax.modes.${m}`)}
              </option>
            ))}
          </select>
        )}
      </Field>
      {mode === "no_vat" && (
        <Field label={t("tax.reason")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={reason ?? "zero_rated"}
              disabled={fixed}
              onChange={(e) => onChange(mode, e.target.value as NoVatReason)}
            >
              {NO_VAT_REASONS.map((r) => (
                <option key={r} value={r}>
                  {t(`tax.reasons.${r}`)}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
    </>
  );
}

/** A tax code choice: shows the code the line takes (``value``, already defaulted); a change is the user's
 * choice. Read-only when the mode or the document decides it. ``stock``: blocked VAT is never offered.
 * ``empty``: the label of "no code" (value ""), where that is a choice — no VAT on a voucher line, no
 * override on a party, standard rated on a product. */
export function TaxCodeField({
  side,
  value,
  readOnly = false,
  stock,
  onChange,
  label,
  hint,
  empty,
  className = "w-48",
}: {
  side: Side;
  value: string;
  readOnly?: boolean;
  stock: boolean;
  onChange: (code: string) => void;
  label?: string;
  hint?: string;
  empty?: string;
  className?: string;
}) {
  const { t, i18n } = useTranslation();
  const codes = useTaxCodes(side);
  const offered = (codes.data ?? []).filter((c) => c.code === value || !(stock && c.treatment === "blocked"));
  const known = (value === "" && empty !== undefined) || offered.some((c) => c.code === value);
  return (
    <Field label={label ?? t("tax.code")} hint={hint} className={className}>
      {(p) => (
        <select
          {...p}
          className={inputClass}
          value={value}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.value)}
        >
          {empty !== undefined && <option value="">{empty}</option>}
          {!known && <option value={value}>{value}</option>}
          {offered.map((c) => (
            <option key={c.code} value={c.code}>
              {i18n.language === "ar" ? c.name_ar : c.name_en}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}
