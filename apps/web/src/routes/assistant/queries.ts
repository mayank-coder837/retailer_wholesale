import { useQuery } from "@tanstack/react-query";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { errorCode, unwrap } from "@/routes/catalog/queries";

export type AiAction = components["schemas"]["AiActionOut"];
export type AiChat = components["schemas"]["AiChatOut"];

/** Things a person can type, shown as a hint. Not translated word for word: each language has its own. */
export const EXAMPLES = ["receipt", "payment", "owed", "owe"] as const;

export function useAiActions(businessId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: ["ai-actions", businessId],
    enabled: !!businessId,
    queryFn: async () =>
      unwrap(await api.GET("/ai/actions", { params: { query: { business_id: businessId! } } })),
  });
}

/** What went wrong, in words: the API's stable error code, or a plain "something went wrong". */
export function errorText(t: (key: string) => string, error: unknown): string {
  const key = `assistant.errors.${errorCode(error)}`;
  return t(key) === key ? t("assistant.errors.generic") : t(key);
}

/** The words for a status or a refusal code, falling back to the code itself so nothing is ever blank. */
export function codeText(
  t: (key: string, o?: { defaultValue: string }) => string,
  group: string,
  code: string,
) {
  return t(`assistant.${group}.${code}`, { defaultValue: code });
}
