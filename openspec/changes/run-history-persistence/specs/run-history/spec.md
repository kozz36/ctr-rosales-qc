# Run History Specification

**Change**: run-history-persistence (SDD#3)
**Capability**: run-history (NEW)
**Domain**: run-history
**Phase**: spec
**Date**: 2026-06-11

---

## Purpose

This spec covers a new `run-history` capability: durable per-run metadata persistence,
cross-restart run listing, past-run re-activation for full editing, failed-run retry,
48 h lazy sweep of failed runs, manual deletion, and the history UI in the frontend header.

No existing capabilities are modified. Re-activation reuses the existing rehydration path
(`build_review_service`) behind a new port; R8/R9 reconciliation invariants are untouched.

---

## Requirements

### RH-001 — Per-run manifest written at pipeline completion

The system MUST write a `run_manifest.json` file inside each run's output directory upon
pipeline completion (success or failure). The write MUST be atomic (same pattern as the
existing `_atomic_json_write`). A manifest write failure MUST NOT fail or corrupt the pipeline
run itself; if the write fails, the run degrades to derive-from-disk on next listing.

The manifest MUST capture at minimum: run status, pipeline start timestamp, pipeline end
timestamp, registro range (min and max), total row count, match/mismatch counts,
vision_calls_made, warnings list, and error reason (if failed). Original PDF filename MUST
NOT be stored (CWE-22 mitigation).

Design-level fields (exact schema, versioning, format) are an Open Question for the design
phase.

#### Scenario RH-001-S01: manifest written on successful pipeline completion

- GIVEN a pipeline run completes successfully
- WHEN the pipeline writes its final output
- THEN `{output_dir}/{run_id}/run_manifest.json` exists and is valid JSON
- AND the manifest contains run status `"completed"`, `started_at`, `completed_at`, and
  the registro range derived from the run's own data

#### Scenario RH-001-S02: manifest failure does not abort the pipeline

- GIVEN the manifest write raises an IO error (e.g. disk full)
- WHEN the pipeline completes
- THEN the pipeline result is still returned to the caller without error
- AND the run is listable via derive-from-disk fallback (RH-002)

#### Scenario RH-001-S03: manifest written on pipeline failure

- GIVEN a pipeline run fails with an error (exception or partial extraction)
- WHEN the error is handled
- THEN a manifest is written with status `"failed"` and the error reason
- AND the manifest exists even when `extraction_cache.json` does not

#### Scenario RH-001-S04: PDF filename is not stored in the manifest

- GIVEN any pipeline run completes
- THEN the manifest MUST NOT contain the original uploaded PDF filename
- AND no field in the run directory references the client-supplied filename

---

### RH-002 — Startup index: scan output directory, degrade gracefully for legacy runs

At server startup the system MUST scan `output_dir` and build an in-memory run index from
all run directories found on disk. For each directory:

- If `run_manifest.json` is present and valid: load all fields from it.
- If `run_manifest.json` is absent (legacy run) or corrupted: derive status from disk
  (`extraction_cache.json` present → completed; PDF present, no cache → failed/incomplete),
  derive timestamps from filesystem mtime where available, and mark metadata fields that
  cannot be derived as unavailable. The run MUST appear in the listing with degraded fields
  rather than be hidden or cause a crash.
- A corrupted manifest (malformed JSON, missing required keys) MUST be skipped with a log
  warning; it MUST NOT crash startup or hide other valid runs.
- An empty `output_dir` (no subdirectories) MUST produce an empty run index without error.

#### Scenario RH-002-S01: completed run with manifest loads fully

- GIVEN a run directory containing `run_manifest.json` with status `"completed"`
- WHEN the server starts
- THEN the run appears in `GET /runs` with all fields populated from the manifest

#### Scenario RH-002-S02: legacy run without manifest appears with degraded fields

- GIVEN a run directory containing `extraction_cache.json` but no `run_manifest.json`
- WHEN the server starts
- THEN the run appears in `GET /runs` with status `"completed"` (derived)
- AND timestamp and warning fields are marked as unavailable (null or absent)
- AND the run is NOT hidden

#### Scenario RH-002-S03: corrupted manifest is skipped; other runs still listed

- GIVEN three run directories: one with a valid manifest, one with corrupted JSON, one legacy
- WHEN the server starts
- THEN the valid-manifest run and the legacy run appear in `GET /runs`
- AND the corrupted-manifest run is absent (skipped)
- AND startup completes without raising an exception

#### Scenario RH-002-S04: empty output directory produces empty listing

- GIVEN `output_dir` exists but contains no subdirectories
- WHEN the server starts
- THEN `GET /runs` returns an empty list
- AND no error is raised

---

### RH-003 — Run listing endpoint

The system MUST expose a `GET /runs` endpoint that returns the current run index as an
ordered list. Runs MUST be sorted by start timestamp descending (most recent first); legacy
runs with unavailable timestamps MUST appear at the end of the list, not be dropped.

The response shape is behavior-level (field names and exact DTO are Open Questions for design).
At minimum each entry MUST expose: `run_id`, `status`, `display_label`
(`fecha + registro_min–registro_max + per-day sequence`), `started_at` (nullable),
`completed_at` (nullable), and metadata indicating whether fields are degraded.

#### Scenario RH-003-S01: returns all runs sorted newest first

- GIVEN three completed runs with different start times
- WHEN `GET /runs` is called
- THEN the response lists all three runs in descending `started_at` order

#### Scenario RH-003-S02: failed runs appear in listing with error flag

- GIVEN a run with status `"failed"`
- WHEN `GET /runs` is called
- THEN the run appears with an error indicator and no `completed_at`

#### Scenario RH-003-S03: degraded legacy runs appear at end of list

- GIVEN two runs with manifests and one legacy run (no manifest, `started_at` unavailable)
- WHEN `GET /runs` is called
- THEN the two manifest runs appear first (newest first)
- AND the legacy run appears last with degraded-field indicators

---

### RH-004 — Per-day run sequence number

Each run MUST be assigned a per-day sequence number (1, 2, 3…) relative to other runs that
share the same calendar date. This number MUST be stable after being assigned and MUST be
included in the run's `display_label`. Sequence derivation approach and race condition
handling for same-day concurrent runs are Open Questions for the design phase.

#### Scenario RH-004-S01: first run of the day gets sequence 1

- GIVEN no prior run exists for today's date
- WHEN a run completes on that date
- THEN its display label includes sequence number `1`

#### Scenario RH-004-S02: second run of the same day gets sequence 2

- GIVEN one completed run already exists for today with sequence `1`
- WHEN a second run completes on the same date
- THEN its display label includes sequence number `2`

#### Scenario RH-004-S03: sequence from different days are independent

- GIVEN a run completed yesterday with sequence `3`
- WHEN a new run completes today
- THEN today's run has sequence `1` (independent of yesterday's runs)

