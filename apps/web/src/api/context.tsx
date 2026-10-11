import type { ReactNode } from "react";

import type { ApiClient } from "@smartbiz/api-client";

import { ApiContext } from "@/api/use-api";

export function ApiProvider({ client, children }: { client: ApiClient; children: ReactNode }) {
  return <ApiContext.Provider value={client}>{children}</ApiContext.Provider>;
}
