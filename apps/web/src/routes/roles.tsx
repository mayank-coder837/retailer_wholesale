import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { components } from "@smartbiz/api-client";

import { useApi } from "@/api/use-api";
import { useSession } from "@/auth/use-session";
import { LanguageSwitch } from "@/components/language-switch";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Role = components["schemas"]["RoleOut"];
type Permission = components["schemas"]["PermissionOut"];

const ACTIONS = [
  "view",
  "create",
  "edit",
  "delete",
  "approve",
  "post",
  "cancel",
  "reverse",
  "export",
] as const;
const SPECIAL_RESOURCES = new Set(["price", "credit", "period"]);

export function RolesPage() {
  const { t } = useTranslation();
  const { accessToken } = useSession();

  if (!accessToken) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-10">
        <p>
          {t("roles.loginFirst")}{" "}
          <Link to="/login" className="underline">
            {t("login.title")}
          </Link>
        </p>
      </main>
    );
  }
  return <RolesEditor />;
}

function RolesEditor() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const roles = useQuery({
    queryKey: ["roles"],
    queryFn: async () => {
      const { data, error } = await api.GET("/roles");
      if (error || !data) throw error ?? new Error("roles");
      return data;
    },
  });
  const permissions = useQuery({
    queryKey: ["permissions"],
    queryFn: async () => {
      const { data, error } = await api.GET("/permissions");
      if (error || !data) throw error ?? new Error("permissions");
      return data;
    },
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (roles.isPending || permissions.isPending) return <p className="px-4 py-10">{t("common.loading")}</p>;
  if (roles.isError || permissions.isError) {
    return (
      <p role="alert" className="px-4 py-10 text-destructive">
        {t("roles.forbidden")}
      </p>
    );
  }

  const selected = roles.data.find((r) => r.id === selectedId) ?? roles.data[0];
  const roleName = (r: Role) => (i18n.language === "ar" ? r.name_ar : r.name_en);

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-8">
      <header className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold">{t("roles.title")}</h1>
        <LanguageSwitch />
      </header>
      <div className="flex flex-col gap-6 md:flex-row">
        <nav aria-label={t("roles.title")} className="flex shrink-0 flex-col gap-1 md:w-56">
          {roles.data.map((r) => (
            <Button
              key={r.id}
              variant={r.id === selected?.id ? "secondary" : "ghost"}
              className={cn("justify-start", r.id === selected?.id && "font-semibold")}
              aria-current={r.id === selected?.id}
              onClick={() => setSelectedId(r.id)}
            >
              {roleName(r)}
            </Button>
          ))}
        </nav>
        {selected && (
          <RoleMatrix
            key={selected.id}
            role={selected}
            name={roleName(selected)}
            registry={permissions.data}
          />
        )}
      </div>
    </main>
  );
}

function RoleMatrix({ role, name, registry }: { role: Role; name: string; registry: Permission[] }) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const [granted, setGranted] = useState(() => new Set(role.permissions));
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const { resources, specials, known } = useMemo(() => {
    const known = new Set(registry.map((p) => p.code));
    const resources = [
      ...new Set(registry.filter((p) => !SPECIAL_RESOURCES.has(p.resource)).map((p) => p.resource)),
    ];
    const specials = registry.filter((p) => SPECIAL_RESOURCES.has(p.resource)).map((p) => p.code);
    return { resources, specials, known };
  }, [registry]);

  const save = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.PATCH("/roles/{role_id}", {
        params: { path: { role_id: role.id } },
        body: { permissions: [...granted].sort() },
      });
      if (error || !data) throw error ?? new Error("save");
      return data;
    },
    onSuccess: async () => {
      setMessage({ kind: "ok", text: t("roles.saved") });
      await queryClient.invalidateQueries({ queryKey: ["roles"] });
    },
    onError: () => setMessage({ kind: "error", text: t("roles.saveFailed") }),
  });

  function toggle(code: string) {
    setMessage(null);
    setGranted((current) => {
      const next = new Set(current);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  const checkbox = (code: string, label: string) => (
    <input
      type="checkbox"
      aria-label={label}
      className="size-4"
      checked={granted.has(code)}
      disabled={!role.editable}
      onChange={() => toggle(code)}
    />
  );

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-4" aria-label={name}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-xl font-semibold">{name}</h2>
          {!role.editable && <p className="text-sm text-muted-foreground">{t("roles.ownerLocked")}</p>}
        </div>
        {role.editable && (
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            {t("roles.save")}
          </Button>
        )}
      </div>
      {message && (
        <p role="status" className={cn("text-sm", message.kind === "error" && "text-destructive")}>
          {message.text}
        </p>
      )}
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-muted">
            <tr>
              <th className="px-3 py-2 text-start font-medium">{t("roles.resource")}</th>
              {ACTIONS.map((a) => (
                <th key={a} className="px-2 py-2 text-center font-medium">
                  {t(`actions.${a}`)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {resources.map((resource) => (
              <tr key={resource} className="border-t">
                <th scope="row" className="px-3 py-1.5 text-start font-normal">
                  {t(`resources.${resource}`)}
                </th>
                {ACTIONS.map((action) => {
                  const code = `${resource}.${action}`;
                  return (
                    <td key={action} className="px-2 py-1.5 text-center">
                      {known.has(code) &&
                        checkbox(code, `${t(`resources.${resource}`)} — ${t(`actions.${action}`)}`)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 font-medium">{t("roles.special")}</legend>
        {specials.map((code) => (
          <label key={code} className="flex items-center gap-2 text-sm">
            {checkbox(code, t(`special.${code}`))}
            {t(`special.${code}`)}
          </label>
        ))}
      </fieldset>
    </section>
  );
}
