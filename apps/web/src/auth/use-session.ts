import { createContext, useContext } from "react";

export interface TenantChoice {
  tenant_id: string;
  tenant_name: string;
}

export type LoginResult = { ok: true } | { ok: false; code: string; memberships?: TenantChoice[] };

export interface Session {
  accessToken: string | null;
  login(email: string, password: string, tenantId?: string): Promise<LoginResult>;
  logout(): void;
}

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside <SessionProvider>");
  return session;
}
