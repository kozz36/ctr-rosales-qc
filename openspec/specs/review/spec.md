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

## Delta — rev 3 (2026-06-02): year_inferred advisory + first_page sentinel propagation to UI

> The requirements below ADD or MODIFY behaviour relative to REV-001 through REV-C04 above.
> Each entry is marked [ADDED] or [MODIFIED: replaces <id>].

### REV-C05 — [ADDED] year_inferred advisory indicator in review grid

When a `ReconciliationRow.any_year_inferred` is `true`, the review UI MUST display an
advisory indicator on that row (distinct from the MISMATCH red flag and the
`requires_review` orange/amber flag).

Recommended presentation: a yellow/informational badge or icon on the `fecha` cell of the
drill-down guía entry, labelled with text such as "Year inferred" or an equivalent
localisation-friendly label.

The advisory indicator MUST link or expand to show the engineer:
- The day-month as read by vision.
- The inferred year.
- The bounds used for inference (`delivery_GRE_date` and `reference_date` where available).

The engineer MUST be able to confirm or override the inferred date using the existing
date-edit path (REV-002 / REV-C03). A confirmed/overridden date clears the advisory
indicator and records the action in the audit trail with
`action_type = "year_inferred_confirmed"` or `"year_inferred_overridden"`.

### REV-C06 — [ADDED] first_page=None sentinel does not crash unresolved-guía display

The `UnresolvedGuiasPanel` (REV-C04) MUST handle `GuiaDeRemision.first_page = None`
without a runtime error or display corruption.

When `first_page` is `None`, the panel MUST display the guía's `source_pages` list (if
non-empty) as the page reference, or "unknown page" if `source_pages` is also empty.
The panel MUST NOT treat `first_page = 0` as absent — page index 0 is a valid reference
and MUST be displayed as "page 0" (or equivalent 1-based display: "page 1").

---

## Acceptance Scenarios — Delta rev 3

### Scenario REV-C06 — [ADDED] year_inferred advisory visible in review grid

**Given** a `ReconciliationRow` where `any_year_inferred = true`
**And** the contributing guía `T009-0741770` has `fecha = 2026-05-28`, `year_inferred = true`
**When** the engineer opens the review UI and expands the row drill-down
**Then** an advisory indicator ("Year inferred") is visible on the fecha cell for `T009-0741770`
**And** the indicator shows: day-month=28-05 (vision), inferred-year=2026,
  bounds=delivery_GRE_date..reference_date
**And** no MISMATCH flag is raised solely because of year inference
**And** the engineer can click to confirm the date, recording
  `action_type="year_inferred_confirmed"` in the audit trail

### Scenario REV-C07 — [ADDED] first_page=None does not crash unresolved-guía panel

**Given** a `GuiaDeRemision` in `unresolved_guias` with `first_page = None`
  and `source_pages = [12, 13]`
**When** the review UI renders the "Unresolved guías" section
**Then** the guía is displayed using `source_pages` ([12, 13]) as the page reference
**And** no JavaScript error or blank panel occurs

### Scenario REV-C08 — [ADDED] first_page=0 displayed correctly; not treated as absent

**Given** a guía block with `first_page = 0` (genuinely the first page of the PDF)
**When** the review UI (or API serialisation) reads `first_page`
**Then** the displayed page reference is 0 (or "page 1" in 1-based display)
**And** the fallback to `source_pages[0]` is NOT triggered
**And** the page reference is not blank or "unknown"

---

---

## Delta — session-2026-06-04: thumbnail fallback + row-click discoverability

> The requirements below ADD or MODIFY behaviour relative to REV-001 through REV-C08 above.
> Source changes: #17 — page thumbnail fallback (merged), #19 — row-click drill-down (merged).
> Gate: 886 backend unit/targeted tests + 188 frontend vitest passing + Playwright visual validation.
> Each entry is marked [ADDED] or [MODIFIED: replaces <id>].

### REV-C07 — [MODIFIED: replaces REV-005 thumbnail source] Source page thumbnail MUST be served via deskew-PNG-or-fitz fallback chain

**[MODIFIED: REV-005 required "the post-deskew render" as the only source for the source page
image. This is incorrect when OCR or vision is disabled — no deskewed PNG is produced in those
modes, causing the `GET /runs/{id}/pages/{n}` endpoint to 404 for all thumbnails. REV-005 is
superseded by this fallback chain.]**

The `GET /runs/{id}/pages/{n}` endpoint MUST serve a thumbnail image for any valid page index
N using the following ordered fallback chain:

| Priority | Source | Condition |
|----------|--------|-----------|
| 1 (primary) | `<run_dir>/pages/{n}.png` | File exists on disk (produced by the deskew / OCR stage) |
| 2 (fallback) | On-demand fitz render | No deskewed PNG on disk; source PDF path is known from the run context |

When the fallback (priority 2) is triggered, the endpoint MUST:
- Render page N from the run's source PDF using PyMuPDF (fitz) at **120 DPI**.
- Cache the rendered PNG to `<run_dir>/pages/{n}.png` so subsequent requests for the same
  page are served from disk (priority 1) without re-rendering.
- Return the rendered image with an appropriate image MIME type.

The endpoint MUST return **404** only when:
- The run context does not exist (unknown `run_id`), OR
- The page index N is out of range for the source PDF.

The endpoint MUST NOT return 404 solely because the deskewed PNG was not produced by the
OCR or vision stage. This ensures thumbnails are available in all pipeline modes
(`ocr.enabled=false`, `vision.enabled=false`, or any combination thereof).

The fitz render path is independent of the deskew/OCR pipeline; it MUST NOT require
PaddleOCR to be installed.

#### Acceptance Scenarios

**Scenario REV-C09 — Deskewed PNG exists: served directly**

Given a completed run where `<run_dir>/pages/47.png` was produced by the OCR stage
When `GET /runs/{run_id}/pages/47` is called
Then the response is `200 OK` with the deskewed PNG content
And fitz is NOT invoked

