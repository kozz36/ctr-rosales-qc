# Proposal: Run History Persistence (SDD#3)

## Intent

`run_registry` is an in-memory dict (`backend/src/reconciliation/main.py:52`): a server restart orphans every completed run even though disk has everything needed (`build_review_service` fully rehydrates a ReviewService from `extraction_cache.json` + `review.json` — proven in SDD#2). The operator loses access to finished work, cannot audit past batches, and cannot resume a run where an error slipped through. SDD#3 makes run history durable, listable, re-activatable, retryable (failed runs), and operator-deletable.

## Proposal question round

Completed 2026-06-11 with the user. Six product decisions are SETTLED (do not re-open):

1. **[historial] is re-activatable** — opening a past run restores full editing (reassign/edit/export) via the existing rehydration path; a working tool, not an audit log.
2. **Run display identity** = `fecha + registro_min–registro_max + per-day sequence (1, 2, 3…)`, derived from the run's own data at manifest-write time. Original PDF filename NOT stored (CWE-22 mitigation intact).
3. **Retention = operator decision** — manual per-entry delete; NO automatic policy for completed runs.
4. **[batch actual] = the latest run** (operator lands there after completing the process).
5. **Failed runs** appear in [historial] with an error flag + **[Reintentar]** (full pipeline re-run from the stored PDF; mid-pipeline checkpoint/resume REJECTED as YAGNI) + manual delete + **auto-delete at 48 h** (lazy sweep at startup/list-time — no daemon).
6. **Storage = Option B**: per-run `run_manifest.json`, atomic write (existing `_atomic_json_write` pattern) at pipeline completion; startup lifespan scan builds a lightweight index; derive-from-disk fallback for the 6 legacy manifest-less dirs. Central-index JSON and sqlite REJECTED.

## Scope

### In Scope
- Per-run `run_manifest.json` written at pipeline completion/failure (status, timestamps, registro range, counts, warnings, vision_calls_made, error).
- Startup index: lifespan scan of `output_dir`; legacy dirs degrade gracefully (derived status, missing fields shown as gaps).
- `GET /runs` list endpoint; on-demand re-activation of a past run into the registry (rehydration path).
- History UI: hamburger in App.vue header — [Nuevo] / [batch actual] / [historial]; ReviewPage cold-load from route param; `runStore.runId` persistence fix.
- Failed-run [Reintentar] (re-run pipeline from stored `{run_id}/{run_id}.pdf`) + 48 h lazy sweep of failed runs.
- Manual per-run delete endpoint + UI.

### Out of Scope
- Mid-pipeline checkpoint/resume (rejected — YAGNI).
- Automatic retention/cleanup for COMPLETED runs.
- Multi-run concurrent review semantics beyond latest-run = [batch actual].
- Per-run config persistence (re-activation rebuilds services with CURRENT config).
- Any change to reconciliation grouping/date invariants (R8/R9 untouched).

## Capabilities

### New Capabilities
- `run-history`: durable per-run manifest, startup index, run listing, past-run re-activation, failed-run retry, operator deletion, 48 h failed-run sweep, history UI.

### Modified Capabilities
- None — re-activation reuses the existing rehydration requirement; all new requirements live under `run-history`.

## Approach

Hexagonal: a `RunHistoryPort` (application-layer Protocol) with a JSON-manifest infrastructure adapter (manifest writer + index scanner + sweeper). `application/pipeline.py` stays ports-only — the manifest write must not leak concrete IO into the pipeline (boundary mechanics → Open Question 6). Endpoints: `GET /runs`, delete, retry. Frontend: `RunHistoryMenu.vue` in the App.vue header; ReviewPage self-initializes from route param; on-mount `runStore.runId` restore. Lazy 48 h failed-run sweep at startup/list-time. Lazy heavy deps; local-first; input PDF read-only; isolated run dirs (delete dir = entry gone — no central index to desync).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/src/reconciliation/application/` | Modified | `RunHistoryPort` Protocol; manifest-write hook at pipeline completion (ports-only) |
| `backend/src/reconciliation/infrastructure/` | New | Manifest writer/scanner/sweeper adapter (atomic JSON) |
| `backend/src/reconciliation/infrastructure/api/` (routes, main lifespan) | Modified | `GET /runs`, delete, retry; startup index scan; on-demand re-activation |
| `frontend/src/` (App.vue, router, stores/run.ts, ReviewPage) | Modified/New | Hamburger menu, history panel, cold-load, runId persistence |
| `backend/src/reconciliation/domain/` | None | Domain stays pure — no run-history concepts enter it |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Legacy dirs (6) lack manifests → zeros/gaps in history | Certain | Accept + degrade: derive status/counts from disk, mark fields unavailable |
| `warnings`/`vision_calls_made`/`error` only in-memory today | High | Manifest captures them at completion; old runs show gaps |
| Re-activation uses CURRENT config (config not persisted per-run) | Med | Document drift implication; reprocess behavior may differ from original run |
| Rehydrated runs lack ephemeral batch state (`reprocess_batches` etc.) | Med | Batch endpoints return "no batch fired" terminal state — acceptable |
| Manifest/extraction_cache divergence if process dies between writes | Low | Fallback to disk-derived status (Option B design property) |
| Delete endpoint touches the filesystem | Low | Scope deletion strictly to `{output_dir}/{run_id}/`; validated run_id (UUID), never client paths |

## Open Questions (for design)

1. Manifest schema fields (exact list + versioning).
2. Per-day sequence derivation: write-time (stored) vs display-time (computed) — write-time races with concurrent same-day runs.
3. Retry run_id semantics: reuse same run_id (in-place status reset) vs new run_id (copy PDF).
4. Where the lazy 48 h sweep hooks (lifespan vs `GET /runs` vs both).
5. Registry entry shape for rehydrated runs (which keys are populated vs marked ephemeral-missing).
6. `pipeline.py` boundary for the manifest write: port injected into pipeline vs write performed by the API-layer background task after pipeline returns.

## Rollback Plan

Pure additive change: revert the PR(s). Manifests are inert extra files in run dirs — old code ignores them; no migration, no schema change to `extraction_cache.json`/`review.json`. Frontend hamburger removal restores current nav.

## Dependencies

- None external. Builds on `build_review_service` rehydration (SDD#2) and `_atomic_json_write`.

## Success Criteria

- [ ] Restart server → [historial] lists past runs with `fecha + registro range + sequence`; legacy dirs appear with degraded fields.
- [ ] Open a past run → full review editable (reassign/edit/export) and exports work.
- [ ] Failed run shows error flag + [Reintentar]; retry re-runs the pipeline from the stored PDF.
- [ ] Manual delete removes the run dir and the history entry.
- [ ] Failed runs older than 48 h are swept lazily; completed runs are never auto-deleted.
- [ ] `application/pipeline.py` imports zero concrete adapters; domain untouched; R8/R9 invariants intact.
