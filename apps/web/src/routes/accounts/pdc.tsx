import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import { usePdc, type Pdc } from "@/routes/accounts/pdc-queries";
import { money, todayIso, useAccounts } from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { AMOUNT } from "@/routes/parties/queries";

type Action = "deposit" | "clear" | "bounce" | "return" | "cancel";

/** What a cheque may do next (posting rules 16 to 22), as the API's state machine allows. */
function actionsFor(p: Pdc): Action[] {
  if (p.direction === "received") {
    if (p.status === "in_hand") return ["deposit", "clear", "bounce", "return"];
    if (p.status === "deposited") return ["clear", "bounce"];
    return [];
  }
  return p.status === "issued" ? ["clear", "bounce", "cancel"] : [];
}

export function PdcPage() {
  const { pdcId } = useParams({ strict: false }) as { pdcId: string };
  const { t, i18n } = useTranslation();
  const pdc = usePdc(pdcId);

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("accounts.title")}</h1>
      <AccountsNav />
      {pdc.isPending && <p>{t("common.loading")}</p>}
      {pdc.isError && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(pdc.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      {pdc.data && (
        <>
          <Details p={pdc.data} language={i18n.language} />
          <Actions p={pdc.data} />
        </>
      )}
    </div>
  );
}

function Details({ p, language }: { p: Pdc; language: string }) {
  const { t } = useTranslation();
  const rows: [string, React.ReactNode][] = [
    [t(`pdc.partyLabel.${p.direction}`), language === "ar" ? p.party_name_ar : p.party_name_en],
    [t("pdc.bank"), p.bank_name ?? ""],
    [t("pdc.chequeDate"), <span dir="ltr">{p.cheque_date}</span>],
    [
      t("pdc.amount"),
      <span dir="ltr" className="tabular-nums">
        {money(p.amount)} {p.currency}
      </span>,
    ],
  ];
  if (p.deposited_on) rows.push([t("pdc.depositedOn"), <span dir="ltr">{p.deposited_on}</span>]);
  if (p.cleared_on) rows.push([t("pdc.clearedOn"), <span dir="ltr">{p.cleared_on}</span>]);
  if (p.bounced_on)
    rows.push([
      t("pdc.bouncedOn"),
      <span>
        <span dir="ltr">{p.bounced_on}</span> — {p.bounce_reason}
      </span>,
    ]);
  if (p.notes) rows.push([t("pdc.notes"), p.notes]);

  return (
    <section className="flex flex-col gap-3" aria-label={t("pdc.cheque")}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b pb-2">
        <h2 className="text-lg font-semibold">
          {t(`pdc.directions.${p.direction}`)}{" "}
          <span className="font-mono" dir="ltr">
            {p.cheque_no}
          </span>
        </h2>
        <span className="rounded bg-secondary px-2 py-0.5 text-sm font-medium">
          {t(`pdc.statuses.${p.status}`)}
        </span>
      </div>
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {p.voucher_id && (
        <p className="text-sm">
          {t(`pdc.postedAs.${p.direction}`)}{" "}
          <Link
            to="/accounts/vouchers/$voucherId"
            params={{ voucherId: p.voucher_id }}
            className="font-mono underline underline-offset-2"
          >
            <span dir="ltr">{p.voucher_number ?? ""}</span>
          </Link>
        </p>
      )}
      {p.charge_voucher_id && (
        <p className="text-sm">
          <Link
            to="/accounts/vouchers/$voucherId"
            params={{ voucherId: p.charge_voucher_id }}
            className="underline underline-offset-2"
          >
            {t("pdc.chargeVoucher")}
          </Link>
        </p>
      )}
      {p.intended_bills.length > 0 && (
        <table className="w-full text-sm" aria-label={t("pdc.intended")}>
          <caption className="py-2 text-start font-semibold">{t("pdc.intended")}</caption>
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 text-start font-medium">{t("accounts.billWise.reference")}</th>
              <th className="py-1 text-right font-medium">{t("pdc.forThisCheque")}</th>
              <th className="py-1 text-right font-medium">{t("pdc.stillOpen")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {p.intended_bills.map((b) => (
              <tr key={b.bill_id}>
                <td className="py-1 font-mono">
                  <span dir="ltr">{b.reference}</span>
                </td>
                <td className="py-1 text-right tabular-nums">
                  <span dir="ltr">{money(b.amount)}</span>
                </td>
                <td className="py-1 text-right tabular-nums">
                  <span dir="ltr">{money(b.amount_open)}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Deposit, clear, bounce, return or cancel: one form at a time, with only what that step needs. */
function Actions({ p }: { p: Pdc }) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const accounts = useAccounts(p.business_id);
  const banks = (accounts.data ?? []).filter((a) => a.account_type === "bank" && !a.is_group);
  const [mode, setMode] = useState<Action | null>(null);
  const [date, setDate] = useState(todayIso());
  const [bank, setBank] = useState("");
  const [reason, setReason] = useState("");
  const [charge, setCharge] = useState("");
  const [lockReason, setLockReason] = useState<string | null>(null);
  const needsBank = mode === "deposit" || (mode === "clear" && !p.deposit_account_id);
  const path = { params: { path: { pdc_id: p.id } } };

  const act = useMutation({
    mutationFn: async () => {
      if (mode === "deposit")
        return unwrap(
          await api.POST("/pdcs/{pdc_id}/deposit", { ...path, body: { bank_account_id: bank, date } }),
        );
      if (mode === "clear")
        return unwrap(
          await api.POST("/pdcs/{pdc_id}/clear", {
            ...path,
            body: {
              date,
              bank_account_id: needsBank ? bank : null,
              override_lock_reason: lockReason?.trim() || null,
            },
          }),
        );
      if (mode === "bounce")
        return unwrap(
          await api.POST("/pdcs/{pdc_id}/bounce", {
            ...path,
            body: { date, reason: reason.trim(), charge: charge.trim() || null },
          }),
        );
      if (mode === "return") return unwrap(await api.POST("/pdcs/{pdc_id}/return", path));
      return unwrap(await api.POST("/pdcs/{pdc_id}/cancel", path));
    },
    onSuccess: async () => {
      setMode(null);
      setLockReason(null);
      // Clearing posts a voucher: ledgers, bills, statements and ageing all change.
      await queryClient.invalidateQueries();
    },
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean };
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
    },
  });

  const actions = actionsFor(p);
  if (actions.length === 0) return null;
  const chargeOk = charge.trim() === "" || (AMOUNT.test(charge.trim()) && Number(charge) > 0);
  const ready =
    (!needsBank || bank !== "") &&
    (mode !== "bounce" || (reason.trim() !== "" && chargeOk)) &&
    (lockReason === null || lockReason.trim() !== "");
  const failure = act.isError ? errorCode(act.error) : null;
  const dated = mode === "deposit" || mode === "clear" || mode === "bounce";

  return (
    <section aria-label={t("pdc.actions")} className="flex flex-col gap-3">
      {mode === null ? (
        <div className="flex flex-wrap gap-2">
          {actions.map((a) => (
            <Button key={a} variant={a === "clear" ? "default" : "outline"} onClick={() => setMode(a)}>
              {t(`pdc.do.${a}`)}
            </Button>
          ))}
        </div>
      ) : (
        <form
          className="flex flex-col gap-3 rounded-lg border p-4"
          aria-label={t(`pdc.do.${mode}`)}
          onSubmit={(e) => {
            e.preventDefault();
            if (ready) act.mutate();
          }}
        >
          <h2 className="font-semibold">{t(`pdc.do.${mode}`)}</h2>
          <p className="text-sm text-muted-foreground">{t(`pdc.hints.${mode}.${p.direction}`)}</p>
          <div className="grid items-start gap-3 sm:grid-cols-2">
            {dated && (
              <Field label={t(`pdc.dates.${mode}`)}>
                {(f) => (
                  <input
                    {...f}
                    type="date"
                    className={inputClass}
                    value={date}
                    onChange={(e) => setDate(e.target.value)}
                  />
                )}
              </Field>
            )}
            {needsBank && (
              <Field label={t("pdc.depositTo")}>
                {(f) => (
                  <select
                    {...f}
                    className={inputClass}
                    value={bank}
                    onChange={(e) => setBank(e.target.value)}
                  >
                    <option value="">{t("accounts.voucher.choose")}</option>
                    {banks.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.code} {a.name_en}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
            )}
            {mode === "bounce" && (
              <>
                <Field label={t("pdc.bounceReason")}>
                  {(f) => (
                    <input
                      {...f}
                      className={inputClass}
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                    />
                  )}
                </Field>
                {p.deposit_account_id && (
                  <Field
                    label={t("pdc.bankCharge")}
                    hint={t("pdc.bankChargeHint")}
                    error={chargeOk ? null : t("pdc.errors.needAmount")}
                  >
                    {(f) => (
                      <input
                        {...f}
                        className={cn(inputClass, "text-right")}
                        dir="ltr"
                        inputMode="decimal"
                        value={charge}
                        onChange={(e) => setCharge(e.target.value)}
                      />
                    )}
                  </Field>
                )}
              </>
            )}
            {lockReason !== null && (
              <Field label={t("accounts.voucher.lockReason")} hint={t("accounts.voucher.lockHint")}>
                {(f) => (
                  <input
                    {...f}
                    className={inputClass}
                    value={lockReason}
                    onChange={(e) => setLockReason(e.target.value)}
                  />
                )}
              </Field>
            )}
          </div>
          {failure && (
            <p role="alert" className="text-destructive">
              {t(`pdc.errors.${failure}`, {
                defaultValue: t(`accounts.errors.${failure}`, { defaultValue: t("accounts.errors.generic") }),
              })}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!ready || act.isPending}>
              {t(`pdc.confirm.${mode}`)}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setMode(null);
                setLockReason(null);
                act.reset();
              }}
            >
              {t("accounts.cancel")}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
