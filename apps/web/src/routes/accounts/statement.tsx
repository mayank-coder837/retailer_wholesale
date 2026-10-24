import { useTranslation } from "react-i18next";

import { Balance } from "@/routes/accounts/accounts-nav";
import { LedgerTable } from "@/routes/accounts/ledger-table";
import { money, usePartyLedger } from "@/routes/accounts/queries";

/** The party's statement in this business (M1.5): ledger lines, open bills and what is on account. */
export function PartyStatement({ businessId, partyId }: { businessId: string; partyId: string }) {
  const { t } = useTranslation();
  const statement = usePartyLedger(businessId, partyId);
  if (!statement.data) return null; // no permission or nothing yet: the rest of the page still works
  const s = statement.data;

  return (
    <section className="flex flex-col gap-3" aria-label={t("accounts.statement.title")}>
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b pb-2">
        <h2 className="text-lg font-semibold">{t("accounts.statement.title")}</h2>
        <p className="text-sm">
          {t("accounts.statement.balance")} <Balance amount={s.closing} />
        </p>
      </div>
      {s.lines.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("accounts.statement.empty")}</p>
      ) : (
        <LedgerTable ledger={s} />
      )}
      {s.open_bills.length > 0 && (
        <table className="w-full text-sm" aria-label={t("accounts.statement.openBills")}>
          <caption className="py-2 text-start font-semibold">{t("accounts.statement.openBills")}</caption>
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 text-start font-medium">{t("accounts.billWise.reference")}</th>
              <th className="py-1 text-start font-medium">{t("accounts.billWise.date")}</th>
              <th className="py-1 text-start font-medium">{t("accounts.billWise.due")}</th>
              <th className="py-1 text-right font-medium">{t("accounts.billWise.open")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {s.open_bills.map((b) => (
              <tr key={b.id}>
                <td className="py-1 font-mono">
                  <span dir="ltr">
                    {b.reference}
                    {b.is_advance && <span className="ms-1 text-xs">({t("accounts.billWise.advance")})</span>}
                  </span>
                </td>
                <td className="py-1">
                  <span dir="ltr">{b.bill_date}</span>
                </td>
                <td className="py-1">
                  <span dir="ltr">{b.due_date ?? ""}</span>
                </td>
                <td className="py-1 text-right">
                  <Balance amount={b.direction === "debit" ? b.amount_open : `-${b.amount_open}`} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {s.on_account !== "0.0000" && (
        <p className="text-sm">
          {t("accounts.statement.onAccount")}{" "}
          <span dir="ltr" className="tabular-nums">
            {money(s.on_account)}
          </span>
        </p>
      )}
    </section>
  );
}
