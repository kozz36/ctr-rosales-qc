# Status — review-table-width

**Change**: `review-table-width`
**Branch**: `fix/review-table-width`
**Date**: 2026-06-03

## Status

Implemented & merged to main via **PR #26**.

**Gate**: ctr-reviewer APPROVED (clean, no findings) + SA-5 Playwright runtime validation
(table fills viewport, no empty right band, no Acciones column visible).

**TDD cycle**: RED commit `a076041` → GREEN commits `742b4d5`, `ada2521`, `f8b206d`.

## Key artifacts

- `frontend/src/features/review/ReconciliationTable.vue` — removed dead Acciones th/td;
  corrected colspans (group: 13→11, drill-down: 12→11); Material set as sole auto column.
- `frontend/src/features/review/__tests__/ReconciliationTable.test.ts` — RED test added.

## Requirement IDs

No new capability spec promoted (UI-only correctness fix; no new requirement ID allocated).
The fix aligns with REV-* correctness requirements in `openspec/specs/review/spec.md`.
