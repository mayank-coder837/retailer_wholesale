import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { money } from "@/routes/accounts/queries";
import { unwrap } from "@/routes/catalog/queries";
import { CheckResults } from "@/routes/imports/check-results";
import { MatchStep } from "@/routes/imports/match-step";
import {
  downloadTemplate,
  errorText,
  label,
  OPENING_KINDS,
  uploadImport,
  useImportKinds,
  type Batch,
  type CheckResult,
  type ImportKind,
  type Mapping,
  type Upload,
} from "@/routes/imports/queries";
import { RecentImports } from "@/routes/imports/recent-imports";
import { useBusiness } from "@/shell/use-business";

const STEPS = ["pick", "file", "match", "check", "done"] as const;

/** Settings → Import data (M1.12d): bring a business across from Excel, CSV or Tally, a step at a time. */
export function ImportPage() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const kinds = useImportKinds();
  const [kind, setKind] = useState<ImportKind | null>(null);
  const [upload, setUpload] = useState<Upload | null>(null);
  const [mapping, setMapping] = useState<Mapping>({});
  const [options, setOptions] = useState<Record<string, string>>({});
  const [result, setResult] = useState<CheckResult | null>(null);
  const [done, setDone] = useState<Batch | null>(null);

  const commit = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/imports/{batch_id}/commit", { params: { path: { batch_id: upload!.id } } })),
    onSuccess: async (batch) => {
      setDone(batch);
      await queryClient.invalidateQueries();
    },
  });

  const stage = done ? 4 : result ? 3 : upload ? 2 : kind ? 1 : 0;
  const reset = () => {
    setKind(null);
    setUpload(null);
    setMapping({});
    setOptions({});
    setResult(null);
    setDone(null);
    commit.reset();
  };
  const choose = (k: ImportKind) => {
    setKind(k);
    setOptions(Object.fromEntries(k.options.map((o) => [o.key, o.default])));
  };

  if (!current) return <p>{t("common.loading")}</p>;
  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold leading-9">{t("import.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("import.hint")}</p>
      </div>

      <ol aria-label={t("import.stepsLabel")} className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
        {STEPS.map((s, i) => (
          <li
            key={s}
            aria-current={i === stage ? "step" : undefined}
            className={i === stage ? "font-semibold" : "text-muted-foreground"}
          >
            {i + 1}. {t(`import.steps.${s}`)}
          </li>
        ))}
      </ol>

      {kinds.isError && (
        <p role="alert" className="text-destructive">
          {errorText(t, kinds.error)}
        </p>
      )}

      {stage === 0 && kinds.data && (
        <>
          <PickStep kinds={kinds.data} onPick={choose} />
          <RecentImports businessId={current.id} timeZone={current.time_zone} kinds={kinds.data} />
        </>
      )}

      {stage >= 1 && kind && (
        <p className="text-sm">
          {t("import.chosen", { kind: label(kind, i18n.language) })}
          {stage < 4 && (
            <Button variant="ghost" size="sm" className="ms-2" onClick={reset}>
              {t("import.change")}
            </Button>
          )}
        </p>
      )}

      {stage === 1 && kind && (
        <FileStep
          kind={kind}
          businessId={current.id}
          onUploaded={(u) => {
            setUpload(u);
            setMapping(u.suggested_mapping);
          }}
        />
      )}

      {stage === 2 && kind && upload && (
        <MatchStep
          kind={kind}
          upload={upload}
          businessId={current.id}
          mapping={mapping}
          onMapping={setMapping}
          options={options}
          onOptions={setOptions}
          onChecked={setResult}
          onBack={() => setUpload(null)}
        />
      )}

      {stage === 3 && kind && result && (
        <div className="flex flex-col gap-4">
          <CheckResults kind={kind} result={result} />
          <p role="status" className={result.errors > 0 ? "text-destructive" : "text-sm"}>
            {result.errors > 0
              ? t("import.check.notClean")
              : result.new === 0
                ? t("import.check.nothingNew")
                : t("import.check.clean")}
          </p>
          {commit.isError && (
            <p role="alert" className="text-destructive">
              {errorText(t, commit.error)}
            </p>
          )}
          <div className="flex gap-3">
            <Button
              variant="outline"
              onClick={() => {
                commit.reset();
                setResult(null);
              }}
            >
              {t("import.check.back")}
            </Button>
            <Button
              onClick={() => commit.mutate()}
              disabled={result.errors > 0 || result.new === 0 || commit.isPending}
            >
              {commit.isPending ? t("import.importing") : t("import.importButton")}
            </Button>
          </div>
        </div>
      )}

      {stage === 4 && kind && done && <DoneStep kind={kind} batch={done} onAnother={reset} />}
    </div>
  );
}

