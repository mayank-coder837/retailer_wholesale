# Architecture

SmartBiz OS is a modular monolith — one FastAPI service over PostgreSQL with row-level security — that runs
**locally on each branch's main PC** (fully offline, office PCs over the LAN) and, from M2.1, also in the cloud,
with the two kept in sync. Every client (desktop, browser, AI agent, imports, later POS and B2B portal) goes through
the same API, so permissions and audit are applied once. Decisions: `docs/adr/`, `docs/decisions-round-1.md`.

## System at a glance

```
 Branch office (works offline)                                    Cloud (M2.1)
 ┌──────────────────────────────────────────────┐                 ┌───────────────────────────────────────┐
 │ Office PCs: SmartBiz OS app (client mode)    │                 │ Browser / phone (same SPA)            │
 │        │ HTTPS over LAN (pinned cert)        │                 │ AI agent · WhatsApp · e-invoice ASP   │
 │        ▼                                     │                 │        │                              │
 │ Main PC: Electron app                        │   sync push/pull│        ▼                              │
 │   SPA ──► SmartBizOS-API (FastAPI) ──────────┼────────────────►│ API (same code) ─► PostgreSQL 17 (RLS)│
 │             │ in-process outbox worker       │  append-only    │   Celery + Redis workers, S3 files    │
 │             ▼                                │  posted records │                                       │
 │   SmartBizOS-PostgreSQL 17 (RLS), files dir, │                 │                                       │
 │   nightly + pre-update backups               │                 │                                       │
 └──────────────────────────────────────────────┘                 └───────────────────────────────────────┘
```

Inside the API, every request flows: routers → services (business rules, posting; wrapped by the AI tool
registry) → repositories → PostgreSQL, with audit, outbox and sync-outbox rows written in the same transaction.

## Modules and boundaries

