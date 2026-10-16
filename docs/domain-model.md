# Domain model — Phase 0 and Phase 1

Every business fact hangs off a **business** inside a **tenant**; documents post to two immutable ledgers —
`journal_line` (money) and `stock_move` (goods) — and every balance or report is derived from them.

**Conventions for every tenant table** (not repeated below): `id uuid (v7)`, `tenant_id`, `created_at`,
`created_by`, `updated_at`, `updated_by`, `origin_node_id` (the node that created the row, for sync — ADR 0010);
RLS policy + isolation test; master data adds `archived_at` and per-field change clocks (ADR 0011).
The same schema runs in each branch's local database and in the cloud. Updated 2026-10-11 for
`decisions-round-1.md`.
Types: money `NUMERIC(19,4)`, quantity `NUMERIC(19,6)`, rate/factor/unit cost `NUMERIC(19,6)`, timestamps
`timestamptz` UTC. Names shown on documents have `_en` and `_ar`.

## At a glance

```
tenant ─┬─ business (TRN, base currency, fiscal start, costing method)
        │     ├─ branch ── warehouse
        │     ├─ account (chart) ── journal_entry ── journal_line ──► party, cost_centre
        │     ├─ fiscal_year ── fiscal_period
        │     ├─ numbering_series
        │     └─ document (all doc types) ── document_line ──► variant, unit, tax_code, warehouse
        │              │ posts                         │ posts
        │              ▼                               ▼
        │        journal_entry                  stock_move ──► average_cost, stock_balance
        │     └─ pdc_register (memo until cleared → creates a receipt/payment)
        ├─ membership (user × tenant) ── role_assignment (role, business?, branch?)
        ├─ party ── customer_profile / supplier_profile (per business)
        ├─ product ── variant ── barcode (per unit) ; product ── product_unit (factor to base)
        └─ price_list ── price_list_item ; promotion ; customer_group
```

