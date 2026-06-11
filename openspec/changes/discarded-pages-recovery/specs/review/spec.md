# Spec — Review Domain (Delta)
**Change**: discarded-pages-recovery (SDD#2)
**Domain**: review (delta against promoted spec at `openspec/specs/review/spec.md`)
**Phase**: spec
**Date**: 2026-06-11

---

## Purpose

This document is an additive delta to the promoted review spec. It specifies the behavioural
requirements for the [Descartadas para revisión] tab and the operator-triggered page-recovery
flow.

All existing review requirements (REV-001 through REV-R26 and all deltas) remain in force
unless explicitly modified below.

**Product decisions fixed by the user (2026-06-11) — not re-openable at spec level:**
1. UI only for the discarded ("possible guías") bucket — no arbitrary-page processing UI.
2. Registro inherited from section — no mandatory assignment dialog on recovery.
3. Bulk recovery WITH thumbnail preview + checkbox selection (mirrors "Procesar todos con IA"
   + selection; PR#49 bulk-progress lesson applies).
4. OCR-first recovery reusing cached lines; vision is last resort.
5. History/persistence out of scope (SDD#3).

---

## What MUST be true after this change is applied

1. `ReviewPage` exposes three tabs: Reconciliación | Pendientes por procesar |
   **Descartadas para revisión**.
2. The Descartadas tab lists every discarded entry from `PipelineResult` (EXT-034/EXT-035),
   each with its page number and thumbnail. Zero entries = empty-state message; no entries
   are hidden.
3. The operator can select individual discarded entries via checkboxes and trigger a bulk
   recovery that OCR-first re-processes selected pages.
4. Recovered guías land in the reconciliation result under the section registro, with ALL
   recovered lines flagged `requires_review=True`.
5. Non-guía sheets deselected by the operator in the bulk-selection UI are NOT processed —
   even if they appear in the discarded list, the operator makes the final call.
6. The Pendientes tab count badge, Reconciliación grid, and all prior review behavior are
   unchanged by this delta.

---

## Delta Requirements

> Each entry is marked [ADDED] or [MODIFIED: modifies <id>].

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

The "Descartadas" tab MUST display a count badge showing the total number of discarded entries
for the run. The badge MUST be `0` (or hidden) when there are no discarded entries; it MUST
NOT be absent from the tab bar when the run produced discarded entries.

The `TAB_ORDER` array (currently `['reconciliacion', 'pendientes']`) MUST be extended to
`['reconciliacion', 'pendientes', 'descartadas']`. No existing tab index or routing behavior
is changed.

The default active tab MUST remain "Reconciliación" on page load.

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

The "Descartadas para revisión" tab MUST render a list (or grid) of discarded entries sourced
from the run's discarded collection (EXT-035). Each entry in the list MUST display:

1. **Page number** — the `source_page` value of the discarded entry (zero-based index as
   returned by the API; display is implementation choice — 0-based or 1-based, but MUST be
   consistent with the rest of the UI).
2. **Thumbnail** — the page image, served via the existing
   `GET /runs/{run_id}/pages/{page}/thumbnail` (or `/image`) endpoint. The REV-C07 fitz
   fallback chain applies: thumbnail MUST be available regardless of whether a deskewed PNG
   was produced.
3. **Registro** — the section registro associated with the discarded entry (`registro` field),
   or a "sin registro" label when `registro` is `None`.
4. **Checkbox** — for operator selection before bulk recovery.

The tab MUST also provide a **per-page sheet viewer** link/action that opens `PageSheetViewer`
(the existing viewer from PR#48) for the selected page.

When the discarded collection is empty, the tab MUST display an empty-state message (e.g.,
"Sin páginas descartadas para esta ejecución").

#### Scenario REV-R28-S01: entry shows page number, thumbnail, registro

Given a run with a discarded entry at page 152, registro "232"
When the engineer opens the "Descartadas" tab
Then the entry is listed with page number 152 (or "Página 153" in 1-based display)
And a thumbnail image is rendered for page 152
And the registro "232" is displayed alongside the thumbnail
And a checkbox is visible for selection

#### Scenario REV-R28-S02: thumbnail available via fitz fallback (no deskewed PNG)

Given a run where the deskewed PNG for page 152 was NOT produced (e.g., OCR produced
  zero lines so no output PNG was written)
When the "Descartadas" tab renders the entry for page 152
Then the thumbnail is still displayed (fitz fallback path — REV-C07)
And the thumbnail renders the correct page image from the source PDF

#### Scenario REV-R28-S03: registro=None entry displayed with label

Given a discarded entry with `registro=None` and `source_page=88`
When the "Descartadas" tab renders this entry
Then the entry is listed with page number 88
And a "sin registro" (or equivalent) label is shown in place of the registro value
And the entry is still selectable via checkbox

#### Scenario REV-R28-S04: sheet viewer opens for selected page

Given the engineer clicks the sheet-viewer action for the discarded entry at page 152
When `PageSheetViewer` opens
Then the viewer shows page 152
And the viewer's page context (viewerRowPages) is set to [152] (just this page)
And the discarded tab remains visible (viewer is a modal or panel, not a navigation)

#### Scenario REV-R28-S05: empty discarded collection shows empty state

Given a run with 0 discarded entries
When the engineer opens the "Descartadas" tab
Then an empty-state message is displayed
And no checkboxes or thumbnails are rendered
And no API call to the discarded collection endpoint returns results

---

### REV-R29 — [ADDED] Descartadas tab: checkbox selection

The "Descartadas" tab MUST provide checkbox selection for individual entries AND a
"select all" / "deselect all" control.

Selection state MUST be tracked in the component (no backend call needed to maintain
selection). The selection is ephemeral — it does not persist across tab switches or page
reloads.

The engineer MUST be able to:
1. Select/deselect individual entries by clicking their checkbox.
2. Select all entries via a header "select all" checkbox (or equivalent control).
3. Deselect all entries via the same control (toggle behavior) or an explicit "deselect all"
   action.

The "Recuperar seleccionadas" (bulk recover) button MUST be disabled when no entries are
selected. The count of selected entries MUST be visible alongside the button (e.g.,
"Recuperar 2 seleccionadas").

#### Scenario REV-R29-S01: select all enables bulk button with full count

Given 3 discarded entries listed
When the engineer clicks "select all"
Then all 3 checkboxes are checked
And the bulk recover button shows "Recuperar 3 seleccionadas" and is enabled

#### Scenario REV-R29-S02: deselect all disables bulk button

Given all 3 entries are selected
When the engineer clicks "deselect all" (or unchecks the header checkbox)
Then all 3 checkboxes are unchecked
And the bulk recover button is disabled

#### Scenario REV-R29-S03: partial selection — non-guía sheet excluded by operator

Given 3 discarded entries, the engineer identifies entry at page 200 as a photo (non-guía)
When the engineer checks only entries at pages 152 and 175 (and leaves page 200 unchecked)
Then the bulk recover button shows "Recuperar 2 seleccionadas"
And when bulk recovery runs, only pages 152 and 175 are processed
And page 200 is NOT submitted for recovery (operator's explicit exclusion)

---

### REV-R30 — [ADDED] Bulk recovery: OCR-first with progress, bounded concurrency, and requires_review

The bulk recovery action MUST:

1. **Confirm before firing**: display the number of selected pages and an estimated cost
   (e.g., "¿Recuperar 2 páginas? OCR-primero, IA como último recurso"). Confirmation MUST be
   required before the request is sent. This mirrors the REV-R21 confirm-before-fire pattern.
2. **Submit to the recovery endpoint** (exact endpoint shape is a design decision —
   `POST /runs/{run_id}/pages/{page}/recover` per-page style with client-orchestrated loop,
   or a batch endpoint). Implementation shape is deferred to design. The spec requires only
   that all selected pages are submitted and processed.
3. **Recover in bounded concurrency**: the recovery service MUST NOT launch unbounded parallel
   recovery calls. Concurrency MUST be bounded (design decides the exact limit; RECOMMENDED:
   reuse the existing `reprocess_max_concurrency=3` semaphore or a dedicated equivalent).
4. **Incremental progress updates**: successfully recovered pages MUST leave the discarded
   list incrementally as they complete. The UI MUST NOT wait for all selected pages to finish
   before showing partial results. This is the same PR#49 SA-5 lesson applied here.
5. **Completion summary**: after all selected pages are processed, the UI MUST display
   a summary (e.g., "2 recuperadas / 1 falló" where the total = selected count).
6. **Failed pages remain in discarded list**: a recovery failure for a specific page (OCR
   empty + vision empty) MUST leave that entry in the discarded list. The entry MUST NOT be
   silently removed on failure.
7. **Button gating**: the "Recuperar seleccionadas" button MUST be disabled while a batch is
   in-flight to prevent double-submission.
8. **All recovered lines MUST carry `requires_review=True`**: this is absolute and applies
   regardless of OCR confidence, vision confidence, or `cached_lines` source.
9. **Never trigger bulk recovery on pages classified as non-guía**: the recovery service
   MUST only process pages from the discarded collection. The endpoint MUST NOT accept
   arbitrary page indices as a UI entry point for this feature (YAGNI guard — API-level
   generality is fine; no UI path for arbitrary-page submission in this change).

#### Scenario REV-R30-S01: confirm dialog before firing

Given 2 pages selected for bulk recovery
When the engineer clicks "Recuperar seleccionadas"
Then a confirm dialog appears: "¿Recuperar 2 páginas? OCR-primero, IA como último recurso"
And the recovery request is NOT sent until the engineer confirms

#### Scenario REV-R30-S02: recovered page leaves discarded list incrementally

Given 3 pages selected for bulk recovery
And page 152 is recovered first (has cached OCR lines)
When the recovery for page 152 completes
Then page 152 disappears from the discarded list
And pages 175 and 200 remain in the discarded list until their recovery completes or fails

#### Scenario REV-R30-S03: all recovered lines have requires_review=True

Given a discarded entry with `cached_lines` where all OCR confidence scores are >= 0.95
When the recovery completes for this page
Then every `MaterialLine` in the recovered `GuiaDeRemision` has `requires_review=True`
And the reconciliation result for the recovered registro shows the group flagged for review

#### Scenario REV-R30-S04: failed page stays in discarded list

Given a discarded entry with `cached_lines=[]`
And OCR re-run returns `[]` for this page
And vision fallback also returns `[]`
When the recovery attempt completes
Then the entry REMAINS in the discarded list with a failure indicator
And no `GuiaDeRemision` is added to the reconciliation result for this page
And the completion summary shows "1 fallaron"

#### Scenario REV-R30-S05: bulk button disabled while in-flight

Given a bulk recovery is currently running for 3 pages
When the engineer views the Descartadas tab
Then the "Recuperar seleccionadas" button is disabled (not clickable)

#### Scenario REV-R30-S06: partial failure — completed pages recovered; failed pages remain

Given 3 pages selected for bulk recovery
And page 152 recovers successfully
And page 175 fails (OCR + vision both empty)
And page 200 recovers successfully
When the batch completes
Then pages 152 and 200 are removed from the discarded list
And page 175 remains in the discarded list with a failure indicator
And the completion summary shows "2 recuperadas / 1 falló"

#### Scenario REV-R30-S07: vision NOT called when OCR succeeds (OCR-first invariant)

Given a discarded entry with `cached_lines=[]`
And OCR re-run for this page returns 2 non-empty `MaterialLine` objects
When the recovery processes this entry
Then `VisionLLMPort` is NOT called for material line extraction
And the 2 OCR-extracted lines are used as the recovered material lines

#### Scenario REV-R30-S08: concurrency bounded

Given 6 pages selected for bulk recovery simultaneously
When the recovery batch executes
Then at no point are more than the configured maximum (e.g., 3) recovery calls
  executing simultaneously

---

### REV-R31 — [ADDED] Recovery endpoint: per-page, accessible from discarded context

The system MUST expose a recovery endpoint keyed by page number (exact path is a design
decision — `POST /runs/{run_id}/pages/{page}/recover` style). This endpoint MUST:

1. Accept a page number corresponding to a discarded entry.
2. Execute the OCR-first recovery strategy (EXT-036): cached lines → OCR re-run → vision
   fallback.
3. On success: add the recovered `GuiaDeRemision` to the `ReviewService` state and trigger
   re-reconciliation for the affected registro.
4. On partial success (lines recovered but low confidence): add the guía with all lines
   flagged `requires_review=True`.
5. On failure (OCR + vision both empty): return a structured error; the discarded entry
   remains in the review state; no `GuiaDeRemision` is created.
6. MUST NOT be reachable from the [Descartadas] UI for arbitrary pages that are NOT in
   the discarded collection. The API-level generality is fine; the UI constrains the input
   to the discarded list.

The endpoint MUST NOT require the caller to supply a registro — it is inherited from the
discarded entry's `registro` field (decision 2).

When `vision.enabled=False` (NullVisionAdapter), the endpoint MUST still attempt OCR
recovery (cached lines → OCR re-run). Vision fallback is simply unavailable; if OCR also
fails, the entry stays discarded with a structured failure reason, NOT a 503.

#### Scenario REV-R31-S01: recovery with cached lines succeeds without OCR/vision calls

Given a discarded entry at page 152 with `cached_lines=[MaterialLine(...)]`
When `POST /runs/{run_id}/pages/152/recover` is called (or equivalent batch submission)
Then `ExtractionPort.extract_printed_table` is NOT called
And `VisionLLMPort` is NOT called
And the recovered `GuiaDeRemision` is assembled from the cached lines
And re-reconciliation for the affected registro is triggered
And the response indicates success

#### Scenario REV-R31-S02: recovery with empty cached lines triggers OCR, succeeds

Given a discarded entry at page 88 with `cached_lines=[]`
When the recovery endpoint processes page 88
Then `ExtractionPort.extract_printed_table` is called with the rendered page image
And the OCR result (non-empty) is used as the recovered lines
And `VisionLLMPort` is NOT called

#### Scenario REV-R31-S03: recovery failure returns structured error; entry stays discarded

Given a discarded entry at page 88 where OCR returns [] and vision returns []
When the recovery endpoint processes page 88
Then the response includes a structured failure reason (not a 500)
And the discarded entry REMAINS in the review state
And no `GuiaDeRemision` is created for page 88

#### Scenario REV-R31-S04: vision-off mode: OCR still attempted; failure is not a 503

Given `vision.enabled=False` (NullVisionAdapter)
And a discarded entry at page 88 with `cached_lines=[]`
When the recovery endpoint processes page 88
Then `ExtractionPort.extract_printed_table` IS called (OCR is still available)
And if OCR returns empty, the response is a structured failure (NOT 503)
And the discarded entry REMAINS in the review state

#### Scenario REV-R31-S05: registro inherited; no caller-supplied registro required

Given a discarded entry at page 152 with `registro="232"`
When `POST /runs/{run_id}/pages/152/recover` is called with no `registro` parameter
Then the recovered guía is assigned `registro="232"` from the discarded entry
And no assignment dialog or redirect is triggered

---

### REV-R32 — [ADDED] Recovered guía flows through canonical matching; re-reconciliation triggered

After recovery, the `GuiaDeRemision` created from the recovered lines MUST flow through the
existing reconciliation pipeline:

1. The recovered guía's material lines MUST pass through the canonical matching algorithm
   (`material-canonical-matching` skill) — same Tier 1 dual-spec normalization + Tier 2
   grade-tolerant recovery as all other guías.
2. Reconciliation MUST group by `(registro, material_canonical, unidad)`. `fecha` MUST NOT
   be introduced as a grouping axis.
3. Units MUST NOT be converted. KG, TN, RD, Rollo summed independently.
4. MATCH tolerance remains EXACT (0). If recovered quantities differ from declared, the
   group MUST show MISMATCH and `requires_review=True`.
5. The recovered guía MUST appear in the drill-down for its registro's reconciliation rows
   (REV-C01) with its `identity_source` and `source_pages`.
6. Re-reconciliation triggered by recovery MUST NOT re-run the OCR, vision, or classification
   stages (REV-006 scope preserved). It MUST recompute only the affected reconciled groups.

#### Scenario REV-R32-S01: recovered guía appears in reconciliation drill-down

Given a discarded entry at page 152, registro "232", recovered with lines matching
  declared material "BARRA A615 G60 1/2 9M" unit TN
When recovery completes and re-reconciliation runs
Then a `ReconciliationRow` for group `(232, "BARRA A615 G60 1/2 9M", TN)` is updated
And the drill-down for that row includes the recovered guía with its synthetic guia_id
And the recovered guía's `source_pages = [152]` is visible

#### Scenario REV-R32-S02: recovered quantity mismatch produces MISMATCH; not auto-corrected

Given the declared quantity for "BARRA A615 G60 1/2 9M" in registro 232 is 0.191 TN
And the recovered OCR quantity is 0.190 TN (slight misread)
When reconciliation runs for the recovered group
Then the group status is MISMATCH
And `requires_review=True` on the recovered row
And the declared value remains 0.191 TN (unchanged)
And the OCR value 0.190 TN is recorded in the audit trail

#### Scenario REV-R32-S03: recovery does not re-trigger OCR/vision/classification

Given a successful recovery that triggers re-reconciliation for registro 232
When the re-reconciliation step runs
Then no `PageClassifier` call is made
And no `ExtractionPort.extract_printed_table` call is made (beyond the recovery step itself)
And no `VisionLLMPort` call is made (beyond the recovery fallback if applicable)
And only reconciliation groups for registro 232 recompute

---

### REV-R33 — [ADDED] API: discarded entries surfaced in run response

The review/run API response MUST include the discarded entries so the frontend can populate
the Descartadas tab. The exact field name and nesting (e.g., `discarded_guia_pages`,
`unidentified_pages`, or a discriminated field on the existing `errored_guias` list) is a
**design decision**. However, the spec constrains:

1. Each discarded entry in the API response MUST carry at minimum: `source_page` (int),
   `registro` (str | None), and a flag indicating whether `cached_lines` is empty or
   non-empty (so the UI can show whether OCR data was cached — MUST NOT expose raw
   `MaterialLine` objects to the frontend as they are not needed for the operator decision).
2. The response field MUST default to `[]` (empty list) for runs that have no discarded
   entries — no breaking change for existing consumers.
3. The API DTO MUST include a `reason` or `type` field that distinguishes discarded entries
   (no QR evidence, possible guía) from errored guías (valid identity, zero OCR lines).
   This is required so the frontend can route entries to the correct tab (Descartadas vs
   Pendientes) without client-side inference.
4. The `identity_source` field on any DTO associated with a recovered page MUST use the new
   additive Literal value (EXT-037 constraint 3 + 4). The schema MUST be updated in lockstep.

#### Scenario REV-R33-S01: discarded entries in API response; empty by default

Given a run with 0 discarded entries
When `GET /runs/{run_id}` (or the equivalent review-table endpoint) is called
Then the response includes a discarded-entries field (or equivalent) equal to `[]`
And no existing consumer of the response is broken

#### Scenario REV-R33-S02: discarded entries include source_page, registro, cached_lines indicator

Given a run with 1 discarded entry: page 152, registro "232", OCR found 2 lines
When the review API response is retrieved
Then the discarded entry in the response includes:
  - `source_page = 152`
  - `registro = "232"`
  - an indicator that cached OCR lines exist (non-empty cache) — exact field is design decision

#### Scenario REV-R33-S03: discarded entry reason distinguishes from errored guía

Given a run with 1 discarded entry (no QR evidence) AND 1 errored guía (valid QR, zero OCR lines)
When the API response is inspected
Then the discarded entry has `reason = "no_identity"` (or equivalent Literal value)
And the errored guía has `reason = "zero_lines"` (or equivalent Literal value)
And neither entry appears in the wrong tab's data bucket

---

## MUST-NOT Invariants for this delta

- All prior MUST-NOT invariants from REV-001 through REV-R26 remain in effect.
- `fecha` MUST NOT be introduced as a grouping axis. Grouping key remains
  `(registro, material_canonical, unidad)`.
- Units MUST NOT be converted. Recovered lines carry units from OCR; they are NEVER
  normalized by multiplication or division.
- Recovered lines MUST carry `requires_review=True`. No auto-accept under any circumstance.
- The existing "Reconciliación" and "Pendientes" tabs MUST NOT be broken by the third tab.
- The REV-C07 thumbnail fallback chain MUST NOT be removed or weakened.
- The input PDF MUST NOT be modified. Recovery renders pages from the read-only source PDF.
- Domain layer MUST remain pure. No SDK/IO import in `domain/`.
- `application/pipeline.py` MUST NOT import concrete adapters.
- The `TAB_ORDER` extension MUST NOT change existing tab indices (Reconciliación=0,
  Pendientes=1 are preserved; Descartadas is appended at index 2).
- Bulk recovery MUST NOT settle progress before all selected pages are truly done — the
  PR#49 SA-5 lesson applies.
- The REINTENTAR (SUNAT) button MUST NOT appear for discarded/no-identity entries. The
  `retry_attempted` flag is exclusive to the SUNAT retry path (REV-R26).

---

## Out of scope for this delta

- History / persistence of discarded entries across application restarts (SDD#3 — requires
  cross-restart persistence; today's `run_registry` is in-memory).
- UI for arbitrary-page recovery (no UI for pages not in the discarded collection; YAGNI).
- Classification changes — the classifier already catches all guías.
- Issues #44 (cross-model consensus), #45 (stale status endpoint), #41 (deadline-guard
  cancel), #43 (unit-map) — unrelated.
- Export changes — recovered guías flow into reconciliation; export reads the reconciliation
  result and is unchanged by this delta.
- Rollback/undo for bulk recovery.
- URL-deep-linked tab routing.