---

### RH-005 — Past-run re-activation (full editing restored)

The system MUST support on-demand re-activation of any completed run from the run index.
Re-activating a past run MUST fully restore the run into the in-memory registry using the
existing rehydration path (`build_review_service`), enabling all editing operations
(reassign, line-edit, export) identically to a freshly processed run.

Re-activation MUST use the CURRENT server configuration (not the config at original run time).
Re-activation MUST NOT restore ephemeral batch state (`reprocess_batches`, `discarded_batches`
and similar in-flight fields); batch endpoints for a rehydrated run MUST return the terminal
"no batch fired" state without error.

The edits already recorded in the run's `review.json` sidecar MUST be replayed by
`ReviewService.restore_from_sidecar` so the review state is identical to pre-restart.

#### Scenario RH-005-S01: opening a past run restores full edit capability

- GIVEN a completed run that was processed before a server restart
- WHEN the operator opens that run from the history panel
- THEN the ReviewPage loads with the full reconciliation table
- AND reassign, line-edit, and export all function correctly
- AND the run's prior sidecar edits are visible (audit trail intact)

#### Scenario RH-005-S02: rehydrated run has no ephemeral batch state

- GIVEN a rehydrated past run that originally had a completed reprocess batch
- WHEN the operator queries batch status for that run
- THEN the response indicates "no batch in progress" (terminal state, no error)

