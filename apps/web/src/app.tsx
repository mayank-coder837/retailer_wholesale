import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useState } from "react";

import type { ApiClient } from "@smartbiz/api-client";

import { ApiProvider } from "@/api/context";
import { createAppRouter } from "@/router";

export function App({ api }: { api: ApiClient }) {
  const [queryClient] = useState(() => new QueryClient());
  const [router] = useState(() => createAppRouter());

  return (
    <ApiProvider client={api}>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ApiProvider>
  );
}
