import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import { BillWisePicker } from "@/routes/accounts/bill-wise";
import { allocationsOf, ON_ACCOUNT, type BillWise } from "@/routes/accounts/bill-wise-model";
import {
  accountLabel,
  CASH_OR_BANK,
  CONTROL,
  fromFils,
  isVoucherType,
  money,
  postable,
  SINGLE_ENTRY,
  todayIso,
  toFils,
  useAccounts,
  useBranches,
  vatFils,
  VOUCHER_AMOUNT,
  WITH_CASH_ACCOUNT,
  WITH_PARTY,
  WITH_VAT,
  type Account,
  type AccountType,
  type VoucherIn,
  type VoucherLineIn,
  type VoucherType,
} from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { ComingSoon } from "@/routes/coming-soon";
import { partyName, useParties, type Role } from "@/routes/parties/queries";
import { useBusiness } from "@/shell/use-business";

/** Accounts a single-entry line may not use (the API refuses them too). */
const NOT_ON_LINES: Partial<Record<VoucherType, AccountType[]>> = {
  receipt: ["cash", "bank"],
  payment: ["cash", "bank"],
  expense: ["cash", "bank", "receivable", "payable", "tax", "stock"],
  credit_note: ["cash", "bank", "receivable", "payable", "tax"],
  debit_note: ["cash", "bank", "receivable", "payable", "tax"],
};
const LINE_SIDE: Partial<Record<VoucherType, "debit" | "credit">> = {
  receipt: "credit",
  payment: "debit",
  expense: "debit",
  credit_note: "debit",
  debit_note: "credit",
};
/** Which parties a line or header offers. */
const PARTY_ROLE: Partial<Record<VoucherType, Role>> = {
  receipt: "customer",
  payment: "supplier",
  credit_note: "customer",
  debit_note: "supplier",
};

interface LineState {
  key: number;
  accountId: string; // empty: the default (party's control account, or the note's default)
  partyId: string;
  amount: string;
  debit: string;
  credit: string;
  vat: "0" | "5";
  description: string;
  billWise: BillWise;
}

let nextKey = 1;
const emptyLine = (): LineState => ({
  key: nextKey++,
  accountId: "",
  partyId: "",
  amount: "",
  debit: "",
  credit: "",
  vat: "0",
  description: "",
  billWise: ON_ACCOUNT,
});

const filsOf = (text: string) => (VOUCHER_AMOUNT.test(text.trim()) ? toFils(text) : 0);

/** The voucher route: the seven accounting vouchers here; sales invoices and purchase bills come later. */
export function VoucherRoute() {
  const { kind } = useParams({ strict: false }) as { kind: string };
  if (!isVoucherType(kind)) return <ComingSoon titleKey={`vouchers.${kind}`} />;
  return <VoucherEntryPage key={kind} kind={kind} />;
}

