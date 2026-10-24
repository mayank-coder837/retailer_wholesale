import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "@tanstack/react-router";
import { useEffect, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import { useApi } from "@/api/use-api";
import { Field, inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { todayIso, useBranches } from "@/routes/accounts/queries";
import { localName } from "@/routes/catalog/product-form";
import { errorCode, unwrap, useProducts } from "@/routes/catalog/queries";
import { ComingSoon } from "@/routes/coming-soon";
import { InventoryNav } from "@/routes/inventory/inventory-nav";
import {
  COST,
  isStockDocType,
  QUANTITY,
  useStockBatches,
  useStockProduct,
  useWarehouses,
  warningsKey,
  type StockDocIn,
  type StockDocType,
  type StockLineIn,
} from "@/routes/inventory/queries";
import { useBusiness } from "@/shell/use-business";
import { cn } from "@/lib/utils";

type Direction = "in" | "out";

interface LineState {
  key: number;
  productId: string;
  variantId: string;
  unit: string;
  qty: string;
  direction: Direction;
  cost: string;
  batchId: string;
  batchNo: string;
  expiry: string;
  trackBatch: boolean;
  trackExpiry: boolean;
}

let nextKey = 1;
const emptyLine = (direction: Direction): LineState => ({
  key: nextKey++,
  productId: "",
  variantId: "",
  unit: "",
  qty: "",
  direction,
  cost: "",
  batchId: "",
  batchNo: "",
  expiry: "",
  trackBatch: false,
  trackExpiry: false,
});

/** The entry route: the three inventory documents. */
export function StockEntryRoute() {
  const { kind } = useParams({ strict: false }) as { kind: string };
  if (!isStockDocType(kind)) return <ComingSoon titleKey={`inventory.docTypes.${kind}`} />;
  return <StockEntryPage key={kind} kind={kind} />;
}

/** Inventory → new adjustment, warehouse transfer or Item Transfer (M1.6). */
export function StockEntryPage({ kind }: { kind: StockDocType }) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { current } = useBusiness();
  const branches = useBranches(current?.id);
  const warehouses = useWarehouses(branches.data?.map((b) => b.id) ?? []);

  const [date, setDate] = useState(todayIso());
  const [branchId, setBranchId] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [toWarehouseId, setToWarehouseId] = useState("");
  const [reason, setReason] = useState("");
  const [reference, setReference] = useState("");
  const [narration, setNarration] = useState("");
  const [lines, setLines] = useState<LineState[]>(() =>
    kind === "item_transfer"
      ? [emptyLine("out"), emptyLine("in")]
      : [emptyLine(kind === "stock_transfer" ? "out" : "in")],
  );
  const [tried, setTried] = useState(false);
  const [lockReason, setLockReason] = useState<string | null>(null);

  const branch = branchId || branches.data?.[0]?.id || "";
  const ownWarehouses = (warehouses.data ?? []).filter((w) => w.branch_id === branch);
  const from = warehouseId || ownWarehouses.find((w) => w.is_default)?.id || ownWarehouses[0]?.id || "";
  const destinations = (warehouses.data ?? []).filter((w) => w.id !== from);
  const to = toWarehouseId || destinations[0]?.id || "";
  const hasDirection = kind !== "stock_transfer";
  const update = (key: number, patch: Partial<LineState>) =>
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));

  function build(): StockDocIn | string {
    if (!current || !branch || !from) return "inventory.doc.noWarehouse";
    if (kind === "stock_transfer" && !to) return "inventory.doc.noDestination";
    const out: StockLineIn[] = [];
    for (const l of lines) {
      if (!l.variantId && !l.qty) continue;
      if (!l.variantId || !l.unit) return "inventory.doc.needItem";
      if (!QUANTITY.test(l.qty.trim()) || /^0*(\.0*)?$/.test(l.qty.trim())) return "inventory.doc.badQty";
      const line: StockLineIn = {
        variant_id: l.variantId,
        unit: l.unit,
        qty: l.qty.trim(),
        direction: l.direction,
      };
      if (kind === "stock_adjustment" && l.direction === "in" && l.cost.trim()) {
        if (!COST.test(l.cost.trim())) return "inventory.doc.badCost";
        line.unit_cost = l.cost.trim();
      }
      if (l.trackBatch) {
        if (l.direction === "out" || kind === "stock_transfer") {
          if (!l.batchId) return "inventory.doc.needBatch";
          line.batch_id = l.batchId;
        } else {
          if (!l.batchNo.trim()) return "inventory.doc.needBatch";
          line.batch_no = l.batchNo.trim();
          if (l.expiry) line.expiry_date = l.expiry;
        }
      }
      out.push(line);
    }
    if (out.length === 0) return "inventory.doc.noLines";
    if (kind === "item_transfer") {
      const sides = new Set(out.map((l) => l.direction));
      if (sides.size < 2) return "inventory.doc.bothSides";
    }
    return {
      doc_type: kind,
      business_id: current.id,
      branch_id: branch,
      doc_date: date,
      warehouse_id: from,
      to_warehouse_id: kind === "stock_transfer" ? to : null,
      reason: reason.trim() || null,
      reference: reference.trim() || null,
      narration: narration.trim() || null,
      lines: out,
    };
  }
  const built = build();

  const save = useMutation({
    mutationFn: async ({ post }: { post: boolean }) => {
      if (typeof built === "string") throw { code: "client" };
      if (post && lockReason !== null) {
        const draft = unwrap(await api.POST("/stock-documents", { body: built }));
        return unwrap(
          await api.POST("/stock-documents/{doc_id}/post", {
            params: { path: { doc_id: draft.id } },
            body: { override_lock_reason: lockReason.trim() },
          }),
        );
      }
      return unwrap(await api.POST("/stock-documents", { body: built, params: { query: { post } } }));
    },
    onSuccess: async (doc) => {
      queryClient.setQueryData(warningsKey(doc.id), doc.warnings ?? []);
      for (const key of [
        "stock-docs",
        "stock-balances",
        "stock-negative",
        "stock-valuation",
        "stock-ledger",
      ]) {
        await queryClient.invalidateQueries({ queryKey: [key] });
      }
      await navigate({ to: "/inventory/documents/$docId", params: { docId: doc.id } });
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

  const problem = typeof built === "string" ? built : null;
  const failure = save.isError ? errorCode(save.error) : null;
  const name = (w: { name_en: string; name_ar: string }) => (i18n.language === "ar" ? w.name_ar : w.name_en);

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
      <h1 className="text-2xl font-bold leading-9">{t(`inventory.docTypes.${kind}`)}</h1>
      <InventoryNav />
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label={t("inventory.doc.date")}>
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
        {(branches.data?.length ?? 0) > 1 && (
          <Field label={t("accounts.voucher.branch")}>
            {(p) => (
              <select
                {...p}
                className={inputClass}
                value={branch}
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
        <Field label={t(kind === "stock_transfer" ? "inventory.doc.from" : "inventory.stock.warehouse")}>
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={from}
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
        {kind === "stock_transfer" && (
          <Field label={t("inventory.doc.to")}>
            {(p) => (
              <select
                {...p}
                className={inputClass}
                value={to}
                onChange={(e) => setToWarehouseId(e.target.value)}
              >
                {destinations.map((w) => (
                  <option key={w.id} value={w.id}>
                    {name(w)}
                  </option>
                ))}
              </select>
            )}
          </Field>
        )}
        {kind !== "stock_transfer" && (
          <Field label={t("inventory.doc.reason")} hint={t("common.optional")}>
            {(p) => (
              <input
                {...p}
                className={inputClass}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            )}
          </Field>
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
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 font-semibold">{t("inventory.doc.lines")}</legend>
        {lines.map((l, i) => (
          <StockLine
            key={l.key}
            kind={kind}
            line={l}
            index={i}
            warehouseId={from}
            businessId={current?.id}
            hasDirection={hasDirection}
            removable={lines.length > 1}
            onChange={(patch) => update(l.key, patch)}
            onRemove={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}
          />
        ))}
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              setLines((ls) => [
                ...ls,
                emptyLine(kind === "item_transfer" ? "in" : (ls.at(-1)?.direction ?? "in")),
              ])
            }
          >
            {t("accounts.voucher.addLine")}
          </Button>
        </div>
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
          {t(`inventory.errors.${failure}`, {
            defaultValue: t(`accounts.errors.${failure}`, { defaultValue: t("inventory.errors.generic") }),
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

function StockLine({
  kind,
  line,
  index,
  warehouseId,
  businessId,
  hasDirection,
  removable,
  onChange,
  onRemove,
}: {
  kind: StockDocType;
  line: LineState;
  index: number;
  warehouseId: string;
  businessId: string | undefined;
  hasDirection: boolean;
  removable: boolean;
  onChange: (patch: Partial<LineState>) => void;
  onRemove: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const products = useProducts(query.trim());
  const product = useStockProduct(line.productId);
  const chosen = line.productId ? product.data : undefined;
  const batches = useStockBatches(businessId, line.trackBatch ? line.variantId : "", warehouseId);
  const takesFromBatch = line.trackBatch && (line.direction === "out" || kind === "stock_transfer");

  function pick(productId: string) {
    if (!productId) {
      onChange({ productId: "", variantId: "", unit: "" });
      return;
    }
    onChange({ productId, variantId: "", unit: "", batchId: "", batchNo: "" });
  }

  // Once the product's details arrive, default its variant and unit (the base unit) and remember tracking.
  const needsDefaults = !!chosen && chosen.id === line.productId && !line.variantId && !!chosen.variants[0];
  useEffect(() => {
    if (needsDefaults && chosen) {
      onChange({
        variantId: chosen.variants[0]!.id,
        unit: chosen.base_unit,
        trackBatch: chosen.track_batch,
        trackExpiry: chosen.track_expiry,
      });
    }
  }, [needsDefaults, chosen, onChange]);

  return (
    <div className="flex flex-wrap items-end gap-2 rounded-lg border p-3">
      <Field label={t("inventory.doc.item")} className="min-w-80 flex-1">
        {(p) => (
          <div className="flex gap-1">
            <input
              className={inputClass.replace("w-full", "w-24") + " shrink-0"}
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
              onChange={(e) => pick(e.target.value)}
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
      {chosen && chosen.variants.length > 1 && (
        <Field label={t("inventory.doc.variant")} className="w-44">
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={line.variantId}
              onChange={(e) => onChange({ variantId: e.target.value, batchId: "" })}
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
      {hasDirection && (
        <Field label={t("inventory.doc.direction")} className="w-40">
          {(p) => (
            <select
              {...p}
              className={inputClass}
              value={line.direction}
              onChange={(e) => onChange({ direction: e.target.value as Direction, batchId: "", batchNo: "" })}
            >
              <option value="in">{t(`inventory.doc.${kind === "item_transfer" ? "into" : "in"}`)}</option>
              <option value="out">{t(`inventory.doc.${kind === "item_transfer" ? "outOf" : "out"}`)}</option>
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
        {(p) => (
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
        )}
      </Field>
      {kind === "stock_adjustment" && line.direction === "in" && (
        <Field label={t("inventory.doc.cost")} hint={t("inventory.doc.costHint")} className="w-36">
          {(p) => (
            <input
              {...p}
              className={cn(inputClass, "text-end")}
              dir="ltr"
              inputMode="decimal"
              value={line.cost}
              onChange={(e) => onChange({ cost: e.target.value })}
            />
          )}
        </Field>
      )}
      {line.trackBatch && takesFromBatch && (
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
      {line.trackBatch && !takesFromBatch && (
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
      {removable && (
        <Button
          type="button"
          variant="ghost"
          aria-label={t("accounts.voucher.removeLine", { n: index + 1 })}
          onClick={onRemove}
        >
          ×
        </Button>
      )}
    </div>
  );
}
