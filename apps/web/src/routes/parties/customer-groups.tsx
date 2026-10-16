import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { errorCode, unwrap } from "@/routes/catalog/queries";
import { useCustomerGroups } from "@/routes/parties/queries";
import { usePriceLists } from "@/routes/pricing/queries";

/** Settings → Pricing → customer groups (M1.4): a group's price list is pricing step 3. */
export function CustomerGroups({ businessId }: { businessId: string }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const groups = useCustomerGroups(businessId);
  const lists = usePriceLists(businessId);
  const empty = { en: "", ar: "", list: "" };
  const [form, setForm] = useState(empty);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["customer-groups"] });
  const add = useMutation({
    mutationFn: async () =>
      unwrap(
        await api.POST("/customer-groups", {
          body: {
            business_id: businessId,
            name_en: form.en.trim(),
            name_ar: form.ar.trim(),
            price_list_id: form.list || null,
          },
        }),
      ),
    onSuccess: async () => {
      setForm(empty);
      await refresh();
    },
  });
  const setList = useMutation({
    mutationFn: async ({ groupId, listId }: { groupId: string; listId: string }) =>
      unwrap(
        await api.PATCH("/customer-groups/{group_id}", {
          params: { path: { group_id: groupId } },
          body: { price_list_id: listId || null },
        }),
      ),
    onSuccess: refresh,
  });
  const name = (x: { name_en: string; name_ar: string }) => (i18n.language === "ar" ? x.name_ar : x.name_en);
  const listOptions = (
    <>
      <option value="">{t("parties.noList")}</option>
      {(lists.data ?? []).map((l) => (
        <option key={l.id} value={l.id}>
          {name(l)}
        </option>
      ))}
    </>
  );

  return (
    <section className="flex flex-col gap-3" aria-label={t("parties.groups")}>
      <h2 className="text-lg font-semibold">{t("parties.groups")}</h2>
      <p className="text-sm text-muted-foreground">{t("parties.groupsHint")}</p>
      {(groups.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("parties.noGroups")}</p>
      ) : (
        <ul className="divide-y rounded-md border text-sm">
          {(groups.data ?? []).map((g) => (
            <li key={g.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <span>{name(g)}</span>
              <select
                className={inputClass + " w-auto"}
                aria-label={t("parties.groupList", { name: name(g) })}
                value={g.price_list_id ?? ""}
                disabled={setList.isPending}
                onChange={(e) => setList.mutate({ groupId: g.id, listId: e.target.value })}
              >
                {listOptions}
              </select>
            </li>
          ))}
        </ul>
      )}
      <form
        className="grid items-end gap-3 sm:grid-cols-[1fr_1fr_1fr_auto]"
        onSubmit={(e) => {
          e.preventDefault();
          if (form.en.trim() && form.ar.trim()) add.mutate();
        }}
      >
        <Field label={t("parties.nameEn")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="ltr"
              value={form.en}
              onChange={(e) => setForm({ ...form, en: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.nameAr")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              dir="rtl"
              value={form.ar}
              onChange={(e) => setForm({ ...form, ar: e.target.value })}
            />
          )}
        </Field>
        <Field label={t("parties.priceList")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={form.list}
              onChange={(e) => setForm({ ...form, list: e.target.value })}
            >
              {listOptions}
            </select>
          )}
        </Field>
        <Button type="submit" disabled={!form.en.trim() || !form.ar.trim() || add.isPending}>
          {t("parties.addGroup")}
        </Button>
      </form>
      {(add.isError || setList.isError) && (
        <p role="alert" className="text-sm text-destructive">
          {t(`parties.errors.${errorCode(add.error ?? setList.error)}`, {
            defaultValue: t("parties.errors.generic"),
          })}
        </p>
      )}
    </section>
  );
}
