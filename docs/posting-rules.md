# Posting rules

Every posted document writes one balanced journal entry (Σ debit = Σ credit) using **account roles**
(`domain-model.md` → `account_role`), so each business maps roles to its own chart. Perpetual inventory with one
moving-average cost per item across all warehouses (ADR 0006). Decisions: `decisions-round-1.md`.

> **Every rule below is "default — to be confirmed by accountant"** (decisions-round-1 F4). Rules are data (a
> posting-rule table per business, seeded with these defaults), so an accountant can change them without code.

**VAT (A3, fixed for every business):** VAT is computed per line, rounded half-up to 2 decimals (fils); total VAT
= sum of the rounded line VAT, so lines always add up to the total. Invoices show line VAT and total VAT. Price
mode (VAT-exclusive, VAT-inclusive, no VAT) changes only how net and VAT are derived, not the posting.

Examples use AED, VAT 5%, and goods sold for 1,000 that cost 600.

## Sales

**1. Sales invoice, no delivery note (credit sale)**

| Account role | Party | Debit | Credit |
| --- | --- | --- | --- |
| ar_control | customer | 1,050 | |
| sales | | | 1,000 |
| vat_output | | | 50 |
| cogs | | 600 | |
| inventory | | | 600 |
| **Total** | | **1,650** | **1,650** |

Cash sale: `cash`/`bank` instead of `ar_control`. Creates a `bill` (receivable, 1,050, due date from credit days).

**2. Delivery note (A8)** — quantities only; its print template has no prices. Stock leaves at average cost:
Dr goods_delivered_not_invoiced 600 / Cr inventory 600.

**3. Sales invoice after a delivery note (A8)**

| Account role | Party | Debit | Credit |
| --- | --- | --- | --- |
| ar_control | customer | 1,050 | |
| sales | | | 1,000 |
| vat_output | | | 50 |
| cogs | | 600 | |
| goods_delivered_not_invoiced | | | 600 |
| **Total** | | **1,650** | **1,650** |

The cost moved is the delivery note's cost, so `goods_delivered_not_invoiced` clears to zero per delivery.

**4. Sales return (goods come back, against invoice)** — return of goods sold for 200 (cost 120):

| Account role | Party | Debit | Credit |
| --- | --- | --- | --- |
| sales_returns | | 200 | |
| vat_output | | 10 | |
| ar_control | customer | | 210 |
| inventory | | 120 | |
| cogs | | | 120 |
| **Total** | | **330** | **330** |

Stock re-enters at the cost it left at. Allocated against the original bill (reduces `amount_open`).

**5. Credit note (financial, no goods — price allowance of 100)** — Dr sales_returns 100, Dr vat_output 5 /
Cr ar_control 105 (customer). In the VAT return it reduces the original sale's box in the credit note's period.

**6. Receipt (customer pays 1,050 by bank transfer)** — Dr bank 1,050 / Cr ar_control 1,050 (customer),
bill-wise allocated. Without a bill: allocation type `advance`.

## Purchasing

**7. Goods receipt (stock arrives, bill not yet received, cost 1,000)** — Dr inventory 1,000 / Cr grni 1,000.

**8. Purchase bill (after goods receipt; supplier bills 1,000 + VAT)**

| Account role | Party | Debit | Credit |
| --- | --- | --- | --- |
| grni | | 1,000 | |
| vat_input | | 50 | |
| ap_control | supplier | | 1,050 |
| **Total** | | **1,050** | **1,050** |

Price difference (billed 1,020 for goods received at 1,000), A4: Dr grni 1,000, Dr purchase_price_variance 20,
Dr vat_input 51 / Cr ap_control 1,071. Direct bill without a goods receipt: Dr inventory instead of grni.
Service/expense bill: Dr the expense account. Blocked input VAT (tax code SR-B): the VAT is added to the expense.

**9. Purchase return (goods sent back, cost 500)** — Dr ap_control 525 (supplier) / Cr inventory 500,
Cr vat_input 25.

**10. Debit note (financial, supplier allowance of 100)** — Dr ap_control 105 / Cr purchase_price_variance 100,
Cr vat_input 5. Reduces Box 9 in the debit note's period.

**11. Payment (pay supplier 1,050 from bank)** — Dr ap_control 1,050 (supplier) / Cr bank 1,050, bill-wise.

**12. Expense voucher (rent 200 + VAT, paid cash)** — Dr expense 200, Dr vat_input 10 / Cr cash 210.

## Cash, bank and journals

**13. Contra (deposit 5,000 cash into bank)** — Dr bank 5,000 / Cr cash 5,000.

**14. Journal** — any balanced lines entered by a user with `journal.post`; `ar_control`/`ap_control` lines
require a party, and `inventory` cannot be posted by hand.

## Post-dated cheques — memo until cleared (A2)

Receiving or issuing a PDC makes **no ledger entry**. It is recorded in the PDC register (party, cheque number,
bank, amount, maturity date, the invoices it is meant for) and only its status changes until clearance.

