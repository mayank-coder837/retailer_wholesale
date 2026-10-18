import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

const LINKS = [
  { to: "/inventory", key: "products" },
  { to: "/inventory/stock", key: "stock" },
  { to: "/inventory/documents", key: "documents" },
  { to: "/inventory/negative", key: "negative" },
] as const;

/** The Inventory area's own menu, under its title. */
export function InventoryNav() {
  const { t } = useTranslation();
  return (
    <nav aria-label={t("inventory.menu")} className="flex flex-wrap gap-1 border-b pb-2 text-sm">
      {LINKS.map((l) => (
        <Link
          key={l.to}
          to={l.to}
          activeOptions={{ exact: true }}
          className="rounded-md px-3 py-1.5 hover:bg-muted"
          activeProps={{ className: "bg-secondary font-semibold", "aria-current": "page" }}
        >
          {t(`inventory.nav.${l.key}`)}
        </Link>
      ))}
    </nav>
  );
}
