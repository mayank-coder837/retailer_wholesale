import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { useBranches } from "@/routes/accounts/queries";
import { unwrap } from "@/routes/catalog/queries";
import {
  errorText,
  hint,
  label,
  type CheckResult,
  type ImportKind,
  type ImportOption,
  type Mapping,
  type Upload,
} from "@/routes/imports/queries";

/** Step 3: which column of the file is which field, and the choices that apply to the whole file. */
export function MatchStep({
  kind,
  upload,
  businessId,
  mapping,
  onMapping,
  options,
  onOptions,
  onChecked,
  onBack,
}: {
  kind: ImportKind;
  upload: Upload;
  businessId: string;
  mapping: Mapping;
  onMapping: (m: Mapping) => void;
  options: Record<string, string>;
  onOptions: (o: Record<string, string>) => void;
  onChecked: (result: CheckResult) => void;
  onBack: () => void;
}) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const check = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/imports/{batch_id}/check", {
          params: { path: { batch_id: upload.id } },
          body: { mapping, options },
        }),
      ),
    onSuccess: onChecked,
  });
  const missing = kind.options.some((o) => o.required && !(options[o.key] ?? o.default));

  return (
    <div className="flex flex-col gap-5">
      {upload.duplicate_of && (
        <p role="status" className="rounded-md border border-amber-500 px-3 py-2 text-sm">
          {t("import.duplicate")}
        </p>
      )}
      {(upload.notes ?? []).map((n) => (
        <p key={n.code} role="note" className="text-sm text-muted-foreground">
          {t(`import.notes.${n.code}`, { defaultValue: n.message, count: n.count })}
        </p>
      ))}

      <section aria-label={t("import.match.title")} className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">{t("import.match.title")}</h2>
        <p className="text-sm text-muted-foreground">{t("import.match.hint")}</p>
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("import.match.field")}</th>
              <th className="py-2 text-start font-medium">{t("import.match.column")}</th>
              <th className="py-2 text-start font-medium">{t("import.match.sample")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {kind.fields.map((f) => {
              const chosen = mapping[f.key] ?? null;
              const name = label(f, i18n.language);
              return (
                <tr key={f.key} className="align-top">
                  <td className="py-2">
                    <div className="font-medium">
                      {name}
                      {f.required && <span className="text-destructive"> *</span>}
                    </div>
                    {hint(f, i18n.language) && (
                      <div className="text-muted-foreground">{hint(f, i18n.language)}</div>
                    )}
                  </td>
                  <td className="py-2">
                    <select
                      aria-label={name}
                      className={inputClass}
                      value={chosen === null ? "" : String(chosen)}
                      onChange={(e) =>
                        onMapping({
                          ...mapping,
                          [f.key]: e.target.value === "" ? null : Number(e.target.value),
                        })
                      }
                    >
                      <option value="">{t("import.match.none")}</option>
                      {upload.headers.map((h, i) => (
                        <option key={i} value={i}>
                          {i + 1}. {h}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="py-2 text-muted-foreground" dir="ltr">
                    {chosen !== null ? (upload.sample[0]?.[chosen] ?? "") : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {kind.options.length > 0 && (
        <section aria-label={t("import.match.options")} className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold">{t("import.match.options")}</h2>
          <div className="grid gap-3 sm:grid-cols-3">
            {kind.options.map((o) => (
              <OptionControl
                key={o.key}
                option={o}
                businessId={businessId}
                value={options[o.key] ?? o.default}
                onChange={(value) => onOptions({ ...options, [o.key]: value })}
              />
            ))}
          </div>
        </section>
      )}

      {check.isError && (
        <p role="alert" className="text-destructive">
          {errorText(t, check.error)}
        </p>
      )}
      <div className="flex gap-3">
        <Button variant="outline" onClick={onBack}>
          {t("import.match.back")}
        </Button>
        <Button onClick={() => check.mutate()} disabled={check.isPending || missing}>
          {check.isPending ? t("import.check.checking") : t("import.check.button")}
        </Button>
      </div>
    </div>
  );
}

function OptionControl({
  option,
  businessId,
  value,
  onChange,
}: {
  option: ImportOption;
  businessId: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const branches = useBranches(option.kind === "branch" ? businessId : undefined);
  const name = label(option, i18n.language);
  return (
    <Field label={name}>
      {(p) =>
        option.kind === "date" ? (
          <input
            {...p}
            type="date"
            className={inputClass}
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
        ) : option.kind === "branch" ? (
          <select {...p} className={inputClass} value={value} onChange={(e) => onChange(e.target.value)}>
            <option value="">{t("import.defaultBranch")}</option>
            {(branches.data ?? []).map((b) => (
              <option key={b.id} value={b.id}>
                {i18n.language.startsWith("ar") ? b.name_ar : b.name_en}
              </option>
            ))}
          </select>
        ) : (
          <select {...p} className={inputClass} value={value} onChange={(e) => onChange(e.target.value)}>
            {option.values.map((v) => (
              <option key={v} value={v}>
                {t(`import.optionValues.${v}`, { defaultValue: v })}
              </option>
            ))}
          </select>
        )
      }
    </Field>
  );
}
