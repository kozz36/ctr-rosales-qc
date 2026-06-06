# Delta for Review Domain — guia-reprocess-bulk-viewer

**Change**: guia-reprocess-bulk-viewer
**Modifies**: openspec/specs/review/spec.md
**Extends**: REV-R10..REV-R19 (guia-reprocess-staged-flow PR #3)
**Date**: 2026-06-06

---

## Scope of This Delta

This spec describes WHAT MUST BE TRUE after `guia-reprocess-bulk-viewer` is applied.
It adds four UX features (F1–F4) and fixes one backend bug (#42).
All requirements are additive to the `review` capability — no existing requirements are removed.

Capability: `review`. No new capability is introduced.

---

## ADDED Requirements

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

## MUST-NOT Invariants (auto-reject)

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

---

## Out of Scope (explicit)

- Cross-model consensus (#44) · deadline-guard request cancellation (#41) · unit-map (#43).
- Streaming/SSE batch progress (polling is MVP).
- URL-deep-linked tab routing.
- Rollback/undo for manual corrections or bulk reprocess.
- Authentication or multi-user concurrent editing.
