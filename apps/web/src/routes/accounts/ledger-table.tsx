import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { Balance } from "@/routes/accounts/accounts-nav";
import { money, type Ledger } from "@/routes/accounts/queries";

/** Opening balance, the lines with a running balance, and the closing balance (account or party ledger). */
export function LedgerTable({ ledger }: { ledger: Ledger }) {
  const { t } = useTranslation();
  return (
    <table className="w-full text-sm">
      <thead className="text-muted-foreground">
        <tr>
          <th className="py-2 text-start font-medium">{t("accounts.ledger.date")}</th>
          <th className="py-2 text-start font-medium">{t("accounts.ledger.voucher")}</th>
          <th className="py-2 text-start font-medium">{t("accounts.ledger.narration")}</th>
          <th className="py-2 text-end font-medium">{t("accounts.dr")}</th>
          <th className="py-2 text-end font-medium">{t("accounts.cr")}</th>
          <th className="py-2 text-end font-medium">{t("accounts.ledger.balance")}</th>
        </tr>
      </thead>
      <tbody className="divide-y">
        <tr className="text-muted-foreground">
          <td className="py-1.5" colSpan={5}>
            {t("accounts.ledger.opening")}
          </td>
          <td className="py-1.5 text-end">
            <Balance amount={ledger.opening} />
          </td>
        </tr>
        {ledger.lines.map((line, i) => (
          <tr key={i}>
            <td className="py-1.5" dir="ltr">
              {line.entry_date}
            </td>
            <td className="py-1.5 font-mono" dir="ltr">
              {line.document_id ? (
                <Link
                  to="/accounts/vouchers/$voucherId"
                  params={{ voucherId: line.document_id }}
                  className="underline-offset-2 hover:underline"
                >
                  {line.number}
                </Link>
              ) : (
                line.number
              )}
            </td>
            <td className="py-1.5">{line.description ?? line.narration ?? ""}</td>
            <td className="py-1.5 text-end tabular-nums" dir="ltr">
              {line.debit === "0.0000" ? "" : money(line.debit)}
            </td>
            <td className="py-1.5 text-end tabular-nums" dir="ltr">
              {line.credit === "0.0000" ? "" : money(line.credit)}
            </td>
            <td className="py-1.5 text-end">
              <Balance amount={line.balance} />
            </td>
          </tr>
        ))}
        <tr className="font-semibold">
          <td className="py-1.5" colSpan={3}>
            {t("accounts.ledger.closing")}
          </td>
          <td className="py-1.5 text-end tabular-nums" dir="ltr">
            {money(ledger.debit_total)}
          </td>
          <td className="py-1.5 text-end tabular-nums" dir="ltr">
            {money(ledger.credit_total)}
          </td>
          <td className="py-1.5 text-end">
            <Balance amount={ledger.closing} />
          </td>
        </tr>
      </tbody>
    </table>
  );
}
