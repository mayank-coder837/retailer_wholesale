# 0005 — One party table with roles

Status: Accepted · 2026-10-10

## Context
UAE traders often buy from and sell to the same company (a fellow trader). Tally keeps them as ledgers under
Sundry Debtors / Sundry Creditors; users want one statement and sometimes offset balances (contra).

## Decision
One `party` table (names `_en`/`_ar`, TRN, contacts, addresses) with flags `is_customer` / `is_supplier`, plus
role profiles per business:
`customer_profile(party_id, business_id, customer_group_id, price_list_id, credit_limit, credit_days,
credit_policy, salesperson_user_id)` and `supplier_profile(party_id, business_id, payment_terms_days)`.
Receivables and payables stay on separate control accounts; `journal_line.party_id` links both to the party.

## Consequences
- One master to import and de-duplicate (by TRN, then name).
- A combined party statement and AR/AP offset (contra journal) are straightforward.
- Permissions split by role (`customer.*`, `supplier.*`) acting on the same table through the profile.

## Alternatives
Separate `customer` and `supplier` tables — simpler queries, but duplicates for two-way traders and no single
statement; rejected.
