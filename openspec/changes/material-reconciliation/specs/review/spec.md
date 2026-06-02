# Spec — Review Domain
**Change**: material-reconciliation
**Domain**: review
**Phase**: spec
**Date**: 2026-05-31

---

## Purpose

The review domain provides the human-in-the-loop interface for the QC engineer to inspect the reconciliation result, correct extracted values, reassign misfiled guías, and resolve flagged items before export. Review operates on top of the reconciliation output; every change the engineer makes triggers an immediate recompute of the affected reconciled groups.

Review is not a post-processing step — it is the accuracy guarantee of the system, as stated in the proposal: "the reconciliation gate + human review are the accuracy guarantee, not the models."

---

## Requirements

### REV-001 — Editable reconciliation grid

The review UI MUST present the reconciliation table as an editable grid.
Each row in the grid corresponds to one reconciled group `(registro, fecha, material_canonical, unidad)`.
The grid MUST display, per row:
- Registro N° and fecha de entrega
- material_canonical
- unit (unidad)
- declared quantity (from digital text — read-only)
- summed guía quantity (computed — updated on edit)
- delta (summed − declared)
- MATCH / MISMATCH / DECLARED_MISSING / GUIA_MISSING flag
- aggregate OCR confidence score

### REV-002 — Editable extracted values

The engineer MUST be able to edit any individual extracted value (quantity, unit, handwritten date) that contributes to the reconciliation.
Each editable cell MUST show the current value alongside the extraction method and confidence score.
When a cell value is changed by the engineer, the corresponding group's sum and MATCH/MISMATCH status MUST recompute immediately (within the same UI interaction, without requiring a full pipeline re-run).

### REV-003 — Guía reassignment action

The engineer MUST be able to reassign a guía (identified by guía number + source page index) to a different `(registro, fecha)` pair.
The reassignment action MUST:
1. Remove the guía's contribution from its current group.
2. Add the guía's contribution to the target group.
3. Recompute MATCH/MISMATCH for both the source and target groups.
4. Record the reassignment in the audit trail.
The UI MUST provide a discoverable control to trigger reassignment (e.g., a reassign button or context action per guía row in the drill-down view).

### REV-004 — Flagged-item surfacing

The review UI MUST prominently surface all items that require human attention in a dedicated section or filter:
- Groups with MISMATCH status
- Groups with DECLARED_MISSING or GUIA_MISSING status
- Pages with `classification_confidence` below threshold (unclassified bucket)
- Pages with `date_requires_review: true`
- Pages with `ocr_empty_after_deskew: true`
- Pages with `orientation_low_confidence: true`
- Pages with `orientation_fallback_failed: true`

Flagged items MUST NOT be hidden by default. The engineer MUST be able to mark a flagged item as "reviewed" to remove it from the active flag list.

### REV-005 — Source page drill-down

For any extracted value in the grid, the engineer MUST be able to view the source page image (rendered thumbnail) alongside the extracted value.
This satisfies the proposal requirement to "show raw OCR confidence + source page thumbnail next to each value."
The source page image MUST be the post-deskew render.

### REV-006 — Recompute scope

Edits and reassignments MUST trigger recomputation of ONLY the affected reconciled groups — not a full pipeline re-run.
The ingestion, classification, and extraction stages MUST NOT be re-executed for in-session edits.

### REV-007 — Audit trail

Every change made in review (value edit, reassignment, flagged-item resolution) MUST be recorded in the run's audit trail with:
- timestamp
- action type (`value_edit` | `guia_reassign` | `flag_resolved`)
- field or guía affected
- old value and new value (where applicable)
- operator identifier (for MVP: constant "engineer" is acceptable)

The audit trail MUST be included in the export.

### REV-008 — Review state persistence (per-run sidecar — REQUIRED)

Review state (edits, reassignments, flag resolutions) MUST be persisted to a per-run sidecar file at `<run_dir>/review.json` after every change.
Edits MUST survive an application restart within the same run — when the application is restarted and the same run directory is loaded, the prior edits MUST be restored automatically from `review.json`.
The sidecar file MUST be separate from the immutable extraction cache; it MUST NOT overwrite or modify any cached extraction artifact.
If `review.json` is absent when a run is resumed, the review state MUST start empty (no edits applied) without crashing.
(Previously: persistence was in-memory only for MVP; sidecar persistence was deferred to post-MVP. Decision reversed: sidecar is now required for MVP.)

### REV-009 — Non-destructive editing

Value edits in the review UI MUST NOT modify the original extracted data or the input PDF.
The system MUST maintain the original extracted value alongside the engineer-corrected value in the audit trail.
At export time, the engineer-corrected value MUST take precedence.

---

## Acceptance Scenarios

### Scenario REV-S01 — MISMATCH group is visible on load

