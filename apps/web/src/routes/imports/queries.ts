import { useQuery } from "@tanstack/react-query";

import type { ApiClient, components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { errorCode, unwrap } from "@/routes/catalog/queries";

export type ImportKind = components["schemas"]["ImportKindOut"];
export type ImportField = components["schemas"]["ImportFieldOut"];
export type ImportOption = components["schemas"]["ImportOptionOut"];
export type Upload = components["schemas"]["ImportUploadOut"];
export type CheckResult = components["schemas"]["ImportCheckOut"];
export type Batch = components["schemas"]["ImportBatchOut"];
export type Reconciliation = components["schemas"]["ReconciliationOut"];
export type Mapping = Record<string, number | null>;

/** The order a business is best brought across in: the chart first, the opening position last. */
export const ORDER = ["accounts", "parties", "products", "opening_balances", "party_bills", "opening_stock"];

/** Kinds whose result is the opening position, which the reconciliation checks. */
export const OPENING_KINDS = ["opening_balances", "party_bills", "opening_stock"];

export function useImportKinds() {
  const api = useApi();
  return useQuery({
    queryKey: ["import-kinds"],
    queryFn: async () => {
      const kinds = unwrap(await api.GET("/imports/kinds"));
      return [...kinds].sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key));
    },
  });
}

export function useImports(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["imports", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/imports", { params: { query: { business_id: businessId!, limit: 20 } } })),
  });
}

export function useReconciliation(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["import-reconciliation", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/imports/reconciliation", { params: { query: { business_id: businessId! } } })),
  });
}

/** Sends the chosen file (the API reads it; nothing is saved to the books yet). */
export async function uploadImport(
  api: ApiClient,
  businessId: string,
  kind: string,
  file: File,
): Promise<Upload> {
  return unwrap(
    await api.POST("/imports", {
      body: { business_id: businessId, kind, file: file as unknown as string },
      bodySerializer: () => {
        const form = new FormData();
        form.append("business_id", businessId);
        form.append("kind", kind);
        form.append("file", file);
        return form;
      },
    }),
  );
}

/** Saves the template the API builds (with headings in the chosen language) as a file download. */
export async function downloadTemplate(api: ApiClient, kind: string, lang: "en" | "ar"): Promise<void> {
  const result = await api.GET("/imports/templates/{kind}", {
    params: { path: { kind }, query: { lang } },
    parseAs: "blob",
  });
  const blob = unwrap(result as { data?: Blob; error?: unknown });
  if (typeof URL.createObjectURL !== "function") return; // no file system to save to (a test)
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${kind}-template-${lang}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000); // not at once: some browsers cancel the download
}

/** A moment as the business's own calendar date ("2026-11-15"), not UTC's. */
export function businessDay(moment: string, timeZone: string): string {
  return new Date(moment).toLocaleDateString("en-CA", { timeZone });
}

/** The name of a kind, field or option in the language of the screen. */
export function label(item: { label_en: string; label_ar: string }, language: string): string {
  return language.startsWith("ar") ? item.label_ar : item.label_en;
}

export function hint(item: { hint_en: string; hint_ar: string }, language: string): string {
  return language.startsWith("ar") ? item.hint_ar : item.hint_en;
}

/** What went wrong, in words: the API's stable error code, or a plain "something went wrong". */
export function errorText(t: (key: string) => string, error: unknown): string {
  const key = `import.errors.${errorCode(error)}`;
  return t(key) === key ? t("import.errors.generic") : t(key);
}
