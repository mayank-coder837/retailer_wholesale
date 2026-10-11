# SmartBiz OS — guide for Claude Code

SmartBiz OS is a multi-tenant, configurable business operating system for UAE/GCC traders, wholesalers and
retailers: accounting, inventory, sales, purchasing, VAT and an AI assistant in one product.
It is **local-first, like Tally**: a Windows desktop app bundles the UI, the API and PostgreSQL on each branch's
main PC, other office PCs connect over the LAN, and data syncs to the cloud (`docs/adr/0008-local-first-desktop.md`).
Full product spec: `docs/product-concept-v2.md`. Original concept: `docs/product-concept-v1.pdf`.
Founder decisions: `docs/decisions-round-1.md` — where it conflicts with other docs, it wins.

The product name lives in one config value (`packages/config/product.json` → `name`, default "SmartBiz OS");
never hard-code it.

First customer (beachhead): UAE trading and wholesale SMBs (2–50 staff) moving off Tally, Excel and WhatsApp.
When a design choice is unclear, choose what serves that customer's daily workflow.

---

## Non-negotiable rules

These protect money, tax and customer data. Never weaken them, and never edit a test to make it pass around them.

1. **Tenant isolation.** Every business table has `tenant_id`. PostgreSQL row-level security is enabled on every
   tenant table; the API sets `app.tenant_id` per request/transaction. Any new table without RLS + an isolation
   test is incomplete. This applies to the local branch database too: the same migrations run locally and in
   the cloud.
2. **No floats for money or quantity.** Python `Decimal`, DB `NUMERIC(19,4)` for amounts, `NUMERIC(19,6)` for
   quantities, rates and conversion factors. Round only at defined points with the currency's rules (AED = 2 dp).
3. **Posted means permanent.** Financial and stock documents move `draft → posted → cancelled | reversed`.
   Posted rows (documents, journal lines, stock moves) are never updated or deleted. Corrections are new rows:
   **Cancel** (open period only) writes reversing entries dated on the original date and frees the number for a
   replacement, keeping the cancelled version in full in the audit log; **Reverse** writes a reversal or credit/debit
   note dated on the chosen day. See `docs/posting-rules.md`.
4. **Double entry always balances.** Every journal entry satisfies sum(debit) = sum(credit), enforced by a
   deferred DB constraint/trigger as well as in code.
5. **Stock comes from the stock ledger.** Current stock and stock value are derived from immutable `stock_move`
   rows (cached balances are updated in the same transaction). Never store stock as an editable number.
6. **Quantities keep their unit.** Document lines store `qty_entered`, `uom_entered`, `factor_to_base`, `qty_base`.
   Conversion happens once, at entry, through the UOM engine.
7. **One door for every write.** Routers → service layer → repositories. The AI agent, imports, POS sync and the
   UI all call the same service functions with the same permission checks. The AI never gets raw SQL.
8. **Everything important is audited.** Create/update/delete/post/cancel on business entities writes to the
   generic audit log: who, when, what, old → new, and `via_ai` / `on_behalf_of` when an AI acted.
9. **Idempotent writes.** Write endpoints accept an `Idempotency-Key` header (needed for offline POS and AI retries).
10. **Bilingual from day one.** Every user-facing string goes through i18n (English + Arabic, RTL). Names that
    appear on documents have `_en` and `_ar` fields.

## Stack

| Area | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic |
| DB | PostgreSQL 17 (RLS) — bundled on each branch's main PC and in the cloud; pgvector later (cloud) |
| Jobs | Transactional outbox table. Local: an in-process worker in the API reads it. Cloud: Redis + Celery workers |
| Files | Local: files folder on the main PC. Cloud: S3-compatible storage (MinIO in dev) |
| Frontend | Vite + React + TypeScript (client-side SPA), Tailwind, shadcn/ui, TanStack Query + Router, react-i18next (RTL) |
| Desktop | Electron + electron-builder (NSIS `.exe`), electron-updater via GitHub Releases; bundles the UI, the API (packaged Python) and PostgreSQL 17 as Windows services; office PCs connect over the LAN |
| Sync | Local outbox → cloud, append-only for posted records; see ADRs 0010–0011 |
| API client | Generated from the FastAPI OpenAPI schema — never hand-write API types |
| Tests | pytest, pytest-asyncio, Hypothesis (property tests), factory_boy; Vitest + Playwright on web |
| Tooling | uv, ruff, mypy (strict on `app/`), pnpm, ESLint, Prettier, pre-commit, GitHub Actions |
| Local dev | Docker Compose (developers only; customer PCs never need Docker) |