**Given** the reconciliation has completed with three MISMATCH groups
**When** the engineer opens the review UI
**Then** the three MISMATCH groups are immediately visible and highlighted
**And** each displays: registro, fecha, material_canonical, unit, declared qty, summed qty, delta, confidence

### Scenario REV-S02 — Engineer corrects an OCR quantity error

**Given** a MISMATCH group where the summed qty is 1260.0 KG vs declared 1250.0 KG
**And** the engineer inspects the source page thumbnail and identifies an OCR misread
**When** the engineer edits the extracted quantity from 1260.0 to 1250.0 in the cell
**Then** the group's sum recomputes to 1250.0 KG
**And** the group status updates from MISMATCH to MATCH
**And** the audit trail records: action=value_edit, old=1260.0, new=1250.0, source_page=47

### Scenario REV-S03 — Engineer reassigns a misfiled guía

**Given** a MISMATCH in registro 4252 caused by a guía that belongs to registro 4251
**When** the engineer triggers the reassign action for that guía and selects registro 4251 as target
**Then** the grupo for registro 4252 recomputes (guía removed) and its status updates
**And** the group for registro 4251 recomputes (guía added) and its status updates
**And** the reassignment appears in the audit trail

### Scenario REV-S04 — Unclassified page surfaces in review

**Given** three pages were flagged as `unclassified` during extraction
**When** the engineer opens the review UI
**Then** the three pages are visible in the "unclassified pages" bucket with their page index and confidence score
**And** the engineer can view the source page image for each
**And** the engineer can mark them as reviewed

### Scenario REV-S05 — Low-confidence handwritten date shown for correction

**Given** page 83 has `date_requires_review: true` with suggested date "15/03/2025" at confidence 0.45
**When** the engineer opens the review UI
**Then** page 83 appears in the flagged-items section
**And** the suggested date "15/03/2025" is shown as an editable pre-filled value
**And** the engineer can confirm or replace the date
**And** after confirmation, the group using this date recomputes

### Scenario REV-S06 — Source page thumbnail visible next to extracted value

**Given** an extracted quantity row from page 47 (guía page, deskewed)
**When** the engineer expands the row detail in the review grid
**Then** the post-deskew render of page 47 is displayed as a thumbnail
**And** the OCR confidence score for the quantity is shown

### Scenario REV-S07 — In-session edits do not trigger re-extraction

**Given** the engineer edits three quantity values in the review grid
**Then** no OCR or vision LLM call is made
**And** no PDF splitting or rendering is re-executed
**And** only the affected reconciled group sums and statuses are recomputed

### Scenario REV-S08b — Review edits survive an app restart

**Given** the engineer has made 3 quantity edits and 1 guía reassignment in the current run session
**When** the application is closed and restarted with the same run directory
**Then** `<run_dir>/review.json` is read automatically on startup
**And** all 3 quantity edits and the reassignment are restored to the review grid
**And** no re-extraction or re-reconciliation is triggered
**And** the restored state is identical to the state before the restart

### Scenario REV-S08 — Original extracted value preserved after edit

**Given** an engineer has edited an extracted quantity from 500.0 to 490.0
**When** the export is generated
**Then** the export includes both the original extracted value (500.0) and the engineer-corrected value (490.0)
**And** the export uses the corrected value for the final sum

---

## Delta — rev 2 (2026-06-01): guía-granularity UI + reassign by guia_id + line edit

> The requirements below ADD or MODIFY behaviour relative to REV-001 through REV-009 above.
> Each entry is marked [ADDED] or [MODIFIED: replaces <id>].

### REV-C01 — [ADDED] Row drill-down to contributing guías

Each row in the reconciliation grid MUST be expandable to reveal the list of contributing
guías for that group (`ReconciliationRow.guias`).
The expanded drill-down view MUST display, per `GuiaContribution`:
- `guia_id` (formatted as `{serie}-{numero}`)
- `source_pages` (comma-separated list of page indices)
- `cantidad` and `unidad`
- `confidence` (with the ConfidenceBadge < 0.85 flag applied)
- `identity_source` indicator (`QR` or `OCR fallback`)

The drill-down MUST be rendered without a separate API call — the data is already inline in
the `ReconciliationRowResponse.guias[]` array (avoiding N+1 fetch on expand).

### REV-C02 — [ADDED] Reassign action targets guia_id

