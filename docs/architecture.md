# Architecture

Business OS is a modular monolith: one FastAPI service over one PostgreSQL database with row-level security,
used by a Windows desktop app, the web app and (later) the AI agent, POS and B2B portal — all through the same
API, so permissions and audit are applied once. Decisions behind this page: `docs/adr/`.

## System at a glance

```
 Clients                 Desktop (.exe, Electron) · Web/PWA · AI agent · Import jobs · POS (P2) · B2B portal (P3)
                                         │  HTTPS, JSON, Idempotency-Key on writes
 API layer               FastAPI routers  ── auth → tenant context → permission check
                                         │
 Service layer           modules/*/service.py  (business rules, posting, validation)  ← AI tool registry wraps these
                                         │
 Data layer              repositories → PostgreSQL 16 (RLS)       S3/MinIO (files, PDFs)     Redis (cache, broker)
                                         │ outbox rows (same transaction)
 Workers                 Celery: outbox dispatcher → e-invoice ASP adapter · WhatsApp/email · PDF render · recost jobs
```

## Modules and boundaries

| Module | Owns (tables) | Calls services of |
| --- | --- | --- |
| `tenancy` | tenant, business, branch, warehouse | — |
| `identity` | user, membership, role, role_permission, role_assignment, refresh_token | tenancy |
| `catalog` | unit_family, unit, unit_conversion, product, product_unit, variant, attribute*, barcode, category, brand, custom_field_def | tenancy |
| `pricing` | price_list, price_list_item, customer_group, price_list_assignment, promotion | catalog, parties |
| `parties` | party, party_address, party_contact, customer_profile, supplier_profile | tenancy, pricing (refs only) |
| `inventory` | stock_move, batch, serial, cost_layer, cost_layer_consumption, stock_balance, average_cost | catalog, tenancy |
| `sales` | sales document types (in `document`), credit checks | catalog, pricing, parties, inventory, accounting, tax, documents |
| `purchasing` | purchase document types (in `document`) | catalog, parties, inventory, accounting, tax, documents |
| `accounting` | account, account_role, journal_entry, journal_line, bill, bill_allocation, fiscal_year, fiscal_period, cost_centre, pdc, vouchers | parties, tenancy |
| `tax` | tax_code, vat_return_box, vat_return | accounting (reads via service) |
| `documents` | document, document_line, voucher_line, document_link, template, rendered PDFs | tenancy |
| `reporting` | none (reads through other modules' query services) | all read services |
| `core` (platform) | audit_log, numbering_series, outbox_event, idempotency_key, attachment, setting, industry_pack | — |

Rule: a module imports another module's `service` only. An import-linter contract in CI enforces it.

## Request lifecycle

1. **Auth.** Middleware verifies the access token (ADR 0007) → `user_id`, `tenant_id`, `membership_id`.
2. **Idempotency.** For `POST/PUT/PATCH/DELETE` with `Idempotency-Key`: look up `(tenant, user, key)`. Same
   request hash → return the stored response; different hash → `409 idempotency_key_reused`; in progress →
   `409 request_in_progress`.
3. **Tenant context.** Open a DB transaction; `SET LOCAL app.tenant_id = :tenant_id`. RLS now scopes every query.
4. **Permission check.** Route dependency `require("sales_invoice.post")` resolves the membership's roles,
   scoped to the business/branch in the request; failure → `403 permission_denied`.
5. **Service.** Validates input (Pydantic v2 → domain types with `Decimal`), applies business rules, calls other
   modules' services, and for posting calls the module's `posting.py`.
6. **Repository.** SQLAlchemy 2 async queries/writes. No business logic.
7. **Audit + outbox.** In the same transaction: `audit_log` row(s) (actor, `via_ai`, `on_behalf_of`, old → new)
   and `outbox_event` rows (`sales_invoice.posted`, …).
8. **Commit** → store the idempotent response → return. Errors become RFC 7807 problems with a stable `code`.

### Posting (the critical path)

`service.post(doc_id)` in one transaction:
lock document `FOR UPDATE` → check status `draft` and period open → allocate number from the numbering series
→ build stock moves (inventory service: costing, cached balances) → build journal lines from posting rules
(`posting-rules.md`) → accounting service writes the entry (DB trigger checks debit = credit at commit) → set
`posted` → audit → outbox. Any failure rolls back everything, including the number (gap-free series).

## Row-level security

- Every tenant table: `ENABLE` + `FORCE ROW LEVEL SECURITY`, policy on `tenant_id = current_setting('app.tenant_id', true)::uuid`.
- API role `app_user` has no `BYPASSRLS`; migrations run as `app_owner`.
- An Alembic helper `enable_tenant_rls(table)` is called in every migration that creates a tenant table; a CI
  check fails on tenant tables without a policy or an `assert_tenant_isolated` test.
- Pre-tenant auth queries use `SECURITY DEFINER` functions only (ADR 0002).

## Posted documents are permanent

- Status machine: `draft → posted → cancelled` (`approved` added in Phase 2 approval workflows).
- DB triggers reject `UPDATE`/`DELETE` on `journal_line`, `stock_move` and on `document`/`document_line` rows
  whose status is `posted`, except the single allowed transition `posted → cancelled` (which writes reversal
  entries and stock moves, never edits the originals).
- An e-invoiced sales invoice cannot be cancelled; it is corrected by a credit note.

## AI agent and the tool registry (M1.13)

- Service functions are registered as tools with a decorator:
  `@tool(name="create_draft_sales_invoice", permission="sales_invoice.create", risk="draft")`.
  The registry derives JSON schemas from the Pydantic input models.
- Risk tiers: `read` and `draft` run directly; `commit` (post, delete, price change, refund, credit override)
  returns a pending action the user must confirm in the UI.
- The agent runs with the requesting user's identity: the same `require()` checks, the same RLS tenant, and
  audit rows with `via_ai = true`, `on_behalf_of = user_id`, `ai_trace_id`.
- `ai_trace` stores prompt, tool calls, results and the confirming user. No tool exposes SQL.
- Metrics ("revenue", "overdue") come from one semantic metrics module shared by reports, dashboard and AI.

## Offline writes and sync (desktop now, POS in Phase 2)

- The client generates the record's UUIDv7 and an `Idempotency-Key` per command, and queues commands locally
  (IndexedDB in the renderer) when offline.
- On reconnect the queue replays in order; replays are safe because of step 2 of the lifecycle.
- Offline-created documents stay `draft` with a provisional local number; the server allocates the real number
  at post time. Conflicts (price changed, stock insufficient, credit blocked) return problem codes the client
  shows for the user to resolve.
- Phase 0–1 scope: only draft creation queues offline; posting requires a connection.

## Desktop client (ADR 0008)

```
Electron main ── creates BrowserWindow (sandboxed) ── loads https://app.<domain>   (offline.html if unreachable)
     │ owns refresh token (safeStorage/DPAPI), printing, shortcuts, auto-update (electron-updater, GitHub Releases)
preload ── exposes window.desktop = { getToken, print, version, onUpdate }   (no Node in the page)
```

- Same Next.js UI as the web app; the UI detects `window.desktop` to enable desktop extras.
- Releases: tag `vX.Y.Z` → GitHub Actions (`windows-latest`) builds the NSIS installer, publishes it to GitHub
  Releases; installed copies update themselves.

## Background work

- `outbox_event` rows are dispatched by a Celery beat task (`SELECT … FOR UPDATE SKIP LOCKED`), at-least-once;
  handlers are idempotent by event id.
- Consumers: e-invoicing ASP adapter (Phase 2), WhatsApp/email notifications, PDF rendering, recost jobs,
  webhooks (Phase 3).

## Hosting (target)

UAE cloud region (PDPL data residency), managed PostgreSQL 16 with encrypted backups and point-in-time recovery,
object storage in-region. Provider is an open question.
