import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type TaxCode = components["schemas"]["TaxCodeOut"];
export type PriceMode = components["schemas"]["SalesDocIn"]["price_mode"];
export type NoVatReason = NonNullable<components["schemas"]["SalesDocIn"]["no_vat_reason"]>;
export type Side = "sales" | "purchase";

export const PRICE_MODES: PriceMode[] = ["vat_exclusive", "vat_inclusive", "no_vat"];
export const NO_VAT_REASONS: NoVatReason[] = ["zero_rated", "exempt", "out_of_scope"];
/** The code every line takes in the no-VAT mode, by reason (as `tax.NO_VAT_CODE`). */
export const NO_VAT_CODE: Record<NoVatReason, string> = {
  zero_rated: "ZR",
  exempt: "EX",
  out_of_scope: "OS",
};
export const STANDARD = "SR";

/** The fixed codes of one side, from the registry (rates live in the seed file, not here). */
export function useTaxCodes(side: Side) {
  const api = useApi();
  return useQuery({
    queryKey: ["tax-codes", side],
    staleTime: Infinity,
    queryFn: async () =>
      unwrap(await api.GET("/tax-codes", { params: { query: { side } } })).filter((c) => c.side === side),
  });
}

/** A code's rate and treatment by code, from the registry (rate "0" until the codes arrive). */
export function useCodeInfo(side: Side): (code: string) => { rate: string; treatment: string | undefined } {
  const codes = useTaxCodes(side);
  return (code) => {
    const c = codes.data?.find((x) => x.code === code);
    return { rate: c?.rate ?? "0", treatment: c?.treatment };
  };
}

/** The price mode a new document starts in: the setting `sales.default_price_mode` for this branch and user. */
export function useDefaultPriceMode(branchId: string): PriceMode | undefined {
  const api = useApi();
  const settings = useQuery({
    queryKey: ["settings", branchId],
    enabled: !!branchId,
    queryFn: async () => unwrap(await api.GET("/settings", { params: { query: { branch_id: branchId } } })),
  });
  const value = settings.data?.find((s) => s.key === "sales.default_price_mode")?.value;
  return PRICE_MODES.includes(value as PriceMode) ? (value as PriceMode) : undefined;
}

/** The code a line takes, in the API's order (`tax.resolve`): a sales business that is not registered and the
 * no-VAT mode decide it outright; then the code chosen, the line it follows, the party's, the item's, standard. */
export function resolveCode(o: {
  side: Side;
  registered: boolean;
  mode: PriceMode;
  reason: NoVatReason | null;
  typed?: string | null;
  followed?: string | null;
  party?: string | null;
  product?: string | null;
}): string {
  if (o.side === "sales" && !o.registered) return "OS";
  if (o.mode === "no_vat") return NO_VAT_CODE[o.reason ?? "out_of_scope"];
  return o.typed || o.followed || o.party || o.product || STANDARD;
}

/** A line's net and VAT in fils from its amount (qty × price, in fils), as `tax.split`: exclusive, VAT = amount ×
 * rate / 100; inclusive (R6), VAT = amount × rate / (100 + rate) and net the rest; half-up to the fils (A3). */
export function splitFils(amountFils: number, rate: string | number, mode: PriceMode): [number, number] {
  const r = Math.round(Number(rate) * 100); // basis points: 5% → 500
  if (mode === "no_vat" || r === 0 || amountFils <= 0) return [amountFils, 0];
  if (mode === "vat_inclusive") {
    const vat = Math.floor((2 * amountFils * r + (10000 + r)) / (2 * (10000 + r)));
    return [amountFils - vat, vat];
  }
  return [amountFils, Math.floor((2 * amountFils * r + 10000) / 20000)];
}
