import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { cn } from "@/lib/utils";

export function ServerStatus() {
  const { t } = useTranslation();
  const api = useApi();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const { data, error } = await api.GET("/health");
      if (error || !data) throw new Error("API not reachable");
      return data;
    },
    refetchInterval: 30_000,
  });
  const status = health.isPending ? "checking" : health.isSuccess ? "online" : "offline";

  return (
    <p
      className="flex items-center gap-2 text-xs text-muted-foreground"
      data-testid="api-status"
      data-status={status}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block size-2 rounded-full",
          status === "online" && "bg-success",
          status === "offline" && "bg-destructive",
          status === "checking" && "bg-muted-foreground",
        )}
      />
      {t("server.label")}: {t(`server.${status}`)}
    </p>
  );
}
