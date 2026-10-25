# ADR 0014 — Invoices are HTML from the API, turned into PDF by Chromium

Status: accepted (M1.10c, 26 Oct 2026)

## Context

Tax invoices and credit notes must print in English and Arabic (right to left, with correct Arabic shaping), show
both TRNs, VAT per line and in total (A3), and look the same from the desktop app, a browser, and — later — an
emailed or WhatsApp'd PDF and the e-invoicing archive. The desktop app is Electron (ADR 0008); every screen is
Chromium. The API is packaged Python on Windows main PCs.

## Decision

The API renders each printable document to **HTML** from Jinja2 templates
(`apps/api/app/modules/documents/templates/`), with autoescaping on, fonts that ship with Windows and the
browser (no web fonts), and print CSS for A4. `GET /documents/{id}/print?lang=en|ar` returns that page. **Chromium
makes the PDF**: the desktop app with Electron's `webContents.printToPDF`, the browser with its own print dialog
("Save as PDF"). The template is pure: data in, HTML out, so it is snapshot-tested.

## Consequences

- One template for every surface; what the user previews is what prints.
- Arabic shaping, bidi and RTL are Chromium's — the best available — with no font or layout engine to bundle.
- Templates are tested as text (snapshots per language) and by reading the numbers back (Σ line VAT = total).
- The API cannot produce a PDF on its own; emailing a PDF from the cloud later needs a headless Chromium worker
  there (a cloud-only job, ADR 0012), not on the main PC.
- New dependency: Jinja2 (small, pure Python, autoescaping).

## Alternatives

- WeasyPrint in the API — rejected: needs GTK/Pango on Windows, hard to bundle into the packaged API, and its
  Arabic shaping depends on those native libraries.
- Headless Chromium (Playwright) inside the API — rejected for the main PC: ~150 MB more per install for something
  the desktop shell already does.
- React print views in the SPA — rejected: the cloud and e-invoicing archive would need a second renderer, and
  server-side snapshot tests would not cover what prints.
