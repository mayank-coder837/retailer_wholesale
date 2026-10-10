# 0002 — Tenant isolation with PostgreSQL row-level security

Status: Accepted · 2026-10-10

## Context
Many tenants share one database. A missing `WHERE tenant_id = …` in one query would leak another trader's
prices, customers or books. Isolation must not depend on every developer (or the AI) remembering a filter.

## Decision
- Shared database, shared schema; every tenant table has `tenant_id uuid not null`.
- RLS is enabled and **forced** on every tenant table with one policy:
  `USING (tenant_id = current_setting('app.tenant_id', true)::uuid) WITH CHECK (same expression)`.
- The API connects as `app_user` (no `BYPASSRLS`, not the table owner). Migrations run as `app_owner`.
- Each request opens a transaction and runs `SET LOCAL app.tenant_id = '<id>'` from the verified token.
  No setting → `NULL` → zero rows, so a forgotten context fails closed.
- Repositories still filter by `tenant_id` (indexes, clarity); RLS is the safety net.
- Global tables (`user`, `permission`, system `unit` / `tax_code` rows) carry no `tenant_id`, or use
  `tenant_id IS NULL OR tenant_id = current_setting(...)` for read-only shared seed rows.
- Login and tenant selection run before a tenant is known; they go through two `SECURITY DEFINER` functions
  (`auth_find_user`, `auth_list_memberships`) — the only paths that cross tenants.
- A shared pytest helper `assert_tenant_isolated(model, factory)` creates rows in tenants A and B and proves B
  cannot read, update or delete A's rows. A CI check lists every table with a `tenant_id` column and fails if
  it has no policy or no isolation test.

## Consequences
- Cross-tenant work (platform admin, billing) uses a separate role and code path, audited.
- `SET LOCAL` is scoped to the transaction, so pooled connections never carry a stale tenant.

## Alternatives
- Schema or database per tenant — rejected at SMB scale (thousands of small tenants, migrations × N).
- Application-only filtering — rejected: one missed filter is a breach.