| Event (received cheque, 5,000) | Ledger entry | Register |
| --- | --- | --- |
| 15. Received | none | status `in_hand`; intended invoices recorded |
| 16. Deposited | none | status `deposited`, deposit bank and date |
| 17. Cleared | Dr bank 5,000 / Cr ar_control 5,000 (customer), dated the clearance date; bill-wise settles the intended invoices | status `cleared`, linked to the receipt voucher it created |
| 18. Bounced | none (a bank charge, if entered, is an expense voucher: Dr bank_charges / Cr bank) | status `bounced`, reason |
| 19. Returned before deposit | none | status `returned` |

| Event (issued cheque, 3,000) | Ledger entry | Register |
| --- | --- | --- |
| 20. Issued | none | status `issued` |
| 21. Cleared | Dr ap_control 3,000 (supplier) / Cr bank 3,000, dated the clearance date, bill-wise | status `cleared` |
| 22. Bounced / cancelled | none | status `bounced` / `cancelled` |

Statements and ageing show two figures: **outstanding** (ledger) and **outstanding after PDCs in hand**. Credit
control uses the ledger balance and shows PDCs in hand beside it.

## Inventory

**23. Stock adjustment — increase (found 10 pcs at average 15)** — Dr inventory 150 / Cr stock_adjustment 150.

**24. Stock adjustment — decrease (damaged, cost 90)** — Dr stock_adjustment 90 / Cr inventory 90.

**25. Stock transfer between warehouses** — stock moves only, at current average; no journal (one cost pool).

**26. Item Transfer (A6; Tally's stock journal)** — moves quantity from item A to item B at **A's average cost**,
e.g. 1 pc of size 41 (average 40) to size 42: stock move out of A (−1, value −40), stock move into B (+1, value
+40). No journal when both items use the same inventory account (the usual case); otherwise Dr inventory (B's
account) 40 / Cr inventory (A's account) 40. B's average updates as for any receipt, including rule 27 if B was
negative.

**27. Negative stock — cost adjustment on arrival (A6)** — selling below zero is allowed (warning only). Example:
10 pcs sold with zero stock, costed at the last purchase cost 60 → Dr cogs 600 / Cr inventory 600 (stock −10,
value −600). Then 20 pcs arrive at 65: Dr inventory 1,300 / Cr grni 1,300, and the 10 negative pcs are revalued
at the incoming cost: **Dr stock_cost_adjustment 50 / Cr inventory 50** (`stock_cost_adjustment` sits under cost
of sales). Result: 10 pcs worth 650 = 10 × 65. If the incoming cost is lower, the entry is reversed in direction.

**28. Recost of a backdated move (open period)** — the change in cost of later issues: Dr/Cr cogs, Cr/Dr inventory.

## Tax special cases

**29. Reverse charge, imported services (RC-S; 100, VAT 5)** — Dr expense 100 / Cr ap_control 100; plus
Dr vat_input 5 / Cr vat_output 5. VAT 201 Box 3 (due) and Box 10 (recovered).

**30. Imported goods (RC-G)** — as rule 29 with inventory instead of expense; Box 6 is pre-filled by Customs and the
app's records are used to check it; recoverable VAT goes to Box 10.

**31. Zero-rated / exempt / out of scope / no-VAT price mode** — same as rules 1/8 without VAT lines; the tax code
still decides the VAT 201 box (`domain-model.md` → Tax).

## Cancel and Reverse (A7)

Both remove the document's effect from **everywhere** — ledger, stock, VAT reports, party outstanding and every
report — by writing **new** reversing rows (posted rows are never edited or deleted, `CLAUDE.md` rule 3). Payments
allocated to the document become unallocated advances; linked delivery notes return to "not invoiced". Both need
a permission (`<doc>.cancel` / `<doc>.reverse`) and a reason, and both write to the audit log.

| | **32. Cancel** | **33. Reverse** |
| --- | --- | --- |
| Use when | A mistake in an open period | Period locked, VAT return filed, or the customer already has the document |
| Allowed when | Period open, VAT return for it not filed, not sent as an e-invoice | Always, with permission |
| Entries | A reversing journal entry and reversing stock moves **dated on the original date**, so the effect vanishes from that period | A reversal document (a credit/debit note for invoices and bills; a reversing voucher otherwise) dated on the day the user picks (default today) |
| Number | Released: the next document in that series is offered the freed number (or the user picks it); the replacement is marked "reissued after cancel" (`reissue_of_id`). If never reused, the number stays visible as CANCELLED, so there is no gap | Original keeps its number; the reversal gets its own number |
| Status | `cancelled` | `reversed` |
| Audit | The cancelled version is kept in full (contents, who, when, why); reports show the number once | Both documents stay visible |

Example — cancelling rule 1's invoice writes, dated on the invoice date: Dr sales 1,000, Dr vat_output 50 /
Cr ar_control 1,050; Dr inventory 600 / Cr cogs 600.

Warnings and limits: if the original was printed, emailed or sent on WhatsApp, the app warns before reusing the
number. Once e-invoicing is live, an invoice already sent through the ASP can only be reversed with a credit note —
never cancelled or its number reused (accountant to confirm before go-live).

## Other

**34. Rounding** — a cash-rounding difference on a document total goes to `rounding`.

**35. Opening balances (import)** — each account's balance against `opening_balance_equity`; customer/supplier
openings create `bill` rows per outstanding invoice; opening stock creates `opening` stock moves at the given cost
against `opening_balance_equity`. When the imported trial balance balances, `opening_balance_equity` nets to 0.
