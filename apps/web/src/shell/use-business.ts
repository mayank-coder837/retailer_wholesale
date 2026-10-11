import { createContext, useContext } from "react";

import type { components } from "@smartbiz/api-client";

export type Business = components["schemas"]["BusinessOut"];

export interface BusinessContextValue {
  businesses: Business[];
  current: Business | null;
  select(id: string): void;
}

export const BusinessContext = createContext<BusinessContextValue | null>(null);

/** The business the user is working in (chosen in the top bar, remembered per account). */
export function useBusiness(): BusinessContextValue {
  const value = useContext(BusinessContext);
  if (!value) throw new Error("useBusiness must be used inside the app shell");
  return value;
}

export function rememberedBusinessKey(tenantId: string): string {
  return `smartbiz.business.${tenantId}`;
}
