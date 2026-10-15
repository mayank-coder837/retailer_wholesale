# ADR 0013 — Standard units are copied into each tenant

Status: accepted (M1.1, 15 Oct 2026)

## Context

`domain-model.md` sketched units of measure as system rows shared by all tenants (`tenant_id` null) plus
tenant-defined rows. CLAUDE.md rule 1 requires every business table to carry `tenant_id` with forced row-level
security, and every table so far uses one uniform policy (`tenant_id = app.tenant_id`).

## Decision

Every tenant gets its own copy of the standard unit families and units, created at sign-up and, for existing
tenants, by the M1.1 migration. There are no shared system rows. Each unit stores one factor to its family's base
unit; there is no table of unit pairs.

## Consequences

- One RLS policy for every table, no "or tenant_id is null" exception to review and test.
- Tenants can rename units and use the names their customers know (e.g. "than" for a bolt of cloth, Arabic names).
- Sync (ADR 0010/0011) treats units like any other master data.
- A few dozen extra rows per tenant; adding a standard unit later needs a data migration for existing tenants.
- Conversions within a family go through the base unit, so they can never contradict each other.

## Alternatives

- Shared system rows with a policy `tenant_id = current OR tenant_id IS NULL` — rejected: an exception to rule 1
  in every policy and test, and tenants could not rename standard units.
- A `unit_conversion` table of pairs — rejected: pairs can disagree (1 km = 1000 m but 1 m = 0.0011 km).
