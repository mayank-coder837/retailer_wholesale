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
      // Sales and purchases have their own document screens (M1.8, M1.7b); payment and receipt are vouchers.
      if (kind === "sales") {
        void navigate({ to: "/sales/new/$kind", params: { kind: "sales_invoice" } });
      } else if (kind === "purchase") {
        void navigate({ to: "/purchases/new/$kind", params: { kind: "purchase_bill" } });
      } else {
        void navigate({ to: "/vouchers/$kind", params: { kind } });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
}
