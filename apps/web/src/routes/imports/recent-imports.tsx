import { Link } from "@tanstack/react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Button } from "@/components/ui/button";
import { unwrap } from "@/routes/catalog/queries";
import { errorText, label, OPENING_KINDS, useImports, type ImportKind } from "@/routes/imports/queries";

/** The imports done lately, with Undo for an opening import that can still be taken back. */
export function RecentImports({ businessId, kinds }: { businessId: string; kinds: ImportKind[] }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const imports = useImports(businessId);
  const [asking, setAsking] = useState<string | null>(null);
  const undo = useMutation({
    mutationFn: async (id: string) =>
      unwrap(await api.POST("/imports/{batch_id}/undo", { params: { path: { batch_id: id } } })),
    onSuccess: async () => {
      setAsking(null);
      await queryClient.invalidateQueries();
    },
  });
  const byKey = new Map(kinds.map((k) => [k.key, k]));
  const rows = imports.data ?? [];

  return (
    <section aria-label={t("import.recent.title")} className="flex flex-col gap-2">
      <h2 className="text-lg font-semibold">{t("import.recent.title")}</h2>
      {imports.isSuccess && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("import.recent.empty")}</p>
      )}
      {undo.isError && (
        <p role="alert" className="text-destructive">
          {errorText(t, undo.error)}
        </p>
      )}
      {rows.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-2 text-start font-medium">{t("import.recent.date")}</th>
              <th className="py-2 text-start font-medium">{t("import.recent.kind")}</th>
              <th className="py-2 text-start font-medium">{t("import.recent.file")}</th>
              <th className="py-2 text-start font-medium">{t("import.recent.state")}</th>
              <th className="py-2" />
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((b) => {
              const kind = byKey.get(b.kind);
              return (
                <tr key={b.id}>
                  <td className="py-1.5 tabular-nums" dir="ltr">
                    {b.created_at.slice(0, 10)}
                  </td>
                  <td className="py-1.5">{kind ? label(kind, i18n.language) : b.kind}</td>
                  <td className="py-1.5" dir="ltr">
                    {b.filename}
                  </td>
                  <td className="py-1.5">{t(`import.status.${b.status}`)}</td>
                  <td className="py-1.5 text-end">
                    {b.status === "committed" && OPENING_KINDS.includes(b.kind) && (
                      <Link
                        to="/settings/import/reconciliation"
                        className="me-3 underline-offset-2 hover:underline"
                      >
                        {t("import.recent.check")}
                      </Link>
                    )}
                    {b.status === "committed" && kind?.undoable && asking !== b.id && (
                      <Button variant="outline" size="sm" onClick={() => setAsking(b.id)}>
                        {t("import.recent.undo")}
                      </Button>
                    )}
                    {asking === b.id && (
                      <span className="inline-flex items-center gap-2">
                        <span>{t("import.recent.undoSure")}</span>
                        <Button size="sm" onClick={() => undo.mutate(b.id)} disabled={undo.isPending}>
                          {t("import.recent.undoYes")}
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => setAsking(null)}>
                          {t("import.recent.undoNo")}
                        </Button>
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