| Module | Owns (tables) | Calls services of |
| --- | --- | --- |
| `tenancy` | tenant, business, branch, warehouse, node | — |
| `identity` | user, membership, role, role_permission, role_assignment, refresh_token | tenancy |
| `catalog` | unit_family, unit, unit_conversion, product, product_unit, variant, attribute*, barcode, category, brand, custom_field_def | tenancy |
| `pricing` | price_list, price_list_item, customer_group, price_list_assignment, promotion; sales-history query | catalog, parties, documents (read) |
| `parties` | party, party_address, party_contact, customer_profile, supplier_profile | tenancy |
| `inventory` | stock_move, batch, serial, average_cost, stock_balance; item transfer posting | catalog, tenancy |
| `sales` | sales document types (in `document`), credit checks | catalog, pricing, parties, inventory, accounting, tax, documents |
| `purchasing` | purchase document types (in `document`) | catalog, parties, inventory, accounting, tax, documents |
| `accounting` | account, account_role, journal_entry, journal_line, bill, bill_allocation, fiscal_year, fiscal_period, cost_centre, pdc_register, pdc_intended_bill, vouchers | parties, tenancy |
| `tax` | tax_code, vat_return_box, vat_return | accounting (reads via service) |
| `documents` | document, document_line, voucher_line, document_link, templates, rendered PDFs | tenancy |
| `reporting` | none (reads through other modules' query services) | all read services |
| `core` (platform) | audit_log, numbering_series, released_number, outbox_event, idempotency_key, attachment, setting, industry_pack, sync_outbox, sync_conflict | — |

Rule: a module imports another module's `service` only. An import-linter contract in CI enforces it.

## Request lifecycle

1. **Auth.** Middleware verifies the access token (ADR 0007) → `user_id`, `tenant_id`, `membership_id`.
2. **Idempotency.** For writes with `Idempotency-Key`: same request hash → stored response; different hash →
   `409 idempotency_key_reused`; in progress → `409 request_in_progress`.
3. **Tenant context.** Open a transaction; `SET LOCAL app.tenant_id = :tenant_id`. RLS scopes every query.
4. **Permission check.** `require("sales_invoice.post")` against the membership's roles, scoped to the
   business/branch; failure → `403 permission_denied`.
5. **Service.** Validates input (Pydantic v2 → domain types with `Decimal`), applies business rules, calls other
   modules' services, and for posting calls the module's `posting.py`.
6. **Repository.** SQLAlchemy 2 async. No business logic.
7. **Audit, outbox, sync outbox.** Same transaction: `audit_log` (actor, `via_ai`, `on_behalf_of`, old → new),
   `outbox_event` (jobs), `sync_outbox` (ADR 0010).
8. **Commit** → store the idempotent response → return. Errors are RFC 7807 problems with a stable `code`.

### Posting (the critical path)

`service.post(doc_id)` in one transaction: lock the document `FOR UPDATE` → check `draft` and period open →
allocate the number from the branch series (or reuse a released number the user accepted) → stock moves at moving
average (negative allowed with warning; revaluation on arrival) → journal lines from `posting-rules.md` (a DB
trigger checks debit = credit at commit) → status `posted` → audit, outbox, sync outbox. Any failure rolls back
everything, including the number.

### Cancel and Reverse

Both write new rows only (`CLAUDE.md` rule 3). **Cancel** (open period, VAT not filed, not e-invoiced): reversing
journal entry and stock moves dated on the original date, bill allocations turned into advances, linked delivery
notes back to "not invoiced", status `cancelled`, full snapshot in the audit log, number released for reuse.
**Reverse**: a reversal document (credit/debit note or reversing voucher) with its own number, dated as chosen;
original status `reversed`.

## Row-level security

- Every tenant table: `ENABLE` + `FORCE ROW LEVEL SECURITY`, policy on `tenant_id = current_setting('app.tenant_id', true)::uuid`.
- API role `app_user` has no `BYPASSRLS`; migrations run as `app_owner`. Identical locally and in the cloud.
- Alembic helper `enable_tenant_rls(table)` in every migration that creates a tenant table; a CI check fails on
  tenant tables without a policy or an `assert_tenant_isolated` test.
- Pre-tenant auth queries use `SECURITY DEFINER` functions only (ADR 0002).

## Posted documents are permanent

- Status machine: `draft → posted → cancelled | reversed` (`approved` in Phase 2).
- DB triggers reject `UPDATE`/`DELETE` on `journal_line`, `stock_move` and posted `document`/`document_line` rows,
  except the status change to `cancelled`/`reversed` and its link columns.

## Numbering

Gap-free series for every document type, per branch × type × fiscal year. Only the series' owner node (the
branch's main PC) allocates numbers, at post (at save for quotations); drafts have none. Cancel releases a number
into `released_number`; it is offered to the next document or stays visible as CANCELLED. Cloud-created documents
are numbered by the branch when they sync (ADR 0010), so local and cloud numbers never clash.

## Local runtime and desktop app (ADR 0008)

```
Electron main ── BrowserWindow (sandboxed) ── loads the SPA, API URL from settings
     │ main PC mode: supervises Windows services SmartBizOS-PostgreSQL (port 5440) and SmartBizOS-API (WinSW)
     │ client mode: paired main PC address + pinned TLS cert
     │ owns refresh token (safeStorage/DPAPI), printing, shortcuts, backups, auto-update with migration rollback
preload ── window.smartbiz = { getToken, print, version, services, onUpdate }   (no Node in the page)
```

- UI: Vite + React SPA; the same `dist/` is served by Electron, by the local API for office PCs, and by a CDN for
  browsers.
- Releases: tag `vX.Y.Z` → GitHub Actions on `windows-latest` builds the NSIS installer with the bundled Python
  runtime and PostgreSQL binaries, and publishes it to GitHub Releases; installed copies update themselves.

## Offline and sync

- Offline is the normal case for a branch: everything except internet features works against the local DB.
- Internet features (AI, WhatsApp, e-invoicing, cloud reports) queue in the outbox or show "needs internet".
- From M2.1: `sync_outbox` rows are pushed to the cloud in `seq` order and other nodes' changes are pulled
  (ADR 0010); master-data conflicts follow ADR 0011.

## AI agent and the tool registry (M1.13)

- Service functions are registered as tools: `@tool(name="create_draft_sales_invoice",
  permission="sales_invoice.create", risk="draft")`; schemas come from the Pydantic input models.
- Risk tiers: `read` and `draft` run directly; `commit` (post, cancel, reverse, delete, price change, refund,
  credit override) returns a pending action the user confirms.
- The agent acts with the requesting user's identity, permissions and tenant; audit rows carry `via_ai = true`,
  `on_behalf_of`, `ai_trace_id`. No tool exposes SQL. Metrics come from one semantic metrics module.

## Background work (ADR 0012)

One `outbox_event` table, idempotent handlers. Locally an in-process asyncio worker runs them (backups, PDC
maturity reminders, recost, PDF rendering, queued online tasks); in the cloud, Celery + Redis run the same handlers.

## Hosting

Phases 0–1: local only (developers use Docker Compose; customers use the installer). Pilots of M2.1: a free cloud
tier (Supabase's Postgres is a candidate). Before any real customer data is in the cloud: a UAE region (PDPL),
encrypted backups and point-in-time recovery (F7).
