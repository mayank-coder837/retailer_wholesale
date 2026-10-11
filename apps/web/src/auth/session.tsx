import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { ApiClient, components, Middleware } from "@smartbiz/api-client";

import {
  SessionContext,
  type AuthResult,
  type SessionStatus,
  type SignupBody,
  type TenantChoice,
} from "@/auth/use-session";

type TokenOut = components["schemas"]["TokenOut"];

/** Renew this many seconds before the access token expires. */
const RENEW_EARLY_S = 60;
const REFRESH_LOCK = "smartbiz-refresh";

function failure(error: unknown): AuthResult {
  const problem = (error ?? {}) as { code?: string; memberships?: TenantChoice[] };
  return { ok: false, code: problem.code ?? "unknown", memberships: problem.memberships };
}

/** Runs `fn` while holding a lock shared by every tab, so two tabs never rotate the same refresh token at once. */
async function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== "undefined" ? navigator.locks : undefined;
  return locks ? locks.request(REFRESH_LOCK, fn) : fn();
}

/**
 * Session (ADR 0007, M0.5): access token in memory, renewed shortly before it expires.
 * Refresh token: web → HttpOnly cookie sent by the browser; desktop → Electron safeStorage via window.smartbiz.
 * On start-up one silent refresh restores a returning user's session.
 */
export function SessionProvider({ api, children }: { api: ApiClient; children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const tokenRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef<Promise<AuthResult> | null>(null);
  const started = useRef(false);
  // The renewal timer calls the latest `refresh` through this ref (refresh → apply → timer → refresh).
  const refreshRef = useRef<(() => Promise<AuthResult>) | null>(null);

  const desktop = typeof window !== "undefined" ? window.smartbiz?.session : undefined;
  const client = desktop ? "desktop" : "web";

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

  const clear = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    tokenRef.current = null;
    setAccessToken(null);
    setTenantId(null);
    setStatus("signed_out");
  }, []);

  const apply = useCallback(
    async (tokens: TokenOut) => {
      tokenRef.current = tokens.access_token;
      setAccessToken(tokens.access_token);
      setTenantId(tokens.tenant_id);
      setStatus("signed_in");
      if (desktop && tokens.refresh_token) await desktop.setRefreshToken(tokens.refresh_token);
      if (timerRef.current) clearTimeout(timerRef.current);
      const delay = Math.max(tokens.expires_in - RENEW_EARLY_S, 10) * 1000;
      timerRef.current = setTimeout(() => void refreshRef.current?.(), delay);
    },
    [desktop],
  );

  const refresh = useCallback((): Promise<AuthResult> => {
    if (inFlight.current) return inFlight.current;
    const run = withRefreshLock(async (): Promise<AuthResult> => {
      const stored = desktop ? await desktop.getRefreshToken() : null;
      if (desktop && !stored) {
        clear();
        return { ok: false, code: "signed_out" };
      }
      const { data, error } = await api.POST("/auth/refresh", { body: { refresh_token: stored } });
      if (!data) {
        if (desktop) await desktop.clear();
        clear();
        return failure(error);
      }
      await apply(data);
      return { ok: true };
    }).finally(() => {
      inFlight.current = null;
    });
    inFlight.current = run;
    return run;
  }, [api, apply, clear, desktop]);

  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  useEffect(() => {
    if (started.current) return; // StrictMode runs effects twice; refresh exactly once
    started.current = true;
    void refresh();
  }, [refresh]);

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  const login = useCallback(
    async (email: string, password: string, tenant?: string): Promise<AuthResult> => {
      const { data, error } = await api.POST("/auth/login", {
        body: { email, password, client, tenant_id: tenant ?? null },
      });
      if (!data) return failure(error);
      await apply(data);
      return { ok: true };
    },
    [api, apply, client],
  );

  const signup = useCallback(
    async (body: Omit<SignupBody, "client">): Promise<AuthResult> => {
      const { data, error } = await api.POST("/auth/signup", { body: { ...body, client } });
      if (!data) return failure(error);
      await apply(data);
      return { ok: true };
    },
    [api, apply, client],
  );

  const switchTenant = useCallback(
    async (tenant: string): Promise<AuthResult> => {
      const { data, error } = await api.POST("/auth/switch-tenant", { body: { tenant_id: tenant, client } });
      if (!data) return failure(error);
      await apply(data);
      return { ok: true };
    },
    [api, apply, client],
  );

  const logout = useCallback(async () => {
    const stored = desktop ? await desktop.getRefreshToken() : null;
    await api.POST("/auth/logout", { body: { refresh_token: stored } });
    if (desktop) await desktop.clear();
    clear();
  }, [api, clear, desktop]);

  const value = useMemo(
    () => ({ status, accessToken, tenantId, login, signup, switchTenant, logout }),
    [status, accessToken, tenantId, login, signup, switchTenant, logout],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}
