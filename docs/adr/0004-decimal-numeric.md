# 0004 — Decimal and NUMERIC for money and quantity

Status: Accepted · 2026-10-10

## Decision
| Kind | Python | Postgres | API (JSON) |
| --- | --- | --- | --- |
| Money amounts | `Decimal` | `NUMERIC(19,4)` | string, e.g. `"1050.0000"` |
| Quantities | `Decimal` | `NUMERIC(19,6)` | string |
| Rates, exchange rates, conversion factors, unit costs | `Decimal` | `NUMERIC(19,6)` | string |

- Floats are banned in `app/` (a lint check on `float` in models, schemas and services).
- Rounding happens only at named points: line amount, line tax, document total, and conversion to base
  currency — with the currency's minor units (AED = 2 dp). One helper, `money.round(amount, currency)`, does it.
  Rounding mode is `ROUND_HALF_UP` until the accountant confirms the FTA rule (open question).
- Stored amounts keep 4 dp so intermediate precision survives; posted totals are rounded to 2 dp.
- The frontend does arithmetic with a decimal library (`decimal.js`), never JS `number`.

## Consequences
Property tests (Hypothesis) assert: conversions round-trip without loss at 6 dp, journals balance exactly,
and line sums equal document totals.
