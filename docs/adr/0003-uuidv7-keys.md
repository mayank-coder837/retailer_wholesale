# 0003 — UUIDv7 primary keys

Status: Accepted · 2026-10-10

## Context
Clients create records offline (desktop/POS queue) and the AI drafts documents; ids must be safe to generate
outside the database. Sequential integers leak volumes and collide across tenants and devices.

## Decision
All primary keys are UUIDv7 (`uuid` column), generated in Python or by the client for offline-created records.
UUIDv7 is time-ordered, so B-tree inserts stay local.

## Consequences
- Human-facing numbers (`INV-2026-00042`) come from the numbering-series service, never from the id.
- Clients may send an id on create; the service accepts it only if it is a valid UUIDv7 not already used.

## Alternatives
- `bigint` identity — rejected (offline creation, enumeration).
- UUIDv4 — rejected (random index inserts).
