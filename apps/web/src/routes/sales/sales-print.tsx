import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Button } from "@/components/ui/button";
import { errorCode } from "@/routes/catalog/queries";

type Lang = "en" | "ar";

/** The tax invoice or credit note as the customer gets it (ADR 0014): the server's HTML in a frame, in English or
 * Arabic, printed or saved as PDF from the system print dialog. Fetched through the API client, so the sign-in
 * token never goes into a URL; the frame runs no scripts. */
export function SalesPrintPage() {
  const { t, i18n } = useTranslation();
  const { docId } = useParams({ strict: false }) as { docId: string };
  const api = useApi();
  const frame = useRef<HTMLIFrameElement>(null);
  const [lang, setLang] = useState<Lang>(i18n.language === "ar" ? "ar" : "en");
  const [height, setHeight] = useState(1100);
  const html = useQuery({
    queryKey: ["print", docId, lang],
    // The other language replaces the page in place, without a blank moment.
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const result = await api.GET("/documents/{doc_id}/print", {
        params: { path: { doc_id: docId }, query: { lang } },
        parseAs: "text",
      });
      if (result.error !== undefined) throw result.error;
      return result.data as string;
    },
  });

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <Link
        to="/sales/$docId"
        params={{ docId }}
        className="self-start text-sm text-muted-foreground underline-offset-2 hover:underline"
      >
        {t("print.back")}
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold leading-9">{t("print.title")}</h1>
        <div className="flex flex-wrap gap-2">
          <div role="group" aria-label={t("print.language")} className="flex gap-1">
            {(["en", "ar"] as const).map((l) => (
              <Button
                key={l}
                variant={lang === l ? "default" : "outline"}
                aria-pressed={lang === l}
                onClick={() => setLang(l)}
              >
                {t(`print.languages.${l}`)}
              </Button>
            ))}
          </div>
          {/* Not while the page on screen is still the other language (or document) waiting to be replaced. */}
          <Button
            disabled={!html.data || html.isPlaceholderData}
            onClick={() => frame.current?.contentWindow?.print()}
          >
            {t("print.print")}
          </Button>
        </div>
      </div>
      <p className="text-sm text-muted-foreground">{t("print.hint")}</p>
      {html.isPending && <p>{t("common.loading")}</p>}
      {html.isError && (
        <p role="alert" className="text-destructive">
          {t(`print.errors.${errorCode(html.error)}`, { defaultValue: t("print.errors.generic") })}
        </p>
      )}
      {html.data && (
        <iframe
          ref={frame}
          title={t("print.title")}
          srcDoc={html.data}
          sandbox="allow-same-origin allow-modals"
          className="w-full max-w-[210mm] self-center rounded-lg border bg-white shadow-sm"
          style={{ height }}
          onLoad={(e) => {
            // The content's own height (the root element is never shorter than the frame, so it would only grow).
            const body = e.currentTarget.contentDocument?.body;
            if (body) setHeight(body.scrollHeight + 2);
          }}
        />
      )}
    </div>
  );
}
