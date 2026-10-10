# Domain model — Phase 0 and Phase 1

Every business fact hangs off a **business** inside a **tenant**; documents post to two immutable ledgers —
`journal_line` (money) and `stock_move` (goods) — and every balance or report is derived from them.

**Conventions for every tenant table** (not repeated below): `id uuid (v7)`, `tenant_id`, `created_at`,
`created_by`, `updated_at`, `updated_by`; RLS policy + isolation test; master data adds `archived_at`.
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
        │        journal_entry                  stock_move ──► cost_layer / average_cost, stock_balance
        ├─ membership (user × tenant) ── role_assignment (role, business?, branch?)
        ├─ party ── customer_profile / supplier_profile (per business)
        ├─ product ── variant ── barcode (per unit) ; product ── product_unit (factor to base)
        └─ price_list ── price_list_item ; promotion ; customer_group
```

## Tenancy

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| tenant | name, status (`trial, active, suspended`), plan, default_locale | Root; not tenant-scoped (RLS on `id`) |
| business | legal_name_en/_ar, trade_license_no, trn (15 digits, nullable if not VAT-registered), vat_registered, base_currency (ISO 4217, default AED), fiscal_year_start_month, time_zone (default Asia/Dubai), emirate, costing_method (`fifo, moving_average`), negative_stock_allowed | tenant 1—n business |
| branch | business_id, code, name_en/_ar, address, emirate, is_head_office | business 1—n branch |
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
| unit_family | code (`count, length, weight, volume, area, time`), base_unit_id | System seed (tenant_id null) + tenant-defined |
| unit | family_id, code (`pcs, m, kg, roll, carton`), name_en/_ar, symbol, decimals (allowed decimal places) | System or tenant rows |
| unit_conversion | from_unit_id, to_unit_id, factor | Standard, within one family (1 km = 1000 m). Always factor > 0 |
| product_unit | product_id, unit_id, factor_to_base, is_purchase_default, is_sales_default, sale_price, min_price | Product-level, may cross families: 1 roll = 50 m, 1 carton = 24 pcs |
| product.base_unit_id | the inventory unit | Stock and costing are always in the base unit |

Rules: conversion happens once, at entry: `qty_base = round(qty_entered × factor_to_base, base_unit.decimals)`.
Lines store `qty_entered, uom_entered, factor_to_base, qty_base`, so history survives later factor changes.
Factors are stored in the direction unit → base, so they are exact for the common cases (24, 50, 0.001).

## Catalog

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| category | parent_id, name_en/_ar, path | Tree |
| brand | name_en/_ar | |
| product | sku, name_en/_ar, description, product_type (`stock, service, non_stock`), category_id, brand_id, base_unit_id, default_tax_code_id, track_batch, track_expiry, track_serial, has_variants, custom_fields (JSONB) | product 1—n variant; 1—n product_unit |
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

Resolution order (concept v2): **customer price list > customer group list > quantity break > promotion >
base price** (`product_unit.sale_price`), then the minimum-price guard. The engine returns the price and the
rule that produced it, stored on the line (`price_source`). Whether promotions stack with list prices is open.

## Parties (ADR 0005)

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| party | code, name_en/_ar, kind (`company, individual`), trn, is_customer, is_supplier, phone, whatsapp, email, default_currency | Tenant-wide master |
| party_address | party_id, type (`billing, shipping`), lines, city, emirate, country | |
| party_contact | party_id, name, role, phone, email | |
| customer_profile | party_id, business_id, customer_group_id, price_list_id, credit_limit, credit_days, credit_policy (`warn, block`), salesperson_user_id | One per (party, business) |
| supplier_profile | party_id, business_id, payment_terms_days | One per (party, business) |

Credit check at order/invoice post: open AR balance + unposted orders + this document > credit_limit, or any
bill overdue beyond credit_days → warn or block; override needs `credit.override`, audited.

## Inventory (ADR 0006)

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| stock_move | business_id, warehouse_id, variant_id, batch_id, serial_id, move_date, qty_base (signed), unit_cost, value (signed), move_type (`receipt, issue, transfer_in, transfer_out, adjustment, return_in, return_out, opening`), source_document_id, source_line_id, reversal_of_id | **Immutable** (trigger blocks update/delete) |
| batch | variant_id, batch_no, mfg_date, expiry_date | FEFO picking suggests earliest expiry |
| serial (Phase 2) | variant_id, serial_no, status | |
| cost_layer (FIFO) | business_id, variant_id, stock_move_id, qty_in, qty_remaining, unit_cost | Consumed oldest first |
| cost_layer_consumption | out_stock_move_id, cost_layer_id, qty, unit_cost | |
| average_cost (cache) | business_id, variant_id, qty_base, value, avg_cost | Moving average; updated in the posting transaction |
| stock_balance (cache) | business_id, warehouse_id, variant_id, batch_id, qty_base, value | Updated in the posting transaction; rebuildable from `stock_move` |

Stock transfers and stock adjustments are `document` types (below) that produce stock moves.

## Documents (ADR 0009)

`document` — shared base for every commercial document and voucher:

| Field group | Fields |
| --- | --- |
| Identity | doc_type, business_id, branch_id, series_id, number (null until posted), doc_date, reference |
| Lifecycle | status (`draft, posted, cancelled`; `approved` in Phase 2), posted_at/by, cancelled_at/by, cancel_reason, reversal_of_id |
| Party & money | party_id, currency, exchange_rate (to base), price_list_id, tax_mode (`exclusive, inclusive`), place_of_supply (emirate) |
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
| delivery_note | → sales_invoice | yes (COGS) | yes (out) |
| sales_invoice | → receipt, credit_note | yes | yes if not already delivered |
| sales_return | refs sales_invoice | yes | yes (in) |
| purchase_order | → goods_receipt | no | no |
| goods_receipt | → purchase_bill | yes (GRNI) | yes (in) |
| purchase_bill | refs goods_receipt | yes | yes if no receipt |
| purchase_return | refs bill/receipt | yes | yes (out) |
| receipt, payment, contra, journal, expense | vouchers | yes | no |
| credit_note, debit_note | financial adjustments | yes | no |
| stock_transfer, stock_adjustment | inventory documents | adjustment only | yes |
| proforma_invoice | → sales_order / receipt (advance) | no | no |

## Accounting

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| account | business_id, code, name_en/_ar, parent_id, is_group, nature (`asset, liability, equity, income, expense`), account_type (`cash, bank, receivable, payable, stock, tax, cogs, sales, …`), currency (nullable), allow_manual_posting | Groups + ledgers tree, seeded from the industry pack's chart |
| account_role | business_id, role (`ar_control, ap_control, inventory, cogs, sales, sales_returns, purchase_price_variance, grni, vat_output, vat_input, pdc_receivable, pdc_payable, cheques_in_clearing, stock_adjustment, rounding, fx_gain_loss, opening_balance_equity`), account_id | Posting rules reference roles, never codes |
| journal_entry | business_id, branch_id, entry_date, fiscal_period_id, number, narration, source_document_id, reversal_of_id, posted_at | Only posted entries exist (drafts are documents) |
| journal_line | entry_id, account_id, party_id, cost_centre_id, debit, credit (base currency), currency, amount_fc, exchange_rate, description | `debit ≥ 0, credit ≥ 0`, exactly one > 0; deferred trigger: Σdebit = Σcredit per entry; immutable |
| bill | business_id, party_id, document_id, account_id (AR/AP), side (`receivable, payable`), bill_date, due_date, amount, amount_open | One per invoice/bill/opening bill; the Tally bill-wise reference |
| bill_allocation | bill_id, journal_line_id, amount, allocation_type (`against_ref, advance, on_account`) | Receipts/payments/credit notes settle bills; `amount_open` cached |
| fiscal_year | business_id, name, start_date, end_date, status (`open, closed`) | |
| fiscal_period | fiscal_year_id, month, start_date, end_date, status (`open, locked`), locked_by/at | Posting into a locked period needs `period.override_lock`, audited |
| cost_centre | business_id, code, name_en/_ar, parent_id | Optional on journal lines (Phase 2 reporting) |
| pdc | business_id, direction (`received, issued`), party_id, cheque_no, bank_name, cheque_date (maturity), amount, currency, status, bank_account_id, voucher_document_id | States: `received/issued → deposited → cleared` or `→ bounced`; also `returned` (given back before deposit). Each transition posts per `posting-rules.md` |

## Tax

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| tax_code | country (`AE`), code (`SR` 5%, `ZR` 0%, `EX` exempt, `OS` out of scope, `RC` reverse charge), name_en/_ar, rate, valid_from, output_role, input_role | System seed per country; businesses can't edit rates |
| line tax | `document_line.tax_code_id, taxable_amount, tax_amount` | Tax computed and rounded per line |
| vat_return_box | country, box (`1a`–`1g` by emirate, `2`–`11`), label_en/_ar, rule (which tax codes, sales/purchase, amount or tax) | VAT 201 mapping, data not code; to be confirmed by accountant |
| vat_return (M1.10+) | business_id, period_from, period_to, box values (JSON), status (`draft, filed`) | Filing locks the periods |

## Platform

| Entity | Key fields | Relationships / rules |
| --- | --- | --- |
| audit_log | at, actor_user_id, via_ai, on_behalf_of_user_id, ai_trace_id, client (`web, desktop, ai, import, system`), action (`create, update, delete, post, cancel`), entity_type, entity_id, changes (JSONB `{field: [old, new]}`), request_id | Append-only, partitioned by month |
| numbering_series | business_id, branch_id (nullable), doc_type, fiscal_year_id, prefix, next_number, padding, gap_free | Number allocated at post, row-locked; rollback returns it |
| outbox_event | event_type, aggregate_type, aggregate_id, payload (JSONB), created_at, dispatched_at, attempts, last_error | Written in the business transaction |
| idempotency_key | user_id, key, request_hash, status, response_code, response_body, expires_at (24 h) | Unique (tenant_id, user_id, key) |
| attachment | entity_type, entity_id, storage_key, filename, content_type, size, sha256 | Files in S3/MinIO |
| setting | scope (`tenant, business, branch, user`), scope_id, key, value (JSONB) | Typed by a settings registry in code |
| industry_pack | code (`general_trading, textile, hardware, electronics, grocery`), version, content (units, attributes, custom fields, chart of accounts, templates) | Seed files in `apps/api/seeds/` |
| industry_pack_application | business_id, pack_code, version, applied_at | Re-applying is idempotent |
| ai_trace (M1.13) | user_id, prompt, tool_calls (JSONB), result, confirmed_by, cost | Linked from audit_log |
