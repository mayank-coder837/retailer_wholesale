import { fromFils, toFils, VOUCHER_AMOUNT, type AllocationIn } from "@/routes/accounts/queries";

export type BillWiseMode = "on_account" | "against_ref" | "advance" | "new_ref";

/** A party line's bill-wise choice, as typed (Tally: Agst Ref, New Ref, Advance, On Account). */
export interface BillWise {
  mode: BillWiseMode;
  against: Record<string, string>; // bill id -> amount typed
  reference: string;
  dueDate: string;
}

export const ON_ACCOUNT: BillWise = { mode: "on_account", against: {}, reference: "", dueDate: "" };

/** The allocations to send, or an error key. Whatever is not settled against bills goes on account. */
export function allocationsOf(choice: BillWise, amountFils: number): AllocationIn[] | string {
  if (amountFils <= 0) return [];
  if (choice.mode === "on_account") return [];
  if (choice.mode === "advance") {
    return [{ type: "advance", amount: fromFils(amountFils), reference: choice.reference.trim() || null }];
  }
  if (choice.mode === "new_ref") {
    if (!choice.reference.trim()) return "accounts.billWise.needReference";
    return [
      {
        type: "new_ref",
        amount: fromFils(amountFils),
        reference: choice.reference.trim(),
        due_date: choice.dueDate || null,
      },
    ];
  }
  const items: AllocationIn[] = [];
  let used = 0;
  for (const [billId, text] of Object.entries(choice.against)) {
    if (!text.trim()) continue;
    if (!VOUCHER_AMOUNT.test(text.trim())) return "accounts.errors.amount";
    const fils = toFils(text);
    if (fils === 0) continue;
    used += fils;
    items.push({ type: "against_ref", amount: fromFils(fils), bill_id: billId });
  }
  if (used > amountFils) return "accounts.billWise.overAllocated";
  if (used < amountFils) items.push({ type: "on_account", amount: fromFils(amountFils - used) });
  return items;
}
