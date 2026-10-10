# 0009 — One shared table for commercial documents and vouchers

Status: Accepted · 2026-10-10

## Context
Quotation, sales order, delivery note, sales invoice, sales return, purchase order, goods receipt, purchase
bill, purchase return and the vouchers share most fields, the status machine, numbering, linking, audit and
e-invoice status.

## Decision
`document` + `document_line` tables with a `doc_type` discriminator. The few type-specific fields are nullable
columns (`due_date`, `supplier_ref`, `valid_until`, `shipping_address_id`) validated per type in the service.
Vouchers without item lines (receipt, payment, contra, journal, expense) use `document` + `voucher_line`.
Per-type behaviour lives in per-type posting classes, not in the table design.

## Consequences
- One implementation of numbering, linking (`document_link`), lifecycle and audit.
- Partial indexes per `doc_type` keep lists fast; a check constraint ties `doc_type` to its allowed statuses.

## Alternatives
A table per document type — clearer per-type columns, but nine copies of the lifecycle, numbering and linking.
