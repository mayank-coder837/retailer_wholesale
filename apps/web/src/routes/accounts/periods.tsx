import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import { todayIso, useFiscalYears, type FiscalPeriod } from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { useBusiness } from "@/shell/use-business";

/** Accounts → fiscal periods (M1.5): lock a month so nothing more posts into it without the override. */
export function PeriodsPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const api = useApi();
  const queryClient = useQueryClient();
  const years = useFiscalYears(current?.id);
  const [day, setDay] = useState(todayIso());

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["fiscal-years"] });
  const lock = useMutation({
    mutationFn: async ({ period, locked }: { period: FiscalPeriod; locked: boolean }) => {
      const path = { params: { path: { period_id: period.id } } };
      return unwrap(
        locked
          ? await api.PUT("/fiscal-periods/{period_id}/lock", path)
          : await api.PUT("/fiscal-periods/{period_id}/unlock", path),
      );
    },
    onSuccess: refresh,
  });
  const create = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/fiscal-years", { body: { business_id: current!.id, day } })),
    onSuccess: refresh,
  });
  const month = (p: FiscalPeriod) =>
    new Intl.DateTimeFormat(i18n.language === "ar" ? "ar-AE" : "en-AE", {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${p.start_date}T00:00:00Z`));
  const failure = lock.error ?? create.error ?? years.error;

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <h1 className="text-2xl font-bold">{t("accounts.title")}</h1>
      <AccountsNav />
      <p className="text-sm text-muted-foreground">{t("accounts.periods.hint")}</p>
      {failure && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(failure)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      {years.isPending && <p>{t("common.loading")}</p>}
      {years.data?.length === 0 && <p className="text-muted-foreground">{t("accounts.periods.none")}</p>}
      {years.data?.map((year) => (
        <section key={year.id} className="flex flex-col gap-2">
          <h2 className="font-semibold">{t("accounts.periods.year", { code: year.code })}</h2>
          <table className="w-full text-sm">
            <tbody className="divide-y">
              {year.periods.map((p) => (
                <tr key={p.id}>
                  <td className="py-1.5">{month(p)}</td>
                  <td className="py-1.5">{t(`accounts.periods.${p.status}`)}</td>
                  <td className="py-1.5 text-end">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={lock.isPending}
                      onClick={() => lock.mutate({ period: p, locked: p.status === "open" })}
                    >
                      {p.status === "open"
                        ? t("accounts.periods.lock", { month: month(p) })
                        : t("accounts.periods.unlock", { month: month(p) })}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
      <form
        className="flex flex-wrap items-end gap-2 border-t pt-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (current && day) create.mutate();
        }}
      >
        <Field label={t("accounts.periods.createFor")} hint={t("accounts.periods.createHint")}>
          {(p) => (
            <input
              {...p}
              type="date"
              className={inputClass}
              value={day}
              onChange={(e) => setDay(e.target.value)}
            />
          )}
        </Field>
        <Button type="submit" variant="outline" disabled={create.isPending}>
          {t("accounts.periods.create")}
        </Button>
      </form>
    </div>
  );
}
