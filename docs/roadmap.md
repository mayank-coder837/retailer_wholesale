# Roadmap — Phase 0 and Phase 1

Phase 0 builds a foundation where every later feature inherits tenancy, permissions, audit and idempotency for
free; Phase 1 delivers the trader MVP and ends with one end-to-end acceptance test. Each milestone stops for
review with: what was built, how to run it, test results, open questions.

**Before M0.1** (founder's PC): Docker Desktop (needs WSL 2), `make`, `uv`, Python 3.12, `pnpm` — see
`open-questions.md` T1.

## Phase 0 — Foundation

| Milestone | Scope | Acceptance criteria | Proving tests |
| --- | --- | --- | --- |
| **M0.1 Repo and tooling** | Monorepo layout from `CLAUDE.md`; Docker Compose (Postgres 16, Redis, MinIO, api, worker, web); FastAPI skeleton with `/health` and settings; Alembic; Next.js with next-intl (en/ar, RTL switch), Tailwind, shadcn/ui; **`apps/desktop` Electron shell** (moves the current Electron scaffold there) that opens the web UI and builds an unsigned NSIS `.exe`; Makefile; pre-commit; GitHub Actions (lint, types, tests, desktop build on tag); OpenAPI → `packages/api-client` | `make up` starts everything; `/health` OK; web shows en/ar with RTL; `make gen-client` produces a typed client; `.exe` installs and opens the app; CI green | API health test; Vitest smoke; Playwright: home renders in en and ar (`dir="rtl"`); CI job builds installer artifact |
| **M0.2 Tenancy and auth** | Tenants, businesses, branches, warehouses; users, memberships; argon2 login, 15-min access + rotating refresh (web cookie, desktop safeStorage); tenant-context middleware (`SET LOCAL app.tenant_id`); RLS policies; `assert_tenant_isolated` helper on every tenant table | Two tenants seeded; neither can see the other's rows through any endpoint | Isolation test per tenant table; refresh rotation + reuse revokes family; no-context query returns 0 rows; CI check: every `tenant_id` table has policy + test |
| **M0.3 Roles and permissions** | Permission registry (`resource.action`); default roles (Owner, Manager, Salesperson, Warehouse, Accountant, Cashier); branch/business scoping; `require()` dependency; roles admin API + simple roles screen | A Salesperson scoped to Branch A cannot read Branch B documents or post vouchers | API permission matrix test (role × endpoint); scope tests |
| **M0.4 Platform services** | Generic audit log (`via_ai`, `on_behalf_of`); numbering series (gap-free option); outbox + Celery dispatcher; idempotency middleware; attachments on S3; settings store; industry-pack loader (general trading, textile, hardware, electronics, grocery) | Replayed write returns identical response; numbers stay gap-free under concurrency and rollback; packs apply idempotently | Idempotency replay/conflict tests; concurrent numbering test (no gaps, no duplicates); outbox at-least-once test; audit old→new test; pack re-apply test |
| **M0.5 App shell** | Login; tenant/business switcher; sidebar; dashboard placeholder with concept KPI cards; onboarding wizard skeleton (business details, industry, VAT/TRN, currency); English + Arabic; desktop shortcuts wired (F5/F6/F8/F9 open placeholders) | A new user signs up, completes onboarding, lands on the dashboard in either language, on web and desktop | Playwright: signup → onboarding → dashboard (en, ar); desktop smoke test launches packaged app |

**Phase 0 gate:** `make up && make migrate && make seed` gives a working demo tenant, all tests pass in CI, and
the cross-tenant tests cover every table.

## Phase 1 — Trader MVP

| Milestone | Scope | Acceptance criteria | Proving tests |
| --- | --- | --- | --- |
| **M1.1 UOM engine** | Unit families, units, standard conversions, product units, conversion at entry | Roll/meter and carton/piece conversions exact; lines store entered + base qty | Hypothesis: round-trip conversions lose nothing at 6 dp; factor > 0; rounding to unit decimals |
| **M1.2 Catalog** | Products, variants, attributes, barcodes (per unit), categories, brands, custom fields | Textile pack creates a fabric with colour/width variants and roll/meter units; barcode scan finds variant + unit | Custom-field validation tests; barcode uniqueness per tenant; isolation tests |
| **M1.3 Pricing engine** | Price lists, groups, assignments, quantity breaks, promotions, min price | Engine returns price + `price_source` following the priority order | Table-driven tests for each priority level; min-price guard + override permission |
| **M1.4 Parties + credit control** | Party with roles, addresses, TRN, customer/supplier profiles, credit limit/days, warn/block | Over-limit order warns or blocks per policy; override audited | Credit check tests (limit, overdue days, override) |
| **M1.5 Accounting core** | Chart of accounts (industry template), account roles, journal, vouchers (payment, receipt, contra, journal, expense, credit/debit note), bills + bill-wise allocation, fiscal years/periods + lock | Unbalanced entry impossible; locked period rejects posting without override | DB trigger test (unbalanced commit fails); Hypothesis: random balanced vouchers keep TB = 0; immutability tests |
| **M1.6 Inventory** | Stock ledger, FIFO + moving average, batches/expiry, transfers, adjustments, cached balances, recost job | Stock value = inventory account after any sequence of moves | Hypothesis: random move sequences → ledger value = GL inventory; FIFO layer tests; negative-stock block |
| **M1.7 Purchasing** | PO → goods receipt → bill, partial receipts, returns, posting | Three-step flow posts per rules 6–8; GRNI clears to zero | Posting tests per rule; partial receipt tests |
| **M1.8 Sales** | Quotation → order → delivery → invoice → receipt, proforma, returns, credit checks, posting | Full flow posts per rules 1–5; ageing correct | Posting tests per rule; fulfilment-quantity tests; ageing buckets |
| **M1.9 PDC management** | Received/issued cheques, maturity calendar, deposit/clear/bounce/return | Each transition posts per rules 14–20; bounce re-opens bills | State-machine tests incl. illegal transitions |
| **M1.10 VAT tax invoices** | Tax codes, per-line tax, tax invoice vs simplified, en/ar HTML templates → PDF, VAT-return box mapping (report draft) | Invoice PDF shows TRNs, Arabic, AED totals; VAT return draft drills down to documents | Tax rounding tests; template snapshot tests (en/ar); box mapping tests |
| **M1.11 Reports** | Trial balance, P&L, balance sheet, AR/AP ageing, stock balance + valuation, day book, party ledger | Reports read only from ledgers; TB balances | Report tests against hand-computed fixtures; semantic metrics shared |
| **M1.12 Import wizards** | Excel/Tally import of masters, opening balances, outstanding bills, stock; reconciliation report | Imported TB matches source; `opening_balance_equity` = 0 | Fixture imports (Excel, Tally XML); reconciliation tests; idempotent re-import |
| **M1.13 AI tool registry** | Typed tools with permission + risk tier, audit of every call, setup assistant, chat-to-voucher drafts | "Received 5,000 cash from Ali Traders against INV-1093" → draft receipt, allocated, awaiting confirm | Tool permission tests; eval set (en/ar) with expected tool calls in CI; `via_ai` audit test |

**Phase 1 gate (acceptance test, automated end to end):** a demo trader buys fabric by the roll, sells it by the
meter on credit, receives a PDC, deposits and clears it — and at the end the trial balance balances, stock value
equals the inventory account, and the customer's ledger shows zero outstanding.
