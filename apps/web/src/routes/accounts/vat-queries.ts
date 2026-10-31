import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type VatReturn = components["schemas"]["VatReturnOut"];
export type VatBox = components["schemas"]["VatBoxOut"];
export type VatBoxLine = components["schemas"]["VatBoxLineOut"];

/** The VAT 201 in the FTA's three parts. 2 and 7 are entered by hand (Phase 2); 8, 11, 12–14 are totals. */
export const OUTPUT_BOXES = ["1a", "1b", "1c", "1d", "1e", "1f", "1g", "2", "3", "4", "5", "6", "7", "8"];
export const INPUT_BOXES = ["9", "10", "11"];
export const NET_BOXES = ["12", "13", "14"];

export interface Period {
  from: string;
  to: string;
}

/** A VAT period of `months` months ending with the month `endMonth` ("2026-09"): FTA quarters are staggered
 * by TRN (Jan–Mar, Feb–Apr or Mar–May), so a quarter is named by the month it ends in, not by calendar. */
export function periodEnding(endMonth: string, months: number): Period {
  const [y = 0, m = 1] = endMonth.split("-").map(Number);
  const start = new Date(Date.UTC(y, m - months, 1));
  const end = new Date(Date.UTC(y, m, 0));
  return { from: start.toISOString().slice(0, 10), to: end.toISOString().slice(0, 10) };
}

/** The `count` months before the one `today` is in, latest first ("2026-09", "2026-08"…): the months a
 * finished period can end in. */
export function recentMonths(today: string, count: number): string[] {
  const [y = 0, m = 1] = today.split("-").map(Number);
  return Array.from({ length: count }, (_, i) =>
    new Date(Date.UTC(y, m - 2 - i, 1)).toISOString().slice(0, 7),
  );
}

/** The exact sum of 4-decimal amounts ("1050.0000"), as a 4-decimal string (no floats for money). */
export function sumAmounts(amounts: string[]): string {
  const scaled = amounts.reduce((sum, a) => {
    const s = a.trim();
    const negative = s.startsWith("-");
    const [whole = "0", fraction = ""] = s.replace(/^[-+]/, "").split(".");
    const units = BigInt(whole || "0") * 10000n + BigInt((fraction + "0000").slice(0, 4));
    return sum + (negative ? -units : units);
  }, 0n);
  const negative = scaled < 0n;
  const abs = negative ? -scaled : scaled;
  return `${negative ? "-" : ""}${abs / 10000n}.${String(abs % 10000n).padStart(4, "0")}`;
}

export function useVatReturn(businessId: string | undefined, period: Period | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["vat-return", businessId, period?.from, period?.to],
    enabled: !!businessId && !!period,
    queryFn: async () =>
      unwrap(
        await api.GET("/vat-returns/draft", {
          params: { query: { business_id: businessId!, from: period!.from, to: period!.to } },
        }),
      ),
  });
}

export function useVatBoxLines(businessId: string | undefined, period: Period | null, box: string | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["vat-return-lines", businessId, period?.from, period?.to, box],
    enabled: !!businessId && !!period && !!box,
    queryFn: async () =>
      unwrap(
        await api.GET("/vat-returns/draft/lines", {
          params: { query: { business_id: businessId!, from: period!.from, to: period!.to, box: box! } },
        }),
      ),
  });
}
