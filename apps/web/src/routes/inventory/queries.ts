import { useQueries, useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type StockDoc = components["schemas"]["StockDocOut"];
export type StockDocSummary = components["schemas"]["StockDocSummary"];
export type StockDocIn = components["schemas"]["StockDocIn"];
export type StockLineIn = components["schemas"]["StockLineIn"];
export type StockDocType = StockDocIn["doc_type"];
export type Balance = components["schemas"]["BalanceOut"];
export type ItemLedger = components["schemas"]["ItemLedgerOut"];
export type Warehouse = components["schemas"]["WarehouseOut"];
export type StockBatch = components["schemas"]["BatchOut"];

export const STOCK_DOC_TYPES: StockDocType[] = ["stock_adjustment", "stock_transfer", "item_transfer"];

export function isStockDocType(kind: string): kind is StockDocType {
  return (STOCK_DOC_TYPES as string[]).includes(kind);
}

/** Every warehouse of the business, across its branches. */
export function useWarehouses(branchIds: string[]) {
  const api = useApi();
  return useQueries({
    queries: branchIds.map((branchId) => ({
      queryKey: ["warehouses", branchId],
      queryFn: async () =>
        unwrap(
          await api.GET("/branches/{branch_id}/warehouses", { params: { path: { branch_id: branchId } } }),
        ),
    })),
    combine: (results) => ({
      data: results.every((r) => r.data) ? results.flatMap((r) => r.data ?? []) : undefined,
      isPending: results.some((r) => r.isPending),
    }),
  });
}

export function useBalances(
  businessId: string | undefined,
  filter: { q?: string; warehouseId?: string; variantId?: string },
) {
  const api = useApi();
  return useQuery({
    queryKey: [
      "stock-balances",
      businessId,
      filter.q ?? "",
      filter.warehouseId ?? "",
      filter.variantId ?? "",
    ],
    enabled: !!businessId,
    queryFn: async () => {
      const query: { business_id: string; q?: string; warehouse_id?: string; variant_id?: string } = {
        business_id: businessId!,
      };
      if (filter.q) query.q = filter.q;
      if (filter.warehouseId) query.warehouse_id = filter.warehouseId;
      if (filter.variantId) query.variant_id = filter.variantId;
      return unwrap(await api.GET("/stock/balances", { params: { query } }));
    },
  });
}

export function useNegativeStock(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["stock-negative", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/stock/negative", { params: { query: { business_id: businessId! } } })),
  });
}

/** The valuation check; ``retry: false`` because people without report.view simply don't see it. */
export function useValuation(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["stock-valuation", businessId],
    enabled: !!businessId,
    retry: false,
    queryFn: async () =>
      unwrap(await api.GET("/stock/valuation", { params: { query: { business_id: businessId! } } })),
  });
}

export function useItemLedger(businessId: string | undefined, variantId: string, warehouseId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["stock-ledger", businessId, variantId, warehouseId],
    enabled: !!businessId,
    queryFn: async () => {
      const query: { business_id: string; warehouse_id?: string } = { business_id: businessId! };
      if (warehouseId) query.warehouse_id = warehouseId;
      return unwrap(
        await api.GET("/stock/variants/{variant_id}/ledger", {
          params: { path: { variant_id: variantId }, query },
        }),
      );
    },
  });
}

export function useStockBatches(businessId: string | undefined, variantId: string, warehouseId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["stock-batches", businessId, variantId, warehouseId],
    enabled: !!businessId && !!variantId,
    queryFn: async () => {
      const query: { business_id: string; variant_id: string; warehouse_id?: string } = {
        business_id: businessId!,
        variant_id: variantId,
      };
      if (warehouseId) query.warehouse_id = warehouseId;
      return unwrap(await api.GET("/stock/batches", { params: { query } }));
    },
  });
}

export function useStockDocs(
  businessId: string | undefined,
  filter: { docType?: StockDocType | null; status?: StockDocSummary["status"] | null },
) {
  const api = useApi();
  return useQuery({
    queryKey: ["stock-docs", businessId, filter.docType ?? null, filter.status ?? null],
    enabled: !!businessId,
    queryFn: async () => {
      const query: { business_id: string; doc_type?: StockDocType; status?: StockDocSummary["status"] } = {
        business_id: businessId!,
      };
      if (filter.docType) query.doc_type = filter.docType;
      if (filter.status) query.status = filter.status;
      return unwrap(await api.GET("/stock-documents", { params: { query } }));
    },
  });
}

export function useStockDoc(docId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["stock-doc", docId],
    queryFn: async () =>
      unwrap(await api.GET("/stock-documents/{doc_id}", { params: { path: { doc_id: docId } } })),
  });
}

/** A quantity as text without trailing zeros: "48.000000" → "48", "2.500000" → "2.5". */
export function qty(text: string): string {
  if (!text.includes(".")) return text;
  return text.replace(/0+$/, "").replace(/\.$/, "");
}

/** Up to 6 decimal places, as quantities are stored. */
export const QUANTITY = /^\d{1,13}(\.\d{1,6})?$/;
/** A cost per unit: up to 6 decimal places. */
export const COST = /^\d{1,13}(\.\d{1,6})?$/;

/** A product's details for a stock line, fetched once one is chosen. */
export function useStockProduct(productId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["product", productId],
    enabled: !!productId,
    queryFn: async () =>
      unwrap(await api.GET("/products/{product_id}", { params: { path: { product_id: productId } } })),
  });
}

export function warehouseName(warehouses: Warehouse[] | undefined, id: string, language: string): string {
  const w = warehouses?.find((x) => x.id === id);
  if (!w) return "";
  return language === "ar" ? w.name_ar : w.name_en;
}

/** Where the warnings of a post just made are handed to the document page (shown once). */
export const warningsKey = (docId: string) => ["stock-warnings", docId] as const;

export type StockWarnings = NonNullable<StockDoc["warnings"]>;
