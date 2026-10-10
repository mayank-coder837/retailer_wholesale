# 0008 — Windows desktop client: Electron shell over the cloud API

Status: Proposed (open questions D1–D2) · 2026-10-10

## Context
The founder wants users to install a Windows application (`.exe`). The product is multi-user and multi-branch,
and it must reach the cloud anyway for e-invoicing, WhatsApp and phone access. Data lives on the hosted API.

## Decision
- `apps/desktop`: an Electron shell (main + preload only, no business logic), packaged with electron-builder as
  an NSIS installer and auto-updated with electron-updater from GitHub Releases.
- The shell shows the same Next.js UI as the web app — one frontend codebase. Default: it loads the hosted app
  URL (`https://app.<domain>`) and ships a bundled offline/error page. Alternative in open question D1.
- It talks only to the cloud API over HTTPS. No local database of business data in Phases 0–1.
- Security: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, navigation locked to the app
  origin, a narrow preload API (secure token storage, printing, app version, scanner/serial access later).
- Desktop extras through preload: silent printing to receipt and label printers, Tally-style shortcuts
  (F5 payment, F6 receipt, F8 sales, F9 purchase), "start at login".
- Code signing: unsigned during development (SmartScreen warning); a certificate (e.g. Azure Trusted Signing)
  before the first customer release.

## Consequences
- One UI to build and test; Playwright runs against the web app, plus a smoke test that launches the packaged
  shell.
- Offline use is limited to the queued-write design in `architecture.md` until POS (Phase 2).
- The web app still exists for browsers, phones and the PWA, which the concept's mobile plan needs.

## Alternatives
- Fully offline desktop with a local database — rejected with the founder: breaks multi-user and branch use,
  RLS tenancy and e-invoicing; syncing accounting data later is very hard.
- Tauri — smaller installer, but adds a Rust toolchain and has weaker printing/serial support.
