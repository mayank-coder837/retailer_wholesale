# Founder Decisions — Round 1

Oct 11, 2026 · @Mayank

Answers to the 27 questions in `docs/open-questions.md`, written so Claude Code can apply them directly.

## What changes from the defaults

13 answers change the design. The other 14 keep the default or are deferred.

| # | Decision | What Claude Code must change |
| --- | --- | --- |
| D1 | Local-first like Tally: full database on the office PC, syncing to the cloud; everything works offline (D2) | Rewrite ADR 0008; desktop app bundles UI, API and local PostgreSQL; new sync ADR |
| F7 | Local and free hosting for now | Develop on local Docker; free cloud tier for pilots; UAE region before real customer data |
| A2 | PDC received stays a memo until it clears (Tally style) | Rewrite posting rules 14–17 |
| A3 | Show VAT on every line and on the total; round each line to 2 decimals | One fixed method, no setting; invoice templates show line VAT and total VAT |
| A5 | One moving-average cost across all warehouses | Moving average only in the MVP; FIFO dropped for now |
| A6 | Negative stock allowed; the user fixes it with an item-to-item transfer | New setting, cost rule, Item Transfer document, negative-stock report |
| A7 | Offer both Cancel and Reverse; Cancel frees the number for a replacement invoice; both remove the bill's effects everywhere | Two actions with different dating and numbering rules |
| A8 | Cost of goods sold at invoice; delivery note shows no prices | Goods-delivered-not-invoiced account; price-free delivery note template |
| A9 | Suggest the last price sold to this customer first; show their purchase history | New first step in the pricing engine; customer history panel |
| A10 | Price entry: VAT-exclusive, VAT-inclusive or no VAT | Line price modes; VAT 201 mapping from this doc |
| A12 | Gap-free numbering on every document and voucher type | Apply to all series, not only tax invoices and credit notes |
| T1 | Install all missing tools before M0.1 | Setup checklist runs first |

## Product and business (F1–F7)

| # | Question | Decision | Note for Claude Code |
| --- | --- | --- | --- |
| F1 | First target customer | UAE trading and wholesale SMBs (default) | No change |
| F2 | AI Tally assistant scope | Tally-style fast entry plus one-time migration (default) | No two-way Tally sync |
| F3 | E-invoicing provider (ASP) | Not needed now | Keep the pluggable adapter. Revisit by Jan 2027: SMB customers must appoint an ASP by 31 Mar 2027 and go live by 1 Jul 2027 |
| F4 | Team | Solo founder with Claude Code | Posting rules stay flagged "to be confirmed" until an accountant reviews them |
| F5 | Name and domain | Product name: **SmartBiz OS**; no domain yet | Use a config value for the name so it can change in one place (default "SmartBiz OS") |
| F6 | Pricing | Not yet | Nothing built in Phases 0–1 |
| F7 | Hosting | Local and free for now | Develop on local Docker. For pilots, use a free cloud tier. Move to a UAE-region host before storing real customer data |

## Desktop app (D1–D4)

| # | Question | Decision |
| --- | --- | --- |
| D1 | Load the hosted site or bundle the app? | **Local-first, like Tally:** a full database on the office PC, syncing to the cloud |
| D2 | What works offline? | Everything in the office: entry, posting, numbering, printing, stock and reports. Only internet features wait for a connection |
| D3 | Code-signing certificate | Unsigned during development (default); buy before the first customer release |
| D4 | Browser version too? | Yes, from one codebase (default); browser and phone use the cloud copy |

**How D1 should be built (local-first):**

- **One main PC per branch.** It holds the branch's database, like Tally's data server. Other PCs in the office connect to it over the local network. A one-PC shop just uses that machine.
- **Same code locally and in the cloud.** The desktop app bundles the user interface, the API and a local PostgreSQL database. PostgreSQL on both sides keeps the ledger constraints, exact decimals and migrations identical; SQLite is lighter but has no true decimal type, which matters for money.
- **The UI runs client-side** (Next.js static export or Vite + React) and talks to whichever API it is pointed at: the local one in the office, the cloud one in a browser.
- **Works fully offline.** Entry, posting, numbering, printing, stock and reports all run against the local database.
- **Cloud sync.** Every change goes into a local outbox and is pushed to the cloud when online; changes from other branches and the web are pulled down. Posted documents never change, so they sync as append-only records. For master data (products, customers, prices) edited in two places, an ADR must set the rule: head office wins, or last edit wins with the conflict logged.
- **Online features** go through the cloud: AI assistant, WhatsApp, e-invoicing, B2B portal, phone and browser access, and multi-branch reports. Offline, they queue (e-invoices, messages) or show "needs internet".
- **Backups and updates.** Automatic local backups plus the cloud copy. Each update runs database migrations on the customer's PC, so it takes a backup first and can roll back.
- **Build order (confirmed):** build and pilot Phases 0–1 as a local-only app (no cloud server needed, which also fits F7), then add cloud sync, browser access and multi-branch in a later milestone.

