import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type PriceList = components["schemas"]["PriceListOut"];
export type Promotion = components["schemas"]["PromotionOut"];
export type Quote = components["schemas"]["QuoteOut"];

export function usePriceLists(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["price-lists", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/price-lists", { params: { query: { business_id: businessId } } })),
  });
}

export function usePromotions(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["promotions", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/promotions", { params: { query: { business_id: businessId } } })),
  });
}

/** "quantity_break:Standard" → the translation key's source and the name. */
export function splitSource(priceSource: string): { source: string; name: string } {
  const at = priceSource.indexOf(":");
  return at < 0
    ? { source: priceSource, name: "" }
    : { source: priceSource.slice(0, at), name: priceSource.slice(at + 1) };
}

/** 3.0000 → 3.00, 2.4500 → 2.45, 1.2345 → 1.2345: money shown with at least 2 decimals, never rounded. */
export function showMoney(value: string | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const [whole, fraction = ""] = value.split(".");
  const trimmed = fraction.replace(/0+$/, "");
  return `${whole}.${trimmed.padEnd(2, "0")}`;
}
