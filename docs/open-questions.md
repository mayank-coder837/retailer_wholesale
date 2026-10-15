# Open questions

Round 1 is answered: every question below links to its decision in
[`decisions-round-1.md`](decisions-round-1.md) (founder, 11 Oct 2026; all interpretations confirmed). Only the
accountant reviews remain open.

## Still open — accountant review

| # | Review | Default in use meanwhile |
| --- | --- | --- |
| R1 | Every rule in [`posting-rules.md`](posting-rules.md) (all marked "default — to be confirmed by accountant") | As written |
| R2 | Negative-stock cost adjustment: revaluing negative quantities at the incoming cost, difference to "stock cost adjustment" under cost of sales ([A6](decisions-round-1.md#rules-to-build); posting rule 27) | As written |
| R4 | VAT round-off (L4): may a business round line VAT or total VAT beyond fils (e.g. to 0.05 or 1.00 AED)? This loosens A3 ("VAT per line to the fils, total = sum of lines, fixed for every business"), and rounding the total VAT breaks "total = sum of lines". Is it acceptable on a UAE tax invoice and in the VAT return, and if so, which account takes the difference? | Option built but off; A3 applies |
| R3 | VAT 201 box mapping per tax code, and the Box 1 labels on the current EmaraTax form, before Phase 2 ([VAT 201 table](decisions-round-1.md#vat-201-box-mapping-a10)) | Mapping in `domain-model.md` → Tax |

Also for the accountant before e-invoicing go-live: an invoice already sent through the ASP can only be reversed
with a credit note, never cancelled ([A7](decisions-round-1.md#rules-to-build)).

## Answered — founder, after round 1

Labelled L1, L2, … so they never collide with round 1's F1–F7.

| # | Question | Decision |
| --- | --- | --- |
| L1 | Copy backups automatically to a folder the owner chooses (USB drive, NAS)? (12 Oct 2026) | **Yes.** Settings → This PC → Backups → "Copy to a USB drive or NAS": every new backup (daily, before updates, "back up now") is also copied there with the same rotation; a failed copy never fails the backup and shows on the page. A NAS share must accept the PC without a user name and password (shares with credentials: later). |
| L2 | Should uninstalling offer "remove all data"? (12 Oct 2026) | **Yes.** An interactive uninstall of a main PC asks, in English and Arabic, with "Keep data" as the default. Silent uninstalls and updates never delete data. |
| L3 | Round converted quantities to the base unit's decimals (1 yd → 0.91 m)? (15 Oct 2026) | **No — exact.** 1 yd = 0.9144 m; a unit's decimals limit only what is typed; a conversion that would need rounding is refused. |
| L4 | Round-off on money (15 Oct 2026) | **An option, off by default**, separately for the bill total, payment receipt, payment voucher, line VAT and total VAT: off / nearest / up / down, step 0.05, 0.25, 0.50 or 1.00 AED; the difference posts to the `rounding` account (posting rule 34). Amounts are always in fils (2 dp) at settlement points. VAT round-off stays off until the accountant answers R4. |

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
