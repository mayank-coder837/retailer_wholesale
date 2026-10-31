import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { CodeTag } from "@/components/code-tag";
import { besideField, Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { accountLabel, fromFils, money, todayIso, useAccounts, useBranches } from "@/routes/accounts/queries";
import { localName } from "@/routes/catalog/product-form";
import { errorCode, unwrap, useProducts } from "@/routes/catalog/queries";
import { ComingSoon } from "@/routes/coming-soon";
import { qty as shortQty, useStockProduct, useWarehouses } from "@/routes/inventory/queries";
import { useParties, useParty } from "@/routes/parties/queries";
import {
  DECIMAL6,
  isPurchaseDocType,
  netFils,
  PURCHASE_KEYS,
  useOpenLines,
  usePurchaseDoc,
  useReleasedNumbers,
  type OpenLine,
  type PurchaseDoc,
  type PurchaseDocIn,
  type PurchaseDocType,
  type PurchaseLineIn,
} from "@/routes/purchasing/queries";
import { BackToPurchases } from "@/routes/purchasing/purchases";
import {
  useCodeInfo,
  resolveCode,
  splitFils,
  useDefaultPriceMode,
  type NoVatReason,
  type PriceMode,
} from "@/routes/tax/tax";
import { PriceModeFields, TaxCodeField } from "@/routes/tax/tax-fields";
import { useBusiness } from "@/shell/use-business";

/** Accounts an expense or service line may debit (the API refuses cash, bank, control, tax and stock). */
const EXPENSE_TYPES = ["expense", "cogs", "current_asset", "fixed_asset"];

interface LineState {
  key: number;
  kind: "item" | "expense";
  productId: string;
  variantId: string;
  unit: string;
  qty: string;
  price: string;
  /** The tax code the user chose; empty: the default (the line followed, the supplier's, the item's, SR). */
  taxCode: string;
  /** The item's own purchase code; none: standard rated. */
  itemCode: string | null;
  /** Goods kept in stock: blocked VAT (SR-B) is never offered on them (it would change the stock's cost). */
  stocked: boolean;
  accountId: string;
  description: string;
  batchNo: string;
  expiry: string;
  trackBatch: boolean;
  trackExpiry: boolean;
  /** Following a line of an earlier document: fixed item, and what is still open there. */
  source: OpenLine | null;
}

let nextKey = 1;
const emptyLine = (kind: LineState["kind"] = "item"): LineState => ({
  key: nextKey++,
  kind,
  productId: "",
  variantId: "",
  unit: "",
  qty: kind === "expense" ? "1" : "",
  price: "",
  taxCode: "",
  itemCode: null,
  stocked: kind === "item",
  accountId: "",
  description: "",
  batchNo: "",
  expiry: "",
  trackBatch: false,
  trackExpiry: false,
  source: null,
});

function fromOpen(o: OpenLine): LineState {
  return {
    ...emptyLine(),
    variantId: o.variant_id ?? "",
    unit: o.unit ?? "",
    qty: shortQty(o.qty_open),
    price: o.unit_price === null || o.unit_price === undefined ? "" : shortQty(o.unit_price),
    source: o,
  };
}

/** The entry route: the four purchase documents; ``from`` starts it from an earlier one. */
export function PurchaseEntryRoute() {
  const { kind } = useParams({ strict: false }) as { kind: string };
  const search = useSearch({ strict: false }) as { from?: string };
  if (!isPurchaseDocType(kind)) return <ComingSoon titleKey={`purchasing.types.${kind}`} />;
  if (search.from) return <StartFrom key={`${kind}-${search.from}`} kind={kind} from={search.from} />;
  return <PurchaseEntryPage key={kind} kind={kind} start={null} />;
}

/** Loads the earlier document and what is still open on it, then opens the form already filled in. */
function StartFrom({ kind, from }: { kind: PurchaseDocType; from: string }) {
  const { t } = useTranslation();
  const source = usePurchaseDoc(from);
  const open = useOpenLines(from);
  if (source.isError || open.isError)
    return (
      <p role="alert" className="text-destructive">
        {t("purchasing.errors.generic")}
      </p>
    );
  // Fresh figures only: what is open changes with every receipt, bill, cancel and reversal.
  if (!source.data || !open.data || source.isFetching || open.isFetching) return <p>{t("common.loading")}</p>;
  return <PurchaseEntryPage kind={kind} start={{ doc: source.data, open: open.data }} />;
}

/** Purchases → new order, goods receipt, supplier bill or return (M1.7c). */
function PurchaseEntryPage({
  kind,
  start,
}: {
  kind: PurchaseDocType;
  start: { doc: PurchaseDoc; open: OpenLine[] } | null;
}) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);
  const accounts = useAccounts(current?.id);
  const [supplierQuery, setSupplierQuery] = useState("");
  const suppliers = useParties(supplierQuery.trim(), "supplier");
  const from = start?.doc.id ?? "";

  const [date, setDate] = useState(todayIso());
  const [branchId, setBranchId] = useState(start?.doc.branch_id ?? "");
  const [supplierId, setSupplierId] = useState(start?.doc.party_id ?? "");
  const [warehouseId, setWarehouseId] = useState(start?.doc.warehouse_id ?? "");
  const [supplierRef, setSupplierRef] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [reference, setReference] = useState("");
  const [narration, setNarration] = useState("");
  // Starting from an earlier document: the lines still open on it.
  const [lines, setLines] = useState<LineState[]>(() => (start ? start.open.map(fromOpen) : [emptyLine()]));
  const [reuse, setReuse] = useState("");
  const [tried, setTried] = useState(false);
  const [lockReason, setLockReason] = useState<string | null>(null);
  const [modeChoice, setModeChoice] = useState<{ mode: PriceMode; reason: NoVatReason | null } | null>(null);

  const branch = branchId || branches.data?.[0]?.id || "";
  const ownWarehouses = (warehouses.data ?? []).filter((w) => w.branch_id === branch);
  const warehouse = warehouseId || ownWarehouses.find((w) => w.is_default)?.id || ownWarehouses[0]?.id || "";
  const released = useReleasedNumbers(branch, kind);
  const expenseAccounts = (accounts.data ?? []).filter(
    (a) =>
      !a.is_group &&
      a.allow_manual_posting &&
      a.archived_at === null &&
      !!a.account_type &&
      EXPENSE_TYPES.includes(a.account_type),
  );
  const update = (key: number, patch: Partial<LineState>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  const isReturn = kind === "purchase_return";
  const isBill = kind === "purchase_bill";
  // Nothing is owed yet on a receipt or on returning goods not yet billed, so no VAT (as the API posts it).
  const noVat = kind === "goods_receipt" || (isReturn && start?.doc.doc_type === "goods_receipt");
  // VAT (M1.10d): a document that follows another keeps its price mode (a return always does); a new one starts
  // from the setting. Purchases record the VAT charged whether or not the business is registered (as the API).
  const defaultMode = useDefaultPriceMode(branch);
  const modeFixed = !!start?.doc.price_mode;
  const mode: PriceMode =
    (start?.doc.price_mode as PriceMode | null | undefined) ??
    modeChoice?.mode ??
    defaultMode ??
    "vat_exclusive";
  const reason: NoVatReason | null =
    mode === "no_vat"
      ? ((start?.doc.no_vat_reason ?? modeChoice?.reason ?? "zero_rated") as NoVatReason)
      : null;
  const supplier = useParty(supplierId);
  const codeInfo = useCodeInfo("purchase");
  const codeOf = (l: LineState): string =>
    resolveCode({
      side: "purchase",
      registered: true,
      mode,
      reason,
      typed: isReturn ? null : l.taxCode,
      followed: l.source ? (l.source.tax_code ?? (Number(l.source.vat_rate) > 0 ? "SR" : "OS")) : null,
      party: supplier.data?.purchase_tax_code,
      product: l.kind === "item" ? l.itemCode : null,
    });
  const codeFixed = isReturn || mode === "no_vat";
  const needsWarehouse =
    kind === "goods_receipt" ||
    isReturn ||
    (isBill && lines.some((l) => l.kind === "item" && !l.source && l.variantId));

  function build(): PurchaseDocIn | string {
    if (!current || !branch) return "purchasing.entry.noBranch";
    if (!supplierId) return "purchasing.entry.needSupplier";
    if (needsWarehouse && !warehouse) return "purchasing.entry.noWarehouse";
    const out: PurchaseLineIn[] = [];
    for (const l of lines) {
      const blank = l.kind === "item" ? !l.variantId && !l.qty : !l.accountId && !l.price;
      if (blank && !l.source) continue;
      const q = l.qty.trim();
      if (!DECIMAL6.test(q) || /^0*(\.0*)?$/.test(q)) return "purchasing.entry.badQty";
      const price = l.price.trim();
      if (price && !DECIMAL6.test(price)) return "purchasing.entry.badPrice";
      if (l.kind === "expense") {
        if (!l.accountId) return "purchasing.entry.needAccount";
        if (!price) return "purchasing.entry.needPrice";
        out.push({
          account_id: l.accountId,
          qty: "1",
          unit_price: price,
          tax_code: l.taxCode && !codeFixed ? (l.taxCode as PurchaseLineIn["tax_code"]) : null,
          description: l.description.trim() || null,
        });
        continue;
      }
      if (!l.variantId || !l.unit) return "purchasing.entry.needItem";
      if (!l.source && !price && !isReturn) return "purchasing.entry.needPrice";
      const line: PurchaseLineIn = { variant_id: l.variantId, unit: l.unit, qty: q };
      if (l.source) line.source_line_id = l.source.line_id;
      if (!isReturn) {
        if (price) line.unit_price = price;
        // Only a code the user chose: otherwise the API applies the same default the screen shows. A receipt
        // carries no VAT; its code (from the order) is the default for the bill.
        if (l.taxCode && !codeFixed && !noVat) line.tax_code = l.taxCode as PurchaseLineIn["tax_code"];
      }
      if (l.trackBatch && !isReturn && kind !== "purchase_order") {
        if (!l.batchNo.trim()) return "purchasing.entry.needBatch";
        line.batch_no = l.batchNo.trim();
        if (l.expiry) line.expiry_date = l.expiry;
      }
      if (l.description.trim()) line.description = l.description.trim();
      out.push(line);
    }
    if (isReturn && !from) return "purchasing.entry.returnNeedsSource";
    if (out.length === 0) return "purchasing.entry.noLines";
    return {
      doc_type: kind,
      price_mode: mode,
      no_vat_reason: reason,
      business_id: current.id,
      branch_id: branch,
      doc_date: date,
      party_id: supplierId,
      warehouse_id: needsWarehouse ? warehouse : null,
      supplier_ref: isBill ? supplierRef.trim() || null : null,
      due_date: isBill && dueDate ? dueDate : null,
      reference: reference.trim() || null,
      narration: narration.trim() || null,
      lines: out,
    };
  }
  const built = build();

  const save = useMutation({
    mutationFn: async ({ post }: { post: boolean }) => {
      if (typeof built === "string") throw { code: "client" };
      if (post && (lockReason !== null || reuse)) {
        const draft = unwrap(await api.POST("/purchase-documents", { body: built }));
        return unwrap(
          await api.POST("/purchase-documents/{doc_id}/post", {
            params: { path: { doc_id: draft.id } },
            body: {
              override_lock_reason: lockReason?.trim() || null,
              reuse_number: reuse ? Number(reuse) : null,
            },
          }),
        );
      }
      return unwrap(await api.POST("/purchase-documents", { body: built, params: { query: { post } } }));
    },
    onSuccess: async (doc) => {
      for (const key of PURCHASE_KEYS) await queryClient.invalidateQueries({ queryKey: [key] });
      await queryClient.invalidateQueries({ queryKey: ["released-numbers"] });
      await navigate({ to: "/purchases/$docId", params: { docId: doc.id } });
    },
    onError: (error) => {
      const body = error as { code?: string; may_override?: boolean };
      if (body.code === "period_locked" && body.may_override && lockReason === null) setLockReason("");
    },
  });

  function submit(post: boolean) {
    setTried(true);
    if (typeof built === "string") return;
    if (post && lockReason !== null && !lockReason.trim()) return;
    save.mutate({ post });
  }

  function onKeyDown(e: KeyboardEvent<HTMLFormElement>) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      submit(true);
    }
  }

  // Totals as the API will compute them: per line, VAT half-up to the fils (decision A3).
  // Reverse-charge VAT is self-assessed: shown, and not owed to the supplier (rules 29, 30).
  let subtotal = 0;
  let vat = 0;
  let reverseCharge = 0;
  for (const l of lines) {
    const price = isReturn ? (l.source?.unit_price ?? "") : l.price.trim();
    const amount =
      l.kind === "expense" ? netFils("1", price) : netFils(l.qty.trim(), price ? shortQty(price) : "");
    const info = codeInfo(codeOf(l));
    const [net, splitVat] = splitFils(amount, info.rate, mode);
    const lineVat = noVat ? 0 : splitVat;
    subtotal += net;
    vat += lineVat;
    if (info.treatment === "reverse_charge") reverseCharge += lineVat;
  }

  const problem = typeof built === "string" ? built : null;
  const failure = save.isError ? errorCode(save.error) : null;
  const name = (x: { name_en: string; name_ar: string }) => (i18n.language === "ar" ? x.name_ar : x.name_en);
  const supplierOptions = suppliers.data ?? [];
  const chosenSupplierMissing = supplierId && !supplierOptions.some((p) => p.id === supplierId);

  return (
    <form
      className="flex max-w-5xl flex-col gap-5"
      noValidate
      onKeyDown={onKeyDown}
      onSubmit={(e) => {
        e.preventDefault();
        submit(true);
      }}
    >
      <BackToPurchases />
      <h1 className="text-2xl font-bold leading-9">{t(`purchasing.types.${kind}`)}</h1>
      {start && (
        <p className="text-sm text-muted-foreground">
          {t("purchasing.entry.from")}{" "}
          <Link to="/purchases/$docId" params={{ docId: from }} className="underline underline-offset-2">
            <span dir="ltr">{start.doc.number}</span>
          </Link>
        </p>
      )}
      {isReturn && !from && (
        <p role="status" className="rounded-lg border bg-muted/40 p-3 text-sm">
          {t("purchasing.entry.returnNeedsSource")}
        </p>
      )}
      <div className="grid items-start gap-3 sm:grid-cols-4">
        <Field label={t("purchasing.doc.date")}>
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
        <Field label={t("purchasing.doc.supplier")} className="sm:col-span-2">
          {(p) => (
            <div className="flex gap-1">
              <input
                className={cn(inputClass, "w-24 shrink-0")}
                type="search"
                aria-label={t("purchasing.entry.findSupplier")}
                placeholder={t("accounts.voucher.find")}
                value={supplierQuery}
                disabled={!!from}
                onChange={(e) => setSupplierQuery(e.target.value)}
              />
              <select
                {...p}
                className={cn(inputClass, "min-w-0 flex-1")}
                value={supplierId}
                disabled={!!from}
                onChange={(e) => setSupplierId(e.target.value)}
              >
                <option value="">{t("accounts.voucher.choose")}</option>
                {chosenSupplierMissing && <option value={supplierId}>{supplierId}</option>}
                {supplierOptions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.code} {name(s)}
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
        {needsWarehouse && (
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
        {isBill && (
          <>
            <Field label={t("purchasing.doc.supplierRef")} hint={t("purchasing.entry.supplierRefHint")}>
              {(p) => (
                <input
                  {...p}
                  className={inputClass}
                  dir="ltr"
                  value={supplierRef}
                  onChange={(e) => setSupplierRef(e.target.value)}
                />
              )}
            </Field>
            <Field label={t("purchasing.doc.dueDate")} hint={t("purchasing.entry.dueHint")}>
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
          </>
        )}
        <Field label={t("accounts.voucher.reference")} hint={t("common.optional")}>
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            />
          )}
        </Field>
        <PriceModeFields
          mode={mode}
          reason={reason}
          fixed={modeFixed}
          onChange={(m, r) => setModeChoice({ mode: m, reason: r })}
        />
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 font-semibold">{t("purchasing.doc.lines")}</legend>
        {start && lines.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("purchasing.entry.nothingOpen")}</p>
        )}
        {lines.map((l, i) => (
          <PurchaseLine
            key={l.key}
            kind={kind}
            line={l}
            index={i}
            expenseAccounts={expenseAccounts}
            taxCode={codeOf(l)}
            taxCodeFixed={codeFixed}
            removable={lines.length > 1 || !!l.source}
            onChange={(patch) => update(l.key, patch)}
            onRemove={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}
          />
        ))}
        {!isReturn && (
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="outline" onClick={() => setLines((ls) => [...ls, emptyLine()])}>
              {t("purchasing.entry.addItem")}
            </Button>
            {isBill && (
              <Button
                type="button"
                variant="outline"
                onClick={() => setLines((ls) => [...ls, emptyLine("expense")])}
              >
                {t("purchasing.entry.addExpense")}
              </Button>
            )}
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

      <dl
        className="ms-auto grid w-full max-w-xs grid-cols-2 gap-1 rounded-lg border p-3 text-sm"
        aria-label={t("purchasing.doc.totals")}
      >
        <dt>{t("purchasing.doc.subtotal")}</dt>
        <dd className="text-right tabular-nums">
          <span dir="ltr">{money(fromFils(subtotal))}</span>
        </dd>
        <dt>{t("purchasing.doc.vat")}</dt>
        <dd className="text-right tabular-nums">
          <span dir="ltr">{money(fromFils(vat))}</span>
        </dd>
        {reverseCharge > 0 && (
          <>
            <dt>{t("tax.reverseCharge")}</dt>
            <dd className="text-right tabular-nums">
              <span dir="ltr">-{money(fromFils(reverseCharge))}</span>
            </dd>
          </>
        )}
        <dt className="font-semibold">{t("purchasing.doc.total")}</dt>
        <dd className="text-right font-semibold tabular-nums">
          <span dir="ltr">{money(fromFils(subtotal + vat - reverseCharge))}</span>
        </dd>
      </dl>

      {(released.data?.length ?? 0) > 0 && (
        <Field
          label={t("purchasing.entry.number")}
          hint={t("purchasing.entry.numberHint")}
          className="max-w-sm"
        >
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
                  {t("purchasing.entry.reuse", { number: r.formatted })}
                </option>
              ))}
            </select>
          )}
        </Field>
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
      {tried && problem && (
        <p role="alert" className="text-destructive">
          {t(problem)}
        </p>
      )}
      {failure && failure !== "client" && !(failure === "period_locked" && lockReason !== null) && (
        <p role="alert" className="text-destructive">
          {t(`purchasing.errors.${failure}`, {
            defaultValue: t(`inventory.errors.${failure}`, {
              defaultValue: t(`accounts.errors.${failure}`, { defaultValue: t("purchasing.errors.generic") }),
            }),
          })}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={save.isPending}>
          {lockReason !== null ? t("accounts.voucher.postLocked") : t("accounts.voucher.post")}
        </Button>
        <Button type="button" variant="outline" disabled={save.isPending} onClick={() => submit(false)}>
          {t("accounts.voucher.saveDraft")}
        </Button>
        <span className="text-xs text-muted-foreground">{t("accounts.voucher.shortcut")}</span>
      </div>
    </form>
  );
}