function PickStep({ kinds, onPick }: { kinds: ImportKind[]; onPick: (k: ImportKind) => void }) {
  const { t, i18n } = useTranslation();
  return (
    <section aria-label={t("import.steps.pick")} className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">{t("import.recommended")}</p>
      <ul className="grid gap-3 sm:grid-cols-2">
        {kinds.map((k, i) => (
          <li key={k.key} className="flex flex-col gap-2 rounded-md border p-3">
            <div>
              <span className="text-muted-foreground">{i + 1}. </span>
              <span className="font-semibold">{label(k, i18n.language)}</span>
              <p className="text-sm text-muted-foreground">
                {i18n.language.startsWith("ar") ? k.description_ar : k.description_en}
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="self-start"
              aria-label={`${t("import.choose")} ${label(k, i18n.language)}`}
              onClick={() => onPick(k)}
            >
              {t("import.choose")}
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function FileStep({
  kind,
  businessId,
  onUploaded,
}: {
  kind: ImportKind;
  businessId: string;
  onUploaded: (u: Upload) => void;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const [file, setFile] = useState<File | null>(null);
  const [templateError, setTemplateError] = useState(false);
  const send = useMutation({
    mutationFn: async () => uploadImport(api, businessId, kind.key, file!),
    onSuccess: onUploaded,
  });
  const template = async (lang: "en" | "ar") => {
    setTemplateError(false);
    try {
      await downloadTemplate(api, kind.key, lang);
    } catch {
      setTemplateError(true);
    }
  };
  return (
    <section aria-label={t("import.file.section")} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm">{t("import.file.template")}</span>
        <Button variant="outline" size="sm" onClick={() => void template("en")}>
          {t("import.file.templateEn")}
        </Button>
        <Button variant="outline" size="sm" onClick={() => void template("ar")}>
          {t("import.file.templateAr")}
        </Button>
      </div>
      {templateError && (
        <p role="alert" className="text-destructive">
          {t("import.errors.generic")}
        </p>
      )}
      <Field label={t("import.file.choose")} hint={t("import.file.hint")} className="max-w-md">
        {(p) => (
          <input
            {...p}
            type="file"
            accept=".csv,.txt,.tsv,.xlsx,.xml"
            className={inputClass + " h-auto py-1.5"}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        )}
      </Field>
      {send.isError && (
        <p role="alert" className="text-destructive">
          {errorText(t, send.error)}
        </p>
      )}
      <div>
        <Button onClick={() => send.mutate()} disabled={!file || send.isPending}>
          {send.isPending ? t("import.file.uploading") : t("import.file.upload")}
        </Button>
      </div>
    </section>
  );
}

/** Where to look at what an import made. */
const LOOK: Record<string, { to: string; key: string }[]> = {
  accounts: [{ to: "/accounts", key: "chart" }],
  parties: [{ to: "/parties", key: "parties" }],
  products: [{ to: "/inventory", key: "items" }],
  opening_balances: [
    { to: "/accounts/trial-balance", key: "trialBalance" },
    { to: "/settings/import/reconciliation", key: "reconciliation" },
  ],
  party_bills: [
    { to: "/accounts/ageing", key: "ageing" },
    { to: "/settings/import/reconciliation", key: "reconciliation" },
  ],
  opening_stock: [
    { to: "/inventory/stock", key: "stock" },
    { to: "/settings/import/reconciliation", key: "reconciliation" },
  ],
};

function DoneStep({ kind, batch, onAnother }: { kind: ImportKind; batch: Batch; onAnother: () => void }) {
  const { t } = useTranslation();
  const counts = Object.entries((batch.result.counts ?? {}) as Record<string, unknown>);
  return (
    <section aria-label={t("import.steps.done")} className="flex flex-col gap-4">
      <p role="status" className="text-lg font-semibold">
        {t("import.done.title")}
      </p>
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {counts.map(([key, value]) => (
          <div key={key} className="rounded-md border px-3 py-2">
            <dt className="text-muted-foreground">{t(`import.done.counts.${key}`, { defaultValue: key })}</dt>
            <dd className="text-lg font-bold tabular-nums" dir="ltr">
              {key === "value" ? money(String(value)) : String(value)}
            </dd>
          </div>
        ))}
      </dl>
      {OPENING_KINDS.includes(kind.key) && (
        <p className="text-sm text-muted-foreground">{t("import.done.opening")}</p>
      )}
      <ul className="flex flex-wrap gap-4 text-sm">
        {(LOOK[kind.key] ?? []).map((l) => (
          <li key={l.key}>
            <Link to={l.to} className="underline underline-offset-2">
              {t(`import.done.look.${l.key}`)}
            </Link>
          </li>
        ))}
      </ul>
      <div>
        <Button onClick={onAnother}>{t("import.done.another")}</Button>
      </div>
    </section>
  );
}
