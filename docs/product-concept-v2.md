# Retail + Wholesale Business OS — Concept v2

Oct 7, 2026 · @Mayank

> Converted from `Retail + Wholesale Business OS — Concept v2.pdf` on 2026-10-10. Tables were rebuilt from
> the PDF text; arrows the PDF lost are restored as → and >. The two diagrams (system architecture, roadmap)
> are images in the PDF and are described in words here; the PDF is the source of truth for them.
>
> **Product name: SmartBiz OS** (decided 11 Oct 2026). "Business OS" below is the concept's working title.
> Where this concept differs from `decisions-round-1.md` (local-first desktop, moving-average costing, PDC memo,
> Cancel/Reverse, pricing order), the decisions file wins.

## Executive summary

v1 is a strong vision; v2 makes it buildable and sellable by adding a real accounting core, a UAE compliance
layer and a sharper first customer.

The six changes that matter most:

1. **Accounts and ledger core.** Every financial document posts to a double-entry general ledger, and every
   stock movement posts to an immutable stock ledger. Profit, VAT, receivables and stock value are read from
   the ledgers, never re-summed from invoices.
2. **AI Tally Assistant.** Tally-speed voucher entry by keyboard, chat, voice or a photo, plus a guided
   migration from Tally and Excel. Tally is the incumbent in many UAE trading companies, so this is both a
   feature and the main way customers switch.
3. **UAE compliance built in.** 5% VAT, FTA-style tax invoices, the VAT return report, and readiness for the
   UAE e-invoicing mandate (structured PINT-AE invoices sent through an Accredited Service Provider).
4. **One beachhead segment.** Launch for UAE trading and wholesale SMBs, where unit conversions, credit sales
   and post-dated cheques are daily pain. Retail POS follows.
5. **AI uses the same permissioned API as people.** No direct database access; every AI action is a draft or
   a confirmed command, logged as "by AI on behalf of user".
6. **A private B2B ordering portal before the marketplace.** Each wholesaler invites its own customers to
   order online. That seeds the network the marketplace needs.

## Review of v1

Keep the core of v1: configuration as a first-class concept, the UOM engine, the connected data chain, the
safe AI action model and the phased roadmap. The gaps below are what would hurt in production or in sales.

| Gap in v1 | Why it matters | Fix in v2 |
| --- | --- | --- |
| No general ledger; vouchers listed but not connected to accounts | Profit, cash flow and VAT reports will disagree with each other; accountants won't trust the system | Chart of accounts, double-entry journal, automatic posting rules per document type |
| No stock costing method | Profit per product is wrong without FIFO or weighted-average cost | Stock ledger with cost layers; FIFO and moving average per business; landed cost |
| Documents can be edited or deleted after posting | Breaks audit, VAT and e-invoicing rules | Draft → Posted → Cancelled states; corrections only via credit/debit notes or reversals |
| No fiscal periods or period lock | Back-dated edits silently change filed VAT returns | Fiscal years, period close and lock with an override permission |
| No local compliance | UAE buyers need VAT invoices, TRN, Arabic and e-invoicing readiness | A country compliance layer (UAE first, KSA next) |
| No data migration path | Switching cost from Tally or Excel kills adoption | Import wizards with AI column mapping and opening balances |
| Online-only POS implied | Shops lose sales when the internet drops | Offline-first POS that syncs later |
| MVP has 11 modules for every industry | Too broad to finish or sell | One segment, one workflow end to end, then widen |
| B2B network depends on users that don't exist yet | Marketplaces fail without both sides | Private ordering portal first; each wholesaler brings its own buyers |
| Audit trail only for products | Price, credit limit and payment changes need it more | Generic audit log on every business entity |

## Positioning and beachhead

Recommended first customer: UAE trading and wholesale SMBs with 2–50 staff that today run on Tally, Excel and
WhatsApp.

Think general trading companies, textile and garment traders, hardware and building-material suppliers, FMCG
distributors and electronics wholesalers. They feel every pain the concept already solves well: buy in cartons
or rolls and sell in pieces or meters, customer-specific prices, credit sales, cheques, and stock across a shop
and a warehouse.

