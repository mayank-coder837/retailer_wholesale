import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import { LedgerTable } from "@/routes/accounts/ledger-table";
import { accountName, useAccountLedger, useAccounts } from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { useBusiness } from "@/shell/use-business";

/** Accounts → one account (M1.5): its ledger for a date range; a group shows every ledger inside it. */
export function AccountPage() {
  const { t, i18n } = useTranslation();
  const { accountId } = useParams({ strict: false }) as { accountId: string };
  const { current } = useBusiness();
  const api = useApi();
  const queryClient = useQueryClient();
  const accounts = useAccounts(current?.id, true);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const ledger = useAccountLedger(accountId, from, to);
  const account = accounts.data?.find((a) => a.id === accountId);

  const archive = useMutation({
    mutationFn: async (archived: boolean) =>
      unwrap(
        await api.PATCH("/accounts/{account_id}", {
          params: { path: { account_id: accountId } },
          body: { archived },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">
          {account ? (
            <>
              <span className="me-2 font-mono text-muted-foreground" dir="ltr">
                {account.code}
              </span>
              {accountName(account, i18n.language)}
            </>
          ) : (
            t("accounts.title")
          )}
        </h1>
        {account && !account.is_system && (
          <Button variant="outline" onClick={() => archive.mutate(account.archived_at === null)}>
            {account.archived_at === null ? t("accounts.chart.archive") : t("accounts.chart.restore")}
          </Button>
        )}
      </div>
      <AccountsNav />
      {account?.archived_at && <p className="text-muted-foreground">{t("accounts.chart.archived")}</p>}
      {archive.isError && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(archive.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      <div className="flex flex-wrap gap-3">
        <Field label={t("accounts.ledger.from")}>
          {(p) => (
            <input
              {...p}
              type="date"
              className={inputClass}
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          )}
        </Field>
        <Field label={t("accounts.ledger.to")}>
          {(p) => (
            <input
              {...p}
              type="date"
              className={inputClass}
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          )}
        </Field>
      </div>
      {ledger.isPending && <p>{t("common.loading")}</p>}
      {ledger.isError && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(ledger.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      {ledger.data && <LedgerTable ledger={ledger.data} />}
    </div>
  );
}
