import { useTranslation } from "react-i18next";

import { AreaNav, type AreaLink } from "@/components/area-nav";
import { money } from "@/routes/accounts/queries";

const SECTIONS = [
  "/accounts/vouchers",
  "/accounts/cheques",
  "/accounts/trial-balance",
  "/accounts/ageing",
  "/accounts/periods",
];

/** The Accounts area's own menu, under its title. An account's ledger belongs to the chart; voucher entry and a
 * voucher's page to Vouchers. */
export function AccountsNav() {
  const { t } = useTranslation();
  const links: AreaLink[] = [
    {
      to: "/accounts",
      label: t("accounts.nav.chart"),
      matches: (p) =>
        p === "/accounts" || (p.startsWith("/accounts/") && !SECTIONS.some((x) => p.startsWith(x))),
    },
    {
      to: "/accounts/vouchers",
      label: t("accounts.nav.vouchers"),
      matches: (p) => p.startsWith("/accounts/vouchers") || p.startsWith("/vouchers/"),
    },
    {
      to: "/accounts/cheques",
      label: t("accounts.nav.cheques"),
      matches: (p) => p.startsWith("/accounts/cheques"),
    },
    {
      to: "/accounts/trial-balance",
      label: t("accounts.nav.trialBalance"),
      matches: (p) => p.startsWith("/accounts/trial-balance"),
    },
    {
      to: "/accounts/ageing",
      label: t("accounts.nav.ageing"),
      matches: (p) => p.startsWith("/accounts/ageing"),
    },
    {
      to: "/accounts/periods",
      label: t("accounts.nav.periods"),
      matches: (p) => p.startsWith("/accounts/periods"),
    },
  ];
  return <AreaNav label={t("accounts.menu")} links={links} />;
}

/** A debit-positive balance as "1,050.00 Dr" / "1,050.00 Cr" (zero: "0.00"). The Dr/Cr slot keeps its width
 * when empty, so a column of balances (right-aligned, in Arabic too) lines up on the decimal point. */
export function Balance({ amount }: { amount: string }) {
  const { t } = useTranslation();
  const negative = amount.trim().startsWith("-");
  const shown = money(negative ? amount.trim().slice(1) : amount);
  const zero = shown === "0.00";
  return (
    <span dir="ltr" className="whitespace-nowrap tabular-nums">
      {shown}
      <span className="ms-1 inline-block w-8 text-start text-xs text-muted-foreground">
        {zero ? "" : negative ? t("accounts.cr") : t("accounts.dr")}
      </span>
    </span>
  );
}
