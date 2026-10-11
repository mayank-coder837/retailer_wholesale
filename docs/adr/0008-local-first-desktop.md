# 0008 — Local-first desktop app (UI + API + PostgreSQL on each branch's main PC)

Status: Accepted · 2026-10-11 · Replaces the 2026-10-10 "Electron shell over the cloud API" proposal
(decisions-round-1 D1, D2, D4, F7)

## Context
Traders expect Tally behaviour: the books live in the office and everything works when the internet is down —
entry, posting, numbering, printing, stock and reports. The cloud adds browser/phone access, multi-branch
reports, AI, WhatsApp and e-invoicing. Phases 0–1 are built and piloted local-only; cloud sync comes after
Phase 1 (ADR 0010).

## Decision

**Topology**
- **Main PC (one per branch)** runs three Windows services installed by the SmartBiz OS installer:
  `SmartBizOS-PostgreSQL` (PostgreSQL 17, bundled binaries, own data directory, port 5440 so it never collides
  with another PostgreSQL on the PC), `SmartBizOS-API` (the same FastAPI app as the cloud) and the in-process
  outbox worker (ADR 0012).
- **Office PCs** install the same `.exe` in *client mode*: no database, the UI talks to the main PC's API over
  the LAN. A one-PC shop just uses the main PC.
- **Browser and phone** (D4) use the same UI build against the cloud API, once sync exists.

**One codebase, configured by API URL**
- The API, migrations and ledger constraints are identical locally and in the cloud (PostgreSQL on both sides
  keeps `NUMERIC`, triggers and RLS identical; SQLite was rejected: no true decimal type).
- The UI is a client-side SPA that reads its API base URL at runtime: `http://localhost:<port>` on the main PC,
  the main PC's LAN address on office PCs, the cloud URL in a browser.

**UI framework: Vite + React (not Next.js static export)**
- The UI must run with no Node server: from Electron, served as static files by the local API, and from a CDN.
  A Next.js static export drops what Next.js is for — server components, route handlers, middleware (which
  next-intl routing relies on), image optimisation and dynamic routes without pre-generation — so we would carry
  the framework's weight and constraints for none of its benefits.
- Vite + React + TypeScript with TanStack Router + Query, react-i18next (RTL), Tailwind and shadcn/ui is a plain
  SPA: fast builds and dev server, one `dist/` folder for every target, runtime API URL, and shadcn/ui and
  TanStack work unchanged.

**Packaging**
- Electron (main + preload, no business logic) + electron-builder NSIS installer; electron-updater from GitHub
  Releases.
- API: a pinned standalone CPython 3.12 runtime plus the locked virtualenv (built with `uv`), started as a
  Windows service via WinSW. Migrations ship as files and run with Alembic.
- PostgreSQL: official Windows binaries (zip) shipped in the installer; `initdb` on first run with a random
  password stored with Windows DPAPI; service account limited to the data directory.

**LAN access**
- The API listens on the LAN only when "allow office PCs" is on; the installer adds a Windows Firewall rule for
  the Private profile only.
- Office PCs pair with the main PC by IP/hostname plus a 6-digit pairing code shown on the main PC; pairing
  pins the main PC's self-signed TLS certificate. Users still log in individually (ADR 0007).

**Backups and updates**
- Automatic `pg_dump` nightly and before every update, kept for 14 days, to a folder the owner chooses (USB/NAS
  allowed); restore from the app's settings.
- An update stops the API, takes a backup, runs migrations, starts the API and checks `/health`; on failure it
  restores the backup and the previous app version.

**Security**
- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, a narrow preload API (token storage via
  `safeStorage`, printing, app version, service status). Navigation locked to the configured API origin.
- Unsigned during development; a code-signing certificate before the first customer release (D3).

## Consequences
- Every branch can trade fully offline; only internet features queue or show "needs internet".
- We support PostgreSQL on customer PCs: disk space checks, service health, backups and safe migrations are
  product features, not ops chores.
- Customer PCs never need Docker; developers use Docker Compose.
- Multi-branch (several main PCs) and browser access depend on cloud sync (ADR 0010, milestone M2.1).

## Alternatives
- Thin desktop shell over a cloud API (the 2026-10-10 proposal) — rejected: no work when offline (D2).
- SQLite locally, PostgreSQL in the cloud — rejected: no exact decimals, different constraints and migrations.
- Next.js static export — rejected, see above.
