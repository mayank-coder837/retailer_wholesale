# 0006 — Inventory costing: one moving-average cost per item, across all warehouses

Status: Accepted · 2026-10-11 (supersedes the 2026-10-10 FIFO + moving-average version; decisions-round-1 A5, A6)

## Decision
- **Moving weighted-average cost only** in the MVP. FIFO is dropped; it may return later as a per-business option.
- Cost pool = business × variant: one average across **all warehouses**. Transfers between warehouses move
  stock at the current average and do not change it (no GL entry).
- Perpetual inventory: every stock move carries its cost and posts to the GL (`posting-rules.md`), so stock value
  in the stock ledger equals the inventory control account.
- Inbound move: `new_avg = (old_value + in_value) / (old_qty + in_qty)` when `old_qty ≥ 0`; outbound moves leave
  at the current average. Sales returns re-enter at the cost they left at; purchase returns leave at current
  average, with any difference to `stock_cost_adjustment`.
- **Negative stock is allowed** (per-business setting, on by default; a warning, never a block). Stock issued
  below zero is costed at the current average, or the last purchase cost if there is no average yet. When stock
  arrives, the negative quantity is revalued at the incoming cost and the difference posts to
  `stock_cost_adjustment` (under cost of sales). A negative-stock report lists every item below zero.
- **Item Transfer** (Tally's stock journal) moves quantity from one item to another at the source item's
  average cost — the user's tool to fix negative stock (e.g. size 41 sold as size 42).
- A backdated stock move inside an open period triggers a recost of that variant's later moves; locked periods
  reject backdated moves.
- Cached `average_cost` and `stock_balance` rows are updated in the posting transaction and can be rebuilt from
  `stock_move`.

## Consequences
- No `cost_layer` tables in the MVP. Profit per invoice is known at posting time.
- The negative-stock adjustment rule is flagged for accountant review (`open-questions.md`).

## Alternatives
- FIFO per business — deferred: more tables and recost complexity for a method the founder doesn't need now.
- Cost per warehouse — rejected (A5).
