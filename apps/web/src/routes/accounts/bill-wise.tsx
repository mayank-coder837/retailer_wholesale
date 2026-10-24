import { useTranslation } from "react-i18next";

import { inputClass } from "@/components/field";
import { Button } from "@/components/ui/button";
import { allocationsOf, type BillWise, type BillWiseMode } from "@/routes/accounts/bill-wise-model";
import { fromFils, money, toFils, useOpenBills } from "@/routes/accounts/queries";
import { cn } from "@/lib/utils";

/**
 * The bill-wise picker for one party line. ``side`` is the line's side: a credit settles the party's
 * debit bills (invoices), a debit settles its credit bills (supplier bills, advances received).
 */
export function BillWisePicker({
  businessId,
  partyId,
  side,
  amountFils,
  value,
  onChange,
}: {
  businessId: string;
  partyId: string;
  side: "debit" | "credit";
  amountFils: number;
  value: BillWise;
  onChange: (next: BillWise) => void;
}) {
  const { t } = useTranslation();
  const bills = useOpenBills(businessId, partyId);
  const settleable = (bills.data ?? []).filter((b) => b.direction !== side);
  const result = allocationsOf(value, amountFils);
  const set = (patch: Partial<BillWise>) => onChange({ ...value, ...patch });

  /** Oldest first, as far as the amount goes. */
  function fill() {
    let left = amountFils;
    const against: Record<string, string> = {};
    for (const bill of settleable) {
      if (left <= 0) break;
      const take = Math.min(left, toFils(bill.amount_open));
      against[bill.id] = fromFils(take);
      left -= take;
    }
    set({ against });
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border bg-muted/20 p-3 text-sm">
      <div role="radiogroup" aria-label={t("accounts.billWise.title")} className="flex flex-wrap gap-1">
        {(["on_account", "against_ref", "advance", "new_ref"] as BillWiseMode[]).map((m) => (
          <Button
            key={m}
            type="button"
            size="sm"
            role="radio"
            aria-checked={value.mode === m}
            variant={value.mode === m ? "default" : "outline"}
            onClick={() => set({ mode: m })}
          >
            {t(`accounts.billWise.${m}`)}
          </Button>
        ))}
      </div>
      {value.mode === "against_ref" && (
        <>
          {bills.isPending && <p>{t("common.loading")}</p>}
          {bills.data && settleable.length === 0 && (
            <p className="text-muted-foreground">{t("accounts.billWise.noBills")}</p>
          )}
          {settleable.length > 0 && (
            <>
              <table className="w-full">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1 text-start font-medium">{t("accounts.billWise.reference")}</th>
                    <th className="py-1 text-start font-medium">{t("accounts.billWise.date")}</th>
                    <th className="py-1 text-right font-medium">{t("accounts.billWise.open")}</th>
                    <th className="py-1 text-right font-medium">{t("accounts.billWise.settle")}</th>
                  </tr>
                </thead>
                <tbody>
                  {settleable.map((b) => (
                    <tr key={b.id}>
                      <td className="py-1 font-mono">
                        <span dir="ltr">
                          {b.reference}
                          {b.is_advance && (
                            <span className="ms-1 text-xs">({t("accounts.billWise.advance")})</span>
                          )}
                        </span>
                      </td>
                      <td className="py-1">
                        <span dir="ltr">{b.bill_date}</span>
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        <span dir="ltr">{money(b.amount_open)}</span>
                      </td>
                      <td className="py-1 ps-4 text-right">
                        <input
                          className={cn(inputClass, "w-32 text-end")}
                          dir="ltr"
                          inputMode="decimal"
                          aria-label={t("accounts.billWise.settleBill", { reference: b.reference })}
                          value={value.against[b.id] ?? ""}
                          onChange={(e) => set({ against: { ...value.against, [b.id]: e.target.value } })}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div>
                <Button type="button" size="sm" variant="outline" onClick={fill}>
                  {t("accounts.billWise.fill")}
                </Button>
              </div>
            </>
          )}
        </>
      )}
      {(value.mode === "advance" || value.mode === "new_ref") && (
        <div className="flex flex-wrap gap-2">
          <input
            className={cn(inputClass, "w-48")}
            dir="ltr"
            aria-label={t("accounts.billWise.reference")}
            placeholder={value.mode === "advance" ? t("accounts.billWise.referenceOptional") : ""}
            value={value.reference}
            onChange={(e) => set({ reference: e.target.value })}
          />
          {value.mode === "new_ref" && (
            <input
              type="date"
              className={cn(inputClass, "w-44")}
              aria-label={t("accounts.billWise.due")}
              value={value.dueDate}
              onChange={(e) => set({ dueDate: e.target.value })}
            />
          )}
        </div>
      )}
      {typeof result === "string" ? (
        <p className="text-destructive">{t(result)}</p>
      ) : (
        value.mode === "against_ref" &&
        (() => {
          const rest = result.find((a) => a.type === "on_account");
          return rest ? (
            <p className="text-muted-foreground">
              {t("accounts.billWise.rest", { amount: money(String(rest.amount)) })}
            </p>
          ) : null;
        })()
      )}
    </div>
  );
}