Positioning line: *"The AI business OS for traders and wholesalers — accounts, stock and sales in one place, as
fast as Tally, as simple as WhatsApp."*

Why not retail POS first: POS is a crowded, price-driven market with hardware support costs. Wholesale
workflows are where the UOM engine, pricing rules and credit control are clear differentiators.

What wins the deal:

- Migration from Tally or Excel in an afternoon, with opening balances and stock
- VAT-correct invoices in English and Arabic, sent on WhatsApp
- Receivables and PDC (post-dated cheque) control the owner can see on a phone
- An assistant that turns "20 cartons of shoes arrived from Ali Traders" into a posted goods receipt

Pricing idea: per business per month with user tiers, plus AI usage credits; a free tier capped by invoices per
month to drive word of mouth among traders.

## New and improved features

The two additions you proposed — accounts/ledger and an AI Tally assistant — become core modules; everything
else extends the v1 modules. Phase numbers refer to the roadmap below.

### Accounts and ledger management (new core module)

| Feature | What it does | Phase |
| --- | --- | --- |
| Chart of accounts | Groups and ledgers (Assets, Liabilities, Income, Expenses, Equity) with an industry template; editable | MVP |
| Double-entry journal | Every posting balances debit = credit, enforced by the database | MVP |
| Auto-posting rules | Invoice, receipt, purchase, GRN, credit note, expense, stock adjustment each post their own journal lines | MVP |
| Party ledgers | Customer and supplier sub-ledgers with bill-wise (invoice-by-invoice) settlement, like Tally | MVP |
| Vouchers | Payment, receipt, contra, journal, sales, purchase, credit/debit note, expense — all v1 voucher types, posted to the GL | MVP |
| Cash and bank books | Multiple bank accounts, petty cash, cash in transit | MVP |
| PDC management | Post-dated cheques received and issued, maturity calendar, deposit, clear, bounce with auto-reversal | MVP |
| Bank reconciliation | Import bank statement (CSV/Excel), auto-match, manual match | Phase 2 |
| Financial statements | Trial balance, P&L, balance sheet, cash flow, day book, ledger statement | MVP (TB, P&L, BS); Phase 2 (cash flow) |
| Cost centres | Profit by branch, salesperson, project or division | Phase 2 |
| Multi-currency | Foreign-currency invoices and bills, exchange rates, realised and unrealised FX gain/loss | Phase 2 |
| Period close | Fiscal years, month lock, year-end closing entries | MVP (lock), Phase 2 (year-end) |
| Budgets | Budget vs actual by account and cost centre | Phase 3 |
| Accountant access | Invite an external accountant with read or post rights across clients | Phase 2 |

### AI Tally Assistant (new)

The goal: a Tally user should be faster here on day one, not slower.

- **Keyboard-first voucher entry.** A command bar and Tally-style shortcuts (for example F8 sales, F9 purchase,
  F5 payment, F6 receipt) so power users never touch the mouse.
- **Natural-language vouchers.** "Received 5,000 cash from Ali Traders against INV-1093" → a filled receipt
  voucher, bill-wise allocated, waiting for confirm.
- **Photo and PDF entry.** A supplier bill, bank slip or cheque photo becomes a draft purchase or receipt.
- **Voice entry** in English, Arabic, Hindi/Urdu for owners and salespeople on the move.
- **Ask your books.** "Who owes me more than 30 days?", "Why is profit lower than last month?", answered from
  ledgers with links to the source entries.
- **Tally migration.** Import masters, ledgers, opening balances, stock and outstanding bills from Tally exports
  (XML/Excel), with AI mapping and a reconciliation report proving the trial balance matches.
- **Accountant checks.** Flags unbalanced suspense entries, missing VAT, duplicate bills, unusual amounts, and
  invoices posted to the wrong period.

### Inventory

