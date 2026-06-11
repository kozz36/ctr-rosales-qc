# Design: Run History Persistence (SDD#3)

## Technical Approach

Hexagonal additive side-channel: a `RunHistoryPort` Protocol (application layer) + `JsonManifestRunHistoryAdapter` (infrastructure). The manifest write hooks into `_run_pipeline_background` (routes.py) — already infrastructure, already the owner of the run status state machine — so **`application/pipeline.py` has a ZERO-line diff**. Lifespan scan rehydrates the registry; review services build lazily on first access (Virtual Proxy / Lazy Initialization, mirroring `build_review_service`). Domain untouched; R8 grouping and R9/R9b/R9c date logic never referenced — manifest consumes only `PipelineResult` aggregates.

## Architecture Decisions

### D1 — Port shape & manifest-write seam
**Choice**: `application/run_history.py` defines `RunManifest` (pydantic, like `AppConfig`) + `RunHistoryPort` Protocol (`write_manifest`, `scan`, `sweep_failed`, `delete_run`). Adapter `infrastructure/run_history_store.py` built once in lifespan → `app.state.run_history`. The write happens in `_run_pipeline_background` success AND except branches, wrapped in `try/except` (manifest failure logs, never fails the run).
**Alternatives rejected**: injecting the port into `ReconciliationPipeline` — the pipeline cannot observe its own exception, doesn't know `started_at`, and "status" is registry vocabulary owned by the wrapper. The wrapper is the natural completion seam; pattern: **Dependency Inversion at the composition boundary**.

### D2 — Manifest schema
```json
{ "schema_version": 1, "run_id": "<uuid>", "status": "review|error",
  "started_at": "<iso-utc>", "completed_at": "<iso-utc>",
  "seq": 2, "registro_min": "232", "registro_max": "245",
  "row_count": 0, "match_count": 0, "mismatch_count": 0,
  "warnings": ["..."], "vision_calls_made": 0, "error": null }
```
`registro_min/max` from `result.declared` numeros (int-sort, lexicographic fallback). Full `warnings` list (small; needed to serve `GET /runs/{id}` cold). **Failure manifest**: written in the except branch — `status="error"`, `error=str(exc)`, counts 0, registro range null, `completed_at`=failure time. Unknown `schema_version` → degraded entry (same path as legacy). Written via `_atomic_json_write` (atomic overwrite — NOT write-once, retry overwrites it).

### D3 — Per-day sequence: write-time, mutex-serialized
**Choice**: stored `seq` allocated at manifest-write time: adapter scans same-day manifests for max seq under a process-wide `threading.Lock`. Safe because deployment is single-process (the in-memory registry already encodes that fact) and BackgroundTasks run in the same process's threadpool — no cross-process race exists.
**Rejected**: display-time derivation — re-enumeration after a delete silently renames surviving runs ("batch #2 de hoy" becomes "#1"), breaking operator references. Stability wins. Legacy dirs: list-time mtime-ordered fallback seq, flagged degraded.