#### Scenario RH-005-S03: re-activation uses current config

- GIVEN the server config changed between original run and re-activation
- WHEN the operator re-activates the past run
- THEN `build_review_service` uses the current config
- AND no error is raised due to missing per-run config

---

### RH-006 — Server-restart durability

After a server restart the run index MUST be repopulated from disk so that every completed
run that existed before the restart is immediately listable and re-activatable without any
operator action.

#### Scenario RH-006-S01: restart preserves full run listing

- GIVEN five completed runs exist on disk before a server restart
- WHEN the server restarts and `GET /runs` is called
- THEN all five runs are listed
- AND opening any of them via the history panel restores a fully editable review

#### Scenario RH-006-S02: edits from before restart are preserved on re-activation

- GIVEN a run where the operator reassigned a guía before the server restarted
- WHEN the server restarts and the operator opens that run
- THEN the reassignment is present in the review table
- AND the audit trail records the original reassignment event

---

### RH-007 — Failed-run display and [Reintentar]

Failed runs MUST appear in the history panel with a visible error indicator and an error
reason string (if available). Each failed run MUST have a [Reintentar] action that triggers
a full pipeline re-run from the stored PDF copy at `{output_dir}/{run_id}/{run_id}.pdf`.

[Reintentar] MUST be a full pipeline re-run from the beginning; mid-pipeline checkpoint/resume
is explicitly out of scope. Retry run_id semantics (reuse same run_id vs new run_id) are an
Open Question for the design phase.

While a retry is in progress, the [Reintentar] button for that run MUST be disabled. Clicking
[Reintentar] when another run is already processing MUST be rejected or queued per existing
pipeline concurrency rules — never silently dropped.

#### Scenario RH-007-S01: failed run visible with error flag in history panel

- GIVEN a run whose pipeline raised an unhandled exception
- WHEN the operator opens the history panel
- THEN the failed run appears with a visual error indicator
- AND the error reason string is shown (or "error desconocido" if unavailable)

#### Scenario RH-007-S02: [Reintentar] fires full pipeline from stored PDF

- GIVEN a failed run with a PDF at `{output_dir}/{run_id}/{run_id}.pdf`
- WHEN the operator clicks [Reintentar]
- THEN the pipeline is submitted as a new background task starting from page 1
- AND the stored PDF is used as input (no new upload required)

#### Scenario RH-007-S03: [Reintentar] disabled while retry is processing

- GIVEN the operator clicked [Reintentar] and the retry is still running
- WHEN the operator views the history panel
- THEN the [Reintentar] button for that run is disabled (not clickable)

#### Scenario RH-007-S04: [Reintentar] while another run is processing is rejected

- GIVEN a different run is currently processing
- WHEN the operator clicks [Reintentar] on a failed run
- THEN the request is rejected with an appropriate error response (or queued per pipeline rules)
- AND no silent no-op occurs

---

### RH-008 — 48 h lazy sweep of failed runs

Failed runs older than 48 hours MUST be automatically deleted (run directory removed from
disk; entry removed from the run index). The sweep MUST be lazy: it runs at server startup
and/or at `GET /runs` call time — no background daemon or scheduled task is required.

Completed runs MUST NEVER be auto-deleted. The sweep MUST touch only failed runs.

A failed run that has been retried and whose retry succeeded MUST be treated as completed for
sweep purposes — it MUST NOT be auto-deleted.

#### Scenario RH-008-S01: failed run older than 48 h is removed by sweep

- GIVEN a failed run with `started_at` more than 48 hours ago
- WHEN the sweep runs (at startup or `GET /runs`)
- THEN the run directory is deleted from disk
- AND the run no longer appears in `GET /runs`

