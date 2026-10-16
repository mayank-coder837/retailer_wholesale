import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { CustomerGroups } from "@/routes/parties/customer-groups";
import { showMoney, usePriceLists, usePromotions } from "@/routes/pricing/queries";
import { useBusiness } from "@/shell/use-business";

/** Settings → Pricing (M1.3): the business's price lists and promotions. Prices themselves are set per product. */
export function PricingSettingsPage() {
  const { t } = useTranslation();
  const { current } = useBusiness();
  if (!current) return <p>{t("common.loading")}</p>;
  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <h1 className="text-2xl font-bold">{t("pricing.settingsTitle")}</h1>
      <p className="text-sm text-muted-foreground">{t("pricing.settingsHint")}</p>
      <PriceLists businessId={current.id} currency={current.base_currency} />
      <Promotions businessId={current.id} />
      <CustomerGroups businessId={current.id} />
    </div>
  );
}

function PriceLists({ businessId, currency }: { businessId: string; currency: string }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const lists = usePriceLists(businessId);
  const empty = { en: "", ar: "", standard: false, from: "", to: "" };
  const [form, setForm] = useState(empty);
  const add = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/price-lists", {
          body: {
            business_id: businessId,
            currency,
            name_en: form.en,
            name_ar: form.ar,
            is_standard: form.standard,
            valid_from: form.from || null,
            valid_to: form.to || null,
          },
        }),
      ),
    onSuccess: async () => {
      setForm(empty);
      await queryClient.invalidateQueries({ queryKey: ["price-lists"] });
    },
  });
  return (
    <section className="flex flex-col gap-3" aria-label={t("pricing.lists")}>
      <h2 className="text-lg font-semibold">{t("pricing.lists")}</h2>
      {(lists.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("pricing.noLists")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {(lists.data ?? []).map((l) => (
            <li key={l.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
              <span>
                {i18n.language === "ar" ? l.name_ar : l.name_en}
                {l.is_standard ? ` · ${t("pricing.standard")}` : ""}
              </span>
              <span className="text-muted-foreground" dir="ltr">
                {l.valid_from || l.valid_to ? `${l.valid_from ?? "…"} → ${l.valid_to ?? "…"}` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
      <form
        aria-label={t("pricing.addList")}
        className="flex flex-col gap-3 rounded-lg border p-4"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate();
        }}
      >
        <h3 className="font-semibold">{t("pricing.addList")}</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("catalog.nameEn")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                required
                value={form.en}
                onChange={(e) => setForm({ ...form, en: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("catalog.nameAr")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                dir="rtl"
                required
                value={form.ar}
                onChange={(e) => setForm({ ...form, ar: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("pricing.validFrom")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                type="date"
                value={form.from}
                onChange={(e) => setForm({ ...form, from: e.target.value })}
              />
            )}
          </Field>
          <Field label={t("pricing.validTo")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                type="date"
                value={form.to}
                onChange={(e) => setForm({ ...form, to: e.target.value })}
              />
            )}
          </Field>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={form.standard}
            onChange={(e) => setForm({ ...form, standard: e.target.checked })}
          />
          {t("pricing.isStandard")}
        </label>
        {add.isError && (
          <p role="alert" className="text-sm text-destructive">
            {t(`pricing.errors.${errorCode(add.error)}`, { defaultValue: t("catalog.errors.generic") })}
          </p>
        )}
        <Button type="submit" className="self-start" disabled={add.isPending}>
          {t("catalog.add")}
        </Button>
      </form>
    </section>
  );
}

function Promotions({ businessId }: { businessId: string }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const promotions = usePromotions(businessId);
  const archive = useMutation({
    mutationFn: async (id: string) =>
      unwrap(
        await api.PATCH("/promotions/{promotion_id}", {
          params: { path: { promotion_id: id } },
          body: { archived: true },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["promotions"] }),
  });
  const active = (promotions.data ?? []).filter((p) => !p.archived_at);
  return (
    <section className="flex flex-col gap-3" aria-label={t("pricing.promotions")}>
      <h2 className="text-lg font-semibold">{t("pricing.promotions")}</h2>
      <p className="text-sm text-muted-foreground">{t("pricing.promotionsHint")}</p>
      {active.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("pricing.noPromotions")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {active.map((p) => (
            <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span>
                {i18n.language === "ar" ? p.name_ar : p.name_en} · {t(`pricing.kinds.${p.kind}`)}{" "}
                {p.kind === "percent" ? `${showMoney(p.value).replace(/\.00$/, "")}%` : showMoney(p.value)}
              </span>
              <span className="text-muted-foreground" dir="ltr">
                {p.valid_from || p.valid_to
                  ? `${p.valid_from ?? "…"} → ${p.valid_to ?? "…"}`
                  : t("pricing.noEnd")}
              </span>
              <Button
                variant="ghost"
                size="sm"
                disabled={archive.isPending}
                onClick={() => archive.mutate(p.id)}
              >
                {t("pricing.end")}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
