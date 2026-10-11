import { Navigate } from "@tanstack/react-router";

import { useSession } from "@/auth/use-session";

/** `/` → the dashboard when signed in, otherwise the login page. */
export function IndexRedirect() {
  const { status } = useSession();
  if (status === "loading") return null;
  return <Navigate to={status === "signed_in" ? "/dashboard" : "/login"} />;
}