**Scenario REV-C10 — Deskewed PNG absent (OCR-off mode): fitz fallback renders and caches**

Given a run with `ocr.enabled = false` (NullOcrExtractor — no deskewed PNGs produced)
And the run's source PDF is accessible from the run context
When `GET /runs/{run_id}/pages/12` is called
Then the endpoint renders page 12 from the source PDF via fitz at 120 DPI
And returns a `200 OK` image response
And `<run_dir>/pages/12.png` is written to disk (cache)
When `GET /runs/{run_id}/pages/12` is called a second time
Then the deskewed-PNG-exists path (priority 1) serves it from disk — fitz is NOT re-invoked

**Scenario REV-C11 — Out-of-range page index: 404**

Given a source PDF with 50 pages (indices 0–49)
When `GET /runs/{run_id}/pages/99` is called
Then the response is `404 Not Found`

**Scenario REV-C12 — Unknown run: 404**

Given no run exists with the requested `run_id`
When `GET /runs/{run_id}/pages/0` is called
Then the response is `404 Not Found`

---

### REV-C08 — [ADDED] Row-click toggles guía drill-down; aria-expanded a11y

**[CONTEXT: REV-C01 specifies that each row is "expandable" to reveal guía contributions but
does not mandate the interaction trigger. Previously the drill-down required interaction with a
dedicated expand control (button or chevron); the row body itself was not clickable. This
reduced discoverability because the expand target was small. #19 makes the full row body the
primary expand trigger.]**

Clicking anywhere on an item row (the `<tr>` or equivalent row container) in the
reconciliation grid MUST toggle the GuiaDrillDown expansion for that row, EXCEPT when the
click target is an interactive control inside the row (e.g., a button, input, select, anchor,
or a custom component with `role="button"` or `role="dialog"`). Inner controls MUST remain
independently activatable without toggling the row expansion.

The row container MUST carry `aria-expanded="true"` when the drill-down is visible and
`aria-expanded="false"` when collapsed. This attribute MUST be updated on every toggle.

The GuiaDrillDown content toggled by row-click MUST be the same component already used by
the existing dedicated expand control (REV-C01). No new component is introduced; this
requirement covers the trigger surface, not the drill-down content.

#### Acceptance Scenarios

**Scenario REV-C13 — Row-click expands drill-down; aria-expanded updates**

Given a reconciliation row in collapsed state (`aria-expanded="false"`)
When the engineer clicks the row body (not a button or input inside it)
Then the GuiaDrillDown for that row becomes visible
And the row container updates to `aria-expanded="true"`

**Scenario REV-C14 — Second row-click collapses drill-down**

Given a reconciliation row in expanded state (`aria-expanded="true"`)
When the engineer clicks the row body again
Then the GuiaDrillDown collapses
And the row container updates to `aria-expanded="false"`

**Scenario REV-C15 — Click on inner control does NOT toggle expansion**