#### Scenario RH-008-S02: completed run is never auto-deleted

- GIVEN a completed run of any age
- WHEN the sweep runs
- THEN the completed run is NOT deleted
- AND it continues to appear in `GET /runs`

#### Scenario RH-008-S03: recently failed run (< 48 h) is not swept

- GIVEN a failed run with `started_at` 24 hours ago
- WHEN the sweep runs
- THEN the run is NOT deleted
- AND it remains in `GET /runs` with error flag

#### Scenario RH-008-S04: sweep does not touch runs from other directories

- GIVEN two failed runs older than 48 h and one unrelated directory in `output_dir`
- WHEN the sweep runs
- THEN only the two failed-run directories are removed
- AND the unrelated directory is untouched

---

### RH-009 — Manual run deletion

The system MUST expose an endpoint to delete a specific run by `run_id`. Deletion MUST
remove the run's directory (`{output_dir}/{run_id}/`) and all its contents, then remove the
entry from the in-memory run index. No other run directory MUST be affected.

The `run_id` MUST be validated as a UUID before any filesystem operation is performed; a
non-UUID or path-traversal value MUST be rejected with a 400 response (client-supplied paths
are never used directly).

Deleting the currently active run (the run whose review is open in the operator's browser)
MUST succeed at the API level. The frontend MUST handle this gracefully (e.g. redirect to
upload).

#### Scenario RH-009-S01: delete removes run directory and index entry

- GIVEN a completed run `{run_id}` exists on disk and in the run index
- WHEN `DELETE /runs/{run_id}` is called
- THEN the directory `{output_dir}/{run_id}/` no longer exists
- AND `GET /runs` no longer lists that run_id

#### Scenario RH-009-S02: delete is scoped to target run only

- GIVEN runs A, B, and C exist on disk
- WHEN `DELETE /runs/A` is called
- THEN runs B and C are unaffected on disk and in the listing

#### Scenario RH-009-S03: non-UUID run_id is rejected before filesystem access

- GIVEN a request `DELETE /runs/../../../etc/passwd`
- WHEN the endpoint validates the run_id
- THEN the response is 400 Bad Request
- AND no filesystem operation is performed

#### Scenario RH-009-S04: deleting active run succeeds at API level

- GIVEN the operator has run `{run_id}` open in their browser
- WHEN `DELETE /runs/{run_id}` is called
- THEN the deletion succeeds (200 or 204)
- AND subsequent calls to `GET /runs/{run_id}` return 404

---

### RH-010 — History UI: hamburger menu in App.vue header

The frontend MUST add a hamburger/menu control to the application header with three sections:

- **[Nuevo]**: navigates to the upload page and resets the run store.
- **[batch actual]**: navigates to `/runs/{runStore.runId}` when a current run is active;
  disabled or hidden when no run is active.
- **[historial]**: displays the run history list fetched from `GET /runs`; each entry shows
  the display label and status; clicking an entry navigates to `/runs/{run_id}` and
  re-activates the run if not already in the registry.

The history list MUST update when runs are added, deleted, or retried (reactive to `GET /runs`
poll or invalidation).

#### Scenario RH-010-S01: [Nuevo] resets state and goes to upload

- GIVEN the operator has an active run open
- WHEN the operator clicks [Nuevo] in the hamburger menu
- THEN the operator is navigated to the upload page
- AND the run store is reset (no active run_id)

#### Scenario RH-010-S02: [historial] lists runs from GET /runs

- GIVEN three completed runs and one failed run exist
- WHEN the operator opens the hamburger menu and expands [historial]
- THEN four entries are shown, each with display label and status indicator

#### Scenario RH-010-S03: clicking a history entry opens that run's review

- GIVEN a past completed run labeled "2026-06-10 · Reg 230–235 · #1"
- WHEN the operator clicks that entry in [historial]
- THEN the browser navigates to `/runs/{run_id}`
- AND the ReviewPage loads with the full reconciliation table for that run

#### Scenario RH-010-S04: [batch actual] is inactive when no run is active

- GIVEN no run has been processed in this session (fresh server start, no active runId)
- WHEN the operator opens the hamburger menu
- THEN [batch actual] is disabled or hidden

---

### RH-011 — ReviewPage cold-load and runStore.runId persistence

`ReviewPage` MUST be able to initialize itself from the route parameter (`/runs/:id`) alone,
without requiring a prior upload action in the same browser session. On mount, if
`runStore.runId` is not set, the page MUST read `run_id` from the route param, set it in
the store, and call the backend to hydrate the review state.

`runStore.runId` MUST survive a browser page refresh for the active session (e.g. stored in
`localStorage` keyed by `run_id`). On refresh, the store MUST re-read the persisted `run_id`
and hydrate from the backend.

#### Scenario RH-011-S01: ReviewPage loads from route param after restart

- GIVEN the server has been restarted and the operator navigates directly to `/runs/{run_id}`
- WHEN the ReviewPage mounts
- THEN the page calls the backend to rehydrate the run
- AND the full reconciliation table is displayed without requiring a new upload

#### Scenario RH-011-S02: browser refresh preserves active run

- GIVEN the operator is on ReviewPage for `run_id=abc123`
- WHEN the operator refreshes the browser tab
- THEN the ReviewPage reloads with the same run_id
- AND the reconciliation table is restored from the backend

#### Scenario RH-011-S03: nav link "Revisión" appears after cold-load

- GIVEN the operator navigated directly to `/runs/{run_id}` (no prior upload in this session)
- WHEN the ReviewPage finishes loading and sets `runStore.runId`
- THEN the "Revisión" nav link becomes visible in App.vue

---

## Hexagonal / Invariant Guard (auto-reject list)

The following are absolute prohibitions for all implementation work in this change:

- `domain/` MUST remain pure: zero SDK, framework, or IO imports. Run-history concepts MUST
  NOT enter the domain layer.
- `application/pipeline.py` MUST import only Protocols (ports) and config/run_context — zero
  concrete adapter imports. Manifest write boundary is an Open Question for the design phase
  (port injected into pipeline vs API-layer write after pipeline returns).
- Deletion MUST be scoped strictly to `{output_dir}/{run_id}/`; `run_id` MUST be UUID-validated
  before any filesystem call.
- The 48 h sweep MUST touch ONLY failed runs; completed runs MUST NEVER be auto-deleted.
- Input PDF (`{run_id}/{run_id}.pdf`) is the per-run copy and is read-only during pipeline
  execution; retry reads it as input without modifying it.
- Reconciliation grouping key remains `(registro, material_canonical, unidad)`; `fecha` is
  NOT added back. R8/R9 date invariants are untouched by this change.
- Deleting a run MUST NOT affect any other run directory. No shared index file is maintained
  (central JSON index is explicitly rejected; isolated run dirs are the invariant).

---

## Open Questions (for design phase — do not resolve in spec)

1. Exact manifest schema: field names, types, and versioning strategy.
2. Per-day sequence derivation: write-time (stored in manifest) vs display-time (computed
   at list time) — write-time races with concurrent same-day runs.
3. Retry run_id semantics: reuse same run_id (in-place status reset) vs new run_id (PDF copy).
4. Where the 48 h sweep hooks: lifespan startup only, `GET /runs` call only, or both.
5. Registry entry shape for rehydrated runs: which keys are populated vs marked ephemeral-missing.
6. `pipeline.py` boundary for manifest write: `RunHistoryPort` injected into pipeline vs
   API-layer background task writes after pipeline returns.

---

## Out of Scope (explicit)

- Mid-pipeline checkpoint/resume (rejected — YAGNI).
- Auto-retention / auto-deletion for completed runs.
- Per-run config persistence (re-activation uses current config).
- Multi-run concurrent active review beyond latest-run = [batch actual].
- Any change to R8/R9 reconciliation or date invariants.
- Original PDF filename storage.
- Authentication or multi-user concurrency.
