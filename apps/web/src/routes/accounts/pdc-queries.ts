import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type Pdc = components["schemas"]["PdcOut"];
export type PdcIn = components["schemas"]["PdcIn"];
export type PdcDirection = Pdc["direction"];
export type PdcStatus = Pdc["status"];

export const RECEIVED_STATUSES: PdcStatus[] = ["in_hand", "deposited", "cleared", "bounced", "returned"];
export const ISSUED_STATUSES: PdcStatus[] = ["issued", "cleared", "bounced", "cancelled"];
/** Still to clear: the register shows these by default, and they count as "PDCs in hand". */
export const PENDING: PdcStatus[] = ["in_hand", "deposited", "issued"];

export function usePdcs(
  businessId: string | undefined,
  filter: { direction: PdcDirection; status: PdcStatus | null },
) {
  const api = useApi();
  return useQuery({
    queryKey: ["pdcs", businessId, filter.direction, filter.status],
    enabled: !!businessId,
    queryFn: async () => {
      const query: { business_id: string; direction: PdcDirection; status?: PdcStatus } = {
        business_id: businessId!,
        direction: filter.direction,
      };
      if (filter.status) query.status = filter.status;
      return unwrap(await api.GET("/pdcs", { params: { query } }));
    },
  });
}

export function usePdc(pdcId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["pdc", pdcId],
    queryFn: async () => unwrap(await api.GET("/pdcs/{pdc_id}", { params: { path: { pdc_id: pdcId } } })),
  });
}

/** Cheques grouped by the month they mature in ("2026-11"), earliest first: the register's calendar. */
export function byMonth(pdcs: Pdc[]): [string, Pdc[]][] {
  const groups = new Map<string, Pdc[]>();
  for (const p of pdcs) {
    const month = p.cheque_date.slice(0, 7);
    groups.set(month, [...(groups.get(month) ?? []), p]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

/** The sum of exact decimal amounts, in fils (no floats for money). */
export function totalFils(amounts: string[]): number {
  return amounts.reduce((sum, a) => {
    const [whole = "0", fraction = ""] = a.split(".");
    return sum + Number(whole) * 100 + Number((fraction + "00").slice(0, 2));
  }, 0);
}

export function filsToAmount(fils: number): string {
  return `${Math.trunc(fils / 100)}.${String(fils % 100).padStart(2, "0")}`;
}