## Tenancy

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| tenant | name, status (`trial, active, suspended`), plan, default_locale | Root; not tenant-scoped (RLS on `id`) |
| business | legal_name_en/_ar, trade_license_no, trn (15 digits, nullable if not VAT-registered), vat_registered, base_currency (ISO 4217, default AED), fiscal_year_start_month, time_zone (default Asia/Dubai), costing_method (`moving_average` only in the MVP — ADR 0006), negative_stock_allowed (default **true**) | tenant 1—n business |
| branch | business_id, code, name_en/_ar, address, **emirate (required)**, is_head_office | business 1—n branch. `emirate` decides the VAT 201 Box 1 line for sales the branch makes (not the customer's emirate) |
| node | kind (`branch_main_pc, cloud`), branch_id (null for cloud), name, last_push_seq, last_pull_cursor, last_seen_at | One per branch main PC plus the cloud; owner of that branch's numbering series (ADR 0010) |
| warehouse | business_id, branch_id, code, name_en/_ar, is_default | branch 1—n warehouse; stock is tracked per warehouse |

## Identity

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| user (global) | email (unique, citext), password_hash (argon2id), full_name, locale, email_verified_at, disabled_at | Not tenant-scoped; reached pre-tenant only via `SECURITY DEFINER` functions |
| membership | tenant_id, user_id, status (`invited, active, suspended`), invited_by | user n—n tenant |
| role | name, description, is_system | Defaults seeded: Owner, Manager, Salesperson, Warehouse, Accountant, Cashier |
| permission (global registry) | code `resource.action` (view, create, edit, delete, approve, post, export) | Defined in code, synced to a table at startup |
| role_permission | role_id, permission_code | role n—n permission |
| role_assignment | membership_id, role_id, business_id (nullable = all), branch_id (nullable = all) | Scope: a Salesperson for Branch A only |
| refresh_token | user_id, family_id, token_hash, client (`web, desktop`), expires_at, rotated_at, revoked_at | ADR 0007 rotation and reuse detection |

## Units of measure (UOM)

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| unit_family | code (`count, length, weight, volume, area, time`), name_en/_ar | Per tenant: standard families copied in at sign-up (ADR 0013) |
| unit | family_id, code (`pcs, m, kg, roll, carton`), name_en/_ar, symbol, decimals (allowed decimal places), factor_to_family_base (> 0), is_family_base (one per family, factor 1), is_standard, archived_at | Per tenant: standard units + the tenant's own. Factor and family are fixed once created |
| product_unit | product_id, unit_id, factor_to_base, is_purchase_default, is_sales_default (+ sale_price, min_price in M1.3) | Product-level, may cross families: 1 roll = 50 m, 1 carton = 24 pcs. The base unit is a row with factor 1; factors exact (L3) and fixed once added; one purchase and one sales default |
| product.base_unit_id | the inventory unit | Stock and costing are always in the base unit |

Rules: conversion happens once, at entry, and is **exact** (founder, 15 Oct 2026): `qty_base = qty_entered ×
factor_to_base`, never rounded (1 yd = 0.9144 m). A unit's `decimals` limits only what may be typed in it; a
conversion whose exact result needs more than 6 decimal places is refused, not rounded.
Lines store `qty_entered, uom_entered, factor_to_base, qty_base`, so history survives later factor changes.
Factors are stored in the direction unit → base, so they are exact for the common cases (24, 50, 0.001).
Standard conversions within a family go through the family base (no table of unit pairs, ADR 0013).
The engine is `app/modules/catalog/uom.py` (M1.1).

## Catalog

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| category | parent_id, name_en/_ar, archived_at | Tree, no cycles; names unique among siblings (path computed on read) |
| brand | name_en/_ar, archived_at | Name unique per tenant |
| product | sku, name_en/_ar, description, product_type (`stock, service, non_stock`), category_id, brand_id, base_unit_id, track_batch, track_expiry, track_serial, custom_fields (JSONB), archived_at | product 1—n variant; 1—n product_unit. SKU and base unit fixed once created. `default_tax_code_id` arrives with tax codes (M1.10) |
| attribute | name_en/_ar, values (attribute_value: value_en/_ar, sort) | Color, Size, Width… from industry packs |
| product_attribute | product_id, attribute_id | Which attributes a product varies by |
| variant | product_id, sku, attribute_values (variant_attribute_value join), custom_fields (JSONB), archived_at | Every product has ≥ 1 variant (a default one if no variants); stock, prices and lines reference **variant** |
| barcode | variant_id, unit_id, code, kind (`ean13, code128, internal, scale`) | Many per variant and per unit; `code` unique per tenant |
| custom_field_def | entity_type (`product, variant, party, document`), key, label_en/_ar, data_type (`text, number, decimal, date, bool, select, multiselect`), options, required, validation (JSON) | Values live in the entity's `custom_fields` JSONB, validated by the service against the defs |

## Pricing

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| customer_group | name, default_price_list_id | |
| price_list | business_id, name, currency, tax_inclusive, valid_from, valid_to | |
| price_list_item | price_list_id, variant_id, unit_id, min_qty (quantity break), price | Highest `min_qty` ≤ line qty wins inside a list |
| price_list_assignment | price_list_id, party_id **or** customer_group_id | |
| promotion | name, kind (`percent, amount, fixed_price, buy_x_get_y`), scope (variant/category/brand), conditions (JSON), valid_from, valid_to, stackable | |
| min price | `product_unit.min_price` | Guard: price below it needs `price.override_min` |

Resolution order (decisions-round-1 A9): **1. last price sold to this customer for this item** (same unit; if the
last sale was in another unit, converted by the unit factors and flagged `converted`) **> 2. customer price list
> 3. customer group list > 4. quantity break > 5. promotion > 6. base price** (`product_unit.sale_price`).
First match wins (promotions do not stack). The minimum-price guard then warns. The engine returns the price and
the rule that produced it, stored on the line (`price_source`, e.g. `last_price:INV-2026-00412`).

**Customer sales-history lookup** (A9) — not a table, a query service over posted sales lines:
`sales_history(party_id, search?, variant_id?)` → item, date, invoice number, qty_entered, unit, unit price, newest
first, per customer. It powers the history panel on every sales document (searchable; one click adds the item)
and step 1 of the pricing engine. Backed by an index on `document_line (party_id, variant_id, doc_date desc)` via
the document header, for posted sales invoices only.

## Parties (ADR 0005)

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| party | code, name_en/_ar, kind (`company, individual`), trn, is_customer, is_supplier, phone, whatsapp, email, default_currency | Tenant-wide master |
| party_address | party_id, type (`billing, shipping`), lines, city, emirate, country | |
| party_contact | party_id, name, role, phone, email | |
| customer_profile | party_id, business_id, customer_group_id, price_list_id, credit_limit, credit_days, credit_policy (`warn, block`), salesperson_user_id | One per (party, business) |
| supplier_profile | party_id, business_id, payment_terms_days | One per (party, business) |

Credit check at order/invoice post: ledger AR balance + unposted orders + this document > credit_limit, or any
bill overdue beyond credit_days → warn or block; override needs `credit.override`, audited. PDCs in hand are shown
beside the balance but do not reduce it (A2).

## Inventory (ADR 0006)

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| stock_move | business_id, warehouse_id, variant_id, batch_id, serial_id, move_date, qty_base (signed), unit_cost, value (signed), move_type (`receipt, issue, transfer_in, transfer_out, item_transfer_out, item_transfer_in, adjustment, return_in, return_out, negative_revaluation, opening`), source_document_id, source_line_id, reversal_of_id | **Immutable** (trigger blocks update/delete) |
| batch | variant_id, batch_no, mfg_date, expiry_date | FEFO picking suggests earliest expiry |
| serial (Phase 2) | variant_id, serial_no, status | |
| average_cost (cache) | business_id, variant_id, qty_base, value, avg_cost, last_purchase_cost | One moving average per item across all warehouses (ADR 0006); updated in the posting transaction |
| stock_balance (cache) | business_id, warehouse_id, variant_id, batch_id, qty_base, value | Updated in the posting transaction; rebuildable from `stock_move` |

No FIFO cost layers in the MVP. Stock may go negative (warning only); a **negative-stock report** lists every
variant/warehouse with `qty_base < 0`. When stock arrives for a negative item, a `negative_revaluation` move
re-costs the negative quantity at the incoming cost (posting rule 27).

**Item Transfer** (`doc_type = item_transfer`, Tally's stock journal): header (business, branch, warehouse, date,
reason) + lines with `direction` (`out` from the source item, `in` to the target item), variant, qty_entered/
uom/factor/qty_base. Value moves at the source item's average cost; in and out values must be equal. Stock
transfers between warehouses and stock adjustments are also `document` types that produce stock moves.

## Documents (ADR 0009)

`document` — shared base for every commercial document and voucher:

| Field group | Fields |
| --- | --- |
| Identity | doc_type, business_id, branch_id, series_id, number (null until posted), doc_date, reference |
| Lifecycle | status (`draft, posted, cancelled, reversed`; `approved` in Phase 2), posted_at/by, cancelled_at/by, cancel_reason, reversed_by_document_id, reversal_of_id, reissue_of_id (replacement that reuses a cancelled number), sent_at (printed/emailed/WhatsApp — triggers the reuse warning) |
| Party & money | party_id, currency, exchange_rate (to base), price_list_id, **price_mode** (`vat_exclusive, vat_inclusive, no_vat`), no_vat_reason (`zero_rated, exempt, out_of_scope` — required when `no_vat`), branch emirate (from branch, for VAT 201) |
| Totals | subtotal, discount_total, tax_total, grand_total, grand_total_base |
| Type-specific (nullable) | due_date, valid_until, supplier_ref (supplier bill no.), shipping_address_id, warehouse_id (header default) |
| Compliance | invoice_kind (`tax_invoice, simplified`), einvoice_status (`not_required, pending, sent, validated, rejected`), einvoice_ref |

`document_line`: line_no, variant_id, description, qty_entered, uom_entered, factor_to_base, qty_base,
unit_price, price_source, discount_pct, discount_amount, tax_code_id, taxable_amount, tax_amount, line_total,
warehouse_id, batch_id, source_line_id (the order/delivery line it fulfils), qty_fulfilled_base.

`voucher_line` (receipt, payment, contra, journal, expense): account_id, party_id, cost_centre_id, debit,
credit, tax_code_id, tax_amount, description, plus `bill_allocation` rows for bill-wise settlement.

`document_link`: from_document_id, to_document_id, kind (`fulfils, returns, credits`).

| doc_type | Flow | Posts journal | Posts stock |
| --- | --- | --- | --- |
| quotation | → sales_order | no | no |
| sales_order | → delivery_note / sales_invoice | no (reserves stock) | no |
| delivery_note | → sales_invoice; prints quantities only, no prices | yes (goods delivered not invoiced) | yes (out) |
| sales_invoice | → receipt, credit_note | yes | yes if not already delivered |
| sales_return | refs sales_invoice | yes | yes (in) |
| purchase_order | → goods_receipt | no | no |
| goods_receipt | → purchase_bill | yes (GRNI) | yes (in) |
| purchase_bill | refs goods_receipt | yes | yes if no receipt |
| purchase_return | refs bill/receipt | yes | yes (out) |
| receipt, payment, contra, journal, expense | vouchers | yes | no |
| credit_note, debit_note | financial adjustments | yes | no |
| stock_transfer, stock_adjustment | inventory documents | adjustment only | yes |
| item_transfer | item A → item B at A's average cost | only if accounts differ | yes |
| proforma_invoice | → sales_order / receipt (advance) | no | no |

Every row above has a **gap-free series** (A12). Lines show VAT per line; header shows total VAT (A3).

## Accounting

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| account | business_id, code, name_en/_ar, parent_id, is_group, nature (`asset, liability, equity, income, expense`), account_type (`cash, bank, receivable, payable, stock, tax, cogs, sales, …`), currency (nullable), allow_manual_posting | Groups + ledgers tree, seeded from the industry pack's chart |
| account_role | business_id, role (`ar_control, ap_control, inventory, cogs, goods_delivered_not_invoiced, stock_cost_adjustment, sales, sales_returns, purchase_price_variance, grni, vat_output, vat_input, stock_adjustment, bank_charges, rounding, fx_gain_loss, opening_balance_equity`), account_id | Posting rules reference roles, never codes |
| journal_entry | business_id, branch_id, entry_date, fiscal_period_id, number, narration, source_document_id, reversal_of_id, posted_at | Only posted entries exist (drafts are documents) |
| journal_line | entry_id, account_id, party_id, cost_centre_id, debit, credit (base currency), currency, amount_fc, exchange_rate, description | `debit ≥ 0, credit ≥ 0`, exactly one > 0; deferred trigger: Σdebit = Σcredit per entry; immutable |
| bill | business_id, party_id, document_id, account_id (AR/AP), side (`receivable, payable`), bill_date, due_date, amount, amount_open | One per invoice/bill/opening bill; the Tally bill-wise reference |
| bill_allocation | bill_id, journal_line_id, amount, allocation_type (`against_ref, advance, on_account`) | Receipts/payments/credit notes settle bills; `amount_open` cached |
| fiscal_year | business_id, name, start_date, end_date, status (`open, closed`) | |
| fiscal_period | fiscal_year_id, month, start_date, end_date, status (`open, locked`), locked_by/at | Posting into a locked period needs `period.override_lock`, audited |
| cost_centre | business_id, code, name_en/_ar, parent_id | Optional on journal lines (Phase 2 reporting) |
| pdc_register | business_id, branch_id, direction (`received, issued`), party_id, cheque_no, bank_name, cheque_date (maturity), amount, currency, status, deposit_bank_account_id, deposited_on, cleared_on, bounce_reason, cleared_voucher_id | **Memo until cleared (A2):** no ledger entry until `cleared`, which creates the receipt/payment voucher dated the clearance date. Received: `in_hand → deposited → cleared` or `→ bounced`; `returned` before deposit. Issued: `issued → cleared` or `→ bounced / cancelled`. Every status change is audited |
| pdc_intended_bill | pdc_id, bill_id, amount | The invoices the cheque is meant for; becomes the bill-wise allocation on clearance |

Statements and ageing show **outstanding** and **outstanding after PDCs in hand** (`pdc_register` with
status `in_hand`/`deposited`).

## Tax

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| tax_code | country (`AE`), code, side (`sales, purchase`), name_en/_ar, rate, recoverable, valid_from, output_role, input_role, vat201_net_box, vat201_tax_box | System seed per country (table below); businesses can't edit rates. Line default: product's tax code, overridden by the customer/supplier (e.g. export customer → ZR) |
| line tax | `document_line.tax_code_id, taxable_amount, tax_amount` | Per line, half-up to 2 dp; document VAT = Σ line VAT (A3) |
| vat_return_box | country, box, label_en/_ar, kind (`input, total, manual`) | Box 1 has one line per emirate; boxes 8 and 11–14 are calculated totals; Box 7 and the Adjustments column are manual |
| vat_return (M1.10+) | business_id, period_from, period_to, box values (JSON), status (`draft, filed`) | Every figure drills down to source documents; filing locks the periods |

Tax codes seeded for the UAE (decisions-round-1, VAT 201 table — accountant to review before Phase 2):

| Code | Side | Rate | VAT 201 box |
| --- | --- | --- | --- |
| SR | sales | 5% | Box 1, net + VAT, emirate of the **branch** making the supply |
| ZR | sales | 0% | Box 4, net only |
| EX | sales | — | Box 5, net only |
| OS | sales / purchase | — | Not reported |
| SR | purchase | 5%, recoverable | Box 9, net + recoverable VAT |
| SR-B | purchase | 5%, blocked | Not in Box 9; VAT becomes part of the expense |
| ZR / EX | purchase | — | Not reported |
| RC-S | purchase (imported services) | 5% | Box 3 (VAT due) and Box 10 (VAT recovered) |
| RC-G | purchase (imported goods) | 5% | Box 6 pre-filled by Customs (records kept to check it); recoverable VAT in Box 10 |
| — | Customs correction | — | Box 7, manual |

Credit notes reduce the original sale's box in the credit note's period; supplier credit notes reduce Box 9. The
price mode (exclusive / inclusive / no VAT) changes only how net and VAT are derived, not the box. Box labels
(1a–1g or one line per emirate) are checked against the EmaraTax form before M1.10.
| vat_return (M1.10+) | business_id, period_from, period_to, box values (JSON), status (`draft, filed`) | Filing locks the periods |

## Platform

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| audit_log | at, actor_user_id, via_ai, on_behalf_of_user_id, ai_trace_id, client (`web, desktop, ai, import, system`), action (`create, update, delete, post, cancel`), entity_type, entity_id, changes (JSONB `{field: [old, new]}`), request_id | Append-only, partitioned by month |
| numbering_series | business_id, branch_id, owner_node_id, doc_type, fiscal_year_id, prefix, next_number, padding | **Gap-free for every document type** (A12). Allocated only by the owner node (the branch's main PC) at post (at save for quotations), row-locked; a rollback returns the number. Drafts have no number |
| released_number | series_id, number, cancelled_document_id, released_at, reused_by_document_id | Numbers freed by Cancel (A7); offered to the next document in the series; unreused numbers stay visible as CANCELLED |
| outbox_event | event_type, aggregate_type, aggregate_id, payload (JSONB), created_at, dispatched_at, attempts, last_error | Written in the business transaction |
| idempotency_key | user_id, key, request_hash, status, response_code, response_body, expires_at (24 h) | Unique (tenant_id, user_id, key) |
| attachment | entity_type, entity_id, storage_key, filename, content_type, size, sha256 | Files in S3/MinIO |
| setting | scope (`tenant, business, branch, user`), scope_id, key, value (JSONB) | Typed by a settings registry in code |
| industry_pack | code (`general_trading, textile, hardware, electronics, grocery`), version, content (units, attributes, custom fields, chart of accounts, templates) | Seed files in `apps/api/seeds/` |
| industry_pack_application | business_id, pack_code, version, applied_at | Re-applying is idempotent |
| sync_outbox | node_id, seq (per node, monotonic), table_name, row_id, op (`insert, master_update`), payload (JSONB), created_at, pushed_at | Written in the business transaction from Phase 0; pushed once cloud sync exists (ADR 0010) |
| sync_conflict | entity_type, entity_id, field, winning_value, losing_value, winning_node/user/at, losing_node/user/at, resolved_by, resolved_at | Master-data conflicts (ADR 0011) |
| ai_trace (M1.13) | user_id, prompt, tool_calls (JSONB), result, confirmed_by, cost | Linked from audit_log |
