# 0010 — Cloud sync between branch databases and the cloud

Status: Accepted (design; built in milestone M2.1 after Phase 1) · 2026-10-11 (decisions-round-1 D1, A12)

## Context
Each branch's main PC holds a full PostgreSQL database and works offline (ADR 0008). The cloud holds the union of
all branches and serves browsers, phones, AI, WhatsApp, e-invoicing and multi-branch reports. Document numbers
must be gap-free and must never clash between a branch and the cloud.

## Decision

**Nodes and ownership**
- Nodes: one per branch main PC, plus the cloud. Every row records `origin_node_id`.
- Each record has exactly one **owner node** that may post it:
  - Branch documents and their ledger rows (journal, stock moves, bills, PDC register) — owned by the branch.
  - Master data (products, parties, prices, chart of accounts) — editable on several nodes; merged by ADR 0011.
- Posted records are immutable, so they sync as **append-only** inserts; a Cancel or Reverse is itself a new
  record (reversing entries), never an update.

**Transport**
- Every committed change writes a `sync_outbox` row (in the same transaction as the business change) with a
  per-node monotonically increasing `seq`.
- Push: the node sends batches of outbox rows to the cloud `POST /sync/push` with an idempotency key per batch;
  the cloud applies them in `seq` order and acknowledges the last `seq`.
- Pull: the node asks `GET /sync/pull?since=<cursor>` for changes from other nodes (other branches, web edits,
  e-invoice statuses) and applies them in order.
- Records keep their UUIDv7 ids everywhere, so no id remapping is needed.
- Sync runs whenever online (every few seconds, plus on demand); being offline only delays it.

**Numbering — one owner per series, so numbers never clash and stay in sync (A12, founder answer 4)**
- Series are per branch × document type × fiscal year, and the **branch's main database is the only allocator**
  for its series. The cloud never allocates a number in a branch series.
- Documents created in the cloud (browser, phone, B2B portal, AI) for a branch are saved as drafts and synced to
  that branch; the branch's main PC posts and numbers them and the number syncs back. With both sides online this
  takes seconds; if the branch PC is offline, the document shows "waiting for branch to number".
- Cloud-only activity that belongs to no branch (e.g. head-office journals, if enabled) uses its own head-office
  series owned by the cloud node.
- Cancelled numbers that are reused (A7) are reused on the owner node only; the audit trail syncs with them.

**Integrity**
- The cloud re-validates every pushed entry (balances, immutability, tenant) with the same constraints; a
  rejected batch stops sync for that node and raises an alert rather than skipping data.
- Each node can compute a per-period checksum (count + sums of journal lines and stock moves); the cloud compares
  them to prove both copies agree.

## Consequences
- Branches never wait for the cloud to trade; the cloud is eventually consistent (seconds when online).
- Cross-branch documents (inter-branch transfers) are two documents, one owned by each branch, linked by id.
- The local DB schema includes `origin_node_id` and `sync_outbox` from Phase 0, so turning sync on later needs no
  migration of existing data.

## Alternatives
- Logical replication of whole databases — rejected: two-way multi-master replication can't enforce our
  numbering and immutability rules.
- Cloud allocates all numbers — rejected: numbering would stop when offline (D2).
- Separate number prefixes for cloud-created documents — rejected: the founder wants one series in sync.
