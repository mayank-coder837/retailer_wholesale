import { createContext, useContext } from "react";

import type { components } from "@smartbiz/api-client";

export type SignupBody = components["schemas"]["SignupIn"];

export interface TenantChoice {
  tenant_id: string;
  tenant_name: string;
}

export type AuthResult = { ok: true } | { ok: false; code: string; memberships?: TenantChoice[] };

export type SessionStatus = "loading" | "signed_in" | "signed_out";

export interface Session {
  status: SessionStatus;
  accessToken: string | null;
  tenantId: string | null;
  login(email: string, password: string, tenantId?: string): Promise<AuthResult>;
  signup(body: Omit<SignupBody, "client">): Promise<AuthResult>;
  switchTenant(tenantId: string): Promise<AuthResult>;
  logout(): Promise<void>;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside <SessionProvider>");
  return session;
}
