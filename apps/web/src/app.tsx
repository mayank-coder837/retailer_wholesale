import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useState } from "react";

import type { ApiClient } from "@smartbiz/api-client";

import { ApiProvider } from "@/api/context";
import { SessionProvider } from "@/auth/session";
import { createAppRouter } from "@/router";

export function App({ api }: { api: ApiClient }) {
  const [queryClient] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false } } }));
  const [router] = useState(() => createAppRouter());

  return (
    <ApiProvider client={api}>
      <SessionProvider api={api}>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </SessionProvider>
    </ApiProvider>
  );
}