/** Keyboard-first voucher entry (M1.5): Tally's single-entry style for receipts, payments and the rest. */
export function VoucherEntryPage({ kind }: { kind: VoucherType }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const businessId = current?.id;
  const accounts = useAccounts(businessId);
  const branches = useBranches(businessId);

  const [date, setDate] = useState(todayIso());
  const [branchId, setBranchId] = useState("");
  const [reference, setReference] = useState("");
  const [narration, setNarration] = useState("");
  const [headerAccount, setHeaderAccount] = useState("");
  const [headerParty, setHeaderParty] = useState("");
  const [headerBillWise, setHeaderBillWise] = useState<BillWise>(ON_ACCOUNT);
  const [lines, setLines] = useState<LineState[]>(() => [
    emptyLine(),
    ...(kind === "journal" || kind === "contra" ? [emptyLine()] : []),
  ]);
  const [tried, setTried] = useState(false);
  const [lockReason, setLockReason] = useState<string | null>(null);

  const all = accounts.data ?? [];
  const byId = new Map(all.map((a) => [a.id, a]));
  const cashAccounts = postable(all, CASH_OR_BANK);
  const branch = branchId || branches.data?.[0]?.id || "";
  const cash = headerAccount || cashAccounts[0]?.id || "";
  const single = SINGLE_ENTRY.includes(kind);
  const side = LINE_SIDE[kind];
  const lineAccounts =
    kind === "contra"
      ? cashAccounts
      : postable(all).filter((a) => !(NOT_ON_LINES[kind] ?? []).includes(a.account_type as AccountType));

  const update = (key: number, patch: Partial<LineState>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  // Totals in fils (integers): exact, like the API.
  const net = lines.reduce((sum, l) => sum + filsOf(l.amount), 0);
  const vat = WITH_VAT.includes(kind)
    ? lines.reduce((sum, l) => sum + vatFils(filsOf(l.amount), Number(l.vat)), 0)
    : 0;
  const debits = lines.reduce((sum, l) => sum + filsOf(l.debit), 0);
  const credits = lines.reduce((sum, l) => sum + filsOf(l.credit), 0);
  const total = single ? net + vat : debits;

  function lineAccount(l: LineState): Account | undefined {
    return l.accountId ? byId.get(l.accountId) : undefined;
  }
  function isPartyLine(l: LineState): boolean {
    if (single) return !!l.partyId;
    const account = lineAccount(l);
    return !!account?.account_type && CONTROL.includes(account.account_type);
  }
  function lineSide(l: LineState): "debit" | "credit" {
    return side ?? (filsOf(l.debit) > 0 ? "debit" : "credit");
  }
  function lineFils(l: LineState): number {
    return single ? filsOf(l.amount) : filsOf(l.debit) || filsOf(l.credit);
  }

  /** The request body, or the first problem to show. */
  function build(): VoucherIn | string {
    if (!businessId || !branch) return "accounts.voucher.noBranch";
    if (WITH_CASH_ACCOUNT.includes(kind) && !cash) return "accounts.voucher.needCash";
    if (WITH_PARTY.includes(kind) && !headerParty) return "accounts.voucher.needParty";
    const out: VoucherLineIn[] = [];
    for (const l of lines) {
      const blank = !l.amount && !l.debit && !l.credit && !l.accountId && !l.partyId;
      if (blank) continue;
      if (single) {
        if (!VOUCHER_AMOUNT.test(l.amount.trim()) || filsOf(l.amount) === 0) return "accounts.errors.amount";
        if (!l.partyId && !l.accountId && !WITH_PARTY.includes(kind)) return "accounts.voucher.needAccount";
      } else {
        const d = l.debit.trim();
        const c = l.credit.trim();
        if ((d && c) || (!d && !c)) return "accounts.voucher.oneSide";
        if (!VOUCHER_AMOUNT.test(d || c) || filsOf(d || c) === 0) return "accounts.errors.amount";
        if (!l.accountId) return "accounts.voucher.needAccount";
        if (isPartyLine(l) && !l.partyId) return "accounts.voucher.needLineParty";
      }
      const allocations = isPartyLine(l) ? allocationsOf(l.billWise, lineFils(l)) : [];
      if (typeof allocations === "string") return allocations;
      const line: VoucherLineIn = {
        account_id: l.accountId || null,
        party_id: l.partyId || null,
        description: l.description.trim() || null,
        vat_rate: WITH_VAT.includes(kind) ? l.vat : "0",
        allocations,
      };
      if (single) line.amount = fromFils(filsOf(l.amount));
      else if (l.debit.trim()) line.debit = fromFils(filsOf(l.debit));
      else line.credit = fromFils(filsOf(l.credit));
      out.push(line);
    }
    if (out.length === 0) return "accounts.voucher.noLines";
    if (!single && debits !== credits) return "accounts.voucher.unbalanced";
    let headerAllocations: VoucherIn["allocations"] = [];
    if (WITH_PARTY.includes(kind)) {
      const result = allocationsOf(headerBillWise, total);
      if (typeof result === "string") return result;
      headerAllocations = result;
    }
    return {
      doc_type: kind,
      business_id: businessId,
      branch_id: branch,
      doc_date: date,
      reference: reference.trim() || null,
      narration: narration.trim() || null,
      account_id: WITH_CASH_ACCOUNT.includes(kind) ? cash : null,
      party_id: WITH_PARTY.includes(kind) ? headerParty : null,
      allocations: headerAllocations,
      lines: out,
    };
  }
  const built = build();

  const save = useMutation({
    mutationFn: async ({ post }: { post: boolean }) => {
      if (typeof built === "string") throw { code: "client" };
      if (post && lockReason !== null) {
        // Into a locked month: save the draft, then post it with the reason (needs period.override_lock).
        const draft = unwrap(await api.POST("/vouchers", { body: built }));
        return unwrap(
          await api.POST("/vouchers/{doc_id}/post", {
            params: { path: { doc_id: draft.id } },
            body: { override_lock_reason: lockReason.trim() },
          }),
        );
      }
      return unwrap(await api.POST("/vouchers", { body: built, params: { query: { post } } }));
    },
    onSuccess: async (voucher) => {
      await queryClient.invalidateQueries({ queryKey: ["vouchers"] });
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      await queryClient.invalidateQueries({ queryKey: ["bills"] });
      await navigate({ to: "/accounts/vouchers/$voucherId", params: { voucherId: voucher.id } });
    },
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean };
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
    },
  });

  function submit(post: boolean) {
    setTried(true);
    if (typeof built === "string") return;
    if (post && lockReason !== null && !lockReason.trim()) return;
    save.mutate({ post });
  }

  function onKeyDown(e: KeyboardEvent<HTMLFormElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submit(true);
    }
  }

  const problem = typeof built === "string" ? built : null;
  const failure = save.isError ? errorCode(save.error) : null;

  return (
    <form
      className="flex max-w-5xl flex-col gap-5"
      noValidate
      onKeyDown={onKeyDown}
      onSubmit={(e) => {
        e.preventDefault();
        submit(true);
      }}
    >
      <h1 className="text-2xl font-bold">{t(`accounts.voucherTypes.${kind}`)}</h1>
      <AccountsNav />
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label={t("accounts.voucher.date")}>
          {(p) => (
            <input
              {...p}
              type="date"
              className={inputClass}
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          )}
        </Field>
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
                    {i18n.language === "ar" ? b.name_ar : b.name_en}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
        {WITH_CASH_ACCOUNT.includes(kind) && (
          <Field label={t(kind === "receipt" ? "accounts.voucher.into" : "accounts.voucher.from")}>
            {(p) => (
              <select
                {...p}
                className={inputClass}
                value={cash}
                onChange={(e) => setHeaderAccount(e.target.value)}
              >
                {cashAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {accountLabel(a, i18n.language)}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
        <Field label={t("accounts.voucher.reference")} hint={t("common.optional")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            />
          )}
        </Field>
      </div>

      {WITH_PARTY.includes(kind) && (
        <div className="flex flex-col gap-2">
          <PartyPicker
            label={t(kind === "credit_note" ? "accounts.voucher.customer" : "accounts.voucher.supplier")}
            role={PARTY_ROLE[kind]!}
            value={headerParty}
            onChange={setHeaderParty}
          />
          {headerParty && businessId && (
            <BillWisePicker
              businessId={businessId}
              partyId={headerParty}
              side={kind === "credit_note" ? "credit" : "debit"}
              amountFils={total}
              value={headerBillWise}
              onChange={setHeaderBillWise}
            />
          )}
        </div>
      )}

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 font-semibold">{t("accounts.voucher.lines")}</legend>
        {lines.map((l, i) => {
          const account = lineAccount(l);
          const needsParty = !single && !!account?.account_type && CONTROL.includes(account.account_type);
          return (
            <div key={l.key} className="flex flex-col gap-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-end gap-2">
                {single && PARTY_ROLE[kind] && !WITH_PARTY.includes(kind) && (
                  <PartyPicker
                    label={t("accounts.voucher.party")}
                    role={PARTY_ROLE[kind]!}
                    value={l.partyId}
                    allowNone
                    onChange={(partyId) => update(l.key, { partyId, accountId: partyId ? "" : l.accountId })}
                  />
                )}
                {!(single && l.partyId) && (
                  <Field label={t("accounts.voucher.account")} className="min-w-56 flex-1">
                    {(p) => (
                      <select
                        {...p}
                        className={inputClass}
                        value={l.accountId}
                        onChange={(e) => update(l.key, { accountId: e.target.value })}
                      >
                        <option value="">
                          {WITH_PARTY.includes(kind)
                            ? t("accounts.voucher.defaultAccount")
                            : t("accounts.voucher.choose")}
                        </option>
                        {lineAccounts.map((a) => (
                          <option key={a.id} value={a.id}>
                            {accountLabel(a, i18n.language)}
                          </option>
                        ))}
                      </select>
                    )}
                  </Field>
                )}
                {needsParty && (
                  <PartyPicker
                    label={t("accounts.voucher.party")}
                    role={account?.account_type === "payable" ? "supplier" : "customer"}
                    value={l.partyId}
                    onChange={(partyId) => update(l.key, { partyId })}
                  />
                )}
                {single ? (
                  <Field label={t("accounts.voucher.amount")} className="w-36">
                    {(p) => (
                      <input
                        {...p}
                        className={inputClass + " text-end"}
                        dir="ltr"
                        inputMode="decimal"
                        value={l.amount}
                        onChange={(e) => update(l.key, { amount: e.target.value })}
                      />
                    )}
                  </Field>
                ) : (
                  <>
                    <Field label={t("accounts.dr")} className="w-32">
                      {(p) => (
                        <input
                          {...p}
                          className={inputClass + " text-end"}
                          dir="ltr"
                          inputMode="decimal"
                          value={l.debit}
                          onChange={(e) => update(l.key, { debit: e.target.value })}
                        />
                      )}
                    </Field>
                    <Field label={t("accounts.cr")} className="w-32">
                      {(p) => (
                        <input
                          {...p}
                          className={inputClass + " text-end"}
                          dir="ltr"
                          inputMode="decimal"
                          value={l.credit}
                          onChange={(e) => update(l.key, { credit: e.target.value })}
                        />
                      )}
                    </Field>
                  </>
                )}
                {WITH_VAT.includes(kind) && (
                  <Field label={t("accounts.voucher.vat")} className="w-24">
                    {(p) => (
                      <select
                        {...p}
                        className={inputClass}
                        value={l.vat}
                        onChange={(e) => update(l.key, { vat: e.target.value as "0" | "5" })}
                      >
                        <option value="0">0%</option>
                        <option value="5">5%</option>
                      </select>
                    )}
                  </Field>
                )}
                <Field label={t("accounts.voucher.description")} className="min-w-40 flex-1">
                  {(p) => (
                    <input
                      {...p}
                      className={inputClass}
                      value={l.description}
                      onChange={(e) => update(l.key, { description: e.target.value })}
                    />
                  )}
                </Field>
                {lines.length > 1 && (
                  <Button
                    type="button"
                    variant="ghost"
                    aria-label={t("accounts.voucher.removeLine", { n: i + 1 })}
                    onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}
                  >
                    ×
                  </Button>
                )}
              </div>
              {isPartyLine(l) && l.partyId && businessId && lineFils(l) > 0 && (
                <BillWisePicker
                  businessId={businessId}
                  partyId={l.partyId}
                  side={lineSide(l)}
                  amountFils={lineFils(l)}
                  value={l.billWise}
                  onChange={(billWise) => update(l.key, { billWise })}
                />
              )}
            </div>
          );
        })}
        <div>
          <Button type="button" variant="outline" onClick={() => setLines((ls) => [...ls, emptyLine()])}>
            {t("accounts.voucher.addLine")}
          </Button>
        </div>
      </fieldset>

      <Field label={t("accounts.voucher.narration")} hint={t("common.optional")}>
        {(p) => (
          <textarea
            {...p}
            className={inputClass + " h-16 py-2"}
            value={narration}
            onChange={(e) => setNarration(e.target.value)}
          />
        )}
      </Field>

      <dl className="grid max-w-md grid-cols-2 gap-1 text-sm" aria-label={t("accounts.voucher.totals")}>
        {single ? (
          <>
            {WITH_VAT.includes(kind) && (
              <>
                <dt>{t("accounts.voucher.net")}</dt>
                <dd className="text-end tabular-nums" dir="ltr">
                  {money(fromFils(net))}
                </dd>
                <dt>{t("accounts.voucher.vat")}</dt>
                <dd className="text-end tabular-nums" dir="ltr">
                  {money(fromFils(vat))}
                </dd>
              </>
            )}
            <dt className="font-semibold">{t("accounts.voucher.total")}</dt>
            <dd className="text-end font-semibold tabular-nums" dir="ltr">
              {money(fromFils(total))}
            </dd>
          </>
        ) : (
          <>
            <dt>{t("accounts.dr")}</dt>
            <dd className="text-end tabular-nums" dir="ltr">
              {money(fromFils(debits))}
            </dd>
            <dt>{t("accounts.cr")}</dt>
            <dd className="text-end tabular-nums" dir="ltr">
              {money(fromFils(credits))}
            </dd>
            {debits !== credits && (
              <>
                <dt className="text-destructive">{t("accounts.voucher.difference")}</dt>
                <dd className="text-end tabular-nums text-destructive" dir="ltr">
                  {money(fromFils(debits - credits))}
                </dd>
              </>
            )}
          </>
        )}
      </dl>

      {lockReason !== null && (
        <Field label={t("accounts.voucher.lockReason")} hint={t("accounts.voucher.lockHint")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={lockReason}
              onChange={(e) => setLockReason(e.target.value)}
            />
          )}
        </Field>
      )}
      {tried && problem && (
        <p role="alert" className="text-destructive">
          {t(problem)}
        </p>
      )}
      {failure && failure !== "client" && !(failure === "period_locked" && lockReason !== null) && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${failure}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={save.isPending}>
          {lockReason !== null ? t("accounts.voucher.postLocked") : t("accounts.voucher.post")}
        </Button>
        <Button type="button" variant="outline" disabled={save.isPending} onClick={() => submit(false)}>
          {t("accounts.voucher.saveDraft")}
        </Button>
        <span className="text-xs text-muted-foreground">{t("accounts.voucher.shortcut")}</span>
      </div>
    </form>
  );
}

/** A searchable customer or supplier choice. */
function PartyPicker({
  label,
  role,
  value,
  onChange,
  allowNone = false,
}: {
  label: string;
  role: Role;
  value: string;
  onChange: (partyId: string) => void;
  allowNone?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const parties = useParties(query.trim(), role);
  return (
    <Field label={label} className="min-w-80 flex-1">
      {(p) => (
        <div className="flex gap-1">
          <input
            className={inputClass.replace("w-full", "w-24") + " shrink-0"}
            type="search"
            aria-label={t("accounts.voucher.findParty")}
            placeholder={t("accounts.voucher.find")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            {...p}
            className={inputClass + " min-w-0 flex-1"}
            value={value}
            onChange={(e) => onChange(e.target.value)}
          >
            <option value="">
              {allowNone ? t("accounts.voucher.noParty") : t("accounts.voucher.choose")}
            </option>
            {parties.data?.map((party) => (
              <option key={party.id} value={party.id}>
                {party.code} {partyName(party, i18n.language)}
              </option>
            ))}
          </select>
        </div>
      )}
    </Field>
  );
}