**[MODIFIED: replaces REV-003's identification scheme]**

The `GuiaReassignDialog` MUST identify the guía to be reassigned by `guia_id`
(`{serie}-{numero}`) — NOT by `row_id` alone.
The dialog MUST be reachable from the drill-down view (REV-C01) with a discoverable
"Reassign" action per `GuiaContribution` entry.
The reassign API call MUST send `guia_id` to `POST /runs/{id}/reassign`.
After a successful reassign:
- Both the source and target `ReconciliationRow` entries MUST update in the grid without a
  full page reload.
- The drill-down for both rows MUST reflect the updated `guias[]` list.

The prior behavior of sending `row_id` as a proxy for `guia_id` is PROHIBITED (this was
identified as CRITICAL-1 in §frontend-review).

### REV-C03 — [ADDED] Guía-line cantidad edit in drill-down

From the drill-down view, the engineer MUST be able to edit the `cantidad` of an
individual line within a `GuiaContribution`.
The edit MUST be submitted via `PATCH /runs/{id}/guias/{guia_id}/lines`.
After a successful edit:
- `GuiaContribution.cantidad` in the drill-down updates to the new value.
- `ReconciliationRow.summed_qty` in the parent row updates (recomputed).
- The MATCH/MISMATCH badge updates immediately.
The cell displaying `summed_qty` in the aggregate row MUST be read-only in the UI;
it MUST NOT be an editable input field.

The prior behavior of presenting `summed_qty` as a directly editable field aliased to
`field:'fecha'` is PROHIBITED (this was identified as CRITICAL-2 in §frontend-review).

### REV-C04 — [ADDED] Unresolved guías bucket

The review UI MUST include an "Unresolved guías" section (or filter) that lists all
`GuiaDeRemision` entries from `reconciliation_result.unresolved_guias`.
Each entry MUST display:
- `guia_id` (or page range if guia_id is unavailable)
- `identity_source`
- `source_pages`
- A manual "Assign to registro" action that triggers `POST /runs/{id}/reassign` with a
  target registro/fecha selected by the engineer.

Unresolved guías MUST NOT appear as rows in the main reconciliation grid until they have
been assigned.

---

## Acceptance Scenarios — Delta rev 2

### Scenario REV-C01 — [ADDED] Drill-down shows guía contributions without extra fetch

**Given** a reconciliation row for group `(registro=232, fecha=2025-03-15, "BARRA CORRUGADA 1/2", "KG")`
**And** `ReconciliationRowResponse.guias` contains 2 `GuiaContributionResponse` entries
**When** the engineer expands the row in the review grid
**Then** both guías are displayed with guia_id, source_pages, cantidad, unidad, confidence
**And** no additional API call is made to fetch guía detail
**And** a "Reassign" button is visible for each guía entry

### Scenario REV-C02 — [ADDED] Reassign dialog uses guia_id

**Given** the engineer clicks "Reassign" for guía `T009-0741770` in the drill-down
**When** the GuiaReassignDialog opens
**Then** the dialog identifies the guía by `guia_id = "T009-0741770"` (not by row_id)
**When** the engineer selects target `(registro=231, fecha=2025-02-10)` and confirms
**Then** the API call is `POST /runs/{id}/reassign` with body `{ "guia_id": "T009-0741770", ... }`
**And** the source row (232 / 2025-03-15) and target row (231 / 2025-02-10) both update
  in the grid without a page reload

### Scenario REV-C03 — [ADDED] Guía-line edit updates summed_qty immediately

**Given** the drill-down for row `(232, 2025-03-15, "BARRA CORRUGADA 1/2", "KG")` shows
  guía `T009-0741770` with `cantidad = 1260.0`
**When** the engineer edits the cantidad cell to `1250.0`
**Then** `PATCH /runs/{id}/guias/T009-0741770/lines` is called with the new value
**And** the `GuiaContribution` cantidad cell updates to 1250.0
**And** the aggregate `summed_qty` in the parent row updates to 1250.0
**And** the MATCH/MISMATCH badge on the parent row updates (from MISMATCH to MATCH if applicable)

### Scenario REV-C04 — [ADDED] summed_qty cell is read-only

**Given** the engineer views a `ReconciliationRow` in the review grid
**When** the engineer attempts to click or activate the `summed_qty` cell
**Then** the cell is NOT editable (no input field rendered)
**And** no PATCH request targeting `summed_qty` as a direct field is issued

### Scenario REV-C05 — [ADDED] Unresolved guías appear in dedicated bucket

**Given** the reconciliation result contains 2 unresolved guías (failed registro derivation)
**When** the engineer opens the review UI
**Then** an "Unresolved guías" section is visible
**And** both guías are listed with their guia_id (or page range), identity_source, and source_pages
**And** each unresolved guía has an "Assign to registro" control
**And** neither unresolved guía appears as a row in the main reconciliation grid

---

## Out of scope for this domain

- Pipeline execution (ingestion, extraction, normalization, reconciliation).
- Export file generation (handled by the export domain).
- Authentication or multi-user concurrent editing.
- Review state persistence across separate runs (different run directories) — each run has its own `review.json`.