| Feature | What it does | Phase |
| --- | --- | --- |
| Stock ledger with costing | FIFO or moving-average cost layers; stock value always ties to the GL | MVP |
| Landed cost | Spread freight, customs duty and clearing charges onto received items | Phase 2 |
| Batch, serial, expiry | FEFO picking for expiry goods; IMEI/serial for electronics | MVP (batch/expiry), Phase 2 (serial) |
| Weighing-scale barcodes | Price- or weight-embedded EAN-13 for grocery | Phase 2 |
| Barcode and label printing | Generate barcodes, print shelf and carton labels | MVP |
| Stock counts | Cycle counts with a mobile scanner, variance posted with approval | Phase 2 |
| Reorder suggestions | Min/max plus sales velocity, turned into a draft PO | Phase 2 |

### Sales, credit and collections

| Feature | What it does | Phase |
| --- | --- | --- |
| Pricing engine | Rule priority: customer price list > group > quantity break > promotion > base; minimum-price guard | MVP |
| Credit control | Credit limit and days; soft warning or hard block; owner override with audit | MVP |
| Receivables ageing | 0–30, 31–60, 61–90, 90+ by customer and salesperson | MVP |
| Collection reminders | Scheduled statement and reminder messages on WhatsApp and email | Phase 2 |
| Proforma invoices | Common for advance payments in trading | MVP |
| Delivery notes | Partial deliveries, signed proof of delivery photo | Phase 2 |
| Salesperson commission | Rules by product, margin or collection | Phase 3 |
| Van and route sales | Offline mobile orders, collections and stock-on-van | Phase 3 |

### Purchasing and imports

- Supplier price history with alerts when a cost rises
- Partial receipts and backorders against a PO
- Import shipments: foreign-currency PO, landed cost, customs reference and HS codes
- Three-way match (PO → goods received → supplier bill) before payment

### Platform features

| Feature | What it does | Phase |
| --- | --- | --- |
| Document lifecycle | Draft → Approved → Posted → Cancelled; posted documents never change | MVP |
| Numbering series | Per branch, document type and fiscal year, gap-free where tax law needs it | MVP |
| Approval workflows | Configurable approvals (discount above X%, PO above AED Y, credit override) | Phase 2 |
| Import wizards | Excel/CSV/Tally import for every master with AI column mapping | MVP |
| Generic audit log | Who, what, when, old → new, and whether AI did it on someone's behalf | MVP |
| Attachments | Files on any record (bills, cheques, delivery photos) | MVP |
| Saved views and exports | Filters saved per user; Excel/PDF export; scheduled email reports | Phase 2 |
| Integrations | WhatsApp Business, payment gateways and BNPL, e-commerce stores, couriers, accounting export | Phase 2–3 |
| Public API and webhooks | For partners and larger customers | Phase 3 |
| Mobile | Responsive web + PWA first; native owner app later | MVP (PWA) |

### B2B ordering portal (stepping stone to the marketplace)

Each wholesaler gets a private portal where its own customers log in, see their own prices and credit, reorder
past items and track deliveries. Orders arrive as sales orders. Once many wholesalers are on the platform, a
retailer that buys from three of them already has an account — that is the seed of the Phase 4 network.

## UAE compliance layer

The biggest market opening: every UAE business under AED 50 million revenue must appoint an e-invoicing
provider by 31 March 2027 and go live by 1 July 2027 — exactly the SMB traders this product targets (Wafeq,
updated Sep 2026).

| Wave | Who | Appoint ASP by | Mandatory from |
| --- | --- | --- | --- |
| Wave 1 | Revenue ≥ AED 50M | 30 Oct 2026 | 1 Jan 2027 |
| Wave 2 | Revenue < AED 50M | 31 Mar 2027 | 1 Jul 2027 |
| Wave 3 | Government entities | 31 Mar 2027 | 1 Oct 2027 |

B2B and B2G invoices are in scope, including free-zone businesses; B2C retail sales are currently out of scope.
Invoices must be structured PINT-AE (Peppol UBL) data sent through an Accredited Service Provider, not PDFs.
Late implementation is fined AED 5,000 per month.

