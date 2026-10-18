import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { AccountsNav, Balance } from "@/routes/accounts/accounts-nav";
import {
  accountLabel,
  accountName,
  useAccountRoles,
  useAccounts,
  type Account,
  type AccountIn,
  type AccountRole,
  type AccountType,
} from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { useBusiness } from "@/shell/use-business";

/** Ledger types that fit under each nature (as the API checks). */
const TYPES_BY_NATURE: Record<Account["nature"], AccountType[]> = {
  asset: ["cash", "bank", "receivable", "stock", "tax", "current_asset", "fixed_asset"],
  liability: ["payable", "tax", "current_liability", "non_current_liability"],
  equity: ["equity"],
  income: ["sales", "other_income"],
  expense: ["cogs", "expense"],
};

function depths(accounts: Account[]): Map<string, number> {
  const byId = new Map(accounts.map((a) => [a.id, a]));
  const out = new Map<string, number>();
  for (const a of accounts) {
    let depth = 0;
    let parent = a.parent_id ? byId.get(a.parent_id) : undefined;
    while (parent) {
      depth += 1;
      parent = parent.parent_id ? byId.get(parent.parent_id) : undefined;
    }
    out.set(a.id, depth);
  }
  return out;
}

/** Accounts → chart of accounts (M1.5): groups and ledgers in tree order, with balances. */
export function ChartPage() {
  const { t, i18n } = useTranslation();
  const { current } = useBusiness();
  const accounts = useAccounts(current?.id);
  const [adding, setAdding] = useState(false);

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">{t("accounts.title")}</h1>
        <Button onClick={() => setAdding((v) => !v)} aria-expanded={adding}>
          {t("accounts.chart.new")}
        </Button>
      </div>
      <AccountsNav />
      {adding && accounts.data && current && (
        <NewAccount businessId={current.id} accounts={accounts.data} onDone={() => setAdding(false)} />
      )}
      {accounts.isPending && <p>{t("common.loading")}</p>}
      {accounts.isError && (
        <p role="alert" className="text-destructive">
          {t(`accounts.errors.${errorCode(accounts.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      {accounts.data && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("accounts.chart.account")}</th>
              <th className="py-2 text-start font-medium">{t("accounts.chart.type")}</th>
              <th className="py-2 text-end font-medium">{t("accounts.chart.balance")}</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {(() => {
              const depth = depths(accounts.data);
              return accounts.data.map((a) => (
                <tr key={a.id} className={a.is_group ? "bg-muted/30 font-semibold" : undefined}>
                  <td
                    className="py-1.5"
                    style={{ paddingInlineStart: `${(depth.get(a.id) ?? 0) * 1.25}rem` }}
                  >
                    <span className="me-2 font-mono text-muted-foreground" dir="ltr">
                      {a.code}
                    </span>
                    <Link
                      to="/accounts/$accountId"
                      params={{ accountId: a.id }}
                      className="underline-offset-2 hover:underline"
                    >
                      {accountName(a, i18n.language)}
                    </Link>
                  </td>
                  <td className="py-1.5 text-muted-foreground">
                    {a.account_type ? t(`accounts.types.${a.account_type}`) : t("accounts.chart.group")}
                    {(a.roles ?? []).length > 0 && (
                      <span className="ms-2 text-xs">
                        ({(a.roles ?? []).map((r) => t(`accounts.roles.${r}`)).join(", ")})
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 text-end">
                    <Balance amount={a.balance} />
                  </td>
                </tr>
              ));
            })()}
          </tbody>
        </table>
      )}
      {accounts.data && current && <RolesSection businessId={current.id} accounts={accounts.data} />}
    </div>
  );
}

function NewAccount({
  businessId,
  accounts,
  onDone,
}: {
  businessId: string;
  accounts: Account[];
  onDone: () => void;
}) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const groups = accounts.filter((a) => a.is_group && a.archived_at === null);
  const [parentId, setParentId] = useState(groups[0]?.id ?? "");
  const [isGroup, setIsGroup] = useState(false);
  const [code, setCode] = useState("");
  const [en, setEn] = useState("");
  const [ar, setAr] = useState("");
  const parent = groups.find((g) => g.id === parentId);
  const types = parent ? TYPES_BY_NATURE[parent.nature] : [];
  const [type, setType] = useState<AccountType | "">("");
  const chosenType = type && types.includes(type) ? type : (types[types.length - 1] ?? "");

  const save = useMutation({
    mutationFn: async () => {
      const body: AccountIn = {
        business_id: businessId,
        code: code.trim(),
        name_en: en.trim(),
        name_ar: ar.trim(),
        parent_id: parentId,
        is_group: isGroup,
        account_type: isGroup ? null : chosenType || null,
        allow_manual_posting: true,
      };
      return unwrap(await api.POST("/accounts", { body }));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
      onDone();
    },
  });
  const ready = code.trim() && en.trim() && ar.trim() && parentId;

  return (
    <form
      className="grid gap-3 rounded-lg border p-4 sm:grid-cols-2"
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) save.mutate();
      }}
    >
      <h2 className="font-semibold sm:col-span-2">{t("accounts.chart.new")}</h2>
      <Field label={t("accounts.chart.parent")}>
        {(p) => (
          <select
            {...p}
            className={inputClass}
            value={parentId}
            onChange={(e) => setParentId(e.target.value)}
          >
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {accountLabel(g, i18n.language)}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label={t("accounts.chart.kind")}>
        {(p) => (
          <select
            {...p}
            className={inputClass}
            value={isGroup ? "group" : chosenType}
            onChange={(e) => {
              if (e.target.value === "group") setIsGroup(true);
              else {
                setIsGroup(false);
                setType(e.target.value as AccountType);
              }
            }}
          >
            {types.map((ty) => (
              <option key={ty} value={ty}>
                {t(`accounts.types.${ty}`)}
              </option>
            ))}
            <option value="group">{t("accounts.chart.group")}</option>
          </select>
        )}
      </Field>
      <Field label={t("accounts.chart.code")}>
        {(p) => (
          <input
            {...p}
            className={inputClass}
            dir="ltr"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        )}
      </Field>
      <div />
      <Field label={t("accounts.chart.nameEn")}>
        {(p) => (
          <input {...p} className={inputClass} dir="ltr" value={en} onChange={(e) => setEn(e.target.value)} />
        )}
      </Field>
      <Field label={t("accounts.chart.nameAr")}>
        {(p) => (
          <input {...p} className={inputClass} dir="rtl" value={ar} onChange={(e) => setAr(e.target.value)} />
        )}
      </Field>
      {save.isError && (
        <p role="alert" className="text-destructive sm:col-span-2">
          {t(`accounts.errors.${errorCode(save.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      <div className="flex gap-2 sm:col-span-2">
        <Button type="submit" disabled={!ready || save.isPending}>
          {t("accounts.chart.add")}
        </Button>
        <Button type="button" variant="outline" onClick={onDone}>
          {t("accounts.cancel")}
        </Button>
      </div>
    </form>
  );
}

/** Which ledger each posting role uses: changing one affects later postings only. */
function RolesSection({ businessId, accounts }: { businessId: string; accounts: Account[] }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const roles = useAccountRoles(businessId);
  const ledgers = accounts.filter((a) => !a.is_group && a.archived_at === null);
  const change = useMutation({
    mutationFn: async ({ role, accountId }: { role: AccountRole["role"]; accountId: string }) =>
      unwrap(
        await api.PUT("/account-roles/{role}", {
          params: { path: { role } },
          body: { business_id: businessId, account_id: accountId },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["account-roles"] });
      await queryClient.invalidateQueries({ queryKey: ["accounts"] });
    },
  });

  return (
    <details className="rounded-lg border p-4">
      <summary className="cursor-pointer font-semibold">{t("accounts.roles.title")}</summary>
      <p className="mt-2 text-sm text-muted-foreground">{t("accounts.roles.hint")}</p>
      {change.isError && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {t(`accounts.errors.${errorCode(change.error)}`, { defaultValue: t("accounts.errors.generic") })}
        </p>
      )}
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {roles.data?.map((r) => (
          <Field key={r.role} label={t(`accounts.roles.${r.role}`)}>
            {(p) => (
              <select
                {...p}
                className={inputClass}
                value={r.account_id}
                disabled={change.isPending}
                onChange={(e) => change.mutate({ role: r.role, accountId: e.target.value })}
              >
                {ledgers.map((a) => (
                  <option key={a.id} value={a.id}>
                    {accountLabel(a, i18n.language)}
                  </option>
                ))}
              </select>
            )}
          </Field>
        ))}
      </div>
    </details>
  );
}
