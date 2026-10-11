import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type { Middleware } from "openapi-fetch";
export type ApiClient = ReturnType<typeof createApiClient>;

/**
 * Typed client for the SmartBiz OS API. `baseUrl` is the local main PC, a LAN address or the cloud.
 * Cookies are sent so the web app's HttpOnly refresh cookie reaches `/auth` (the API allows its own origins only).
 */
export function createApiClient(baseUrl: string, fetchImpl?: typeof fetch) {
  return createClient<paths>({ baseUrl, fetch: fetchImpl, credentials: "include" });
}