## Accounting, tax and posting (A1–A12)

| # | Question | Decision |
| --- | --- | --- |
| A1 | Posting rules | As written (default), except where A2, A6, A7 and A8 change them |
| A2 | PDC received | Memo until it clears, like Tally |
| A3 | VAT rounding | VAT shown on each line and on the total; per-line rounding, 2 decimals |
| A4 | Price difference between bill and goods receipt | Variance account (default) |
| A5 | Cost pool | One moving-average cost per item across all warehouses |
| A6 | Negative stock | Allowed; the user corrects it with an item-to-item transfer |
| A7 | Cancelling a posted document | Both Cancel and Reverse available; Cancel frees the number for reuse; both remove the bill everywhere |
| A8 | When cost of goods sold is booked | At invoice; the delivery note carries no prices |
| A9 | Pricing | Last price sold to this customer first, with their full purchase history shown |
| A10 | VAT entry and VAT 201 | Prices entered VAT-exclusive, VAT-inclusive or with no VAT; box mapping below |
| A11 | Chart of accounts per industry | Generic UAE trading chart (default) |
| A12 | Gap-free numbering | Every document and voucher type |

### Rules to build

**A2 — Post-dated cheques (memo until cleared)**

- Receiving a PDC makes no ledger entry. It goes into a PDC register with party, cheque number, bank, amount, maturity date and the invoices it is meant for.
- On clearance, post Dr Bank / Cr Customer on the clearance date and settle the linked invoices bill-wise.
- A bounced PDC changes only its status in the register (plus any bank charge the user enters).
- PDCs issued to suppliers work the same way: Dr Supplier / Cr Bank on clearance.
- Customer statements and the ageing report show two figures: outstanding, and outstanding after PDCs in hand. Credit control uses the ledger balance and shows PDCs in hand beside it.

**A3 — VAT rounding**

- Every invoice, credit note and purchase document shows the VAT amount on each line and the total VAT at the bottom.
- Each line's VAT is rounded to 2 decimals (fils), half-up. The total VAT is the sum of the rounded line amounts, so the lines always add up exactly to the total. The FTA says to round tax to the nearest fils using mathematical rounding ([VATP006 summary](https://www.mbgcorp.com/ae/insights/fta-public-clarification-vatp006-tax-invoices/)).
- This is one fixed method for every business; there is no rounding setting.

**A5 — Costing**

- One moving weighted-average cost per item for the whole business. Transfers between warehouses move stock at the current average and do not change it.
- FIFO is dropped from the MVP; the costing ADR should be updated.

**A6 — Negative stock**

- A per-business setting, on by default. Selling below zero shows a warning but is not blocked.
- Stock issued below zero is costed at the current average cost (or the last purchase cost if there is no average yet).
- When stock arrives, the negative quantity is revalued at the incoming cost, and the difference goes to a "stock cost adjustment" account under cost of sales.
- New document: **Item Transfer** (Tally calls it a stock journal). It moves quantity from one item to another, for example when size 41 was sold as size 42, at the source item's average cost.
- A negative-stock report lists every item below zero, so the user can fix them. Fixing them is the user's responsibility.

**A7 — Cancel and Reverse**

|  | Cancel | Reverse |
| --- | --- | --- |
| When to use | A mistake in an open period | The period is locked, the VAT return is filed, or the customer already has the invoice |
| Number | Number is released so the corrected document can be issued with the same number; if no replacement is made, it stays marked CANCELLED, so there is no gap | Original keeps its number; the reversal (for example a credit note) gets its own number |
| Date of effect | Reversing entries dated on the original date, so the effect vanishes from that period | Reversal dated on the day the user chooses (default today) |
| Allowed when | Period open, VAT return not filed, and not yet sent as an e-invoice | Always, with permission |

Both actions recall the bill from everywhere: ledger, stock, VAT reports, customer outstanding and every report. Payments allocated to it become unallocated advances, and linked delivery notes return to "not invoiced". Both need a permission and a reason, and both are written to the audit log. Once e-invoicing is live, an invoice already sent through the ASP can only be reversed with a credit note, not cancelled.

