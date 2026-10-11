# 0011 — Master-data conflict rule

Status: Accepted · 2026-10-11 (decisions-round-1 D1: "head office wins, or last edit wins with the conflict logged")

## Context
Products, parties, prices and similar master data can be edited at two branches or in the cloud while one side
is offline. Posted documents never conflict (append-only, one owner — ADR 0010); master data can.

## Decision
**Field-level last edit wins, with every conflict logged — except control fields, where head office wins.**

1. Each master record keeps, per field, the last change's hybrid logical clock (HLC) timestamp and node.
2. When two nodes changed **different fields**, both changes are kept (no conflict).
3. When two nodes changed **the same field**, the later HLC wins and a `sync_conflict` row records both values,
   nodes, users and times. Owners/managers see a conflict list and can restore the losing value in one click.
4. **Control fields — head office wins:** a change made at the head-office node (or in the cloud by a user with
   the head-office role) beats a branch change regardless of time. Control fields: credit limit, credit days and
   credit policy; price lists, price-list items and minimum prices; chart of accounts and account roles; tax
   codes; units and conversion factors; costing and negative-stock settings. Branches may still edit them when
   permitted; the edit applies locally and is overwritten if head office changed the same field.
5. Deletion is archiving (`archived_at`); an edit to an archived record un-archives it only if it is later than
   the archive and is logged as a conflict.
6. Duplicate creations (the same new customer added at two branches) are not merged automatically; the conflict
   list suggests a merge by TRN or phone, which a user confirms.

## Consequences
- No silent data loss: every overwritten value is recoverable from `sync_conflict` and the audit log.
- Unit conversion factors are control fields because posted lines already store `factor_to_base` (rule 6), so a
  later change never rewrites history.

## Alternatives
- Whole-record last-write-wins — rejected: loses unrelated edits.
- Head office wins for everything — rejected: branches would lose day-to-day edits (phone, address) made offline.
