import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";

export type Unit = components["schemas"]["UnitOut"];
export type Category = components["schemas"]["CategoryOut"];
export type Brand = components["schemas"]["BrandOut"];
export type Attribute = components["schemas"]["AttributeOut"];
export type CustomField = components["schemas"]["CustomFieldOut"];
export type Product = components["schemas"]["ProductOut"];
export type ProductSummary = components["schemas"]["ProductSummary"];
export type ProductIn = components["schemas"]["ProductIn"];

/** The stable error code of a failed request (RFC 7807 body), for translated messages. */
export function errorCode(error: unknown): string {
  const code = (error as { code?: unknown } | undefined)?.code;
  return typeof code === "string" ? code : "generic";
}

/** Throws the API's error body so mutations and queries can show its code. */
export function unwrap<T>(result: { data?: T; error?: unknown }): T {
  if (result.error !== undefined || result.data === undefined) throw result.error ?? new Error("generic");
  return result.data;
}

export function useUnits() {
  const api = useApi();
  return useQuery({ queryKey: ["units"], queryFn: async () => unwrap(await api.GET("/units")) });
}

export function useCategories() {
  const api = useApi();
  return useQuery({ queryKey: ["categories"], queryFn: async () => unwrap(await api.GET("/categories")) });
}

export function useBrands() {
  const api = useApi();
  return useQuery({ queryKey: ["brands"], queryFn: async () => unwrap(await api.GET("/brands")) });
}

export function useAttributes() {
  const api = useApi();
  return useQuery({ queryKey: ["attributes"], queryFn: async () => unwrap(await api.GET("/attributes")) });
}

export function useCustomFields(entity?: "product" | "variant") {
  const api = useApi();
  return useQuery({
    queryKey: ["custom-fields", entity ?? "all"],
    queryFn: async () =>
      unwrap(await api.GET("/custom-fields", { params: { query: entity ? { entity_type: entity } : {} } })),
  });
}

export function useProduct(productId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["product", productId],
    queryFn: async () =>
      unwrap(await api.GET("/products/{product_id}", { params: { path: { product_id: productId } } })),
  });
}

export function useProducts(q: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["products", q],
    queryFn: async () => unwrap(await api.GET("/products", { params: { query: q ? { q } : {} } })),
  });
}