**Re-using a cancelled number:**

- Cancel works on any document posted by mistake: sales invoices, purchase bills, vouchers and the rest. It removes the effect from the customer or supplier ledger, stock, VAT and every report.
- The corrected document can then be issued with the same number. The app offers the freed number when the next document in that series is created, or the user picks it.
- The audit log keeps the cancelled version in full (contents, who, when, why), and the replacement is marked "reissued after cancel". Reports show the number once, but nothing is lost.
- If the original was already printed, emailed or sent on WhatsApp, the app warns before reusing the number, because the customer may have recorded the first version.
- Once e-invoicing is live (1 Jul 2027 for SMBs), an invoice already sent through the ASP cannot be cancelled or its number reused; only a credit note works. An accountant should confirm this rule before go-live.

**A8 — Delivery note and cost of goods sold**

- The delivery note shows quantities only; its print template has no prices.
- Posting a delivery note moves stock out at average cost: Dr Goods delivered not invoiced / Cr Inventory.
- The invoice posts the sale and VAT, and moves the cost: Dr Cost of goods sold / Cr Goods delivered not invoiced.
- An invoice with no delivery note posts Dr Cost of goods sold / Cr Inventory directly.

**A9 — Pricing**

1. Last price sold to this customer for this item, in the same unit (if the last sale was in another unit, convert by the unit factor and mark it as converted)
2. Customer price list
3. Customer group price list
4. Quantity break
5. Promotion
6. Base price

The minimum-price guard still warns. A **customer history panel** on every sales document lists all items previously supplied to that customer, with date, invoice number, quantity, unit and price, newest first. It is searchable, and one click adds an item to the document. Each customer has their own history.

**A10 — Price entry modes**

- Each sales and purchase document has a price mode: VAT-exclusive, VAT-inclusive, or no VAT.
- "No VAT" must still record the reason (zero-rated, exempt or out of scope), because each goes to a different VAT 201 box. The tax code defaults from the product and customer.

**A12 — Gap-free numbering everywhere**

- Every series is gap-free: quotations, sales orders, delivery notes, tax invoices, credit and debit notes, receipt and payment vouchers, contra vouchers, journals, adjustments, item transfers and purchase documents.
- Numbers are given by the branch's main database when a document is saved (quotations) or posted (everything else), per branch series and fiscal year. Each branch has its own series, so numbering works offline and two branches never clash. Drafts get no number.
- A cancelled document's number can be reused for its replacement; if it isn't, the number stays marked CANCELLED, so there is never a gap (A7).

## VAT 201 box mapping (A10)

Each line's tax code decides its box. Whether the price was typed with or without VAT only changes how the net and VAT amounts are calculated, not which box they go in. Sources: [Business Dubai VAT 201 guide](https://www.businessdubai.ae/blogs/uae-vat-return-filing), [Zoho VAT return guide](https://www.zoho.com/ae/books/vat/guides/vat-return.html).

| Tax code | Used for | VAT 201 box |
| --- | --- | --- |
| SR — standard 5% (sales) | Normal local sales | Box 1, net amount and VAT, in the emirate of **your branch** that made the supply, not the customer's emirate |
| ZR — zero-rated (sales) | Qualifying exports and other zero-rated supplies | Box 4, net amount only |
| EX — exempt (sales) | Exempt supplies (rare for traders) | Box 5, net amount only |
| OS — out of scope | Not a supply for VAT | Not reported |
| SR — standard 5% (purchases, recoverable) | Normal business purchases from VAT-registered suppliers | Box 9, net amount and recoverable VAT |
| SR-B — 5%, input VAT blocked | Entertainment and other blocked costs | Not in Box 9; the VAT becomes part of the expense |
| ZR / EX / OS (purchases) | Zero-rated, exempt or out-of-scope purchases | Not reported |
| RC-S — reverse charge, imported services | Services bought from outside the UAE | Box 3 (VAT due) and Box 10 (VAT recovered) |
| RC-G — reverse charge, imported goods | Goods imported through UAE Customs | Box 6 is pre-filled by Customs; the app keeps these records to check it, and the recoverable VAT goes in Box 10 |
| Customs correction | Box 6 figures that are wrong or missing | Box 7 (manual entry) |

**Rules for the VAT return report:**

