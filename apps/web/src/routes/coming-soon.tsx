import { useTranslation } from "react-i18next";

/** Placeholder for the Phase 1 areas, so navigation and shortcuts work end to end today. */
export function ComingSoon({ titleKey }: { titleKey: string }) {
  const { t } = useTranslation();
  const title = t(titleKey);
  return (
    <section className="flex max-w-xl flex-col gap-2">
      <h1 className="text-2xl font-bold leading-9">{title}</h1>
      <p>{t("shell.comingSoon", { title })}</p>
      <p className="text-sm text-muted-foreground">{t("shell.comingSoonHint")}</p>
    </section>
  );
}
