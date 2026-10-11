# 0012 — Background jobs: in-process worker locally, Celery in the cloud

Status: Accepted · 2026-10-11 (founder approval of the local-runtime conflict)

## Context
`CLAUDE.md` specified Redis + Celery. On a customer's main PC that would mean two more Windows services (Redis has
no maintained native Windows build) for a handful of jobs.

## Decision
- One `outbox_event` table and one set of **idempotent handler functions** (`app/core/jobs/handlers/*`).
- **Local runtime:** an asyncio worker inside the API process polls the outbox
  (`SELECT … FOR UPDATE SKIP LOCKED`), runs handlers, retries with backoff and records failures. Scheduled jobs
  (nightly backup, PDC maturity reminders, recost) use the same loop.
- **Cloud:** Celery workers with Redis run the same handlers; Celery beat schedules them.
- The dispatcher is chosen by configuration (`JOBS_BACKEND=inprocess|celery`); handlers never know which one runs
  them.

## Consequences
- Customer PCs run only PostgreSQL and the API.
- Local jobs pause while the API is stopped and resume from the outbox; nothing is lost.
- Tests run handlers directly and through the in-process dispatcher; Celery is covered by a cloud integration test.

## Alternatives
- Celery + Redis everywhere — rejected for customer PCs (extra services, no native Redis for Windows).
- Dramatiq — no advantage over the same split.
