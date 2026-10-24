import { useTranslation } from "react-i18next";

import { AreaNav, type AreaLink } from "@/components/area-nav";

/** The Inventory area's own menu, under its title. A product belongs to Products, an item's ledger to Stock, a
 * stock document to Stock documents. */
export function InventoryNav() {
  const { t } = useTranslation();
  const links: AreaLink[] = [
    {
      to: "/inventory",
      label: t("inventory.nav.products"),
      matches: (p) => p === "/inventory" || p.startsWith("/inventory/products"),
    },
    {
      to: "/inventory/stock",
      label: t("inventory.nav.stock"),
      matches: (p) => p.startsWith("/inventory/stock") || p.startsWith("/inventory/items/"),
    },
    {
      to: "/inventory/documents",
      label: t("inventory.nav.documents"),
      matches: (p) => p.startsWith("/inventory/documents"),
    },
    {
      to: "/inventory/negative",
      label: t("inventory.nav.negative"),
      matches: (p) => p.startsWith("/inventory/negative"),
    },
  ];
  return <AreaNav label={t("inventory.menu")} links={links} />;
}
