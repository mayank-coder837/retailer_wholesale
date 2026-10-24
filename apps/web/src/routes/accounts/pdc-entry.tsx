import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import type { PdcDirection, PdcIn } from "@/routes/accounts/pdc-queries";
import { money, todayIso, useAccounts, useBranches, useOpenBills } from "@/routes/accounts/queries";
import { PartyPicker } from "@/routes/accounts/voucher-entry";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { AMOUNT } from "@/routes/parties/queries";
import { useBusiness } from "@/shell/use-business";

export function PdcEntryRoute() {
  const search = useSearch({ strict: false }) as { direction?: string };
  const direction: PdcDirection = search.direction === "issued" ? "issued" : "received";
  return <PdcEntryPage key={direction} direction={direction} />;
}

/** Record a post-dated cheque (rules 15 and 20): received from a customer or issued to a supplier, with the open
 * bills it is meant for. Nothing reaches the books until it clears. */
export function PdcEntryPage({ direction }: { direction: PdcDirection }) {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const businessId = current?.id;
  const branches = useBranches(businessId);
  const accounts = useAccounts(businessId);
  const banks = (accounts.data ?? []).filter((a) => a.account_type === "bank" && !a.is_group);

  const [branchId, setBranchId] = useState("");
  const [partyId, setPartyId] = useState("");
  const [chequeNo, setChequeNo] = useState("");
  const [bankName, setBankName] = useState("");
  const [chequeDate, setChequeDate] = useState(todayIso());
  const [amount, setAmount] = useState("");
  const [drawnOn, setDrawnOn] = useState("");
  const [notes, setNotes] = useState("");
  const [intended, setIntended] = useState<Record<string, string>>({});
  const bills = useOpenBills(businessId, partyId);
  const owed = direction === "received" ? "debit" : "credit";
  const open = (bills.data ?? []).filter((b) => b.direction === owed && !b.is_advance);
  const branch = branchId || branches.data?.[0]?.id || "";

  const problem = (): string | null => {
    if (!businessId || !branch) return "accounts.voucher.noBranch";
    if (!partyId) return `pdc.errors.needParty.${direction}`;
    if (!chequeNo.trim()) return "pdc.errors.needChequeNo";
    if (!AMOUNT.test(amount.trim()) || Number(amount) <= 0) return "pdc.errors.needAmount";
    if (direction === "issued" && !drawnOn) return "pdc.errors.needBank";
    if (Object.values(intended).some((v) => v.trim() !== "" && !AMOUNT.test(v.trim())))
      return "pdc.errors.billAmount";
    return null;
  };

  const body = (): PdcIn => ({
    business_id: businessId!,
    branch_id: branch,
    direction,
    party_id: partyId,
    cheque_no: chequeNo.trim(),
    bank_name: bankName.trim() || null,
    cheque_date: chequeDate,
    amount: amount.trim(),
    deposit_account_id: direction === "issued" ? drawnOn : null,
    intended_bills: open
      .filter((b) => (intended[b.id] ?? "").trim() !== "" && Number(intended[b.id]) > 0)
      .map((b) => ({ bill_id: b.id, amount: intended[b.id]!.trim() })),
    notes: notes.trim() || null,
  });

  const save = useMutation({
    mutationFn: async () => unwrap(await api.POST("/pdcs", { body: body() })),
    onSuccess: async (pdc) => {
      await queryClient.invalidateQueries({ queryKey: ["pdcs"] });
      await navigate({ to: "/accounts/cheques/$pdcId", params: { pdcId: pdc.id } });
    },
  });
  const [tried, setTried] = useState(false);
  const local = problem();
  const failure = save.isError ? errorCode(save.error) : null;

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <h1 className="text-2xl font-bold leading-9">{t("accounts.title")}</h1>
      <AccountsNav />
      <form
        className="flex flex-col gap-4"
        aria-label={t(`pdc.new.${direction}`)}
        onSubmit={(e) => {
          e.preventDefault();
          setTried(true);
          if (!local) save.mutate();
        }}
      >
        <h2 className="text-lg font-semibold">{t(`pdc.new.${direction}`)}</h2>
        <p className="text-sm text-muted-foreground">{t("pdc.memoNote")}</p>
        <div className="flex flex-wrap items-start gap-3">
          <PartyPicker
            label={t(`pdc.partyLabel.${direction}`)}
            role={direction === "received" ? "customer" : "supplier"}
            value={partyId}
            onChange={(id) => {
              setPartyId(id);
              setIntended({});
            }}
          />
          {(branches.data?.length ?? 0) > 1 && (
            <Field label={t("accounts.voucher.branch")}>
              {(p) => (
                <select
                  {...p}
                  className={inputClass}
                  value={branch}
                  onChange={(e) => setBranchId(e.target.value)}
                >
                  {branches.data?.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.code}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
        </div>
        <div className="grid items-start gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label={t("pdc.chequeNo")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                dir="ltr"
                value={chequeNo}
                onChange={(e) => setChequeNo(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("pdc.bank")} hint={t("pdc.bankHint")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                value={bankName}
                onChange={(e) => setBankName(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("pdc.chequeDate")} hint={t("pdc.chequeDateHint")}>
            {(p) => (
              <input
                {...p}
                type="date"
                className={inputClass}
                value={chequeDate}
                onChange={(e) => setChequeDate(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("pdc.amount")}>
            {(p) => (
              <input
                {...p}
                className={cn(inputClass, "text-right")}
                dir="ltr"
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            )}
          </Field>
        </div>
        {direction === "issued" && (
          <Field label={t("pdc.drawnOn")} className="max-w-sm">
            {(p) => (
              <select
                {...p}
                className={inputClass}
                value={drawnOn}
                onChange={(e) => setDrawnOn(e.target.value)}
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
        {partyId && (
          <section aria-label={t("pdc.intended")} className="flex flex-col gap-2">
            <h3 className="font-semibold">{t("pdc.intended")}</h3>
            <p className="text-sm text-muted-foreground">{t("pdc.intendedHint")}</p>
            {open.length === 0 ? (
              <p className="text-sm text-muted-foreground">{t("pdc.noOpenBills")}</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 text-start font-medium">{t("accounts.billWise.reference")}</th>
                    <th className="py-1 text-start font-medium">{t("accounts.billWise.due")}</th>
                    <th className="py-1 text-right font-medium">{t("accounts.billWise.open")}</th>
                    <th className="py-1 text-right font-medium">{t("pdc.forThisCheque")}</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {open.map((b) => (
                    <tr key={b.id}>
                      <td className="py-1 font-mono">
                        <span dir="ltr">{b.reference}</span>
                      </td>
                      <td className="py-1">
                        <span dir="ltr">{b.due_date ?? ""}</span>
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        <span dir="ltr">{money(b.amount_open)}</span>
                      </td>
                      <td className="py-1 text-right">
                        <input
                          className={cn(inputClass, "ms-auto w-32 text-right")}
                          dir="ltr"
                          inputMode="decimal"
                          aria-label={t("pdc.forBill", { reference: b.reference })}
                          value={intended[b.id] ?? ""}
                          onChange={(e) => setIntended((m) => ({ ...m, [b.id]: e.target.value }))}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        )}
        <Field label={t("pdc.notes")}>
          {(p) => (
            <input {...p} className={inputClass} value={notes} onChange={(e) => setNotes(e.target.value)} />
          )}
        </Field>
        {tried && local && (
          <p role="alert" className="text-destructive">
            {t(local)}
          </p>
        )}
        {failure && (
          <p role="alert" className="text-destructive">
            {t(`pdc.errors.${failure}`, {
              defaultValue: t(`accounts.errors.${failure}`, { defaultValue: t("accounts.errors.generic") }),
            })}
          </p>
        )}
        <div>
          <Button type="submit" disabled={save.isPending}>
            {t("pdc.save")}
          </Button>
        </div>
      </form>
    </div>
  );
}