### D4 — Startup index merged into run_registry
**Choice**: lifespan scans `output_dir/*/` (UUID-named dirs only). Per dir: read manifest → registry entry with the SAME dict shape as upload entries plus `manifest` + `hydrated: False`, `review_service/ctx: None`. No manifest or corrupt JSON → derive: `extraction_cache.json` present → `status="review"`, counts parsed from cache rows; PDF only → `status="error"`, `error="interrupted"`; timestamps from mtime; `degraded: True`. Per-dir `try/except` → never crash startup. **No second index structure** — one source of truth (the registry), no desync.
**Lazy hydration**: new FastAPI dependency `_get_hydrated_entry(run_id, registry, config)` replacing `_require_run` on review-service endpoints: builds `RunContext(run_id=...)` + `build_review_service` + `build_reprocess_service` on first access, caches into the entry (mirrors today's on-demand pattern). `GET /runs/{id}` polling needs NO hydration — served from manifest fields.
**48h sweep**: adapter `sweep_failed(cutoff)` called at lifespan (post-scan) AND at `GET /runs`. Only `status=="error"` entries with `completed_at` (or mtime) > 48h; never sweeps pending/processing/review.

### D5 — Endpoints
| Endpoint | Behavior |
|---|---|
| `GET /runs` | sweep, then registry → `RunSummaryResponse[]` (run_id, status, started_at, completed_at, seq, registro_min/max, counts, warnings_count, degraded, error), sorted started_at desc |
| `DELETE /runs/{id}` | 404 unknown; **409 if pending/processing**; UUID-validate, `rmtree(config.output_dir / run_id)` only, pop registry → 204 |
| `POST /runs/{id}/retry` | **SAME run_id** (history identity preserved, no 100MB PDF copy). 409 unless `status=="error"`. Reset dir: delete `extraction_cache.json` (write-once guard would raise), `review.json`, `pages/`; KEEP `{run_id}.pdf` and `sunat/` (immutable fetch cache). Re-fire `_run_pipeline_background` |

**Rejected**: new-run_id retry — duplicates history identity, copies the PDF, leaves a corpse dir for the sweep.

### D6 — Frontend
- `RunHistoryMenu.vue` (new, `features/run/`) in App.vue header, always visible: [Nuevo batch] (`runStore.reset()` → `/`), [Batch actual] (→ `/runs/{latest}`), [Historial] (→ new route `/historial`).
- `RunHistoryPage.vue` (new route): TanStack Query `useRunsList`; rows show `DD-MM-YYYY · Registros 232–245 · #seq` + status badge; [Reintentar] (error only) / [Eliminar] with confirm dialog (reuse DescartadasTab dialog pattern); delete/retry = mutations invalidating the runs query. es-PE strings.
- **runStore fix**: ReviewPage setup sets `runStore.runId = props.id` (route-param mount hook) — nav survives refresh/cold-load. Cold-load UX: existing "Esperando..." + `tableQuery.isFetching` spinner already cover the lazy backend hydration; no new state machine.

### D7 — Accepted tradeoff
Manifest counts are a completion-time snapshot; review edits drift them. Recomputing at list-time would force hydration of every run — rejected. Documented; history list is identity + triage, the table is truth.

## Data Flow

```
upload → _run_pipeline_background ──success──→ manifest write (try/except, atomic)
   │                              └─failure──→ failure manifest
restart → lifespan scan (manifest | derive | degrade) → run_registry entries (hydrated=False)
GET /runs ──sweep_failed(48h, error-only)──→ summaries
GET /runs/{id}/table → _get_hydrated_entry → build_review_service (lazy, once) → rows
DELETE → guard active → rmtree({output_dir}/{uuid}) → pop entry
retry → reset dir (keep pdf, sunat/) → same run_id → background task → manifest overwrite
```

## File Changes

| File | Action | Layer |
|---|---|---|
| `backend/src/reconciliation/application/run_history.py` | Create | application — `RunManifest`, `RunHistoryPort` |
| `backend/src/reconciliation/infrastructure/run_history_store.py` | Create | infrastructure — JSON adapter (write/scan/sweep/delete, seq lock) |
| `backend/src/reconciliation/infrastructure/api/main.py` | Modify | lifespan scan + sweep; `app.state.run_history` |
| `backend/src/reconciliation/infrastructure/api/routes.py` | Modify | manifest hooks in `_run_pipeline_background`; `GET /runs`, `DELETE`, retry; `_get_hydrated_entry` |
| `backend/src/reconciliation/infrastructure/api/schemas.py` | Modify | `RunSummaryResponse` |
| `backend/src/reconciliation/application/pipeline.py` | **None** | zero diff — ports-only proven by absence |
| `backend/src/reconciliation/domain/` | **None** | pure |
| `frontend/src/features/run/RunHistoryMenu.vue` | Create | header menu |
| `frontend/src/features/run/RunHistoryPage.vue` | Create | history list + actions |
| `frontend/src/app/{App.vue,router.ts}` | Modify | mount menu; `/historial` route |
| `frontend/src/features/review/ReviewPage.vue` | Modify | `runStore.runId = props.id` on setup |
| `frontend/src/api/{client.ts,types.ts}` | Modify | listRuns/deleteRun/retryRun |

## Testing Strategy (strict-TDD — RED first per slice)

| Layer | RED tests |
|---|---|
| Adapter unit | write/read round-trip; corrupted manifest → degraded (never raises); legacy derive (cache→review, pdf-only→error); same-day seq under concurrent threads; sweep boundary 47h vs 49h; delete scoped to own dir |
| Wrapper | manifest on success AND on pipeline exception; adapter IOError → run still completes (invariant) |
| API | lifespan scan populates registry; cold `GET /runs/{id}` no hydration; cold `/table` hydrates lazily; DELETE 409 on processing; retry 409 on review, resets dir, write-once cache no longer raises |
| Frontend vitest | menu actions; history render/confirm-delete; runStore restored from route param |
| **Real-data** | restart round-trip against the 6 real legacy dirs in `backend/runs/` (degraded listing, reopen → editable table, export) |
| SA-5 | Playwright: upload → historial → reopen → reassign → export; refresh mid-review keeps nav |

## Migration / Rollout

None — manifests are inert extra files; legacy dirs degrade; revert PRs to roll back.

## PR Chain (stacked-to-main, ≤400 lines each)

1. **PR-1 backend core**: port + adapter + manifest hooks + lifespan scan + `GET /runs`. Gate: **JD** (persistence core).
2. **PR-2 backend lifecycle**: lazy hydration dep, `DELETE`, retry, 48h sweep wiring. Gate: **JD** (filesystem delete + retry semantics).
3. **PR-3 frontend**: menu, `/historial`, store fix, cold-load UX. Gate: **ctr-review + SA-5 Playwright**.

## Open Questions

None blocking.
