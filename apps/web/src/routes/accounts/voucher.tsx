import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { AccountsNav } from "@/routes/accounts/accounts-nav";
import { money, useVoucher } from "@/routes/accounts/queries";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { CodeTag } from "@/components/code-tag";
import { useParty } from "@/routes/parties/queries";

/** Accounts → one voucher (M1.5): what it posted; a draft can be posted or deleted here. */
export function VoucherPage() {
  const { t, i18n } = useTranslation();
  const { voucherId } = useParams({ strict: false }) as { voucherId: string };
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const voucher = useVoucher(voucherId);
  const [lockReason, setLockReason] = useState<string | null>(null);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["voucher", voucherId] });
    await queryClient.invalidateQueries({ queryKey: ["vouchers"] });
    await queryClient.invalidateQueries({ queryKey: ["accounts"] });
  };
  const post = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/vouchers/{doc_id}/post", {
          params: { path: { doc_id: voucherId } },
          body: { override_lock_reason: lockReason?.trim() || null },
        }),
      ),
    onSuccess: refresh,
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean };
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
    },
  });
  const remove = useMutation({
    mutationFn: async () => {
      const result = await api.DELETE("/vouchers/{doc_id}", { params: { path: { doc_id: voucherId } } });
      if (result.error !== undefined) throw result.error;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["vouchers"] });
      await navigate({ to: "/accounts/vouchers" });
    },
  });

  if (voucher.isPending) return <p>{t("common.loading")}</p>;
  if (voucher.isError)
    return (
      <p role="alert" className="text-destructive">
        {t(`accounts.errors.${errorCode(voucher.error)}`, { defaultValue: t("accounts.errors.generic") })}
      </p>
    );
  const v = voucher.data;
  const failure = post.error ?? remove.error;

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold leading-9">
          {t(`accounts.voucherTypes.${v.doc_type}`)}{" "}
          <span className="font-mono" dir="ltr">
            {v.number ?? ""}
          </span>
        </h1>
        <span className="rounded-full bg-muted px-3 py-1 text-sm">{t(`accounts.status.${v.status}`)}</span>
      </div>
      <AccountsNav />
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
        <dt className="text-muted-foreground">{t("accounts.voucher.date")}</dt>
        <dd dir="ltr">{v.doc_date}</dd>
        <dt className="text-muted-foreground">{t("accounts.voucher.total")}</dt>
        <dd className="tabular-nums" dir="ltr">
          {money(v.total)}
        </dd>
        {v.reference && (
          <>
            <dt className="text-muted-foreground">{t("accounts.voucher.reference")}</dt>
            <dd>{v.reference}</dd>
          </>
        )}
        {v.vat_total !== "0.0000" && (
          <>
            <dt className="text-muted-foreground">{t("accounts.voucher.vat")}</dt>
            <dd className="tabular-nums" dir="ltr">
              {money(v.vat_total)}
            </dd>
          </>
        )}
      </dl>
      {v.narration && <p>{v.narration}</p>}

      {v.journal.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h2 className="font-semibold">{t("accounts.voucher.posted")}</h2>
          <table className="w-full text-sm">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-2 text-start font-medium">{t("accounts.voucher.account")}</th>
                <th className="py-2 text-right font-medium">{t("accounts.dr")}</th>
                <th className="py-2 text-right font-medium">{t("accounts.cr")}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {v.journal.map((j) => (
                <tr key={j.line_no}>
                  <td className="py-1.5">
                    <CodeTag>{j.account_code}</CodeTag>
                    {i18n.language === "ar" ? j.account_name_ar : j.account_name_en}
                    {j.party_id && <PartyName partyId={j.party_id} />}
                    {j.description && <span className="ms-2 text-muted-foreground">— {j.description}</span>}
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    <span dir="ltr">{j.debit === "0.0000" ? "" : money(j.debit)}</span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums">
                    <span dir="ltr">{j.credit === "0.0000" ? "" : money(j.credit)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : (
        <p className="text-muted-foreground">{t("accounts.voucher.draftNote")}</p>
      )}

      {v.status === "draft" && (
        <div className="flex flex-col gap-3">
          {lockReason !== null && (
            <Field label={t("accounts.voucher.lockReason")} hint={t("accounts.voucher.lockHint")}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  value={lockReason}
                  onChange={(e) => setLockReason(e.target.value)}
                />
              )}
            </Field>
          )}
          {failure && !(errorCode(failure) === "period_locked" && lockReason !== null) && (
            <p role="alert" className="text-destructive">
              {t(`accounts.errors.${errorCode(failure)}`, { defaultValue: t("accounts.errors.generic") })}
            </p>
          )}
          <div className="flex gap-2">
            <Button
              disabled={post.isPending || (lockReason !== null && !lockReason.trim())}
              onClick={() => post.mutate()}
            >
              {lockReason !== null ? t("accounts.voucher.postLocked") : t("accounts.voucher.post")}
            </Button>
            <Button variant="outline" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t("accounts.voucher.deleteDraft")}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/** The customer or supplier of a party line, after its account. */
function PartyName({ partyId }: { partyId: string }) {
  const { i18n } = useTranslation();
  const party = useParty(partyId);
  if (!party.data) return null;
  return (
    <span className="ms-2 text-muted-foreground">
      · {i18n.language === "ar" ? party.data.name_ar : party.data.name_en}
    </span>
  );
}
