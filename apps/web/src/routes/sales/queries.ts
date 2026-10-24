import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type SalesDoc = components["schemas"]["SalesDocOut"];
export type SalesDocSummary = components["schemas"]["SalesDocSummary"];
export type SalesDocIn = components["schemas"]["SalesDocIn"];
export type SalesLineIn = components["schemas"]["SalesLineIn"];
export type SalesDocType = SalesDocIn["doc_type"];
export type OpenSalesLine = components["schemas"]["OpenSalesLineOut"];
export type SalesWarning = components["schemas"]["SalesWarningOut"];
export type HistoryLine = components["schemas"]["SalesHistoryLineOut"];
export type Quote = components["schemas"]["QuoteOut"];
export type Ageing = components["schemas"]["AgeingOut"];

export const SALES_DOC_TYPES: SalesDocType[] = [
  "quotation",
  "proforma_invoice",
  "sales_order",
  "delivery_note",
  "sales_invoice",
  "sales_return",
];

export function isSalesDocType(kind: string): kind is SalesDocType {
  return (SALES_DOC_TYPES as string[]).includes(kind);
}

/** What may follow a posted document, from the lines still open on it. */
export const NEXT_STEPS: Partial<Record<SalesDocType, SalesDocType[]>> = {
  quotation: ["sales_order"],
  sales_order: ["delivery_note", "sales_invoice"],
  delivery_note: ["sales_invoice"],
  sales_invoice: ["sales_return"],
};

/** Every list and page that a sales posting changes. */
export const SALES_KEYS = [
  "sales-docs",
  "sales-doc",
  "sales-open-lines",
  "sales-history",
  "doc-state",
  "stock-balances",
  "stock-valuation",
  "bills",
  "ledger",
  "ageing",
] as const;

export function useSalesDocs(
  businessId: string | undefined,
  filter: { docType?: SalesDocType | null; status?: SalesDocSummary["status"] | null },
) {
  const api = useApi();
  return useQuery({
    queryKey: ["sales-docs", businessId, filter.docType ?? null, filter.status ?? null],
    enabled: !!businessId,
    queryFn: async () => {
      const query: { business_id: string; doc_type?: SalesDocType; status?: SalesDocSummary["status"] } = {
        business_id: businessId!,
      };
      if (filter.docType) query.doc_type = filter.docType;
      if (filter.status) query.status = filter.status;
      return unwrap(await api.GET("/sales-documents", { params: { query } }));
    },
  });
}

export function useSalesDoc(docId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["sales-doc", docId],
    enabled: !!docId,
    queryFn: async () =>
      unwrap(await api.GET("/sales-documents/{doc_id}", { params: { path: { doc_id: docId } } })),
  });
}

export function useSalesOpenLines(docId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["sales-open-lines", docId],
    enabled: !!docId,
    queryFn: async () =>
      unwrap(await api.GET("/sales-documents/{doc_id}/open-lines", { params: { path: { doc_id: docId } } })),
  });
}

/** The customer history panel (A9): every item invoiced to this customer, newest first. */
export function useSalesHistory(businessId: string | undefined, partyId: string, q: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["sales-history", businessId, partyId, q],
    enabled: !!businessId && !!partyId,
    queryFn: async () => {
      const query: { business_id: string; q?: string } = { business_id: businessId! };
      if (q) query.q = q;
      return unwrap(
        await api.GET("/parties/{party_id}/sales-history", {
          params: { path: { party_id: partyId }, query },
        }),
      );
    },
  });
}

/** Receivables or payables ageing (today's figures); ``retry: false``: without report.view it is simply not shown. */
export function useAgeing(businessId: string | undefined, side: "receivables" | "payables") {
  const api = useApi();
  return useQuery({
    queryKey: ["ageing", businessId, side],
    enabled: !!businessId,
    retry: false,
    queryFn: async () => {
      const params = { params: { query: { business_id: businessId! } } };
      return unwrap(
        side === "receivables"
          ? await api.GET("/receivables/ageing", params)
          : await api.GET("/payables/ageing", params),
      );
    },
  });
}

/** Where the warnings of a post just made are handed to the document page (shown once). */
export const salesWarningsKey = (docId: string) => ["sales-warnings", docId] as const;
