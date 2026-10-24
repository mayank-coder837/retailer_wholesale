import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { Button } from "@/components/ui/button";
import type { PairingResult, RuntimeResult } from "@/config/runtime";

type Backup = components["schemas"]["BackupOut"];

/** Settings → This PC (M0.6): the local server on the main PC, the connection on an office PC. */
export function ThisPcPage() {
  const { t } = useTranslation();
  const role = window.smartbiz?.role;

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <h1 className="text-2xl font-bold leading-9">{t("thisPc.title")}</h1>
      {role === "main" && <MainPc />}
      {role === "office" && <OfficePc />}
      {!role && <p className="text-muted-foreground">{t("thisPc.browser")}</p>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border p-5" aria-label={title}>
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Problem({ code }: { code: string | null }) {
  const { t } = useTranslation();
  if (!code) return null;
  return (
    <p role="alert" className="text-sm text-destructive">
      {t(`thisPc.errors.${code}`, { defaultValue: t("thisPc.errors.failed") })}
    </p>
  );
}

function MainPc() {
  const { t } = useTranslation();
  const runtime = window.smartbiz?.runtime;
  const info = useQuery({ queryKey: ["runtime-info"], queryFn: () => runtime!.info(), enabled: !!runtime });
  const [problem, setProblem] = useState<string | null>(null);
  const [pairing, setPairing] = useState<Extract<PairingResult, { ok: true }> | null>(null);

  const report = (result: RuntimeResult | PairingResult) => setProblem(result.ok ? null : result.code);

  const lan = useMutation({
    mutationFn: (enabled: boolean) => runtime!.setLan(enabled),
    onSuccess: report, // on success the desktop app reloads this window at the new address
  });
  const openPairing = useMutation({
    mutationFn: () => runtime!.openPairing(),
    onSuccess: (result) => {
      report(result);
      setPairing(result.ok ? result : null);
    },
  });
  const closePairing = useMutation({
    mutationFn: () => runtime!.closePairing(),
    onSuccess: () => setPairing(null),
  });
  const makePrivate = useMutation({
    mutationFn: () => runtime!.makeNetworkPrivate(),
    onSuccess: (result) => {
      report(result);
      void info.refetch();
    },
  });

  if (!runtime || info.isPending) return <p>{t("common.loading")}</p>;
  if (info.isError) return <Problem code="failed" />;
  const server = info.data;
  const publicNetworks = server.networks.filter((n) => n.category === "Public");

  return (
    <>
      <p>{t("thisPc.mainRole")}</p>
      <Problem code={problem} />
      <Section title={t("thisPc.serverTitle")}>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
          <dt className="text-muted-foreground">{t("thisPc.serverName")}</dt>
          <dd>{server.serverName}</dd>
          <dt className="text-muted-foreground">{t("thisPc.version")}</dt>
          <dd dir="ltr" className="text-start">
            {server.version}
          </dd>
        </dl>
      </Section>

      <Section title={t("thisPc.lanTitle")}>
        <p className="text-sm">{server.lan ? t("thisPc.lanOn") : t("thisPc.lanOff")}</p>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant={server.lan ? "outline" : "default"}
            disabled={lan.isPending}
            onClick={() => lan.mutate(!server.lan)}
          >
            {server.lan ? t("thisPc.lanTurnOff") : t("thisPc.lanTurnOn")}
          </Button>
          <span className="text-xs text-muted-foreground">{t("thisPc.adminHint")}</span>
        </div>
        {server.lan && publicNetworks.length > 0 && (
          <div
            role="alert"
            className="flex flex-col gap-2 rounded-md border border-destructive/40 p-3 text-sm"
          >
            <p>
              {t("thisPc.publicNetwork", {
                networks: publicNetworks.map((n) => n.network || n.adapter).join(", "),
              })}
            </p>
            <Button
              variant="outline"
              className="self-start"
              disabled={makePrivate.isPending}
              onClick={() => makePrivate.mutate()}
            >
              {t("thisPc.makePrivate")}
            </Button>
          </div>
        )}
        {server.lan && <p className="text-xs text-muted-foreground">{t("thisPc.fixedIpHint")}</p>}
      </Section>

      <Section title={t("thisPc.pairTitle")}>
        {!server.lan && <p className="text-sm text-muted-foreground">{t("thisPc.pairNeedsLan")}</p>}
        {server.lan && !pairing && (
          <Button
            className="self-start"
            disabled={openPairing.isPending}
            onClick={() => openPairing.mutate()}
          >
            {t("thisPc.pairShow")}
          </Button>
        )}
        {pairing && <PairingCode pairing={pairing} onClose={() => closePairing.mutate()} />}
      </Section>

      <Backups onProblem={setProblem} />
    </>
  );
}

function PairingCode({
  pairing,
  onClose,
}: {
  pairing: Extract<PairingResult, { ok: true }>;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const time = new Intl.DateTimeFormat(i18n.language, { timeStyle: "short" }).format(
    new Date(pairing.expiresAt),
  );
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm">{t("thisPc.pairInstructions")}</p>
      <dl className="grid grid-cols-[max-content_1fr] items-baseline gap-x-6 gap-y-1">
        <dt className="text-sm text-muted-foreground">{t("thisPc.pairAddress")}</dt>
        <dd dir="ltr" className="text-start font-mono">
          {[pairing.computerName, ...pairing.addresses].map((a) => `${a}:${pairing.port}`).join("  ·  ")}
        </dd>
        <dt className="text-sm text-muted-foreground">{t("thisPc.pairCode")}</dt>
        <dd dir="ltr" className="text-start font-mono text-3xl tracking-[0.3em]" data-testid="pairing-code">
          {pairing.code}
        </dd>
      </dl>
      <p className="text-xs text-muted-foreground">{t("thisPc.pairExpires", { time })}</p>
      <Button variant="outline" className="self-start" onClick={onClose}>
        {t("thisPc.pairClose")}
      </Button>
    </div>
  );
}

function Backups({ onProblem }: { onProblem: (code: string | null) => void }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState<string | null>(null);
  const backups = useQuery({
    queryKey: ["backups"],
    queryFn: async () => {
      const result = await api.GET("/system/backups");
      const status: number = result.response.status; // 503 is not in the schema: read it before narrowing
      if (result.data) return result.data;
      throw new Error(status === 503 ? "unavailable" : "forbidden");
    },
    retry: false,
  });
  const backupNow = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/system/backups");
      if (error || !data) throw new Error((error as { code?: string } | undefined)?.code ?? "failed");
      return data;
    },
    onSuccess: (backup) => {
      onProblem(null);
      setSaved(backup.name);
      void queryClient.invalidateQueries({ queryKey: ["backups"] });
    },
    onError: (error) => onProblem(error.message),
  });
  const restore = useMutation({
    mutationFn: (name: string) => window.smartbiz!.runtime!.restore(name),
    onSuccess: (result) => onProblem(result.ok ? null : result.code),
  });

  const date = (b: Backup) =>
    new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(
      new Date(b.created_at),
    );
  const size = (b: Backup) =>
    new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 }).format(b.size_bytes / 1024 / 1024);

  return (
    <Section title={t("thisPc.backupsTitle")}>
      {backups.isPending && <p>{t("common.loading")}</p>}
      {backups.isError && (
        <p className="text-sm text-muted-foreground">
          {backups.error.message === "unavailable"
            ? t("thisPc.backupsUnavailable")
            : t("thisPc.backupsForbidden")}
        </p>
      )}
      {backups.isSuccess && (
        <>
          <p className="text-sm text-muted-foreground">
            {t("thisPc.backupsHint", { keep: backups.data.keep })}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button className="self-start" disabled={backupNow.isPending} onClick={() => backupNow.mutate()}>
              {backupNow.isPending ? t("thisPc.backingUp") : t("thisPc.backupNow")}
            </Button>
            {saved && (
              <span role="status" className="text-sm">
                {t("thisPc.backupDone", { name: saved })}
              </span>
            )}
          </div>
          {backups.data.backups.length === 0 ? (
            <p className="text-sm">{t("thisPc.backupsNone")}</p>
          ) : (
            <ul className="divide-y rounded-md border text-sm">
              {backups.data.backups.map((b) => (
                <li key={b.name} className="flex items-center justify-between gap-4 px-3 py-2">
                  <span>
                    {date(b)}{" "}
                    <span className="text-muted-foreground">· {t("thisPc.sizeMb", { size: size(b) })}</span>
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={restore.isPending}
                    onClick={() => {
                      if (window.confirm(t("thisPc.restoreConfirm", { date: date(b) })))
                        restore.mutate(b.name);
                    }}
                  >
                    {t("thisPc.restore")}
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <BackupCopy
            copyDir={backups.data.copy_dir ?? null}
            lastCopy={backups.data.last_copy ?? null}
            onProblem={onProblem}
          />
        </>
      )}
    </Section>
  );
}

/** Founder decision L1: every backup is also copied to a folder the owner picks (USB drive, NAS). */
function BackupCopy({
  copyDir,
  lastCopy,
  onProblem,
}: {
  copyDir: string | null;
  lastCopy: components["schemas"]["BackupCopyOut"] | null;
  onProblem: (code: string | null) => void;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const runtime = window.smartbiz?.runtime;
  const change = useMutation({
    mutationFn: (choose: boolean) => (choose ? runtime!.chooseBackupCopy() : runtime!.stopBackupCopy()),
    onSuccess: (result) => {
      if (!result.ok && result.code === "cancelled") return; // closed the folder picker
      onProblem(result.ok ? null : result.code);
      if (result.ok) void queryClient.invalidateQueries({ queryKey: ["backups"] });
    },
  });
  const when = (at: string) =>
    new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(new Date(at));

  return (
    <div className="mt-2 flex flex-col gap-2 border-t pt-4" aria-label={t("thisPc.copyTitle")} role="group">
      <h3 className="font-semibold">{t("thisPc.copyTitle")}</h3>
      <p className="text-sm text-muted-foreground">{t("thisPc.copyHint")}</p>
      <p className="text-sm">
        {copyDir ? (
          <span dir="ltr" className="font-mono" data-testid="copy-dir">
            {copyDir}
          </span>
        ) : (
          t("thisPc.copyNone")
        )}
      </p>
      {copyDir && lastCopy && (
        <p className={lastCopy.error ? "text-sm text-destructive" : "text-sm"} data-testid="last-copy">
          {lastCopy.error
            ? t("thisPc.copyLastFailed", { date: when(lastCopy.at), error: lastCopy.error })
            : t("thisPc.copyLastOk", { date: when(lastCopy.at) })}
        </p>
      )}
      {runtime && (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" disabled={change.isPending} onClick={() => change.mutate(true)}>
            {copyDir ? t("thisPc.copyChange") : t("thisPc.copyChoose")}
          </Button>
          {copyDir && (
            <Button variant="ghost" disabled={change.isPending} onClick={() => change.mutate(false)}>
              {t("thisPc.copyStop")}
            </Button>
          )}
          <span className="text-xs text-muted-foreground">{t("thisPc.copyNasHint")}</span>
        </div>
      )}
    </div>
  );
}

function OfficePc() {
  const { t } = useTranslation();
  const disconnect = window.smartbiz?.disconnect;
  return (
    <>
      <p>{t("thisPc.officeRole", { address: window.location.host })}</p>
      {disconnect && (
        <Button
          variant="outline"
          className="self-start"
          onClick={() => {
            if (window.confirm(t("thisPc.disconnectConfirm"))) void disconnect();
          }}
        >
          {t("thisPc.disconnect")}
        </Button>
      )}
    </>
  );
}