What the product must do (design now, build by Phase 2):

- **Tax engine.** Standard 5%, zero-rated, exempt and out-of-scope; reverse charge on imports; designated-zone
  rules; tax per line, rounding per FTA rules.
- **Tax invoice data.** Supplier and buyer TRN, tax invoice vs simplified invoice, AED totals for
  foreign-currency invoices.
- **VAT return report.** Box-by-box VAT 201 with drill-down to every source document; emirate split for sales.
- **E-invoicing adapter.** Every posted sales invoice and credit note is mapped to PINT-AE fields and sent
  through a partner ASP; status (sent, validated, rejected) is stored on the document. Build it as a pluggable
  adapter so KSA (ZATCA) or other countries can follow.
- **Arabic and RTL.** Bilingual invoices and a full Arabic interface, designed in from day one.
- **Corporate tax support.** Clean P&L and adjustments export for the 9% UAE corporate tax filing.
- **Data residency and privacy.** Host in a UAE cloud region; meet the UAE data protection law (PDPL);
  encrypted backups.

**Partner, don't become an ASP.** Becoming an accredited provider is a regulated business of its own. Integrate
with one or two ASPs first and market "e-invoicing ready" — the March 2027 deadline is the sales hook.

## AI Copilot v2

The AI is a user with delegated permissions: it calls the same API commands a person would, can never exceed
the permissions of the person it acts for, and every action it takes is a draft or needs a confirm.

Five design rules:

1. **Tools, not SQL.** The backend exposes typed tools such as `create_draft_invoice`, `post_receipt`,
   `get_receivables_ageing`. No free-form database access.
2. **Semantic metrics layer.** "Revenue", "gross profit", "overdue" are defined once in code and used by
   reports, dashboard and AI alike, so the AI never invents its own formula.
3. **Risk tiers.** Read = no confirm; create draft = no confirm; post, delete, change price, refund, override
   credit = explicit confirm plus role check.
4. **Full trace.** Each AI action stores the prompt, the tools called, the result and the confirming user in the
   audit log.
5. **Evaluated before release.** A test set of real requests (English and Arabic) with expected tool calls runs
   in CI; cost per tenant is metered and capped.

Where the AI shows up:

| Surface | Example | Phase |
| --- | --- | --- |
| Setup assistant | "I sell fabric by roll and meter" → configures units, conversions, attributes | MVP |
| Import mapping | Maps messy Excel/Tally columns to fields and flags bad rows | MVP |
| AI Tally Assistant | Chat, voice or photo → draft voucher | MVP (chat), Phase 2 (voice, photo) |
| Ask your business | "Top 10 products by margin this year" with linked source data | Phase 2 |
| Daily brief | Morning summary of sales, cash, overdue, low stock, cheques due | Phase 2 |
| WhatsApp assistant | Owner asks or approves from WhatsApp; salesperson sends an order by voice note | Phase 3 |
| Anomaly alerts | Selling below cost, duplicate bills, unusual discounts, supplier price jumps | Phase 3 |
| Forecasting | Reorder quantities from sales velocity and seasonality (Ramadan, Eid, back to school) | Phase 3 |

Languages: English, Arabic, Hindi/Urdu and Malayalam cover most UAE traders and their staff; the AI should
answer in the language it is asked in.

## Architecture and engineering principles

Build a modular monolith on the v1 stack, with ledgers and tenant isolation enforced in the database rather than
in application code.

> **Diagram in PDF: "System architecture · 5 layers"** (image, not reproduced). Its caption: every client,
> including the AI agent, goes through the same API, so permissions and audit are applied once. The detailed
> layering is written out in `docs/architecture.md`.

Principles:

1. **Modular monolith first.** One FastAPI service, modules with explicit interfaces and no reads of another
   module's tables. Split out a service only when it needs independent scaling.