## Repository layout

```
apps/
  api/
    app/
      core/            # config, db session, tenancy, auth, permissions, audit, outbox, idempotency, i18n
      modules/
        <module>/      # models.py, schemas.py, repository.py, service.py, router.py, posting.py, tests/
      ai/              # tool registry (later phases)
    migrations/        # Alembic
    seeds/             # industry packs, default chart of accounts, units (JSON/YAML)
  web/                 # Vite + React SPA (same build for desktop and browser)
  desktop/             # Electron: main/preload, runs bundled API + PostgreSQL, LAN setup, backups, updates (no business logic)
packages/
  api-client/          # generated TypeScript client
  config/              # product.json (product name and shared constants)
docs/
  product-concept-v2.md
  architecture.md
  domain-model.md
  adr/                 # one file per architecture decision: NNNN-title.md
infra/                 # compose files, Dockerfiles
```

Modules: `tenancy`, `identity`, `catalog` (products, variants, UOM), `pricing`, `inventory`, `parties`
(customers, suppliers), `sales`, `purchasing`, `accounting`, `tax`, `documents` (templates, PDFs), `reporting`.
A module may call another module's **service** functions, never query its tables directly.

## Conventions

- Primary keys: UUIDv7. Timestamps: `timestamptz`, stored in UTC; display in the tenant's time zone (default Asia/Dubai).
- Every tenant table: `id, tenant_id, created_at, created_by, updated_at, updated_by`; soft delete only on master
  data (`archived_at`), never on posted documents.
- Permissions are strings `resource.action` (actions: view, create, edit, delete, approve, post, export).
  Check with a FastAPI dependency `require("sales_invoice.post")`; scope by business/branch where relevant.
- Errors: typed domain exceptions mapped to RFC 7807 problem responses with a stable `code`.
- Document numbers come from the numbering-series service (per branch, document type, fiscal year), allocated by
  the branch's main database at post time (at save for quotations), gap-free for **every** document type. Each
  series has exactly one owner, so local and cloud numbers never clash (ADR 0010).
- Business configuration (industries, units, attributes, chart of accounts, templates) is seed data, not code branches.
- Keep functions small and typed; no business logic in routers or React components.

## Commands (create these in the first milestone, then keep them working)

```
make up          # start the stack with Docker Compose
make down
make migrate     # alembic upgrade head
make seed        # load seed data and a demo tenant
make test        # backend + frontend tests
make lint        # ruff, mypy, eslint, tsc
make gen-client  # regenerate packages/api-client from OpenAPI
```

## How to work in this repo

- **Plan before code.** For any milestone, write or update the plan in `docs/` first, then implement in small,
  reviewable commits.
- **Stop at milestone boundaries** and summarise: what was built, how to run it, test results, open questions.
- **Ask, don't guess,** on accounting treatment, tax rules or anything that changes money. Write the question
  into `docs/open-questions.md` and use a clearly marked, configurable default meanwhile.
- **Record decisions** as ADRs in `docs/adr/` when choosing between real alternatives.
- **Tests come with the code:** unit tests for services, property tests for UOM conversion and ledger balancing,
  API tests for permissions, and a cross-tenant read test for every new tenant table.
- Run `make lint` and `make test` before declaring a task done. Never mark work complete with failing tests.
- Don't add dependencies without a one-line reason in the commit message.

## Glossary

- **Tenant**: a customer account. Holds one or more **businesses** (legal entities with a TRN), each with
  **branches** and **warehouses**.
- **UOM**: unit of measure. Each product has a base (inventory) unit and optional purchase/sales units with factors.
- **GL**: general ledger. **Voucher**: an accounting document (payment, receipt, journal, contra, credit/debit note).
- **PDC**: post-dated cheque — received or issued, tracked until cleared or bounced.
- **TRN**: UAE tax registration number. **FTA**: Federal Tax Authority.
- **ASP**: accredited e-invoicing service provider. **PINT-AE**: the UAE structured e-invoice format (Peppol).
- **Bill-wise**: settling payments against specific invoices, as in Tally.
- **Main PC**: the branch PC that runs the bundled API and PostgreSQL; other office PCs are clients over the LAN.
- **Item Transfer**: moves quantity from one item to another at the source's average cost (Tally's stock journal).
