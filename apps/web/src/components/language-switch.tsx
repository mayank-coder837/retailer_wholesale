import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { setLanguage } from "@/i18n";

export function LanguageSwitch() {
  const { t, i18n } = useTranslation();
  const next = i18n.language === "ar" ? "en" : "ar";

  return (
    <Button
      variant="outline"
      size="sm"
      aria-label={t("language.label")}
      onClick={() => void setLanguage(next)}
    >
      {t("language.switchTo")}
    </Button>
  );
}
