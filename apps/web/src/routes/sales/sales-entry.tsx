import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { CodeTag } from "@/components/code-tag";
import { besideField, Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  accountLabel,
  fromFils,
  money,
  todayIso,
  useAccounts,
  useBranches,
  vatFils,
} from "@/routes/accounts/queries";
import { localName } from "@/routes/catalog/product-form";
import { errorCode, unwrap, useProducts } from "@/routes/catalog/queries";
import { ComingSoon } from "@/routes/coming-soon";
import { qty as shortQty, useStockBatches, useStockProduct, useWarehouses } from "@/routes/inventory/queries";
import { useParties } from "@/routes/parties/queries";
import { DECIMAL6, netFils, useReleasedNumbers } from "@/routes/purchasing/queries";
import {
  isSalesDocType,
  SALES_KEYS,
  salesWarningsKey,
  useSalesDoc,
  useSalesHistory,
  useSalesOpenLines,
  type HistoryLine,
  type OpenSalesLine,
  type Quote,
  type SalesDoc,
  type SalesDocIn,
  type SalesDocType,
  type SalesLineIn,
} from "@/routes/sales/queries";
import { BackToSales } from "@/routes/sales/sales";
import { useBusiness } from "@/shell/use-business";

interface Fixed {
  sku: string | null;
  name_en: string | null;
  name_ar: string | null;
}

interface LineState {
  key: number;
  productId: string;
  variantId: string;
  unit: string;
  qty: string;
  /** What the user typed; until then the price shown is the pricing engine's suggestion. */
  price: string;
  priceTouched: boolean;
  priceSource: string | null;
  vat: "5" | "0";
  batchId: string;
  trackBatch: boolean;
  /** Following a line of an earlier document: fixed item, and what is still open there. */
  source: OpenSalesLine | null;
  /** Added from the customer's history: fixed item and unit. */
  fixed: Fixed | null;
}

let nextKey = 1;
const emptyLine = (): LineState => ({
  key: nextKey++,
  productId: "",
  variantId: "",
  unit: "",
  qty: "",
  price: "",
  priceTouched: false,
  priceSource: null,
  vat: "5",
  batchId: "",
  trackBatch: false,
  source: null,
  fixed: null,
});

function fromOpen(o: OpenSalesLine): LineState {
  const price = o.unit_price === null || o.unit_price === undefined ? "" : shortQty(o.unit_price);
  return {
    ...emptyLine(),
    variantId: o.variant_id ?? "",
    unit: o.unit ?? "",
    qty: shortQty(o.qty_open),
    price,
    priceTouched: price !== "",
    vat: Number(o.vat_rate) > 0 ? "5" : "0",
    source: o,
    fixed: { sku: o.sku, name_en: o.name_en, name_ar: o.name_ar },
  };
}

function fromHistory(h: HistoryLine): LineState {
  return {
    ...emptyLine(),
    variantId: h.variant_id,
    unit: h.unit,
    qty: "1",
    price: h.unit_price ? shortQty(h.unit_price) : "",
    priceTouched: !!h.unit_price,
    priceSource: h.unit_price ? `last_price:${h.number}` : null,
    fixed: { sku: h.sku, name_en: h.name_en, name_ar: h.name_ar },
  };
}

/** The entry route: the six sales documents; ``from`` starts it from an earlier one. */
export function SalesEntryRoute() {
  const { kind } = useParams({ strict: false }) as { kind: string };
  const search = useSearch({ strict: false }) as { from?: string };
  if (!isSalesDocType(kind)) return <ComingSoon titleKey={`sales.types.${kind}`} />;
  if (search.from) return <StartFrom key={`${kind}-${search.from}`} kind={kind} from={search.from} />;
  return <SalesEntryPage key={kind} kind={kind} start={null} />;
}

