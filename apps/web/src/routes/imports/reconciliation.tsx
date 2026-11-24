import { Link } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { CodeTag } from "@/components/code-tag";
import { accountName, money } from "@/routes/accounts/queries";
import { errorCode } from "@/routes/catalog/queries";
import { useReconciliation } from "@/routes/imports/queries";
import { useBusiness } from "@/shell/use-business";

const isZero = (amount: string) => Number(amount) === 0;

/** Did the books come across right? The imported trial balance against the books, account by account. */
export function ReconciliationPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const recon = useReconciliation(current?.id);
  const data = recon.data;

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold leading-9">{t("import.recon.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("import.recon.hint")}</p>
      </div>
      {recon.isPending && <p>{t("common.loading")}</p>}
      {recon.isError && (
        <p role="alert" className="text-destructive">
          {t(`import.errors.${errorCode(recon.error)}`, { defaultValue: t("import.errors.generic") })}
        </p>
      )}
      {data && !data.imported && (
        <p className="text-muted-foreground">
          {t("import.recon.none")}{" "}
          <Link to="/settings/import" className="underline underline-offset-2">
            {t("import.recon.goImport")}
          </Link>
        </p>
      )}
      {data?.imported && (
        <>
          <p
            role="status"
            className={
              data.ties
                ? "rounded-md border border-green-600 px-3 py-2 font-semibold"
                : "rounded-md border border-destructive px-3 py-2 font-semibold text-destructive"
            }
          >
            {data.ties ? t("import.recon.ties") : t("import.recon.differs")}
          </p>
          <p className="text-sm text-muted-foreground">
            {t("import.recon.openingDate")}: <span dir="ltr">{data.opening_date}</span>
          </p>
          <table className="w-full text-sm" aria-label={t("import.recon.title")}>
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 text-start font-medium">{t("import.recon.account")}</th>
                <th className="py-2 text-right font-medium">{t("import.recon.file")}</th>
                <th className="py-2 text-right font-medium">{t("import.recon.books")}</th>
                <th className="py-2 text-right font-medium">{t("import.recon.difference")}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.rows.map((r) => (
                <tr key={r.account_id}>
                  <td className="py-1.5">
                    <CodeTag>{r.code}</CodeTag>
                    <Link
                      to="/accounts/$accountId"
                      params={{ accountId: r.account_id }}
                      className="underline-offset-2 hover:underline"
                    >
                      {accountName(r, i18n.language)}
                    </Link>
                    {r.control && (
                      <span className="ms-2 text-xs text-muted-foreground">{t("import.recon.control")}</span>
                    )}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    <span dir="ltr">{money(r.file_balance)}</span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    <span dir="ltr">{money(r.books_balance)}</span>
                  </td>
                  <td
                    className={
                      isZero(r.difference)
                        ? "py-1.5 text-right tabular-nums text-muted-foreground"
                        : "py-1.5 text-right font-semibold tabular-nums text-destructive"
                    }
                  >
                    <span dir="ltr">{money(r.difference)}</span>
                  </td>
                </tr>
              ))}
              <tr className="font-semibold">
                <td className="py-1.5">
                  {t("import.recon.equity")}
                  {data.equity_code && (
                    <span className="ms-2 font-mono text-xs font-normal" dir="ltr">
                      {data.equity_code}
                    </span>
                  )}
                </td>
                <td />
                <td className="py-1.5 text-right tabular-nums">
                  <span dir="ltr">{money(data.equity_balance)}</span>
                </td>
                <td
                  className={
                    isZero(data.equity_balance)
                      ? "py-1.5 text-right text-muted-foreground"
                      : "py-1.5 text-right text-destructive"
                  }
                >
                  {isZero(data.equity_balance) ? t("import.recon.equityOk") : t("import.recon.equityNot")}
                </td>
              </tr>
            </tbody>
          </table>
          <dl className="flex flex-col gap-1 text-sm">
            <div className="flex gap-2">
              <dt className="text-muted-foreground">{t("import.recon.fileTotals")}</dt>
              <dd dir="ltr" className="tabular-nums">
                {money(data.file_debit)} / {money(data.file_credit)}
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="text-muted-foreground">{t("import.recon.booksTotals")}</dt>
              <dd dir="ltr" className="tabular-nums">
                {money(data.books_debit)} / {money(data.books_credit)}
              </dd>
            </div>
          </dl>
          {!data.ties && <p className="text-sm">{t("import.recon.whatToDo")}</p>}
        </>
      )}
    </div>
  );
}
