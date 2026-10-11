# 0001 — Modular monolith

Status: Accepted · 2026-10-10

## Context
One founder building with Claude Code, one beachhead segment, and accounting flows that must post documents,
journals and stock moves in a single transaction. Microservices would turn every posting into a distributed
transaction.

## Decision
One FastAPI service (`apps/api`) split into modules under `app/modules/<module>/`
(`models, schemas, repository, service, router, posting`). Modules call each other only through **service**
functions, never through another module's tables or repositories. Background work runs the same handler code
in-process on branch PCs and in Celery workers in the cloud (ADR 0012). The same service runs on each branch's
main PC and in the cloud (ADR 0008). A module is split into its own service only when it needs independent scaling.

## Consequences
- Posting is one database transaction: document status, number, journal, stock moves, cached balances, audit
  row and outbox events commit or roll back together.
- Boundaries need discipline: an import-linter contract in CI forbids `modules.a` importing `modules.b.models`
  or `modules.b.repository`.
- One deploy unit, one migration history (Alembic).

## Alternatives
- Microservices per module — rejected: distributed transactions for every posting, too much ops for the team.
- Unstructured monolith — rejected: the AI tool registry and permission checks need clear service entry points.