Given a reconciliation row in collapsed state
And the row contains a "Reassign" button (GuiaReassignDialog trigger)
When the engineer clicks the "Reassign" button
Then the GuiaReassignDialog opens (button's default behavior)
And the row expansion state is NOT changed (drill-down remains collapsed)

---

## Acceptance Scenarios — Delta session-2026-06-04

*(Inline above per requirement — REV-C09 through REV-C15.)*

---

## Delta — guia-reprocess-bulk-viewer (2026-06-06): bulk AI reprocess + page viewer + Acciones menu

> The requirements below ADD behaviour relative to REV-001 through REV-C15 above.
> This change introduces four UX features (F1–F4), fixes one backend bug (#42), and extends the operator-correction capability.
> All requirements are additive to the `review` capability — no existing requirements are removed.
> Change: guia-reprocess-bulk-viewer. Gate: 576 backend + 322 frontend tests passing + SA-5 Playwright validation (4 features: tabs, bulk reprocess, page viewer, acciones/corregir).
> Commits PR-A #47, PR-B #48, PR-C #49 merged to main (2026-06-06). Each entry is marked [ADDED].

### REV-R20 — Bulk per-Registro AI reprocess endpoint (F1)

The system MUST expose `POST /runs/{run_id}/registros/{registro}/reprocess` that accepts a
batch reprocess request for all errored guías belonging to a specific `registro`.

The endpoint MUST:
- Return **202 Accepted** immediately; processing runs in a background task.
- Dispatch `ReprocessService.apply_reprocess` for each errored guía in the registro, bounded
  by the existing `Semaphore(reprocess_max_concurrency=3)` — no new unbounded vision path.
- Use `asyncio.gather(..., return_exceptions=True)` so that a per-guía failure MUST NOT abort
  the batch.
- Return a `ReprocessBatchResponse` DTO with at minimum: `registro`, `total`, `recovered`,
  `failed` counts.

When `vision.enabled=False` (NullVisionAdapter), the endpoint MUST return **503 Service
Unavailable** with a diagnostic body — bulk reprocess requires vision; no-vision mode is not
a valid target.

The endpoint MUST NOT set `retry_attempted=True` on any `ErroredGuia` for a vision failure.
`retry_attempted` gates the SUNAT REINTENTAR button; bulk AI reprocess is stateless-retryable
and MUST NOT consume that flag.

Every line recovered by the batch MUST carry `requires_review=True` (same invariant as
REV-R12).

#### Scenario REV-R20-S01: all guías recovered — 202 with full count

- GIVEN a registro with 3 errored guías, vision enabled
- WHEN `POST /runs/{run_id}/registros/{registro}/reprocess` is called
- THEN the response is `202 Accepted`
- AND after processing completes, `recovered=3`, `failed=0` in the batch result
- AND all recovered lines have `requires_review=True`

#### Scenario REV-R20-S02: partial failure — failed guías stay errored; batch not aborted

- GIVEN a registro with 3 errored guías
- AND vision returns empty `[]` for guía 2 (transient vision-empty result)
- WHEN the batch runs
- THEN guías 1 and 3 are recovered; guía 2 stays in errored state
- AND `recovered=2`, `failed=1`
- AND the batch does NOT abort due to guía 2's failure

#### Scenario REV-R20-S03: concurrency bounded at 3

- GIVEN a registro with 6 errored guías, all processing concurrently
- WHEN the batch executes
- THEN at no point are more than 3 `apply_reprocess` calls executing simultaneously
- AND the shared `Semaphore(reprocess_max_concurrency=3)` bounds both per-guía and bulk calls

#### Scenario REV-R20-S04: vision disabled — 503

- GIVEN `vision.enabled=False`
- WHEN `POST /runs/{run_id}/registros/{registro}/reprocess` is called
- THEN the response is `503 Service Unavailable`
- AND no `apply_reprocess` call is made

#### Scenario REV-R20-S05: retry_attempted NOT set on vision failure

- GIVEN a registro with 2 errored guías
- AND vision returns `[]` for both (vision-empty; no recovery)
- WHEN the batch completes with `failed=2`
- THEN `retry_attempted` is `False` on both `ErroredGuia` objects
- AND no `mark_retry_attempted` is called by the bulk path

#### Scenario REV-R20-S06: recovered lines are always requires_review=True

- GIVEN the vision adapter returns lines with `requires_review=False` (hypothetical override)
- WHEN the batch builds recovered guías
- THEN every stored `MaterialLine` has `requires_review=True`

---

### REV-R21 — Bulk reprocess — frontend confirm + live progress + summary (F1)

The frontend MUST present the following flow for bulk per-Registro reprocess:

1. **Confirm dialog**: before firing, MUST display the number of errored guías in that
   registro and the equivalent number of AI vision calls (N guías = N calls). The engineer
   MUST confirm before the request is sent.
2. **Live progress**: while the background batch runs, successfully recovered guías MUST
   leave the pending list incrementally (incremental table invalidation / polling); the UI
   MUST NOT wait for all guías to complete before updating.
3. **Completion summary**: after the batch is fully done, the UI MUST display
   "N recuperadas / M fallaron" (or equivalent localizable text) where N + M = total.
4. **Button gating**: the bulk button MUST be disabled while a batch is in-flight for that
   registro to prevent double-submission.

#### Scenario REV-R21-S01: confirm dialog shows call count before firing

- GIVEN a registro with 4 errored guías
- WHEN the engineer clicks "Procesar todos con IA"
- THEN a confirm dialog is shown: "¿Procesar 4 guías con IA? = 4 llamadas" (or equivalent)
- AND the request is NOT sent until the engineer confirms

#### Scenario REV-R21-S02: recovered guías leave pending list incrementally

- GIVEN a batch of 5 errored guías is processing
- WHEN guía 2 is recovered mid-batch
- THEN guía 2 disappears from the pending list before guías 3–5 are done
- AND the rest of the pending list remains visible until each is resolved or fails

#### Scenario REV-R21-S03: completion summary displayed

- GIVEN a batch of 5 guías, 3 recovered and 2 failed
- WHEN the batch completes
- THEN the UI shows "3 recuperadas / 2 fallaron"
- AND the 2 failed guías remain in the pending panel

#### Scenario REV-R21-S04: bulk button disabled while in-flight

- GIVEN a batch is currently running for registro 232
- WHEN the engineer views the Pendientes tab for registro 232
- THEN the "Procesar todos con IA" button is disabled (not clickable)

---

### REV-R22 — Drill-down page chips open PageSheetViewer at guía source pages (F2)

In `GuiaDrillDown`, the page reference for each `GuiaContribution` MUST be rendered as
interactive chips (using `<SourcePages>`). Clicking a chip MUST open `PageSheetViewer` at
the corresponding page, with `viewerRowPages` bound to that guía's own `source_pages` array.

The plain-text "Páginas" span MUST be replaced by `<SourcePages>`.
The `pageClick` event MUST bubble from `GuiaDrillDown` → `ReconciliationRow` → `ReviewPage`
→ `PageSheetViewer`.
No additional API call is required; `source_pages` is already present in
`GuiaContributionResponse`.

#### Scenario REV-R22-S01: page chip click opens PageSheetViewer at correct page

- GIVEN the drill-down for guía `T009-0741770` shows `source_pages = [45, 46]`
- WHEN the engineer clicks chip "45"
- THEN `PageSheetViewer` opens showing page 45
- AND `viewerRowPages` is `[45, 46]` (that guía's own pages, not the full row's pages)

#### Scenario REV-R22-S02: pageClick event does not trigger row expansion toggle

- GIVEN a collapsed reconciliation row
- AND the drill-down chip for page 45 is visible after expansion
- WHEN the engineer clicks the page chip
- THEN `PageSheetViewer` opens
- AND the drill-down expansion state is unchanged (REV-C08 inner-control exemption applies)

---

### REV-R23 — ReviewPage tabs: Reconciliación | Pendientes por procesar (F3)

`ReviewPage` MUST expose two tabs:

- **"Reconciliación"** (default): the existing reconciliation grid (REV-C01 through REV-C08
  are unchanged in this tab).
- **"Pendientes por procesar"**: contains `ErroredGuiasPanel` (the errored guías list, per
  REV-E05) and the per-Registro bulk reprocess button (REV-R20/REV-R21).

The "Pendientes" tab MUST display a count badge showing the total number of errored guías
across all registros. The badge MUST update when guías are recovered.

The default active tab MUST be "Reconciliación" on page load.

URL-deep-linked tab routing is OUT OF SCOPE.

#### Scenario REV-R23-S01: default tab is Reconciliación on load

- GIVEN the engineer navigates to ReviewPage for a run with 3 errored guías
- WHEN the page renders
- THEN the "Reconciliación" tab is active and the reconciliation grid is visible
- AND the "Pendientes" tab shows badge "3"
- AND `ErroredGuiasPanel` is NOT rendered in the default view

#### Scenario REV-R23-S02: Pendientes tab shows errored guías and bulk button

- GIVEN 3 errored guías for registro 232 and 1 for registro 230
- WHEN the engineer clicks the "Pendientes" tab
- THEN `ErroredGuiasPanel` is visible grouped by registro
- AND each registro group shows a "Procesar todos con IA" button
- AND the reconciliation grid is NOT visible in this tab

#### Scenario REV-R23-S03: badge updates after bulk recovery

- GIVEN the Pendientes tab badge shows "4" (4 errored guías)
- WHEN a batch recovers 3 guías for registro 232
- THEN the badge updates to "1"

---

### REV-R24 — Drill-down [Acciones] menu: Reasignar · Reprocesar · Corregir manual (F4)

The single `[Reasignar]` button in `GuiaDrillDown` MUST be replaced by an `[Acciones]`
dropdown menu with three items:

| Item | Behavior |
|------|----------|
| Reasignar | Existing reassign flow (REV-C02) — no behavior change |
| Reprocesar | Triggers single-guía AI reprocess (reuses the per-guía reprocess endpoint from PR#3) |
| Corregir manual | Opens a correction form (REV-R25) |

The menu MUST be visible per `GuiaContribution` entry in the drill-down.

#### Scenario REV-R24-S01: Reasignar opens reassign dialog (unchanged)

- GIVEN a guía in the drill-down
- WHEN the engineer opens [Acciones] and clicks "Reasignar"
- THEN the `GuiaReassignDialog` opens, behavior identical to existing REV-C02

#### Scenario REV-R24-S02: Reprocesar triggers single-guía reprocess

- GIVEN a guía `T009-0741770` in the drill-down
- WHEN the engineer opens [Acciones] and clicks "Reprocesar"
- THEN the per-guía reprocess endpoint (PR#3) is called for `T009-0741770`

#### Scenario REV-R24-S03: [Acciones] does not trigger row expansion toggle

- GIVEN a collapsed reconciliation row with the drill-down visible after expand
- WHEN the engineer opens the [Acciones] menu
- THEN the row expansion state is unchanged (REV-C08 inner-control exemption applies)

---

### REV-R25 — Corregir manual: operator assigns declared material + cantidad (F4)

"Corregir manual" MUST open a correction form for the selected `GuiaContribution` with:

1. **Material dropdown**: populated with the declared materials for that guía's `registro`
   (i.e., the `(material_canonical, unidad)` pairs from the reconciliation rows of that
   registro). Only that registro's declared materials are listed — NOT all registros.
2. **Cantidad input**: a numeric field for the operator to enter the corrected quantity.

On submit, the correction MUST:
- Update the guía line to the operator-selected `material_canonical` and `unidad`, and the
  entered `cantidad`.
- Trigger a re-reconcile of the affected registro groups.
- Set `requires_review=True` on the corrected line.
- Record the action in the audit trail with `action_type="manual_correction"`.

The corrected line MUST NOT bypass `requires_review`. Manual correction is an operator-assigned
match, not a confirmed-accurate value.

#### Scenario REV-R25-S01: dropdown lists only that Registro's declared materials

- GIVEN guía `T009-0741770` belongs to `registro=232`
- AND registro 232 has declared materials: ["BARRA CORRUGADA 1/2" KG, "BARRA CORRUGADA 3/4" KG]
- AND registro 230 has declared material: ["ALAMBRE MQ #16" KG]
- WHEN the engineer opens "Corregir manual" for `T009-0741770`
- THEN the material dropdown shows ONLY "BARRA CORRUGADA 1/2 KG" and "BARRA CORRUGADA 3/4 KG"
- AND "ALAMBRE MQ #16 KG" is NOT in the dropdown

#### Scenario REV-R25-S02: submit updates guía line and re-reconciles

- GIVEN the engineer selects "BARRA CORRUGADA 1/2 KG" and enters `cantidad=500.0`
- WHEN the correction is submitted
- THEN the guía line is updated: `material_canonical="BARRA CORRUGADA 1/2"`, `unidad="KG"`,
  `cantidad=500.0`
- AND the reconciliation group for `(232, "BARRA CORRUGADA 1/2", "KG")` recomputes
- AND the MATCH/MISMATCH badge updates

#### Scenario REV-R25-S03: corrected line stays requires_review=True

- GIVEN the correction is submitted with a valid material and cantidad
- WHEN the guía line is stored
- THEN `requires_review=True` on the corrected line
- AND the audit trail records `action_type="manual_correction"`, old material, new material,
  old cantidad, new cantidad, `guia_id`, `registro`

#### Scenario REV-R25-S04: correction does not re-run OCR or vision

- GIVEN a manual correction is submitted
- THEN no `VisionLLMPort.read_material_table` call is made
- AND no OCR or PDF rendering step is triggered
- AND only the affected reconciliation groups recompute (REV-006 scope preserved)

---

### REV-R26 — Fix #42: _retry_batch calls mark_retry_attempted per guía

The SUNAT `_retry_batch` function MUST call `mark_retry_attempted` for each guía it processes,
matching the behavior of the synchronous per-guía retry path.

This is a bug fix: the absence of `mark_retry_attempted` in `_retry_batch` means the
`retry_attempted` flag is never set after a batch SUNAT retry, leaving REINTENTAR buttons
permanently active even after the guía has already been retried.

The bulk AI reprocess (REV-R20) MUST NOT call `mark_retry_attempted` — that flag is exclusive
to the SUNAT REINTENTAR path.

#### Scenario REV-R26-S01: _retry_batch sets retry_attempted per guía

- GIVEN a registro with 2 errored guías, `retry_attempted=False` for both
- WHEN `_retry_batch` completes (regardless of SUNAT success or failure)
- THEN both guías have `retry_attempted=True`
- AND the change is persisted so the REINTENTAR button becomes inactive

#### Scenario REV-R26-S02: bulk AI reprocess does NOT set retry_attempted

- GIVEN the bulk `POST /registros/{registro}/reprocess` completes
- THEN `retry_attempted` is unchanged on all guías (still `False` or whatever it was before)
- AND `mark_retry_attempted` is NOT called by the bulk AI path

---

## MUST-NOT Invariants (auto-reject) — Extended for guia-reprocess-bulk-viewer

- All prior MUST-NOT invariants from REV-001 through REV-C15 remain in effect.
- `fecha` MUST NOT be introduced as a grouping axis. Group key remains
  `(registro, material_canonical, unidad)`.
- Units MUST NOT be converted across `KG`, `TN`, `RD`, `Rollo`.
- Grades G60, G42, G75 MUST remain distinct — NEVER collapsed.
- Recovered and manually corrected lines MUST carry `requires_review=True`. No auto-accept.
- Bulk reprocess MUST use the bounded `apply_reprocess` path — no new unbounded vision call.
- `vision.enabled=False` MUST return 503 for bulk reprocess — no silent no-op.
- The input PDF MUST NOT be modified. Runs write to isolated output directories.
- Domain layer (`domain/`) MUST remain pure — no SDK, framework, or IO import.
- `application/pipeline.py` MUST NOT import concrete adapters — Protocols only.

## MUST-NOT Invariants (auto-reject) — Extended for optional-vision-key-ui

- All prior MUST-NOT invariants from REV-001 through REV-R33 remain in effect.
- Reprocess controls MUST NOT be removed from the DOM when vision is off (hidden ≠ disabled).
- The backend 503 backstop (REV-R20-S04) MUST NOT be removed or weakened by this delta.
- The gating state MUST NOT be based on a hardcoded compile-time flag; it MUST read the
  runtime capabilities store.
- SA-5 Playwright runtime validation is MANDATORY for gating and settings modal flows before
  marking this capability complete.

---

## Acceptance Scenarios — Delta guia-reprocess-bulk-viewer

*(Inline above per requirement — REV-R20-S01 through REV-R26-S02.)*

---

## SDD#2 Delta — discarded-pages-recovery (merged 2026-06-11)

> Additive delta from `openspec/changes/archive/discarded-pages-recovery/specs/review/spec.md`.
> All existing review requirements (REV-001 through REV-R26 and all deltas) remain in force.
> **Product decisions fixed (2026-06-11) — not re-openable at spec level:**
> 1. UI only for the discarded ("possible guías") bucket — no arbitrary-page processing UI.
> 2. Registro inherited from section — no mandatory assignment dialog on recovery.
> 3. Bulk recovery WITH thumbnail preview + checkbox selection (mirrors PR#49 bulk flow).
> 4. OCR-first recovery reusing cached lines; vision is last resort.
> 5. History/persistence out of scope (SDD#3).

### What MUST be true after this change is applied

1. `ReviewPage` exposes three tabs: Reconciliación | Pendientes por procesar |
   **Descartadas para revisión**.
2. The Descartadas tab lists every discarded entry from `PipelineResult` (EXT-034/EXT-035),
   each with its page number and thumbnail. Zero entries = empty-state message; no entries
   are hidden.
3. The operator can select individual discarded entries via checkboxes and trigger a bulk
   recovery that OCR-first re-processes selected pages.
4. Recovered guías land in the reconciliation result under the section registro, with ALL
   recovered lines flagged `requires_review=True`.
5. Non-guía sheets deselected by the operator in the bulk-selection UI are NOT processed.
6. The Pendientes tab count badge, Reconciliación grid, and all prior review behavior are
   unchanged by this delta.

---

### REV-R27 — [ADDED] ReviewPage: three-tab layout including [Descartadas para revisión]

**[MODIFIED: REV-R23 specifies a two-tab layout (Reconciliación | Pendientes). This
requirement extends it to three tabs. REV-R23's behavior for the first two tabs is
unchanged.]**

`ReviewPage` MUST expose three tabs:

| Tab | Content |
|---|---|
| Reconciliación | Existing reconciliation grid (unchanged — REV-C01 through REV-R26) |
| Pendientes por procesar | Existing errored guías + bulk AI reprocess (unchanged — REV-R20 through REV-R26) |
| Descartadas para revisión | New — discarded pages list (this delta) |

The tab order MUST be: Reconciliación (index 0, default) → Pendientes (index 1) → Descartadas
(index 2).

The "Descartadas" tab MUST display a count badge. The `TAB_ORDER` array MUST be extended to
`['reconciliacion', 'pendientes', 'descartadas']`. No existing tab index or routing behavior
is changed. The default active tab MUST remain "Reconciliación" on page load.

#### Scenario REV-R27-S01: three tabs visible; default is Reconciliación

Given a run with 2 discarded pages and 1 errored guía
When the engineer navigates to ReviewPage
Then three tabs are visible: "Reconciliación", "Pendientes", "Descartadas para revisión"
And the "Reconciliación" tab is active (default)
And the "Pendientes" tab shows badge "1"
And the "Descartadas" tab shows badge "2"

#### Scenario REV-R27-S02: zero discarded entries — badge is 0 or hidden; tab still present

Given a run with 0 discarded pages
When the engineer views ReviewPage
Then the "Descartadas" tab is present in the tab bar
And the badge shows "0" or is hidden (implementation choice)
And clicking the tab shows an empty-state message (no entries)

#### Scenario REV-R27-S03: Reconciliación and Pendientes tabs unaffected

Given the SDD#2 change applied
When the engineer uses the Reconciliación and Pendientes tabs
Then all behavior from REV-R20 through REV-R26 is unchanged
And no existing test scenario from the prior review deltas is invalidated

---

### REV-R28 — [ADDED] Descartadas tab: discarded entries list with thumbnail and page number

The "Descartadas para revisión" tab MUST render a list of discarded entries sourced from the
run's discarded collection (EXT-035). Each entry MUST display: page number, thumbnail (via
`GET /runs/{run_id}/pages/{page}/thumbnail` with REV-C07 fitz fallback), registro (or "sin
registro" label when `None`), and a checkbox for operator selection. The tab MUST also provide
a per-page sheet viewer link that opens `PageSheetViewer` (PR#48). Empty state = empty-state
message; no entries hidden.

Entries are grouped into **contiguous page runs** per registro (A1 grouping). Groups are
**collapsed by default** (zero `<img>` rendered on mount — avoids 343 thumbnail requests).
Expanding a group renders `<img loading="lazy">` thumbnails per page (A2 lazy load).

#### Scenario REV-R28-S01: entry shows page number, thumbnail, registro

Given a run with a discarded entry at page 152, registro "232"
When the engineer opens the "Descartadas" tab
Then the entry is listed with page number 152
And a thumbnail image is rendered for page 152
And the registro "232" is displayed alongside the thumbnail
And a checkbox is visible for selection

#### Scenario REV-R28-S02: thumbnail available via fitz fallback (no deskewed PNG)

Given a run where the deskewed PNG for page 152 was NOT produced
When the "Descartadas" tab renders the entry for page 152
Then the thumbnail is still displayed (fitz fallback path — REV-C07)

#### Scenario REV-R28-S03: registro=None entry displayed with label

Given a discarded entry with `registro=None` and `source_page=88`
When the "Descartadas" tab renders this entry
Then a "sin registro" label is shown in place of the registro value
And the entry is still selectable via checkbox

#### Scenario REV-R28-S04: sheet viewer opens for selected page

Given the engineer clicks the sheet-viewer action for page 152
When `PageSheetViewer` opens
Then the viewer shows page 152 and the discarded tab remains visible

#### Scenario REV-R28-S05: empty discarded collection shows empty state

Given a run with 0 discarded entries
When the engineer opens the "Descartadas" tab
Then an empty-state message is displayed
And no checkboxes or thumbnails are rendered

---

### REV-R29 — [ADDED] Descartadas tab: checkbox selection

The "Descartadas" tab MUST provide per-page checkbox selection, per-group tri-state header
checkbox (all/some/none), and a global "Seleccionar todas (N)" control. Selection state is
ephemeral — no backend call to maintain it.

The "Recuperar seleccionadas" button MUST be disabled when `selected.size === 0`.

#### Scenario REV-R29-S01: select all enables bulk button with full count

Given 3 discarded entries listed
When the engineer clicks "select all"
Then all 3 checkboxes are checked and the bulk recover button shows "Recuperar 3 seleccionadas"

#### Scenario REV-R29-S02: deselect all disables bulk button

Given all 3 entries are selected
When the engineer clicks "deselect all"
Then all 3 checkboxes are unchecked and the bulk recover button is disabled

#### Scenario REV-R29-S03: partial selection — non-guía sheet excluded by operator

Given 3 discarded entries; engineer checks only pages 152 and 175
Then bulk recover button shows "Recuperar 2 seleccionadas"
And when bulk recovery runs, only pages 152 and 175 are processed

---

### REV-R30 — [ADDED] Bulk recovery: OCR-first with progress, bounded concurrency, and requires_review

The bulk recovery action MUST:

1. **Confirm before firing**: display count + ETA + vision-cost warning when OCR-empty pages
   are selected. Confirmation MUST be required before the request is sent (mirrors REV-R21).
2. **Submit to `POST /runs/{run_id}/discarded-pages/recover-batch`**: body `{pages: [...]}`
   returns 202. Batch status polled via `GET /runs/{run_id}/discarded-pages/recover-status`.
3. **Bounded concurrency**: recovery service semaphore bounds parallel calls.
4. **Incremental progress**: recovered pages leave the discarded list incrementally via
   parent prop refresh on each poll tick. Settlement EXCLUSIVELY on `status.done === true`
   (PR#49 SA-5 lesson — no timing heuristic).
5. **Completion summary**: "N recuperadas / M falló" after `done=true`.
6. **Failed pages remain**: a recovery failure MUST leave the entry in the discarded list.
7. **Button gating**: "Recuperar seleccionadas" disabled while batch in-flight; per-page
   buttons also disabled (A4 mount re-attach).
8. **All recovered lines MUST carry `requires_review=True`** — absolute, no exception.
9. **Payload is `selectedLive`**: `selected` ∩ current `discardedPages` — a singly-recovered
   page that disappears from the prop before batch fire NEVER reaches the payload.

#### Scenario REV-R30-S01: confirm dialog before firing

Given 2 pages selected
When the engineer clicks "Recuperar seleccionadas"
Then a confirm dialog appears and the recovery request is NOT sent until confirmed

#### Scenario REV-R30-S02: recovered page leaves discarded list incrementally

Given 3 pages selected; page 152 is recovered first
When recovery for page 152 completes
Then page 152 disappears from the discarded list before 175 and 200 complete

#### Scenario REV-R30-S03: all recovered lines have requires_review=True

Given a discarded entry with cached OCR confidence >= 0.95
When recovery completes
Then every `MaterialLine` in the recovered `GuiaDeRemision` has `requires_review=True`

#### Scenario REV-R30-S04: failed page stays in discarded list

Given OCR returns `[]` and vision returns `[]` for page 88
When the recovery attempt completes
Then the entry REMAINS in the discarded list and the completion summary shows "1 fallaron"

#### Scenario REV-R30-S05: bulk button disabled while in-flight

Given a bulk recovery is currently running
Then the "Recuperar seleccionadas" button is disabled

#### Scenario REV-R30-S06: partial failure — completed pages recovered; failed pages remain

Given pages 152 and 200 recover; page 175 fails
When the batch completes
Then pages 152 and 200 are removed; page 175 remains; summary "2 recuperadas / 1 falló"

---

### REV-R31 — [ADDED] Recovery endpoint: per-page, accessible from discarded context

Endpoints:
- `POST /runs/{run_id}/discarded-pages/{page}/recover` — single-page, 404 if not in
  discarded list, 409 if run not READY.
- `POST /runs/{run_id}/discarded-pages/recover-batch` — body `{pages: list[int]}`, 202,
  409 if batch already in-flight.
- `GET /runs/{run_id}/discarded-pages/recover-status` — `{total, recovered, failed, done}`.
  Terminal shape when no batch submitted: `{total: 0, recovered: 0, failed: 0, done: true}`.

The endpoint MUST NOT require the caller to supply a registro — inherited from the discarded
entry. When `vision.enabled=False`, OCR is still attempted; if OCR also fails, the entry
stays discarded with a structured failure reason, NOT a 503.

#### Scenario REV-R31-S01: recovery with cached lines succeeds without OCR/vision calls

Given page 152 with `cached_lines=[MaterialLine(...)]`
When `POST /runs/{run_id}/discarded-pages/152/recover` is called
Then `ExtractionPort` NOT called; `VisionLLMPort` NOT called; response indicates success

#### Scenario REV-R31-S02: recovery with empty cached lines triggers OCR, succeeds

Given page 88 with `cached_lines=[]`
When the recovery endpoint processes page 88
Then `ExtractionPort.extract_printed_table` is called; OCR result used

#### Scenario REV-R31-S03: recovery failure returns structured error; entry stays discarded

Given page 88 where OCR returns [] and vision returns []
When the recovery endpoint processes page 88
Then response includes a structured failure reason (not 500); entry REMAINS in review state

#### Scenario REV-R31-S04: vision-off mode: OCR still attempted; failure is not a 503

Given `vision.enabled=False` and page 88 with `cached_lines=[]`
When the recovery endpoint processes page 88
Then OCR IS called; if OCR returns empty, response is a structured failure (NOT 503)

#### Scenario REV-R31-S05: registro inherited; no caller-supplied registro required

Given page 152 with `registro="232"` in the discarded entry
When recovery completes
Then `guia.registro = "232"` and no assignment dialog is triggered

---

### REV-R32 — [ADDED] Recovered guía flows through canonical matching; re-reconciliation triggered

After recovery, the `GuiaDeRemision` created from the recovered lines MUST flow through the
existing reconciliation pipeline (canonical matching, grouping by
`(registro, material_canonical, unidad)`, MATCH/MISMATCH detection). Units MUST NOT be
converted. Re-reconciliation MUST NOT re-run OCR, vision, or classification stages.

#### Scenario REV-R32-S01: recovered guía appears in reconciliation drill-down

Given page 152, registro "232", recovered with lines matching declared material
When recovery completes
Then a `ReconciliationRow` for the group is updated and the drill-down includes the recovered
guía with its synthetic `guia_id` and `source_pages = [152]`

#### Scenario REV-R32-S02: recovered quantity mismatch produces MISMATCH; not auto-corrected

Given declared qty = 0.191 TN and recovered OCR qty = 0.190 TN
When reconciliation runs
Then status = MISMATCH; `requires_review=True`; declared value unchanged

#### Scenario REV-R32-S03: recovery does not re-trigger OCR/vision/classification

Given a successful recovery triggering re-reconciliation for registro 232
When the re-reconciliation step runs
Then no `PageClassifier`, `ExtractionPort`, or `VisionLLMPort` call is made beyond the
recovery step itself

---

### REV-R33 — [ADDED] API: discarded entries surfaced in run response

The `GET /table` (review-table endpoint) MUST include `discarded_pages: DiscardedPageResponse[]`
alongside the existing `errored_guias` list. Each `DiscardedPageResponse` MUST carry at
minimum: `source_page` (int), `registro` (str | None), `has_cached_lines` (bool — indicates
whether `cached_lines` is non-empty; raw `MaterialLine` objects MUST NOT be exposed to the
frontend). The field MUST default to `[]` for runs with no discarded entries.

**[SUPERSEDED by design D1 — structural discrimination]**: the original constraint proposed
a per-entry `reason`/`type` discriminator field on a shared DTO. Design D1 superseded this
with **structural discrimination**: `discarded_pages` and `errored_guias` are separate
top-level lists, each with its own DTO. The list a DTO lives in IS the discriminator —
the frontend routes `discarded_pages` → Descartadas and `errored_guias` → Pendientes by
structure. No `reason`/`type` field is needed for tab routing.

The `identity_source` field on any DTO associated with a recovered page MUST use the new
`"operator"` Literal value (EXT-037). The schema MUST be updated in lockstep at all four
sites.

#### Scenario REV-R33-S01: discarded entries in API response; empty by default

Given a run with 0 discarded entries
When `GET /table` is called
Then `discarded_pages: []` in the response and no existing consumer is broken

#### Scenario REV-R33-S02: discarded entries include source_page, registro, cached_lines indicator

Given a run with 1 discarded entry at page 152, registro "232", 2 OCR lines
When the review API response is retrieved
Then the entry includes `source_page=152`, `registro="232"`, `has_cached_lines=true`

#### Scenario REV-R33-S03: discarded entries are structurally distinguished from errored guías

**[SUPERSEDED by design D1 — structural discrimination]** Under D1:

Given a run with 1 discarded entry AND 1 errored guía
When the API response is inspected
Then the discarded entry appears ONLY in `discarded_pages` (as `DiscardedPageResponse`)
And the errored guía appears ONLY in `errored_guias` (as `ErroredGuiaResponse`)
And neither entry appears in the wrong list (structural discrimination, no `reason`/`type` field)

---

## MUST-NOT Invariants (auto-reject) — Extended for discarded-pages-recovery

- All prior MUST-NOT invariants from REV-001 through REV-R26 remain in effect.
- `fecha` MUST NOT be introduced as a grouping axis. Grouping key remains
  `(registro, material_canonical, unidad)`.
- Units MUST NOT be converted. Recovered lines carry units from OCR; NEVER normalized.
- Recovered lines MUST carry `requires_review=True`. No auto-accept under any circumstance.
- The existing "Reconciliación" and "Pendientes" tabs MUST NOT be broken by the third tab.
- The REV-C07 thumbnail fallback chain MUST NOT be removed or weakened.
- The input PDF MUST NOT be modified. Recovery renders pages from the read-only source PDF.
- Domain layer MUST remain pure. No SDK/IO import in `domain/`.
- `application/pipeline.py` MUST NOT import concrete adapters.
- The `TAB_ORDER` extension MUST NOT change existing tab indices (Reconciliación=0,
  Pendientes=1 are preserved; Descartadas is appended at index 2).
- Bulk recovery MUST NOT settle progress before all selected pages are truly done —
  the PR#49 SA-5 lesson applies. Settlement ONLY on `status.done === true`.
- The REINTENTAR (SUNAT) button MUST NOT appear for discarded/no-identity entries. The
  `retry_attempted` flag is exclusive to the SUNAT retry path (REV-R26).

---

## Acceptance Scenarios — Delta discarded-pages-recovery

*(Inline above per requirement — REV-R27-S01 through REV-R33-S03.)*

---

## Delta — optional-vision-key-ui (2026-06-12): vision-availability gating + settings modal

> The requirements below ADD to REV-001 through REV-R33 above.
> All existing review requirements remain in force.
> Change: optional-vision-key-ui. Gate: 61 backend targeted tests + 405 frontend vitest passing + SA-5 Playwright validation.
> PR #74 (backend) and PR #75 (frontend) merged to main. Each entry is marked [ADDED].

### REV-R34 — Reprocess surfaces gated visible-but-disabled when vision unavailable

The three AI reprocess surfaces MUST be rendered **visible but disabled** (not hidden) with an
explanatory tooltip when `capabilities.vision_enabled=false`. They MUST be rendered enabled
(interactive) when `capabilities.vision_enabled=true`.

Affected surfaces:

| Surface | Component | Action |
|---|---|---|
| Single-guía Reprocesar | `GuiaDrillDown` — [Acciones] Reprocesar item (REV-R24) | Single-guía AI reprocess |
| Errored guía Reprocesar con IA | `ErroredGuiasPanel` — per-guía reprocess button | Per-guía AI reprocess |
| Bulk Procesar todos con IA | `PendientesPorProcesarTab` / `ErroredGuiasPanel` — bulk button (REV-R20/R21) | Bulk AI reprocess |

Gating MUST be applied as a pre-click guard: the controls are non-interactive BEFORE the
engineer clicks, not as a post-click 503 error. The existing backend 503 (`vision.enabled=False`
returns 503 for reprocess endpoints — REV-R20-S04) remains as a safety backstop but MUST NOT
be the primary UX signal.

The tooltip text MUST communicate that a vision API key is required and direct the engineer to
the Settings modal. Exact copy is implementation-level; the spec requires the message conveys
the actionable path.

The disabled state MUST NOT remove the controls from the DOM — they MUST remain visible and
accessible (e.g., `disabled` attribute or equivalent non-interactive state), so the engineer
understands the feature exists and how to enable it.

#### Scenario REV-R34-S01: vision off — all three surfaces disabled with tooltip

- GIVEN `capabilities.vision_enabled=false`
- WHEN the engineer views the review UI (GuiaDrillDown, ErroredGuiasPanel, PendientesPorProcesarTab)
- THEN the [Acciones] > Reprocesar item is visible but disabled
- AND the per-guía reprocess button in ErroredGuiasPanel is visible but disabled
- AND the "Procesar todos con IA" bulk button is visible but disabled
- AND hovering each disabled control reveals a tooltip explaining that a vision key is required

#### Scenario REV-R34-S02: vision on — all three surfaces enabled

- GIVEN `capabilities.vision_enabled=true`
- WHEN the engineer views the review UI
- THEN all three reprocess surfaces are interactive (not disabled)
- AND no vision-key tooltip is shown on those controls

#### Scenario REV-R34-S03: disabled controls remain in the DOM

- GIVEN `capabilities.vision_enabled=false`
- WHEN the DOM is inspected
- THEN each reprocess control is present in the DOM with a disabled or non-interactive attribute
- AND NO reprocess control is conditionally absent (v-if=false or display:none hiding)

#### Scenario REV-R34-S04: pre-click gating — no 503 reaches engineer from disabled button

- GIVEN `capabilities.vision_enabled=false`
- AND all reprocess controls are disabled
- WHEN the engineer attempts to interact with a disabled control
- THEN no API call is made
- AND no 503 error is displayed to the engineer from this interaction

---

### REV-R35 — Capabilities state drives gating reactively

The disabled/enabled state of the reprocess surfaces MUST be derived reactively from the
`capabilitiesStore.vision_enabled` value (CAP-002). Gating MUST NOT be hardcoded.

If the capabilities store is not yet populated (loading state), the reprocess controls MUST
default to disabled until the store is resolved.

#### Scenario REV-R35-S01: loading state — controls default disabled

- GIVEN the app just started and the capabilities fetch is in-flight
- WHEN the engineer navigates to ReviewPage before the fetch resolves
- THEN all reprocess controls are in disabled state
- AND they transition to the correct enabled/disabled state once the fetch resolves

#### Scenario REV-R35-S02: gating is reactive — no hardcoded vision flag

- GIVEN the `capabilitiesStore.vision_enabled` value changes (e.g., simulated in tests)
- THEN the three reprocess controls reflect the updated state without a page reload

---

## Out of scope for this domain

- Pipeline execution (ingestion, extraction, normalization, reconciliation).
- Export file generation (handled by the export domain).
- Authentication or multi-user concurrent editing.
- Review state persistence across separate runs (different run directories) — each run has its own `review.json`.
- Cross-model consensus (#44) · deadline-guard request cancellation (#41) · unit-map (#43).
- Streaming/SSE batch progress (polling is MVP).
- URL-deep-linked tab routing.
- Rollback/undo for manual corrections or bulk reprocess.
- History/persistence of discarded entries across application restarts (SDD#3 — requires
  cross-restart persistence; today's `run_registry` is in-memory).
- UI for arbitrary-page recovery (no UI for pages not in the discarded collection).
