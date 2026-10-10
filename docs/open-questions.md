# Open questions

Decisions needed from the founder (F), an accountant (A), or both. Until answered, the system uses the marked
default, made configurable where it touches money.

## Founder — product and business

| # | Question | Default meanwhile |
| --- | --- | --- |
| F1 | Confirm the beachhead: UAE trading/wholesale SMBs, or another segment? | UAE trading/wholesale |
| F2 | "AI Tally assistant": Tally-style entry + one-time migration, or two-way sync with customers who keep Tally? | Entry + migration only |
| F3 | Which ASP partner(s) to approach for e-invoicing (Phase 2, deadline 1 Jul 2027)? | Pluggable adapter, no partner yet |
| F4 | Team: solo founder with Claude Code, or hire a backend lead and an accountant advisor? | Solo; posting rules flagged for review |
| F5 | Product name and domain (needed for the desktop app's name, installer and `app.<domain>` URL) | "Business OS", placeholder domain |
| F6 | Pricing tiers and free-tier limit | Not built in Phase 0–1 |
| F7 | Hosting provider in a UAE region (Azure UAE North, AWS me-central-1, Oracle Dubai, local)? | Decide before first customer data |

## Desktop app

| # | Question | Default meanwhile |
| --- | --- | --- |
| D1 | Should the Electron shell load the hosted web app URL (always current, needs internet) or bundle a static build of the UI (works offline-first, but a static Next.js export loses server rendering)? | Load hosted URL + offline page (ADR 0008) |
| D2 | How much must work offline in Phases 0–1? Only queued drafts, or also viewing recent data? | Queued drafts only |
| D3 | Code-signing certificate before first customer release (Azure Trusted Signing ~$10/month)? | Unsigned in development |
| D4 | Keep a browser/web version for customers too, or desktop-only for users (web only for phones/portal)? | Both, from one codebase |

## Accountant — money, tax and posting

| # | Question | Default meanwhile |
| --- | --- | --- |
| A1 | Confirm every rule in `posting-rules.md` (all marked "default — to be confirmed") | As written |
| A2 | PDC received: reduce the customer balance at receipt (PDC asset account) or keep as memo until maturity like Tally? | Reduce at receipt (rules 14–17) |
| A3 | VAT rounding: per line or per invoice; half-up or another FTA-accepted method? | Per line, half-up, 2 dp |
| A4 | Purchase price variance when the bill differs from the goods receipt: to inventory (if still in stock) or to a variance account? | Variance account |
| A5 | Cost pool per business (warehouses share cost) or per warehouse? | Per business |
| A6 | Allow negative stock? If yes, at which cost, and how is it trued up? | Blocked |
| A7 | Cancelling a posted document: reversal dated on the cancel date or the original date (if period open)? | Cancel date |
| A8 | Delivery note before invoice: recognise COGS at delivery (default) or at invoice via a "goods delivered not invoiced" account? | COGS at delivery |
| A9 | Do promotions stack with customer price lists, or does the first matching rule win? | First match wins |
| A10 | VAT 201 box mapping (boxes 1a–1g by emirate, 2–11): confirm the rule for each tax code and document type | Draft mapping in seed data |
| A11 | Default chart of accounts per industry pack: review before M1.5 | Generic UAE trading chart |
| A12 | Gap-free numbering: which document types legally need it (tax invoices, credit notes)? | Tax invoices and credit notes gap-free |

## Technical setup

| # | Question | Default meanwhile |
| --- | --- | --- |
| T1 | The PC lacks Docker Desktop (needs WSL 2), `make`, `uv` and `pnpm`; Python is 3.13, spec says 3.12. Install these before M0.1? (`winget install Docker.DockerDesktop GnuWin32.Make astral-sh.uv pnpm.pnpm Python.Python.3.12`) | Install before M0.1 |
| T2 | Celery or Dramatiq for jobs? (`CLAUDE.md` says Celery; Concept v2 allows either) | Celery |
| T3 | LLM provider for M1.13 and data-residency limits on sending business data to it | Decide before M1.13 |
| T4 | Keep `docs/kickoff-prompt.md` in the repo for history? | Not added |
