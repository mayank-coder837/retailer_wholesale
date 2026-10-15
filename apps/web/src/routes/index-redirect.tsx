import { Navigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { useSession } from "@/auth/use-session";

/** `/` → the dashboard when signed in, otherwise the login page. */
export function IndexRedirect() {
  const { t } = useTranslation();
  const { status } = useSession();
  if (status === "loading")
    return <p className="p-10 text-sm text-muted-foreground">{t("common.loading")}</p>;
  return <Navigate to={status === "signed_in" ? "/dashboard" : "/login"} />;
}
