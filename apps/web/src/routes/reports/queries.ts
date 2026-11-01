import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type ProfitLoss = components["schemas"]["ProfitLossOut"];
export type BalanceSheet = components["schemas"]["BalanceSheetOut"];
export type DayBook = components["schemas"]["DayBookOut"];
export type Section = components["schemas"]["SectionOut"];
export type StatementRow = components["schemas"]["StatementRowOut"];

export interface Period {
  from: string;
  to: string;
}
export type Preset = "this_month" | "last_month" | "this_year" | "custom";
export const PRESETS: Preset[] = ["this_month", "last_month", "this_year", "custom"];

const iso = (y: number, m: number, d: number) => new Date(Date.UTC(y, m, d)).toISOString().slice(0, 10);

/** A preset period as of ``today`` ("2026-11-01"): the year is the fiscal year, starting in ``startMonth``. */
export function presetPeriod(preset: Exclude<Preset, "custom">, today: string, startMonth: number): Period {
  const [y = 0, m = 1] = today.split("-").map(Number);
  if (preset === "this_month") return { from: iso(y, m - 1, 1), to: today };
  if (preset === "last_month") return { from: iso(y, m - 2, 1), to: iso(y, m - 1, 0) };
  const startYear = m >= startMonth ? y : y - 1;
  return { from: iso(startYear, startMonth - 1, 1), to: today };
}

export function useProfitLoss(businessId: string | undefined, period: Period | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["profit-loss", businessId, period?.from, period?.to],
    enabled: !!businessId && !!period,
    queryFn: async () =>
      unwrap(
        await api.GET("/reports/profit-loss", {
          params: { query: { business_id: businessId!, from: period!.from, to: period!.to } },
        }),
      ),
  });
}

export function useBalanceSheet(businessId: string | undefined, asOf: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["balance-sheet", businessId, asOf],
    enabled: !!businessId && !!asOf,
    queryFn: async () =>
      unwrap(
        await api.GET("/reports/balance-sheet", {
          params: { query: { business_id: businessId!, as_of: asOf } },
        }),
      ),
  });
}

export function useDayBook(businessId: string | undefined, period: Period | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["day-book", businessId, period?.from, period?.to],
    enabled: !!businessId && !!period,
    queryFn: async () =>
      unwrap(
        await api.GET("/reports/day-book", {
          params: { query: { business_id: businessId!, from: period!.from, to: period!.to } },
        }),
      ),
  });
}
