# Phase 2 build plan (founder: "all the basic features first, questions at the end")

Order is by value to the beachhead customer (UAE trading and wholesale, moving off Tally/Excel/WhatsApp). Each item is
one milestone: small commits, tests with the code, pushed as it lands. Anything that needs a founder answer is written
to `docs/open-questions.md` with a clearly marked, configurable default and built on that default (CLAUDE.md rule).
No external account is needed for any of it: provider integrations (WhatsApp, e-invoicing ASP, payment gateways) are
built behind an interface with a recording "outbox" implementation, so the real provider is one adapter later.

**Progress (2026-11-27):** P2.1, P2.2 and P2.3 are built, tested and pushed (API 765 passed, web 149 passed).
Next: P2.4 approvals. Open questions raised so far: R20 (year-end close style), R21 (bank matching looseness).
The conflict-list screen, bank reconciliation screens and approval screens are API-first; screens wait for the
founder's Figma UI.

| # | Milestone | Scope (basic version) | Depends on |
| --- | --- | --- | --- |
| P2.1 | Dashboard figures (DONE) | One read endpoint for the six tiles (sales today, receivables, after PDCs, cheques due this week, cash and bank, low stock); tiles wired | reports, PDC |
| P2.2 | Year-end close + cash flow (DONE) | Close a fiscal year (income and expense to retained earnings, one reversible closing entry), cash-flow statement (indirect, from ledgers) | accounting |
| P2.3 | Bank reconciliation (DONE) | Import a bank statement (CSV/Excel), auto-match to ledger lines by amount, date and reference, manual match, reconciled-to-date | accounting, imports |
| P2.4 | Approval workflows | Rules (discount over X%, PO over AED Y, credit override, stock adjustment); a document waits for an approver; audited | permissions |
| P2.5 | Landed cost | Spread freight, duty and clearing onto received items by value, qty or weight; revalues stock | inventory, purchasing |
| P2.6 | Stock counts + reorder | Count sheet, variance posted as an adjustment with approval; min/max and sales-velocity reorder list to a draft PO | inventory, P2.4 |
| P2.7 | Cost centres | Tag lines with a cost centre; profit by cost centre | accounting |
| P2.8 | Multi-currency | Foreign-currency invoices and bills, rates, realised FX gain/loss at settlement, revaluation | sales, purchasing, accounting |
| P2.9 | E-invoicing (PINT-AE) | Build the structured invoice (XML), validate, queue to an ASP adapter, status; ASP not chosen so a recording adapter | VAT invoices |
| P2.10 | Retail POS | Fast sale screen, cash/card split, shift open and close, offline queue with idempotent sync, receipt print | sales, sync |
| P2.11 | Collection reminders | Overdue statement and reminder messages queued to a messaging adapter (WhatsApp/email), schedule | ageing, outbox |
| P2.12 | Accountant access + exports | Invite an external accountant; saved views; Excel/PDF export of reports; scheduled report e-mail | permissions, reports |
| P2.13 | Serial numbers, weighing-scale barcodes, proof of delivery | IMEI/serial tracking, EAN-13 price/weight barcodes, delivery photo | inventory, attachments |

Phase 3 and 4 (B2B portal, van sales, commission, budgets, public API, forecasting, marketplace) are after this list.

Then, in this order: the founder's answers to every question in `docs/open-questions.md`, fixes, his Figma UI, the
security re-check, the desktop installer and release.
