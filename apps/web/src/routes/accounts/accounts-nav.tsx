import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { money } from "@/routes/accounts/queries";

const LINKS = [
  { to: "/accounts", key: "chart" },
  { to: "/accounts/vouchers", key: "vouchers" },
  { to: "/accounts/trial-balance", key: "trialBalance" },
  { to: "/accounts/periods", key: "periods" },
] as const;

/** The Accounts area's own menu, under its title. */
export function AccountsNav() {
  const { t } = useTranslation();
  return (
    <nav aria-label={t("accounts.menu")} className="flex flex-wrap gap-1 border-b pb-2 text-sm">
      {LINKS.map((l) => (
        <Link
          key={l.to}
          to={l.to}
          activeOptions={{ exact: true }}
          className="rounded-md px-3 py-1.5 hover:bg-muted"
          activeProps={{ className: "bg-secondary font-semibold", "aria-current": "page" }}
        >
          {t(`accounts.nav.${l.key}`)}
        </Link>
      ))}
    </nav>
  );
}

/** A debit-positive balance as "1,050.00 Dr" / "1,050.00 Cr" (zero: "0.00"). */
export function Balance({ amount }: { amount: string }) {
  const { t } = useTranslation();
  const negative = amount.trim().startsWith("-");
  const shown = money(negative ? amount.trim().slice(1) : amount);
  const zero = shown === "0.00";
  return (
    <span dir="ltr" className="tabular-nums">
      {shown}
      {!zero && (
        <span className="ms-1 text-xs text-muted-foreground">
          {negative ? t("accounts.cr") : t("accounts.dr")}
        </span>
      )}
    </span>
  );
}
