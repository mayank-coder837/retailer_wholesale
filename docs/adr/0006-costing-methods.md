# 0006 — Inventory costing: FIFO and moving average, chosen per business

Status: Accepted (details pending accountant, see open questions) · 2026-10-10

## Decision
- Each business picks `costing_method` = `fifo` or `moving_average` at setup. It is locked once the first stock
  move posts; changing it later needs a revaluation, out of scope for Phase 1.
- Perpetual inventory: every stock move carries its cost and posts to the GL (`posting-rules.md`), so stock value
  in the stock ledger always equals the inventory control account.
- Cost pool = business × variant. Warehouses share one cost; transfers move at cost and post no GL.
- FIFO: inbound moves create `cost_layer` rows; outbound moves consume the oldest layers and record
  `cost_layer_consumption`. Moving average: the `average_cost` cache is updated on every inbound move.
- Sales returns re-enter at the original outbound cost; purchase returns leave at the original inbound cost.
- A backdated stock move inside an open period triggers a recost job for that variant's later moves (with the
  GL difference posted); locked periods reject backdated moves.
- Negative stock is blocked by default.
- Landed cost (Phase 2) adds cost to the layers of the receipt it is allocated to.

## Alternatives
Periodic weighted average — rejected: profit per invoice would be unknown until period end.
