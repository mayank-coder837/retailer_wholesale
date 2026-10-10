# Posting rules

Every posted document writes one balanced journal entry (Σ debit = Σ credit) using **account roles**
(`domain-model.md` → `account_role`), so each business maps roles to its own chart. Perpetual inventory: stock
moves post at cost when they happen.

> **Every rule below is "default — to be confirmed by accountant".** Rules are data (a posting-rule table per
> business, seeded with these defaults), so an accountant can change them without code changes.

Examples use AED, VAT 5%, and these figures: goods sold for 1,000 that cost 600.

## Sales

**1. Sales invoice (credit sale, goods not yet delivered)**

| Account role | Party | Debit | Credit |
| --- | --- | --- | --- |
| ar_control | customer | 1,050 | |
| sales | | | 1,000 |
| vat_output | | | 50 |
| cogs | | 600 | |
| inventory | | | 600 |
| **Total** | | **1,650** | **1,650** |

Cash sale: `cash`/`bank` instead of `ar_control`. Creates a `bill` (receivable, 1,050, due date from credit days).
If a delivery note already posted COGS (rule 2), the invoice posts only the first three lines (1,050 = 1,050).

**2. Delivery note (goods leave before invoicing)** — Dr cogs 600 / Cr inventory 600. Stock out at cost.

**3. Sales return (goods come back, against invoice)** — return of goods sold for 200 (cost 120):

| Account role | Party | Debit | Credit |
| --- | --- | --- | --- |
| sales_returns | | 200 | |
| vat_output | | 10 | |
| ar_control | customer | | 210 |
| inventory | | 120 | |
| cogs | | | 120 |
| **Total** | | **330** | **330** |

Stock returns at the original outbound cost. Allocated against the original bill (reduces `amount_open`).

**4. Credit note (financial, no goods — e.g. price allowance of 100)** — Dr sales_returns 100, Dr vat_output 5 /
Cr ar_control 105 (customer). Total 105 = 105.

**5. Receipt (customer pays 1,050 by bank transfer)** — Dr bank 1,050 / Cr ar_control 1,050 (customer),
bill-wise allocated to the invoice. Advance without a bill: allocation type `advance`.

## Purchasing

**6. Goods receipt (stock arrives, bill not yet received, cost 1,000)** — Dr inventory 1,000 / Cr grni 1,000.

**7. Purchase bill (after goods receipt; supplier bills 1,000 + VAT)**

| Account role | Party | Debit | Credit |
| --- | --- | --- | --- |
| grni | | 1,000 | |
| vat_input | | 50 | |
| ap_control | supplier | | 1,050 |
| **Total** | | **1,050** | **1,050** |

Price difference (billed 1,020 for goods received at 1,000): extra line Dr purchase_price_variance 20 (or
Dr inventory 20 while the goods are still in stock — open question); VAT 51; Cr ap_control 1,071.
Direct bill without a goods receipt: Dr inventory instead of grni. Service/expense bill: Dr the expense account.

**8. Purchase return (goods sent back, cost 500)** — Dr ap_control 525 (supplier) / Cr inventory 500,
Cr vat_input 25. Total 525 = 525. Stock leaves at the original inbound cost.

**9. Debit note (financial, supplier allowance of 100)** — Dr ap_control 105 / Cr purchase_price_variance 100,
Cr vat_input 5. Total 105 = 105.

**10. Payment (pay supplier 1,050 from bank)** — Dr ap_control 1,050 (supplier) / Cr bank 1,050, bill-wise.

**11. Expense voucher (rent 200 + VAT, paid cash)** — Dr expense 200, Dr vat_input 10 / Cr cash 210.

## Cash, bank and journals

**12. Contra (deposit 5,000 cash into bank)** — Dr bank 5,000 / Cr cash 5,000.

**13. Journal** — any balanced lines entered by a user with `journal.post`; control accounts (`ar_control`,
`ap_control`, `inventory`) require a party (AR/AP) or are blocked (inventory) to keep sub-ledgers in sync.

## Post-dated cheques (PDC)

Default method: the PDC reduces the customer's balance when received and sits in a PDC asset account until it
clears. (Alternative "memo only until maturity", as in Tally — open question.)

| Event (received cheque, 5,000) | Debit | Credit |
| --- | --- | --- |
| 14. Received | pdc_receivable 5,000 | ar_control 5,000 (customer, bill-wise) |
| 15. Deposited | cheques_in_clearing 5,000 | pdc_receivable 5,000 |
| 16. Cleared | bank 5,000 | cheques_in_clearing 5,000 |
| 17a. Bounced (after deposit) | ar_control 5,000 (customer) | cheques_in_clearing 5,000 |
| 17b. Returned (before deposit) | ar_control 5,000 (customer) | pdc_receivable 5,000 |

On bounce/return, the bill allocations from step 14 are reversed, so the invoice is open again. Bank charges
for a bounce are a separate expense voucher.

| Event (issued cheque, 3,000) | Debit | Credit |
| --- | --- | --- |
| 18. Issued | ap_control 3,000 (supplier) | pdc_payable 3,000 |
| 19. Cleared | pdc_payable 3,000 | bank 3,000 |
| 20. Bounced / cancelled | pdc_payable 3,000 | ap_control 3,000 (supplier) |

## Inventory

**21. Stock adjustment — increase (found 10 pcs at cost 15)** — Dr inventory 150 / Cr stock_adjustment 150.
**22. Stock adjustment — decrease (damaged, cost 90)** — Dr stock_adjustment 90 / Cr inventory 90.
**23. Stock transfer between warehouses of one business** — stock moves only, no journal (same cost pool).
**24. Recost of a backdated receipt** — difference between old and new COGS: Dr/Cr cogs / Cr/Dr inventory.

## Tax special cases

**25. Reverse charge on imported services (100, VAT 5)** — Dr expense 100 / Cr ap_control 100; plus
Dr vat_input 5 / Cr vat_output 5. Total 105 = 105.
**26. Zero-rated / exempt / out of scope** — same as rules 1/7 without VAT lines; the tax code still drives the
VAT-return box.

## Other

**27. Rounding** — when a document total is rounded (e.g. cash rounding), the difference goes to `rounding`.
**28. Opening balances (import)** — each account's balance against `opening_balance_equity`; customer/supplier
openings create `bill` rows per outstanding invoice; opening stock creates `opening` stock moves and cost layers
at the given cost, against `opening_balance_equity`. The import's reconciliation report shows
`opening_balance_equity` = 0 when the imported trial balance balances.
**29. Cancelling a posted document** — a reversal entry with every line's debit and credit swapped, dated on the
cancel date (or the original date if that period is still open — open question), plus reversing stock moves.