function PurchaseLine({
  kind,
  line,
  index,
  expenseAccounts,
  taxCode,
  taxCodeFixed,
  removable,
  onChange,
  onRemove,
}: {
  kind: PurchaseDocType;
  line: LineState;
  index: number;
  expenseAccounts: { id: string; code: string; name_en: string; name_ar: string }[];
  taxCode: string;
  taxCodeFixed: boolean;
  removable: boolean;
  onChange: (patch: Partial<LineState>) => void;
  onRemove: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const products = useProducts(query.trim());
  const product = useStockProduct(line.productId);
  const chosen = line.productId ? product.data : undefined;
  const isReturn = kind === "purchase_return";

  // Once the product's details arrive, default its variant and unit and remember batch tracking.
  const needsDefaults = !!chosen && chosen.id === line.productId && !line.variantId && !!chosen.variants[0];
  useEffect(() => {
    if (needsDefaults && chosen) {
      onChange({
        variantId: chosen.variants[0]!.id,
        unit: chosen.base_unit,
        trackBatch: chosen.track_batch,
        trackExpiry: chosen.track_expiry,
        itemCode: chosen.purchase_tax_code ?? null,
        stocked: chosen.product_type === "stock",
      });
    }
  }, [needsDefaults, chosen, onChange]);

  const removeButton = removable && (
    <Button
      type="button"
      variant="ghost"
      className={besideField}
      aria-label={t("accounts.voucher.removeLine", { n: index + 1 })}
      onClick={onRemove}
    >
      ×
    </Button>
  );
  // A receipt carries no VAT (nothing is owed yet): its code, from the order, waits for the bill.
  const vatField = kind !== "goods_receipt" && (
    <TaxCodeField
      side="purchase"
      value={taxCode}
      readOnly={taxCodeFixed}
      stock={line.stocked}
      onChange={(code) => onChange({ taxCode: code })}
    />
  );

  if (line.kind === "expense") {
    return (
      <div className="flex flex-wrap items-start gap-2 rounded-lg border p-3">
        <Field label={t("purchasing.entry.expenseAccount")} className="min-w-72 flex-1">
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={line.accountId}
              onChange={(e) => onChange({ accountId: e.target.value })}
            >
              <option value="">{t("accounts.voucher.choose")}</option>
              {expenseAccounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {accountLabel(a as Parameters<typeof accountLabel>[0], i18n.language)}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label={t("purchasing.entry.amount")} className="w-36">
          {(p) => (
            <input
              {...p}
              className={cn(inputClass, "text-end")}
              dir="ltr"
              inputMode="decimal"
              value={line.price}
              onChange={(e) => onChange({ price: e.target.value })}
            />
          )}
        </Field>
        {vatField}
        <Field label={t("accounts.voucher.description")} className="min-w-48 flex-1">
          {(p) => (
            <input
              {...p}
              className={inputClass}
              value={line.description}
              onChange={(e) => onChange({ description: e.target.value })}
            />
          )}
        </Field>
        {removeButton}
      </div>
    );
  }

  const open = line.source;
  return (
    <div className="flex flex-wrap items-start gap-2 rounded-lg border p-3">
      {open ? (
        <div className="flex min-w-72 flex-1 flex-col gap-1 text-sm">
          <span className="font-medium">{t("inventory.doc.item")}</span>
          <span className="flex h-9 items-center">
            <CodeTag>{open.sku}</CodeTag>
            {i18n.language === "ar" ? open.name_ar : open.name_en}
          </span>
          <span className="text-muted-foreground">
            {t("purchasing.entry.stillOpen", { qty: shortQty(open.qty_open), unit: open.unit ?? "" })}
          </span>
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
                    batchNo: "",
                    expiry: "",
                    taxCode: "",
                    itemCode: null,
                    stocked: true,
                  })
                }
              >
                <option value="">{t("accounts.voucher.choose")}</option>
                {products.data
                  ?.filter((x) => x.product_type === "stock" && x.archived_at === null)
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
          open ? (
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
      {isReturn ? (
        <div className="flex w-36 flex-col gap-1 text-sm">
          <span className="font-medium">{t("purchasing.doc.price")}</span>
          <span className="flex h-9 items-center justify-end tabular-nums" dir="ltr">
            {open?.unit_price ? shortQty(open.unit_price) : ""}
          </span>
        </div>
      ) : (
        <Field label={t("purchasing.doc.price")} className="w-36">
          {(p) => (
            <input
              {...p}
              className={cn(inputClass, "text-end")}
              dir="ltr"
              inputMode="decimal"
              value={line.price}
              onChange={(e) => onChange({ price: e.target.value })}
            />
          )}
        </Field>
      )}
      {vatField}
      {line.trackBatch && !isReturn && kind !== "purchase_order" && (
        <>
          <Field label={t("inventory.stock.batch")} className="w-36">
            {(p) => (
              <input
                {...p}
                className={inputClass}
                dir="ltr"
                value={line.batchNo}
                onChange={(e) => onChange({ batchNo: e.target.value })}
              />
            )}
          </Field>
          {line.trackExpiry && (
            <Field label={t("inventory.doc.expiry")} className="w-44">
              {(p) => (
                <input
                  {...p}
                  type="date"
                  className={inputClass}
                  value={line.expiry}
                  onChange={(e) => onChange({ expiry: e.target.value })}
                />
              )}
            </Field>
          )}
        </>
      )}
      {removeButton}
    </div>
  );
}
