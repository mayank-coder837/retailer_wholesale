import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type { components, paths };
export type ApiClient = ReturnType<typeof createApiClient>;

/** Typed client for the SmartBiz OS API. `baseUrl` is the local main PC, a LAN address or the cloud. */
export function createApiClient(baseUrl: string, fetchImpl?: typeof fetch) {
  return createClient<paths>({ baseUrl, fetch: fetchImpl });
}
