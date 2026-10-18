import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type Account = components["schemas"]["AccountOut"];
export type AccountIn = components["schemas"]["AccountIn"];
export type AccountRole = components["schemas"]["AccountRoleOut"];
export type FiscalYear = components["schemas"]["FiscalYearOut"];
export type FiscalPeriod = components["schemas"]["FiscalPeriodOut"];
export type Voucher = components["schemas"]["VoucherOut"];
export type VoucherSummary = components["schemas"]["VoucherSummary"];
export type VoucherIn = components["schemas"]["VoucherIn"];
export type VoucherLineIn = components["schemas"]["VoucherLineIn"];
export type AllocationIn = components["schemas"]["AllocationIn"];
export type Bill = components["schemas"]["BillOut"];
export type Ledger = components["schemas"]["LedgerOut"];
export type PartyLedger = components["schemas"]["PartyLedgerOut"];
export type TrialBalance = components["schemas"]["TrialBalanceOut"];
export type VoucherType = VoucherIn["doc_type"];
export type AccountType = NonNullable<Account["account_type"]>;

/** Entry order on screens and in the menu. Sales invoices and purchase bills come with M1.8 / M1.7. */
export const VOUCHER_TYPES: VoucherType[] = [
  "receipt",
  "payment",
  "expense",
  "contra",
  "journal",
  "credit_note",
  "debit_note",
];
export const SINGLE_ENTRY: VoucherType[] = ["receipt", "payment", "expense", "credit_note", "debit_note"];
export const WITH_CASH_ACCOUNT: VoucherType[] = ["receipt", "payment", "expense"];
export const WITH_PARTY: VoucherType[] = ["credit_note", "debit_note"];
export const WITH_VAT: VoucherType[] = ["expense", "credit_note", "debit_note"];
export const CASH_OR_BANK: AccountType[] = ["cash", "bank"];
export const CONTROL: AccountType[] = ["receivable", "payable"];

export function isVoucherType(kind: string): kind is VoucherType {
  return (VOUCHER_TYPES as string[]).includes(kind);
}

export function useAccounts(businessId: string | undefined, includeArchived = false) {
  const api = useApi();
  return useQuery({
    queryKey: ["accounts", businessId, includeArchived],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(
        await api.GET("/accounts", {
          params: { query: { business_id: businessId!, include_archived: includeArchived } },
        }),
      ),
  });
}

export function useAccountRoles(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["account-roles", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/account-roles", { params: { query: { business_id: businessId! } } })),
  });
}

export function useBranches(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["branches", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(
        await api.GET("/businesses/{business_id}/branches", {
          params: { path: { business_id: businessId! } },
        }),
      ),
  });
}

export function useFiscalYears(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["fiscal-years", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/fiscal-years", { params: { query: { business_id: businessId! } } })),
  });
}

export function useVouchers(
  businessId: string | undefined,
  filter: { docType?: VoucherType | null; status?: VoucherSummary["status"] | null },
) {
  const api = useApi();
  return useQuery({
    queryKey: ["vouchers", businessId, filter.docType ?? null, filter.status ?? null],
    enabled: !!businessId,
    queryFn: async () => {
      const query: { business_id: string; doc_type?: VoucherType; status?: VoucherSummary["status"] } = {
        business_id: businessId!,
      };
      if (filter.docType) query.doc_type = filter.docType;
      if (filter.status) query.status = filter.status;
      return unwrap(await api.GET("/vouchers", { params: { query } }));
    },
  });
}

export function useVoucher(voucherId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["voucher", voucherId],
    queryFn: async () =>
      unwrap(await api.GET("/vouchers/{doc_id}", { params: { path: { doc_id: voucherId } } })),
  });
}

export function useOpenBills(businessId: string | undefined, partyId: string | null | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["bills", businessId, partyId],
    enabled: !!businessId && !!partyId,
    queryFn: async () =>
      unwrap(
        await api.GET("/bills", { params: { query: { business_id: businessId!, party_id: partyId! } } }),
      ),
  });
}

export function useAccountLedger(accountId: string, from: string, to: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["account-ledger", accountId, from, to],
    queryFn: async () => {
      const query: { from?: string; to?: string } = {};
      if (from) query.from = from;
      if (to) query.to = to;
      return unwrap(
        await api.GET("/accounts/{account_id}/ledger", {
          params: { path: { account_id: accountId }, query },
        }),
      );
    },
  });
}

export function usePartyLedger(businessId: string | undefined, partyId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["party-ledger", businessId, partyId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(
        await api.GET("/parties/{party_id}/ledger", {
          params: { path: { party_id: partyId }, query: { business_id: businessId! } },
        }),
      ),
  });
}

export function useTrialBalance(businessId: string | undefined, asOf: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["trial-balance", businessId, asOf],
    enabled: !!businessId,
    queryFn: async () => {
      const query: { business_id: string; as_of?: string } = { business_id: businessId! };
      if (asOf) query.as_of = asOf;
      return unwrap(await api.GET("/trial-balance", { params: { query } }));
    },
  });
}

export function accountName(a: { name_en: string; name_ar: string }, language: string): string {
  return language === "ar" ? a.name_ar : a.name_en;
}

/** "1110 Cash" — the code keeps its left-to-right order inside Arabic text. */
export function accountLabel(a: Account, language: string): string {
  return `${a.code} ${accountName(a, language)}`;
}

/** Ledgers a user may post to by hand, optionally of some types only. */
export function postable(accounts: Account[], types?: AccountType[]): Account[] {
  return accounts.filter(
    (a) =>
      !a.is_group &&
      a.archived_at === null &&
      a.allow_manual_posting &&
      (!types || (a.account_type !== null && types.includes(a.account_type))),
  );
}

/** Exact decimal text → hundredths (fils) as an integer, so sums never use floating point. */
export function toFils(amount: string): number {
  const [whole = "0", fraction = ""] = amount.trim().split(".");
  return Number(whole) * 100 + Number((fraction + "00").slice(0, 2));
}

export function fromFils(fils: number): string {
  const sign = fils < 0 ? "-" : "";
  const abs = Math.abs(fils);
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

/** An API amount ("-1050.0000") as "-1,050.00", from the text itself (never through floating point).
 * Amounts reach the screen already in fils, so the extra places are zeros. */
export function money(amount: string): string {
  const negative = amount.trim().startsWith("-");
  const [whole = "0", fraction = ""] = amount.trim().replace(/^[-+]/, "").split(".");
  const grouped = whole.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fils = (fraction + "00").slice(0, 2);
  const zero = /^0*$/.test(whole) && /^0*$/.test(fraction);
  return `${negative && !zero ? "-" : ""}${grouped}.${fils}`;
}

/** Up to two decimals (fils): what an AED voucher amount may have. */
export const VOUCHER_AMOUNT = /^\d{1,13}(\.\d{1,2})?$/;

/** VAT of one line, half-up to the fils, exactly as the API computes it (decision A3). */
export function vatFils(netFils: number, rate: number): number {
  return Math.floor((netFils * rate + 50) / 100);
}

export function todayIso(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}