/** Loads the earlier document and what is still open on it, then opens the form already filled in. */
function StartFrom({ kind, from }: { kind: SalesDocType; from: string }) {
  const { t } = useTranslation();
  const source = useSalesDoc(from);
  const open = useSalesOpenLines(from);
  if (source.isError || open.isError)
    return (
      <p role="alert" className="text-destructive">
        {t("sales.errors.generic")}
      </p>
    );
  // Fresh figures only: what is open changes with every delivery, invoice, cancel and reversal.
  if (!source.data || !open.data || source.isFetching || open.isFetching) return <p>{t("common.loading")}</p>;
  return <SalesEntryPage kind={kind} start={{ doc: source.data, open: open.data }} />;
}

interface CreditBlock {
  reasons: string[];
  exposure?: string;
  credit_limit?: string | null;
  available?: string | null;
  may_override: boolean;
}

/** Sales → new quotation, proforma, order, delivery note, invoice or return (M1.8c). */
function SalesEntryPage({
  kind,
  start,
}: {
  kind: SalesDocType;
  start: { doc: SalesDoc; open: OpenSalesLine[] } | null;
}) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);
  const accounts = useAccounts(current?.id);
  const [customerQuery, setCustomerQuery] = useState("");
  const customers = useParties(customerQuery.trim(), "customer");
  const from = start?.doc.id ?? "";

  const [date, setDate] = useState(todayIso());
  const [branchId, setBranchId] = useState(start?.doc.branch_id ?? "");
  const [customerId, setCustomerId] = useState(start?.doc.party_id ?? "");
  const [warehouseId, setWarehouseId] = useState(start?.doc.warehouse_id ?? "");
  const [cashSale, setCashSale] = useState(false);
  const [cashAccountId, setCashAccountId] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [reference, setReference] = useState(start?.doc.reference ?? "");
  const [narration, setNarration] = useState("");
  const [lines, setLines] = useState<LineState[]>(() => (start ? start.open.map(fromOpen) : [emptyLine()]));
  const [reuse, setReuse] = useState("");
  const [tried, setTried] = useState(false);
  const [lockReason, setLockReason] = useState<string | null>(null);
  const [creditBlock, setCreditBlock] = useState<CreditBlock | null>(null);
  const [creditReason, setCreditReason] = useState("");

  const branch = branchId || branches.data?.[0]?.id || "";
  const ownWarehouses = (warehouses.data ?? []).filter((w) => w.branch_id === branch);
  const warehouse = warehouseId || ownWarehouses.find((w) => w.is_default)?.id || ownWarehouses[0]?.id || "";
  const released = useReleasedNumbers(branch, kind);
  const moneyAccounts = (accounts.data ?? []).filter(
    (a) => !a.is_group && a.archived_at === null && (a.account_type === "cash" || a.account_type === "bank"),
  );
  const cashAccount = cashAccountId || moneyAccounts[0]?.id || "";
  const update = (key: number, patch: Partial<LineState>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const isReturn = kind === "sales_return";
  const isInvoice = kind === "sales_invoice";
  const isDelivery = kind === "delivery_note";
  const priced = !isDelivery;
  const followsDelivery = start?.doc.doc_type === "delivery_note";
  const movesStock = isDelivery || isReturn || (isInvoice && !followsDelivery);

  // The pricing engine's suggestion for each line the user has not priced (last price first, A9).
  const quotes = useQueries({
    queries: lines.map((l) => ({
      queryKey: ["quote", current?.id, customerId, l.variantId, l.unit, l.qty, date],
      enabled: priced && !isReturn && !!current && !!l.variantId && !!l.unit && DECIMAL6.test(l.qty.trim()),
      retry: false,
      queryFn: async (): Promise<Quote> =>
        unwrap(
          await api.POST("/pricing/quote", {
            body: {
              business_id: current!.id,
              variant_id: l.variantId,
              unit: l.unit,
              qty: l.qty.trim(),
              date,
              party_id: customerId || null,
            },
          }),
        ),
    })),
  });
  const quoteOf = (i: number): Quote | undefined => quotes[i]?.data;
  const priceOf = (l: LineState, i: number): string => {
    if (isReturn) return l.source?.unit_price ? shortQty(l.source.unit_price) : "";
    if (l.priceTouched) return l.price.trim();
    const q = quoteOf(i);
    return q?.price ? shortQty(q.price) : "";
  };

  function build(): SalesDocIn | string {
    if (!current || !branch) return "sales.entry.noBranch";
    if (!customerId) return "sales.entry.needCustomer";
    if (movesStock && !warehouse) return "sales.entry.noWarehouse";
    if (isInvoice && cashSale && !cashAccount) return "sales.entry.needCashAccount";
    const out: SalesLineIn[] = [];
    for (const [i, l] of lines.entries()) {
      if (!l.variantId && !l.qty && !l.source) continue;
      const q = l.qty.trim();
      if (!l.variantId || !l.unit) return "sales.entry.needItem";
      if (!DECIMAL6.test(q) || /^0*(\.0*)?$/.test(q)) return "sales.entry.badQty";
      const line: SalesLineIn = { variant_id: l.variantId, unit: l.unit, qty: q };
      if (l.source) line.source_line_id = l.source.line_id;
      if (priced && !isReturn) {
        const price = priceOf(l, i);
        if (!price) return "sales.entry.needPrice";
        if (!DECIMAL6.test(price)) return "sales.entry.badPrice";
        line.unit_price = price;
        line.vat_rate = l.vat;
        line.price_source = l.priceTouched ? (l.priceSource ?? "manual") : (quoteOf(i)?.price_source ?? null);
      }
      if (l.trackBatch && movesStock && !isReturn) {
        if (!l.batchId) return "sales.entry.needBatch";
        line.batch_id = l.batchId;
      }
      out.push(line);
    }
    if (isReturn && !from) return "sales.entry.returnNeedsSource";
    if (out.length === 0) return "sales.entry.noLines";
    return {
      doc_type: kind,
      business_id: current.id,
      branch_id: branch,
      doc_date: date,
      party_id: customerId,
      warehouse_id: movesStock ? warehouse : null,
      cash_account_id: isInvoice && cashSale ? cashAccount : null,
      valid_until: (kind === "quotation" || kind === "proforma_invoice") && validUntil ? validUntil : null,
      due_date: isInvoice && !cashSale && dueDate ? dueDate : null,
      reference: reference.trim() || null,
      narration: narration.trim() || null,
      lines: out,
    };
  }
  const built = build();

  const save = useMutation({
    mutationFn: async ({ post, override }: { post: boolean; override: boolean }) => {
      if (typeof built === "string") throw { code: "client" };
      // A quotation is numbered when saved; other documents post through the draft when options are needed.
      if (kind !== "quotation" && post && (lockReason !== null || reuse || override)) {
        const draft = unwrap(await api.POST("/sales-documents", { body: built }));
        return unwrap(
          await api.POST("/sales-documents/{doc_id}/post", {
            params: { path: { doc_id: draft.id } },
            body: {
              override_lock_reason: lockReason?.trim() || null,
              credit_override_reason: override ? creditReason.trim() : null,
              reuse_number: reuse ? Number(reuse) : null,
            },
          }),
        );
      }
      return unwrap(await api.POST("/sales-documents", { body: built, params: { query: { post } } }));
    },
    onSuccess: async (doc) => {
      queryClient.setQueryData(salesWarningsKey(doc.id), doc.warnings ?? []);
      for (const key of SALES_KEYS) await queryClient.invalidateQueries({ queryKey: [key] });
      await queryClient.invalidateQueries({ queryKey: ["released-numbers"] });
      await navigate({ to: "/sales/$docId", params: { docId: doc.id } });
    },
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean } & Partial<CreditBlock>;
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
      if (body.code === "credit_blocked")
        setCreditBlock({
          reasons: body.reasons ?? [],
          exposure: body.exposure,
          credit_limit: body.credit_limit,
          available: body.available,
          may_override: !!body.may_override,
        });
    },
  });

  function submit(post: boolean, override = false) {
    setTried(true);
    if (typeof built === "string") return;
    if (post && lockReason !== null && !lockReason.trim()) return;
    if (override && !creditReason.trim()) return;
    save.mutate({ post, override });
  }

  function onKeyDown(e: KeyboardEvent<HTMLFormElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submit(true);
    }
  }

  // Totals as the API will compute them: per line, VAT half-up to the fils (decision A3).
  let subtotal = 0;
  let vat = 0;
  for (const [i, l] of lines.entries()) {
    if (!priced) break;
    const net = netFils(l.qty.trim(), priceOf(l, i));
    subtotal += net;
    vat += vatFils(net, isReturn ? Number(l.source?.vat_rate ?? 0) : Number(l.vat));
  }

  const problem = typeof built === "string" ? built : null;
  const failure = save.isError ? errorCode(save.error) : null;
  const name = (x: { name_en: string; name_ar: string }) => (i18n.language === "ar" ? x.name_ar : x.name_en);
  const customerOptions = customers.data ?? [];
  const chosenMissing = customerId && !customerOptions.some((p) => p.id === customerId);
  const chosenReuse = released.data?.find((r) => String(r.number) === reuse);

  return (
    <div className="flex max-w-5xl flex-col gap-5">
      <form
        className="flex flex-col gap-5"
        noValidate
        onKeyDown={onKeyDown}
        onSubmit={(e) => {
          e.preventDefault();
          submit(true);
        }}
      >
        <BackToSales />
        <h1 className="text-2xl font-bold leading-9">{t(`sales.types.${kind}`)}</h1>
        {start && (
          <p className="text-sm text-muted-foreground">
            {t("sales.entry.from")}{" "}
            <Link to="/sales/$docId" params={{ docId: from }} className="underline underline-offset-2">
              <span dir="ltr">{start.doc.number}</span>
            </Link>
          </p>
        )}
        {isReturn && !from && (
          <p role="status" className="rounded-lg border bg-muted/40 p-3 text-sm">
            {t("sales.entry.returnNeedsSource")}
          </p>
        )}
        {isDelivery && <p className="text-sm text-muted-foreground">{t("sales.entry.noPricesHint")}</p>}
        <div className="grid items-start gap-3 sm:grid-cols-4">
          <Field label={t("sales.doc.date")}>
            {(p) => (
              <input
                {...p}
                type="date"
                className={inputClass}
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            )}
          </Field>
          <Field label={t("sales.doc.customer")} className="sm:col-span-2">
            {(p) => (
              <div className="flex gap-1">
                <input
                  className={cn(inputClass, "w-24 shrink-0")}
                  type="search"
                  aria-label={t("sales.entry.findCustomer")}
                  placeholder={t("accounts.voucher.find")}
                  value={customerQuery}
                  disabled={!!from}
                  onChange={(e) => setCustomerQuery(e.target.value)}
                />
                <select
                  {...p}
                  className={cn(inputClass, "min-w-0 flex-1")}
                  value={customerId}
                  disabled={!!from}
                  onChange={(e) => {
                    setCustomerId(e.target.value);
                    setCreditBlock(null);
                  }}
                >
                  <option value="">{t("accounts.voucher.choose")}</option>
                  {chosenMissing && <option value={customerId}>{customerId}</option>}
                  {customerOptions.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.code} {name(c)}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </Field>
          {(branches.data?.length ?? 0) > 1 && (
            <Field label={t("accounts.voucher.branch")}>
              {(p) => (
                <select
                  {...p}
                  className={inputClass}
                  value={branch}
                  disabled={!!from}
                  onChange={(e) => setBranchId(e.target.value)}
                >
                  {branches.data?.map((b) => (
                    <option key={b.id} value={b.id}>
                      {name(b)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
          {movesStock && (
            <Field label={t("inventory.stock.warehouse")}>
              {(p) => (
                <select
                  {...p}
                  className={inputClass}
                  value={warehouse}
                  onChange={(e) => setWarehouseId(e.target.value)}
                >
                  {ownWarehouses.map((w) => (
                    <option key={w.id} value={w.id}>
                      {name(w)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
          {(kind === "quotation" || kind === "proforma_invoice") && (
            <Field label={t("sales.doc.validUntil")} hint={t("common.optional")}>
              {(p) => (
                <input
                  {...p}
                  type="date"
                  className={inputClass}
                  min={date}
                  value={validUntil}
                  onChange={(e) => setValidUntil(e.target.value)}
                />
              )}
            </Field>
          )}
          {isInvoice && !cashSale && (
            <Field label={t("sales.doc.dueDate")} hint={t("sales.entry.dueHint")}>
              {(p) => (
                <input
                  {...p}
                  type="date"
                  className={inputClass}
                  min={date}
                  value={dueDate}
                  onChange={(e) => setDueDate(e.target.value)}
                />
              )}
            </Field>
          )}
          <Field label={t("sales.doc.customerRef")} hint={t("common.optional")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                value={reference}
                onChange={(e) => setReference(e.target.value)}
              />
            )}
          </Field>
        </div>
        {isInvoice && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={cashSale} onChange={(e) => setCashSale(e.target.checked)} />
              {t("sales.entry.cashSale")}
            </label>
            {cashSale && (
              <select
                aria-label={t("sales.entry.cashAccount")}
                className={cn(inputClass, "w-auto")}
                value={cashAccount}
                onChange={(e) => setCashAccountId(e.target.value)}
              >
                {moneyAccounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {accountLabel(a, i18n.language)}
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 font-semibold">{t("sales.doc.lines")}</legend>
          {start && lines.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("sales.entry.nothingOpen")}</p>
          )}
          {lines.map((l, i) => (
            <SalesLine
              key={l.key}
              kind={kind}
              line={l}
              index={i}
              quote={quoteOf(i)}
              price={priceOf(l, i)}
              businessId={current?.id}
              warehouseId={warehouse}
              movesStock={movesStock}
              removable={lines.length > 1 || !!l.source}
              onChange={(patch) => update(l.key, patch)}
              onRemove={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}
            />
          ))}
          {!isReturn && !from && (
            <div>
              <Button type="button" variant="outline" onClick={() => setLines((ls) => [...ls, emptyLine()])}>
                {t("sales.entry.addItem")}
              </Button>
            </div>
          )}
        </fieldset>

        <Field label={t("accounts.voucher.narration")} hint={t("common.optional")}>
          {(p) => (
            <textarea
              {...p}
              className={cn(inputClass, "h-16 py-2")}
              value={narration}
              onChange={(e) => setNarration(e.target.value)}
            />
          )}
        </Field>

        {priced && (
          <dl
            className="ms-auto grid w-full max-w-xs grid-cols-2 gap-1 rounded-lg border p-3 text-sm"
            aria-label={t("sales.doc.totals")}
          >
            <dt>{t("sales.doc.subtotal")}</dt>
            <dd className="text-right tabular-nums">
              <span dir="ltr">{money(fromFils(subtotal))}</span>
            </dd>
            <dt>{t("sales.doc.vat")}</dt>
            <dd className="text-right tabular-nums">
              <span dir="ltr">{money(fromFils(vat))}</span>
            </dd>
            <dt className="font-semibold">{t("sales.doc.total")}</dt>
            <dd className="text-right font-semibold tabular-nums">
              <span dir="ltr">{money(fromFils(subtotal + vat))}</span>
            </dd>
          </dl>
        )}

        {(released.data?.length ?? 0) > 0 && kind !== "quotation" && (
          <Field label={t("sales.entry.number")} hint={t("purchasing.entry.numberHint")} className="max-w-sm">
            {(p) => (
              <select
                {...p}
                className={cn(inputClass, "w-auto")}
                value={reuse}
                onChange={(e) => setReuse(e.target.value)}
              >
                <option value="">{t("purchasing.entry.nextNumber")}</option>
                {released.data?.map((r) => (
                  <option key={`${r.fiscal_year}-${r.number}`} value={String(r.number)}>
                    {t(r.was_sent ? "sales.entry.reuseSent" : "purchasing.entry.reuse", {
                      number: r.formatted,
                    })}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
        {chosenReuse?.was_sent && (
          <p role="status" className="rounded-lg border border-amber-500/60 bg-amber-500/10 p-3 text-sm">
            {t("sales.entry.reuseSentWarning", { number: chosenReuse.formatted })}
          </p>
        )}
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
        {creditBlock && (
          <div
            role="alert"
            className="flex flex-col gap-3 rounded-lg border border-destructive/60 bg-destructive/5 p-4 text-sm"
          >
            <p className="font-semibold">{t("sales.credit.blocked")}</p>
            <p>
              {creditBlock.reasons
                .map((r) => t(`sales.credit.reasons.${r}`, { defaultValue: r }))
                .join(" · ")}
            </p>
            {creditBlock.credit_limit && (
              <p className="tabular-nums">
                {t("sales.credit.figures", {
                  exposure: money(creditBlock.exposure ?? "0"),
                  limit: money(creditBlock.credit_limit),
                })}
              </p>
            )}
            {creditBlock.may_override ? (
              <div className="flex flex-wrap items-end gap-2">
                <Field label={t("sales.credit.reason")} className="min-w-72 flex-1">
                  {(p) => (
                    <input
                      {...p}
                      className={inputClass}
                      value={creditReason}
                      onChange={(e) => setCreditReason(e.target.value)}
                    />
                  )}
                </Field>
                <Button
                  type="button"
                  disabled={!creditReason.trim() || save.isPending}
                  onClick={() => submit(true, true)}
                >
                  {t("sales.credit.postAnyway")}
                </Button>
              </div>
            ) : (
              <p>{t("sales.credit.askSomeone")}</p>
            )}
          </div>
        )}
        {tried && problem && (
          <p role="alert" className="text-destructive">
            {t(problem)}
          </p>
        )}
        {failure &&
          failure !== "client" &&
          failure !== "credit_blocked" &&
          !(failure === "period_locked" && lockReason !== null) && (
            <p role="alert" className="text-destructive">
              {t(`sales.errors.${failure}`, {
                defaultValue: t(`inventory.errors.${failure}`, {
                  defaultValue: t(`accounts.errors.${failure}`, { defaultValue: t("sales.errors.generic") }),
                }),
              })}
            </p>
          )}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={save.isPending}>
            {kind === "quotation"
              ? t("sales.entry.saveQuotation")
              : lockReason !== null
                ? t("accounts.voucher.postLocked")
                : t("accounts.voucher.post")}
          </Button>
          {kind !== "quotation" && (
            <Button type="button" variant="outline" disabled={save.isPending} onClick={() => submit(false)}>
              {t("accounts.voucher.saveDraft")}
            </Button>
          )}
          <span className="text-xs text-muted-foreground">{t("accounts.voucher.shortcut")}</span>
        </div>
      </form>
      {customerId && !isReturn && !from && (
        <HistoryPanel
          businessId={current?.id}
          customerId={customerId}
          onAdd={(h) =>
            setLines((ls) => [...ls.filter((l) => l.variantId || l.qty || l.source), fromHistory(h)])
          }
        />
      )}
    </div>
  );
}

/** The customer history panel (A9): what this customer bought, newest first; one click adds the item. */
function HistoryPanel({
  businessId,
  customerId,
  onAdd,
}: {
  businessId: string | undefined;
  customerId: string;
  onAdd: (h: HistoryLine) => void;
}) {
  const { t, i18n } = useTranslation();
  const [q, setQ] = useState("");
  const history = useSalesHistory(businessId, customerId, q.trim());
  return (
    <section aria-label={t("sales.history.title")} className="flex flex-col gap-2 rounded-lg border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">{t("sales.history.title")}</h2>
        <input
          type="search"
          className={cn(inputClass, "w-56")}
          aria-label={t("sales.history.search")}
          placeholder={t("sales.history.search")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>
      {history.data?.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("sales.history.none")}</p>
      )}
      {history.data && history.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="py-1 text-start font-medium">{t("sales.doc.date")}</th>
              <th className="py-1 text-start font-medium">{t("sales.doc.number")}</th>
              <th className="py-1 text-start font-medium">{t("inventory.doc.item")}</th>
              <th className="py-1 text-right font-medium">{t("inventory.stock.qty")}</th>
              <th className="py-1 text-right font-medium">{t("sales.doc.price")}</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody className="divide-y">
            {history.data.slice(0, 20).map((h, i) => (
              <tr key={`${h.document_id}-${i}`}>
                <td className="py-1">
                  <span dir="ltr">{h.doc_date}</span>
                </td>
                <td className="py-1 font-mono">
                  <span dir="ltr">{h.number}</span>
                </td>
                <td className="py-1">
                  <CodeTag>{h.sku}</CodeTag>
                  {i18n.language === "ar" ? h.name_ar : h.name_en}
                </td>
                <td className="py-1 text-right tabular-nums">
                  <span dir="ltr">
                    {shortQty(h.qty)} {h.unit}
                  </span>
                </td>
                <td className="py-1 text-right tabular-nums">
                  <span dir="ltr">{h.unit_price ? shortQty(h.unit_price) : ""}</span>
                </td>
                <td className="py-1 text-end">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    aria-label={t("sales.history.addItem", { sku: h.sku })}
                    onClick={() => onAdd(h)}
                  >
                    {t("sales.history.add")}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function SalesLine({
  kind,
  line,
  index,
  quote,
  price,
  businessId,
  warehouseId,
  movesStock,
  removable,
  onChange,
  onRemove,
}: {
  kind: SalesDocType;
  line: LineState;
  index: number;
  quote: Quote | undefined;
  price: string;
  businessId: string | undefined;
  warehouseId: string;
  movesStock: boolean;
  removable: boolean;
  onChange: (patch: Partial<LineState>) => void;
  onRemove: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const products = useProducts(query.trim());
  const product = useStockProduct(line.productId);
  const chosen = line.productId ? product.data : undefined;
  const batches = useStockBatches(
    businessId,
    line.trackBatch && movesStock ? line.variantId : "",
    warehouseId,
  );
  const isReturn = kind === "sales_return";
  const priced = kind !== "delivery_note";
  const stockOnly = kind === "delivery_note" || isReturn;

  // Once the product's details arrive, default its variant and unit and remember batch tracking.
  const needsDefaults = !!chosen && chosen.id === line.productId && !line.variantId && !!chosen.variants[0];
  useEffect(() => {
    if (needsDefaults && chosen) {
      onChange({ variantId: chosen.variants[0]!.id, unit: chosen.base_unit, trackBatch: chosen.track_batch });
    }
  }, [needsDefaults, chosen, onChange]);

  const minimum = quote?.min_price ? Number(quote.min_price) : null;
  const belowMin = priced && !isReturn && minimum !== null && price !== "" && Number(price) < minimum;
  const fixed = line.fixed;
  return (
    <div className="flex flex-wrap items-start gap-2 rounded-lg border p-3">
      {fixed ? (
        <div className="flex min-w-72 flex-1 flex-col gap-1 text-sm">
          <span className="font-medium">{t("inventory.doc.item")}</span>
          <span className="flex h-9 items-center">
            <CodeTag>{fixed.sku}</CodeTag>
            {i18n.language === "ar" ? fixed.name_ar : fixed.name_en}
          </span>
          {line.source && (
            <span className="text-muted-foreground">
              {t("sales.entry.stillOpen", {
                qty: shortQty(line.source.qty_open),
                unit: line.source.unit ?? "",
              })}
            </span>
          )}
        </div>
      ) : (
        <Field label={t("inventory.doc.item")} className="min-w-72 flex-1">
          {(p) => (
            <div className="flex gap-1">
              <input
                className={cn(inputClass, "w-24 shrink-0")}
                type="search"
                aria-label={t("inventory.doc.findItem")}
                placeholder={t("accounts.voucher.find")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <select
                {...p}
                className={cn(inputClass, "min-w-0 flex-1")}
                value={line.productId}
                onChange={(e) =>
                  onChange({
                    productId: e.target.value,
                    variantId: "",
                    unit: "",
                    batchId: "",
                    priceTouched: false,
                  })
                }
              >
                <option value="">{t("accounts.voucher.choose")}</option>
                {products.data
                  ?.filter((x) => x.archived_at === null && (!stockOnly || x.product_type === "stock"))
                  .map((x) => (
                    <option key={x.id} value={x.id}>
                      {x.sku} {localName(x, i18n.language)}
                    </option>
                  ))}
              </select>
            </div>
          )}
        </Field>
      )}
      {chosen && chosen.variants.length > 1 && (
        <Field label={t("inventory.doc.variant")} className="w-40">
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={line.variantId}
              onChange={(e) => onChange({ variantId: e.target.value })}
            >
              {chosen.variants.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.sku}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
      <Field label={t("inventory.stock.qty")} className="w-28">
        {(p) => (
          <input
            {...p}
            className={cn(inputClass, "text-end")}
            dir="ltr"
            inputMode="decimal"
            value={line.qty}
            onChange={(e) => onChange({ qty: e.target.value })}
          />
        )}
      </Field>
      <Field label={t("inventory.doc.unit")} className="w-28">
        {(p) =>
          fixed ? (
            <input {...p} className={inputClass} value={line.unit} readOnly />
          ) : (
            <select
              {...p}
              className={inputClass}
              value={line.unit}
              disabled={!chosen}
              onChange={(e) => onChange({ unit: e.target.value })}
            >
              {chosen?.units.map((u) => (
                <option key={u.unit} value={u.unit}>
                  {u.unit}
                </option>
              ))}
            </select>
          )
        }
      </Field>
      {priced && (
        <Field
          label={t("sales.doc.price")}
          className="w-52"
          hint={
            isReturn
              ? undefined
              : line.priceTouched
                ? undefined
                : quote?.price_source
                  ? t(`sales.priceSource.${quote.source}`, { name: quote.source_name })
                  : undefined
          }
          error={belowMin ? t("sales.entry.belowMin", { min: shortQty(quote!.min_price!) }) : null}
        >
          {(p) => (
            <input
              {...p}
              className={cn(inputClass, "text-end")}
              dir="ltr"
              inputMode="decimal"
              readOnly={isReturn}
              value={price}
              onChange={(e) => onChange({ price: e.target.value, priceTouched: true, priceSource: "manual" })}
            />
          )}
        </Field>
      )}
      {priced && !isReturn && (
        <Field label={t("sales.doc.vatRate")} className="w-24">
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={line.vat}
              onChange={(e) => onChange({ vat: e.target.value as LineState["vat"] })}
            >
              <option value="5">5%</option>
              <option value="0">0%</option>
            </select>
          )}
        </Field>
      )}
      {line.trackBatch && movesStock && !isReturn && (
        <Field label={t("inventory.stock.batch")} className="w-48">
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={line.batchId}
              onChange={(e) => onChange({ batchId: e.target.value })}
            >
              <option value="">{t("accounts.voucher.choose")}</option>
              {batches.data?.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.batch_no}
                  {b.expiry_date ? ` · ${b.expiry_date}` : ""}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
      {removable && (
        <Button
          type="button"
          variant="ghost"
          className={besideField}
          aria-label={t("accounts.voucher.removeLine", { n: index + 1 })}
          onClick={onRemove}
        >
          ×
        </Button>
      )}
    </div>
  );
}