- Sales credit notes reduce the box of the original sale in the period the credit note is issued. Supplier credit notes reduce Box 9 the same way.
- Each branch needs an emirate field, because Box 1 is split by emirate. The FTA guidance says to use the emirate of the establishment most connected to the supply. Using the customer's emirate is described as the most common mistake.
- Boxes 8 and 11–14 are totals calculated by the report. Box 2 (tourist refunds) does not apply.
- The Adjustments column (bad-debt relief, annual apportionment, capital assets) is entered manually and can wait until Phase 2.
- Every figure drills down to its source documents.

The sources disagree on whether Box 1 is labelled 1a–1g; both describe one line per emirate. Claude Code should check the current form on the FTA EmaraTax portal before building the report, and an accountant should review this table before Phase 2.

## Technical setup (T1–T4)

| # | Question | Decision |
| --- | --- | --- |
| T1 | Install missing tools | Install everything before M0.1: Docker Desktop (with WSL 2), make, uv, pnpm and Python 3.12 |
| T2 | Background jobs | Celery (default) |
| T3 | AI provider for M1.13 | Decide later, before M1.13 |
| T4 | Keep the kickoff prompt in the repo | No |

## Confirmed interpretations

The founder confirmed every item below on 11 Oct 2026. Items that mention an accountant still need that review.

- [x] **D1:** Confirmed: a full local database on the office PC, like Tally, syncing to the cloud. Phases 0–1 are built as a local-only app; cloud sync comes in a later milestone.
- [x] **A3:** Confirmed: invoices show VAT on each line and on the total, using per-line rounding. No rounding setting.
- [x] **A7:** Confirmed: cancelling a document posted by mistake removes it from everywhere (customer or supplier ledger, stock, VAT, reports), and its number can be reused for the corrected document.
- [x] **A9:** Promotions vs price lists wasn't answered directly, so "first match wins" applies after the last-price rule.
- [x] **A10:** "Excl / none / incl" means the three price entry modes. Box mapping was researched for you and needs an accountant's review.
- [x] **A12:** "Credit voucher" means the receipt voucher (money received). "CC voucher" means the contra voucher (cash to bank, bank to bank). Correct these if you meant something else, for example a credit-card voucher.
- [x] **A6:** Negative stock is allowed by default, and cost differences go to a "stock cost adjustment" account. Worth an accountant's check.
- [x] **F3:** Put a reminder in January 2027 to choose an e-invoicing provider. Your UAE SMB customers must appoint one by 31 Mar 2027.
- [x] **F5:** Name chosen: SmartBiz OS. Before registering it, check the UAE trademark register and the .ae and .com domains.

## Message to paste into Claude Code

Save this doc into the repo as `docs/decisions-round-1.md` (export it as Markdown), then paste:

```markdown
I've answered every question in docs/open-questions.md. My answers are in docs/decisions-round-1.md — read it fully. Where it conflicts with earlier docs, the decisions file wins. The product name is SmartBiz OS: replace the "Business OS" placeholder everywhere (app name, desktop app, docs), keeping it in one config value.

1. Update the docs to match the decisions:
   - open-questions.md: mark each question answered with a link to the decision; all interpretations are confirmed; keep open only the accountant reviews (posting rules, negative-stock cost adjustment, VAT 201 mapping).
   - posting-rules.md: rewrite the PDC rules (memo until cleared), add Cancel (removes the document everywhere; its number can be reused for the corrected document, with the cancelled version kept in the audit log) vs Reverse, goods-delivered-not-invoiced at invoice, negative-stock cost adjustment and the Item Transfer document.
   - domain-model.md: add the PDC register, Item Transfer, branch emirate, document price mode (VAT-exclusive / inclusive / none), tax codes from the VAT 201 table, the customer sales-history lookup, and gap-free series for every document type.
   - ADRs: replace ADR 0008 with a local-first desktop app (UI + API + local PostgreSQL on each branch's main PC, other office PCs over the LAN, cloud sync; pick Next.js static export or Vite + React for the UI and justify it); add ADRs for the sync design and the master-data conflict rule; update the costing ADR to moving average across all warehouses, no FIFO in the MVP.
   - roadmap.md: add the local runtime (bundled API + local PostgreSQL + LAN access for office PCs) to Phase 0 and a cloud-sync milestone after Phase 1; add the pricing "last price to this customer first" rule and the customer history panel to M1.3, Item Transfer and negative stock to M1.6, Cancel/Reverse to M1.7 and M1.8.
2. Show me a short summary of what changed and anything that still conflicts. Wait for my OK.
3. Then do T1: give me the exact install commands for Docker Desktop (with WSL 2), make, uv, pnpm and Python 3.12 on Windows, and check each version after I run them.
4. When every tool is verified, start M0.1.
```
