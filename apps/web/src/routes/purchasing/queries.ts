import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type PurchaseDoc = components["schemas"]["PurchaseDocOut"];
export type PurchaseDocSummary = components["schemas"]["PurchaseDocSummary"];
export type PurchaseDocIn = components["schemas"]["PurchaseDocIn"];
export type PurchaseLineIn = components["schemas"]["PurchaseLineIn"];
export type PurchaseDocType = PurchaseDocIn["doc_type"];
export type OpenLine = components["schemas"]["OpenLineOut"];

export const PURCHASE_DOC_TYPES: PurchaseDocType[] = [
  "purchase_order",
  "goods_receipt",
  "purchase_bill",
  "purchase_return",
];

export function isPurchaseDocType(kind: string): kind is PurchaseDocType {
  return (PURCHASE_DOC_TYPES as string[]).includes(kind);
}

/** What may follow a posted document, and from which of its lines (the open-lines endpoint). */
export const NEXT_STEPS: Partial<Record<PurchaseDocType, PurchaseDocType[]>> = {
  purchase_order: ["goods_receipt"],
  goods_receipt: ["purchase_bill", "purchase_return"],
  purchase_bill: ["purchase_return"],
};

/** Every list and page that a purchase posting changes. */
export const PURCHASE_KEYS = [
  "purchase-docs",
  "purchase-doc",
  "open-lines",
  "doc-state",
  "stock-balances",
  "stock-valuation",
  "bills",
  "ledger",
] as const;

export function usePurchaseDocs(
  businessId: string | undefined,
  filter: { docType?: PurchaseDocType | null; status?: PurchaseDocSummary["status"] | null },
) {
  const api = useApi();
  return useQuery({
    queryKey: ["purchase-docs", businessId, filter.docType ?? null, filter.status ?? null],
    enabled: !!businessId,
    queryFn: async () => {
      const query: {
        business_id: string;
        doc_type?: PurchaseDocType;
        status?: PurchaseDocSummary["status"];
      } = { business_id: businessId! };
      if (filter.docType) query.doc_type = filter.docType;
      if (filter.status) query.status = filter.status;
      return unwrap(await api.GET("/purchase-documents", { params: { query } }));
    },
  });
}

export function usePurchaseDoc(docId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["purchase-doc", docId],
    enabled: !!docId,
    queryFn: async () =>
      unwrap(await api.GET("/purchase-documents/{doc_id}", { params: { path: { doc_id: docId } } })),
  });
}

/** A posted order's lines still to receive, a receipt's still to bill or return, a bill's still to return. */
export function useOpenLines(docId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["open-lines", docId],
    enabled: !!docId,
    queryFn: async () =>
      unwrap(
        await api.GET("/purchase-documents/{doc_id}/open-lines", { params: { path: { doc_id: docId } } }),
      ),
  });
}

/** Numbers freed by cancelled documents of this type and branch, offered to the next one (decision A7). */
export function useReleasedNumbers(branchId: string, docType: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["released-numbers", branchId, docType],
    enabled: !!branchId,
    retry: false,
    queryFn: async () =>
      unwrap(
        await api.GET("/documents/released-numbers", {
          params: { query: { branch_id: branchId, doc_type: docType } },
        }),
      ),
  });
}

/** Up to 6 decimal places, as quantities and prices are stored. */
export const DECIMAL6 = /^\d{1,13}(\.\d{1,6})?$/;

/** Quantity × price in fils, half-up, from the text (6 places each, never through floating point). */
export function netFils(qtyText: string, priceText: string): number {
  if (!DECIMAL6.test(qtyText) || !DECIMAL6.test(priceText)) return 0;
  const scaled = (text: string) => {
    const [whole = "0", fraction = ""] = text.split(".");
    return BigInt(whole + (fraction + "000000").slice(0, 6));
  };
  const product = scaled(qtyText) * scaled(priceText); // 12 places
  return Number((product + 5_000_000_000n) / 10_000_000_000n);
}
