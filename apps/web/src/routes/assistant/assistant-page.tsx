import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { money } from "@/routes/accounts/queries";
import {
  codeText,
  errorText,
  EXAMPLES,
  useAiActions,
  type AiAction,
  type AiChat,
} from "@/routes/assistant/queries";
import { unwrap } from "@/routes/catalog/queries";
import { useBusiness } from "@/shell/use-business";

type Facts = Record<string, unknown>;
const text = (value: unknown) => (typeof value === "string" ? value : "");

/** The Assistant (M1.13): type what happened; it prepares the books and waits for a yes before anything is final. */
export function AssistantPage() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const [sentence, setSentence] = useState("");
  const [answer, setAnswer] = useState<AiChat | null>(null);
  const history = useAiActions(current?.id);

  const ask = useMutation({
    mutationFn: async (body: { business_id: string; text: string; lang: "en" | "ar" }) =>
      unwrap(await api.POST("/ai/chat", { body })),
    onSuccess: async (chat) => {
      setAnswer(chat);
      setSentence("");
      await queryClient.invalidateQueries({ queryKey: ["ai-actions"] });
    },
  });

  if (!current) return <p>{t("common.loading")}</p>;
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!sentence.trim()) return;
    ask.mutate({
      business_id: current.id,
      text: sentence.trim(),
      lang: i18n.language.startsWith("ar") ? "ar" : "en",
    });
  };
  const waiting = (history.data ?? []).filter((a) => a.status === "pending");
  const recent = (history.data ?? []).filter((a) => a.status !== "pending").slice(0, 10);

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold leading-9">{t("assistant.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("assistant.hint")}</p>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor="assistant-text" className="text-sm font-medium">
          {t("assistant.ask")}
        </label>
        <div className="flex gap-2">
          <input
            id="assistant-text"
            className={inputClass}
            value={sentence}
            maxLength={1000}
            onChange={(e) => setSentence(e.target.value)}
            placeholder={t("assistant.placeholder")}
          />
          <Button type="submit" disabled={ask.isPending || !sentence.trim()}>
            {ask.isPending ? t("assistant.working") : t("assistant.send")}
          </Button>
        </div>
        <ul className="flex flex-wrap gap-x-4 text-xs text-muted-foreground">
          {EXAMPLES.map((key) => (
            <li key={key}>
              <button
                type="button"
                className="underline underline-offset-2"
                onClick={() => setSentence(t(`assistant.examples.${key}`))}
              >
                {t(`assistant.examples.${key}`)}
              </button>
            </li>
          ))}
        </ul>
      </form>

      {ask.isError && (
        <p role="alert" className="text-destructive">
          {errorText(t, ask.error)}
        </p>
      )}

      {answer && (
        <section aria-label={t("assistant.answer")} className="flex flex-col gap-3">
          {answer.message && (
            <p role="status" className="rounded-md border px-3 py-2 text-sm">
              {t(`assistant.messages.${answer.message}`, {
                defaultValue: answer.message,
                name: text((answer.message_facts as Facts).name),
              })}
            </p>
          )}
          {answer.message === "party_ambiguous" && (
            <ul className="list-disc ps-6 text-sm">
              {(((answer.message_facts as Facts).matches as Facts[] | undefined) ?? []).map((p) => (
                <li key={text(p.id)}>{i18n.language.startsWith("ar") ? text(p.name_ar) : text(p.name_en)}</li>
              ))}
            </ul>
          )}
          {answer.actions
            .filter((a) => a.tool !== "find_party" || a.status !== "done")
            .map((a) => (
              <ActionCard key={a.id} action={a} businessId={current.id} />
            ))}
        </section>
      )}

      <section aria-label={t("assistant.waiting")} className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">{t("assistant.waiting")}</h2>
        {waiting.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("assistant.nothingWaiting")}</p>
        )}
        {waiting.map((a) => (
          <ActionCard key={a.id} action={a} businessId={current.id} />
        ))}
      </section>

      <section aria-label={t("assistant.recent")} className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">{t("assistant.recent")}</h2>
        {history.isSuccess && recent.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("assistant.noHistory")}</p>
        )}
        <ul className="flex flex-col gap-1 text-sm">
          {recent.map((a) => (
            <li key={a.id} className="flex flex-wrap gap-x-3">
              <span className="text-muted-foreground" dir="ltr">
                {new Date(a.created_at).toLocaleString(i18n.language, { timeZone: current.time_zone })}
              </span>
              <span>{t(`assistant.tools.${a.tool}`, { defaultValue: a.tool })}</span>
              <span className="text-muted-foreground">{codeText(t, "status", a.status)}</span>
              {a.prompt && <span className="text-muted-foreground">“{a.prompt}”</span>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function ActionCard({ action, businessId }: { action: AiAction; businessId: string }) {
  const { t } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const decide = useMutation({
    mutationFn: async (verb: "confirm" | "reject") =>
      unwrap(
        await api.POST(`/ai/actions/{action_id}/${verb}` as "/ai/actions/{action_id}/confirm", {
          params: { path: { action_id: action.id } },
          body: { business_id: businessId },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries(); // a post changes the books: every figure may have moved
    },
  });
  const pending = action.status === "pending";
  const refused = action.status === "failed" || action.status === "denied";
  const facts = (action.result ?? {}) as Facts;

  return (
    <article
      className="flex flex-col gap-2 rounded-md border p-3"
      aria-label={t(`assistant.tools.${action.tool}`, { defaultValue: action.tool })}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">
          {t(`assistant.tools.${action.tool}`, { defaultValue: action.tool })}
        </h3>
        <span className="rounded border px-1.5 text-xs">{codeText(t, "status", action.status)}</span>
        <span className="text-xs text-muted-foreground">{codeText(t, "risk", action.risk)}</span>
      </div>
      {refused && (
        <p role="alert" className="text-sm text-destructive">
          {codeText(t, "codes", action.error_code ?? "generic")}
          {action.status === "denied" && facts.permission ? ` (${text(facts.permission)})` : ""}
        </p>
      )}
      {!refused && <Summary action={action} />}
      {pending && (
        <div className="flex gap-2">
          <Button size="sm" onClick={() => decide.mutate("confirm")} disabled={decide.isPending}>
            {t("assistant.confirm")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => decide.mutate("reject")}
            disabled={decide.isPending}
          >
            {t("assistant.reject")}
          </Button>
        </div>
      )}
      {decide.isError && (
        <p role="alert" className="text-sm text-destructive">
          {errorText(t, decide.error)}
        </p>
      )}
    </article>
  );
}

function VoucherLink({ id }: { id: string }) {
  const { t } = useTranslation();
  return (
    <Link
      to="/accounts/vouchers/$voucherId"
      params={{ voucherId: id }}
      className="underline underline-offset-2"
    >
      {t("assistant.openDraft")}
    </Link>
  );
}

function Summary({ action }: { action: AiAction }) {
  const { t, i18n } = useTranslation();
  const facts = (action.result ?? {}) as Facts;
  const args = action.args as Facts;
  const ar = i18n.language.startsWith("ar");

  switch (action.tool) {
    case "create_draft_receipt":
    case "create_draft_payment":
      return (
        <div className="flex flex-col gap-1 text-sm">
          <p>
            {t("assistant.summary.draft", { party: text(facts.party), amount: money(text(facts.amount)) })}
          </p>
          {facts.bill ? (
            <p>
              {t("assistant.summary.settles", { bill: text(facts.bill), amount: money(text(facts.settled)) })}
              {Number(facts.on_account) > 0 &&
                ` ${t("assistant.summary.onAccount", { amount: money(text(facts.on_account)) })}`}
            </p>
          ) : (
            <p>{t("assistant.summary.allOnAccount")}</p>
          )}
          {facts.document_id ? <VoucherLink id={text(facts.document_id)} /> : null}
          <p className="text-muted-foreground">{t("assistant.summary.stillDraft")}</p>
        </div>
      );
    case "post_voucher":
      return (
        <p className="text-sm">
          {action.status === "confirmed"
            ? t("assistant.summary.posted", { number: text(facts.number) })
            : t("assistant.summary.willPost")}{" "}
          <VoucherLink id={text(args.document_id)} />
        </p>
      );
    case "cancel_document":
      return (
        <p className="text-sm">
          {action.status === "confirmed"
            ? t("assistant.summary.cancelled")
            : t("assistant.summary.willCancel")}{" "}
          {text(args.reason) && `“${text(args.reason)}”`}
        </p>
      );
    case "apply_industry_pack":
      return <p className="text-sm">{t("assistant.summary.pack", { code: text(args.code) })}</p>;
    case "find_party": {
      const parties = (facts.parties as Facts[] | undefined) ?? [];
      return parties.length === 0 ? (
        <p className="text-sm">{t("assistant.summary.noParties")}</p>
      ) : (
        <ul className="list-disc ps-6 text-sm">
          {parties.map((p) => (
            <li key={text(p.id)}>{ar ? text(p.name_ar) : text(p.name_en)}</li>
          ))}
        </ul>
      );
    }
    case "get_open_bills": {
      const bills = (facts.bills as Facts[] | undefined) ?? [];
      return bills.length === 0 ? (
        <p className="text-sm">{t("assistant.summary.noBills")}</p>
      ) : (
        <table className="w-full text-sm">
          <tbody>
            {bills.map((b) => (
              <tr key={text(b.id)}>
                <td className="py-1">{text(b.reference)}</td>
                <td className="py-1 text-end tabular-nums" dir="ltr">
                  {money(text(b.amount_open))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      );
    }
    case "get_receivables_ageing":
    case "get_payables_ageing":
      return <Ageing facts={facts} />;
    case "list_industry_packs": {
      const packs = (facts.packs as Facts[] | undefined) ?? [];
      return (
        <ul className="list-disc ps-6 text-sm">
          {packs.map((p) => (
            <li key={text(p.code)}>{ar ? text(p.name_ar) : text(p.name_en)}</li>
          ))}
        </ul>
      );
    }
    default:
      return null;
  }
}

function Ageing({ facts }: { facts: Facts }) {
  const { t, i18n } = useTranslation();
  const rows = (facts.rows as Facts[] | undefined) ?? [];
  const totals = (facts.totals ?? {}) as Facts;
  if (rows.length === 0) return <p className="text-sm">{t("assistant.summary.nobodyOwes")}</p>;
  return (
    <table className="w-full text-sm">
      <tbody>
        {rows.slice(0, 8).map((r) => (
          <tr key={text(r.party_id)}>
            <td className="py-1">{i18n.language.startsWith("ar") ? text(r.name_ar) : text(r.name_en)}</td>
            <td className="py-1 text-end tabular-nums" dir="ltr">
              {money(text(r.total))}
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="border-t font-semibold">
          <td className="py-1">{t("assistant.summary.total")}</td>
          <td className="py-1 text-end tabular-nums" dir="ltr">
            {money(text(totals.total))}
          </td>
        </tr>
      </tfoot>
    </table>
  );
}