2. **Tenant isolation.** `tenant_id` on every row, Postgres row-level security, and CI tests that try to read
   across tenants. Tenant → business → branch → warehouse hierarchy.
3. **Money and quantity types.** Decimal everywhere, never float. Quantities are stored in the base unit plus
   the entered unit and conversion factor, so history survives a later change to a conversion.
4. **Posted means permanent.** Documents move Draft → Posted → Cancelled; corrections are reversals or
   credit/debit notes. Journal lines must balance, enforced by a database constraint.
5. **Events through an outbox.** Posting a document writes outbox events in the same transaction; workers send
   e-invoices, notifications and webhooks.
6. **Idempotency.** Every write accepts an idempotency key, which offline POS sync and AI retries depend on.
7. **Configuration as data.** Industry packs are seed data (units, attributes, chart of accounts, templates),
   not code branches.
8. **Documents as templates.** Invoices render from HTML templates to PDF; tenants edit templates, not code.
9. **Built for Arabic.** i18n and RTL from the first screen; per-tenant time zone, currency and number formats.
10. **API-first.** OpenAPI schema generates the TypeScript client; the frontend never hand-writes API types.

### Stack (v1 with refinements)

| Layer | Choice |
| --- | --- |
| Frontend | Next.js + TypeScript, Tailwind, shadcn/ui, TanStack Query, next-intl (RTL) |
| Offline POS | PWA with IndexedDB queue and idempotent sync |
| Backend | FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic |
| Database | PostgreSQL 16 with row-level security; pgvector for AI search |
| Jobs | Redis + Celery (or Dramatiq), transactional outbox |
| Files and PDFs | S3-compatible storage; HTML-to-PDF rendering |
| AI | LLM with tool calling behind an internal tool registry; per-tenant metering |
| Dev and CI | Docker Compose, GitHub Actions, pytest + Hypothesis, Playwright |

## Revised roadmap

Foundation, then a narrow trader MVP with accounting built in, then compliance — Phase 2 has a hard external
deadline: SMB customers need e-invoicing by 1 July 2027.

> **Diagram in PDF: "Roadmap · 5 phases, 4 gates"** (image, not reproduced). Each phase ends at a gate; nothing
> in the next phase starts until the gate's test is met. From the text of this document the phases are:
> Phase 0 foundation · Phase 1 (MVP) trader core with accounting · Phase 2 compliance (VAT return,
> e-invoicing) and retail POS · Phase 3 B2B ordering portal and wider AI · Phase 4 B2B network/marketplace.
> Gate criteria are only in the image — see the PDF.

What moved compared with v1: accounting moved from "vouchers" into the MVP core; POS moved to Phase 2 because
the beachhead is wholesale; the B2B ordering portal was added in Phase 3 as the bridge to the Phase 4 network;
e-invoicing and the VAT return were added to Phase 2.

## Risks and open decisions

| Risk | Mitigation |
| --- | --- |
| Scope creep: an ERP is never finished | Ship one segment end to end; every feature must serve the beachhead's daily workflow |
| Accounting bugs destroy trust | Ledger invariants enforced in the database, property-based tests, an accountant advisor reviewing posting rules |
| Strong incumbents (Tally, Zoho Books, Odoo, local players such as Wafeq) | Win on wholesale depth (UOM, pricing, PDC), AI speed and migration, not on feature count |
| E-invoicing deadline arrives before the product | Prioritise the ASP integration in Phase 2; partner rather than self-build |
| AI cost and errors | Drafts by default, metering per tenant, cheap models for routine extraction |
| Multi-tenant data leak | Postgres row-level security plus tenant tests in CI; no cross-tenant queries outside admin |

Decisions for you:

- Confirm the beachhead: UAE trading/wholesale SMBs, or another segment
- Is "AI Tally assistant" mainly Tally-style entry and migration, or a two-way sync with customers who keep Tally?
- Which ASP partner(s) to approach for e-invoicing
- Team: solo founder with Claude Code, or hire a backend lead and an accountant advisor
- Product name and domain
- Pricing tiers and the free-tier limit
