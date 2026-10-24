import { useQuery } from "@tanstack/react-query";

import { useApi } from "@/api/use-api";
import { unwrap } from "@/routes/catalog/queries";

/** Where a document stands for Cancel and Reverse (GET /documents/{id}). */
export function useDocState(docId: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["doc-state", docId],
    queryFn: async () =>
      unwrap(await api.GET("/documents/{doc_id}", { params: { path: { doc_id: docId } } })),
  });
}
