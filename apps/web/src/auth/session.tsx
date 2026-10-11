import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { ApiClient, components, Middleware } from "@smartbiz/api-client";

import { SessionContext, type LoginResult, type TenantChoice } from "@/auth/use-session";

type TokenOut = components["schemas"]["TokenOut"];

/**
 * Minimal session for M0.3: the access token lives in memory only. M0.5 adds refresh (web cookie / desktop
 * safeStorage) and the full app shell.
 */
export function SessionProvider({ api, children }: { api: ApiClient; children: ReactNode }) {
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const tokenRef = useRef<string | null>(null);

  // Every API call carries the current access token.
  useEffect(() => {
    const auth: Middleware = {
      onRequest({ request }) {
        if (tokenRef.current) request.headers.set("Authorization", `Bearer ${tokenRef.current}`);
        return request;
      },
    };
    api.use(auth);
    return () => api.eject(auth);
  }, [api]);

  const apply = useCallback((token: string | null) => {
    tokenRef.current = token;
    setAccessToken(token);
  }, []);

  const login = useCallback(
    async (email: string, password: string, tenantId?: string): Promise<LoginResult> => {
      const client = window.smartbiz?.isDesktop ? "desktop" : "web";
      const { data, error } = await api.POST("/auth/login", {
        body: { email, password, client, tenant_id: tenantId ?? null },
      });
      if (data) {
        apply((data as TokenOut).access_token);
        return { ok: true };
      }
      const problem = (error ?? {}) as { code?: string; memberships?: TenantChoice[] };
      return { ok: false, code: problem.code ?? "unknown", memberships: problem.memberships };
    },
    [api, apply],
  );

  const logout = useCallback(() => apply(null), [apply]);
  const value = useMemo(() => ({ accessToken, login, logout }), [accessToken, login, logout]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
