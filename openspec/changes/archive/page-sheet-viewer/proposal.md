# Proposal — page-sheet-viewer

**Change**: `page-sheet-viewer`
**Phase**: archived (implemented & merged)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Gate**: ctr-reviewer APPROVE WITH FINDINGS (0 Critical; W1/W2 non-blocking a11y warnings
  deferred to follow-up issue #31) + SA-5 Playwright runtime validation.
**Status**: Implemented & merged to main via PR #30 (branch `feat/page-sheet-viewer`).
**Issue**: #27

---

## 1. Intent

### Problem

Source-page chips in the review table displayed small page-number badges that became
unreadable at small chip sizes. The operator needed to inspect the full guía sheet to
verify handwritten information (date stamps, quantities, supplier details) but had no
way to view the full-resolution page image from the UI.

### Why now

User-requested during SA-5 Playwright validation of a prior PR. Dense guía pages need
close inspection for QC work; reading handwritten annotations from a 120 DPI thumbnail
is impractical.

### Success looks like

- Clicking a source-page chip opens a full-resolution (200 DPI) lightbox showing the PDF
  page rendered by the backend.
- The viewer includes zoom controls (50% steps, 100–400%), rotate (90° steps), reset, and
  a hand/pan tool to drag the zoomed image — all at client-side CSS transform speed.
- A persistent page-number overlay badge is always visible on every chip.
- The lightbox is keyboard-navigable (ESC to close, arrow keys for page navigation, zoom
  keys, rotate key).

---

## 2. Scope

**Backend:**
- `GET /runs/{run_id}/pages/{page}/image` endpoint: full-res page render at 200 DPI.
  Sibling of the existing `get_page_thumbnail` endpoint (120 DPI). Returns PNG.

**Frontend:**
- `PageSheetViewer.vue`: lightbox component with zoom/rotate/pan controls (CSS transform,
  no extra network calls after initial image load).
- Persistent page-number overlay badge on `SourcePages` chip (always visible, not only on
  hover).
- Integration in `SourcePages.vue`: chip click triggers the viewer.

**Out of scope:** no domain, pipeline, or reconciliation logic change.

---

## 3. Rollback / Abort plan

Remove the backend endpoint and the `PageSheetViewer.vue` component. No data impact.
The 120 DPI thumbnail endpoint is unchanged.
