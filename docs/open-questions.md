# Open questions

Round 1 is answered: every question below links to its decision in
[`decisions-round-1.md`](decisions-round-1.md) (founder, 11 Oct 2026; all interpretations confirmed). Only the
accountant reviews remain open.

## Still open — accountant review

| # | Review | Default in use meanwhile |
| --- | --- | --- |
| R1 | Every rule in [`posting-rules.md`](posting-rules.md) (all marked "default — to be confirmed by accountant") | As written |
| R2 | Negative-stock cost adjustment: revaluing negative quantities at the incoming cost, difference to "stock cost adjustment" under cost of sales ([A6](decisions-round-1.md#rules-to-build); posting rule 27) | As written |
| R3 | VAT 201 box mapping per tax code, and the Box 1 labels on the current EmaraTax form, before Phase 2 ([VAT 201 table](decisions-round-1.md#vat-201-box-mapping-a10)) | Mapping in `domain-model.md` → Tax |

Also for the accountant before e-invoicing go-live: an invoice already sent through the ASP can only be reversed
with a credit note, never cancelled ([A7](decisions-round-1.md#rules-to-build)).

## Still open — founder (from M0.6)

| # | Question | Default in use meanwhile |
| --- | --- | --- |
| F1 | Should owners be able to copy backups to a folder they choose (USB drive, NAS) automatically? ADR 0008 suggested it. A copy outside the PC protects against disk failure and theft but leaves the protected folder. | Backups stay in `C:\ProgramData\SmartBizOS\backups`, readable by administrators only |
| F2 | Should uninstalling offer "remove all data"? | Uninstall always keeps the database, backups and files, so reinstalling continues where the PC left off |

## Answered — round 1

### Founder — product and business

| # | Question | Answer |
| --- | --- | --- |
| F1 | Beachhead | UAE trading/wholesale SMBs — [F1](decisions-round-1.md#product-and-business-f1f7) |
| F2 | AI Tally assistant scope | Fast entry + one-time migration, no two-way sync — [F2](decisions-round-1.md#product-and-business-f1f7) |
| F3 | E-invoicing ASP | Not now; keep the adapter; choose by Jan 2027 — [F3](decisions-round-1.md#product-and-business-f1f7) |
| F4 | Team | Solo founder with Claude Code — [F4](decisions-round-1.md#product-and-business-f1f7) |
| F5 | Name and domain | **SmartBiz OS**, in one config value; no domain yet — [F5](decisions-round-1.md#product-and-business-f1f7) |
| F6 | Pricing tiers | Not yet — [F6](decisions-round-1.md#product-and-business-f1f7) |
| F7 | Hosting | Local Docker for development; free cloud tier for pilots; UAE region before real data — [F7](decisions-round-1.md#product-and-business-f1f7) |

### Desktop app

| # | Question | Answer |
| --- | --- | --- |
| D1 | Hosted URL or bundled? | Local-first like Tally: UI + API + PostgreSQL on each branch's main PC, cloud sync — [D1](decisions-round-1.md#desktop-app-d1d4), ADR 0008 |
| D2 | Offline scope | Everything in the office works offline — [D2](decisions-round-1.md#desktop-app-d1d4) |
| D3 | Code signing | Unsigned in development; buy before first customer release — [D3](decisions-round-1.md#desktop-app-d1d4) |
| D4 | Browser version | Yes, one codebase, against the cloud — [D4](decisions-round-1.md#desktop-app-d1d4) |

### Accountant topics (founder decisions)

| # | Question | Answer |
| --- | --- | --- |
| A1 | Posting rules | As written except A2, A6, A7, A8 — [A1](decisions-round-1.md#accounting-tax-and-posting-a1a12); review still open (R1) |
| A2 | PDC treatment | Memo until cleared, like Tally — [A2](decisions-round-1.md#rules-to-build) |
| A3 | VAT rounding | Per line, half-up, 2 dp; line and total VAT shown; no setting — [A3](decisions-round-1.md#rules-to-build) |
| A4 | Bill vs receipt price difference | Variance account — [A4](decisions-round-1.md#accounting-tax-and-posting-a1a12) |
| A5 | Cost pool | One moving average across all warehouses; no FIFO in MVP — [A5](decisions-round-1.md#rules-to-build), ADR 0006 |
| A6 | Negative stock | Allowed; Item Transfer to fix; negative-stock report — [A6](decisions-round-1.md#rules-to-build); review open (R2) |
| A7 | Cancel vs reverse | Both; Cancel frees the number — [A7](decisions-round-1.md#rules-to-build) |
| A8 | COGS timing | At invoice via goods delivered not invoiced; delivery note without prices — [A8](decisions-round-1.md#rules-to-build) |
| A9 | Pricing / promotions | Last price to this customer first; history panel; first match wins — [A9](decisions-round-1.md#rules-to-build) |
| A10 | VAT entry and VAT 201 | Exclusive / inclusive / no VAT; box mapping table — [A10](decisions-round-1.md#vat-201-box-mapping-a10); review open (R3) |
| A11 | Chart of accounts | Generic UAE trading chart — [A11](decisions-round-1.md#accounting-tax-and-posting-a1a12) |
| A12 | Gap-free numbering | Every document and voucher type — [A12](decisions-round-1.md#rules-to-build) |

### Technical

| # | Question | Answer |
| --- | --- | --- |
| T1 | Install missing tools | Yes, before M0.1 — [T1](decisions-round-1.md#technical-setup-t1t4) |
| T2 | Jobs | Celery in the cloud; in-process worker on branch PCs (ADR 0012) — [T2](decisions-round-1.md#technical-setup-t1t4) |
| T3 | LLM provider | Decide before M1.13 — [T3](decisions-round-1.md#technical-setup-t1t4) |
| T4 | Keep kickoff prompt | No — [T4](decisions-round-1.md#technical-setup-t1t4) |
