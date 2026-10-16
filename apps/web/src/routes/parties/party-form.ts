import type { Emirate } from "@/routes/parties/queries";

export const EMPTY_PARTY = {
  customer: true,
  supplier: false,
  kind: "company" as "company" | "individual",
  en: "",
  ar: "",
  trn: "",
  phone: "",
  whatsapp: "",
  email: "",
  line1: "",
  city: "",
  emirate: "" as Emirate | "",
};

export type PartyForm = typeof EMPTY_PARTY;

/** What the form says is wrong before it is sent, as translation keys per field. */
export function partyFormErrors(form: PartyForm): Partial<Record<keyof PartyForm, string>> {
  const errors: Partial<Record<keyof PartyForm, string>> = {};
  if (!form.customer && !form.supplier) errors.customer = "parties.errors.noRole";
  if (!form.en.trim()) errors.en = "parties.errors.required";
  if (!form.ar.trim()) errors.ar = "parties.errors.required";
  if (form.trn && !/^\d{15}$/.test(form.trn)) errors.trn = "parties.errors.trn";
  if (form.line1.trim() && !form.emirate) errors.emirate = "parties.errors.emirate";
  return errors;
}
