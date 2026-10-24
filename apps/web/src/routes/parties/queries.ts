import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

export type Party = components["schemas"]["PartyOut"];
export type PartySummary = components["schemas"]["PartySummary"];
export type PartyIn = components["schemas"]["PartyIn"];
export type PartyPatch = components["schemas"]["PartyPatch"];
export type AddressIn = components["schemas"]["AddressIn"];
export type CustomerGroup = components["schemas"]["CustomerGroupOut"];
export type CreditCheck = components["schemas"]["CreditCheckOut"];
export type Role = "customer" | "supplier";

export function useParties(q: string, role: Role | null) {
  const api = useApi();
  return useQuery({
    queryKey: ["parties", q, role],
    queryFn: async () => {
      const query: { q?: string; role?: Role } = {};
      if (q) query.q = q;
      if (role) query.role = role;
      return unwrap(await api.GET("/parties", { params: { query } }));
    },
  });
}

export function useParty(partyId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["party", partyId],
    enabled: !!partyId,
    queryFn: async () =>
      unwrap(await api.GET("/parties/{party_id}", { params: { path: { party_id: partyId } } })),
  });
}

export function useCustomerGroups(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["customer-groups", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/customer-groups", { params: { query: { business_id: businessId } } })),
  });
}

/** The name in the screen's language. */
export function partyName(p: { name_en: string; name_ar: string }, language: string): string {
  return language === "ar" ? p.name_ar : p.name_en;
}

/** Up to 4 decimals, no thousands separators: amounts stay exact text all the way to the API. */
export const AMOUNT = /^\d{1,15}(\.\d{1,4})?$/;

export const EMIRATES = [
  "abu_dhabi",
  "dubai",
  "sharjah",
  "ajman",
  "umm_al_quwain",
  "ras_al_khaimah",
  "fujairah",
] as const;

export type Emirate = (typeof EMIRATES)[number];

export function roleLabel(
  t: (key: string) => string,
  p: { is_customer: boolean; is_supplier: boolean },
): string {
  if (p.is_customer && p.is_supplier) return t("parties.both");
  return p.is_customer ? t("parties.customer") : t("parties.supplier");
}
