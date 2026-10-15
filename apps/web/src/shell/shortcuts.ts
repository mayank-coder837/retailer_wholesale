import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

/** Tally-style voucher keys. Desktop only: in a browser F5 reloads the page and users expect that. */
export const VOUCHER_KEYS: Record<string, "sales" | "purchase" | "payment" | "receipt"> = {
  F8: "sales",
  F9: "purchase",
  F5: "payment",
  F6: "receipt",
};

export function useDesktopShortcuts(): void {
  const navigate = useNavigate();
  useEffect(() => {
    if (!window.smartbiz?.isDesktop) return;
    function onKey(event: KeyboardEvent) {
      const kind = VOUCHER_KEYS[event.key];
      if (!kind || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
      event.preventDefault();
      void navigate({ to: "/vouchers/$kind", params: { kind } });
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
}
