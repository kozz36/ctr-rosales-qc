# Proposal — review-table-width

**Change**: `review-table-width`
**Phase**: archived (implemented & merged)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Gate**: ctr-reviewer APPROVED + SA-5 Playwright runtime validation.
**Status**: Implemented & merged to main via PR #26 (branch `fix/review-table-width`).
**Issue**: #23

---

## 1. Intent

### Problem

The reconciliation review table left a large empty band on the right side of the viewport.
Two independent root causes:

1. The `Acciones` column existed in both `<th>` and `<td>` markup but the reassign button
   had been moved into the guía drill-down long ago, leaving an empty column header and
   empty cells — a dead column consuming width.
2. The `Material` column had `width: 100%` set alongside every other column also having
   explicit widths, so under `table-layout: fixed` the `min-width` hints were ignored and
   no column absorbed the remaining table slack.
3. The group-row colspan was `13` (matching the old 13-column count including `Acciones`),
   producing a miscount after the column was removed and the drill-down colspan was also
   inconsistent.

### Why now

Visual validation on real data surfaced the issue during the SA-5 Playwright session for
#23. Correctness of the table structure is a prerequisite for any further UI work.

### Success looks like

- The table fills 100% of the available viewport width with no empty right band.
- The dead `Acciones` column is gone from both header and body rows.
- `Material` is the sole `width: auto` column and absorbs all table slack.
- Group-row and drill-down colspans are correct (11).

---

## 2. Scope

**In scope (frontend-only):**
- Remove `Acciones` `<th>` / `<td>` from `ReconciliationTable.vue`.
- Set `Material` as the sole `width: auto` column; remove conflicting `width: 100%` from
  other columns.
- Correct group-row colspan `13 → 11` and drill-down colspan `12 → 11`.
- RED test asserting no `Acciones` column exists and group colspan is 11.

**Out of scope:** no backend, domain, or pipeline change.

---

## 3. Rollback / Abort plan

Revert the frontend component changes; no data impact.
