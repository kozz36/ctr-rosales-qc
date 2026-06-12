# Tasks: run-history-persistence (SDD#3)

**Change**: run-history-persistence
**Artifact store**: hybrid (engram + openspec)
**Delivery strategy**: ask-on-risk → chained PRs approved (stacked-to-main)
**Chain strategy**: stacked-to-main
**Strict TDD**: ACTIVE (runner: `cd backend && uv run pytest <targeted-path>` — NEVER monolithic, paddle hangs; frontend: `cd frontend && npm test`)
**Date**: 2026-06-11

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines — PR-1 | ~300–360 LOC incl. tests |
| Estimated changed lines — PR-2 | ~350–420 LOC incl. tests |
| Estimated changed lines — PR-3 | ~320–380 LOC incl. vitest |
| 400-line budget risk | **High** (PR-2 at limit; PR-3 near limit) |
| Chained PRs recommended | **Yes** |
| PR order | PR-1 → PR-2 → PR-3 (stacked-to-main) |
| Decision needed before apply | No — chain already approved; strategy: stacked-to-main |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High

### PR Budget Breakdown

| PR | Scope | Estimate | Risk |
|----|-------|----------|------|
| PR-1 | `RunHistoryPort` + `JsonManifestRunHistoryAdapter` + manifest hooks in `_run_pipeline_background` (success+except) + `extraction_cache` atomic-overwrite change + lifespan scan + lazy hydration dep + `GET /runs` + `RunSummaryResponse` schema | ~300–360 LOC | Medium |
| PR-2 | `_get_hydrated_entry` dep replacement on review-service endpoints + `DELETE /runs/{id}` (409 guard, UUID-validate, rmtree) + `POST /runs/{id}/retry` (dir reset, keep PDF+sunat/, re-fire pipeline) + 48h sweep wiring (lifespan + GET /runs) + cold-load `GET /runs/{id}` polling path | ~350–420 LOC | High (if tests push past 400: split PR-2a=DELETE+guard / PR-2b=retry+sweep) |
| PR-3 | `RunHistoryMenu.vue` in App.vue header + `RunHistoryPage.vue` (/historial route) + `router.ts` + `api/types.ts` + `api/client.ts` + `ReviewPage.vue` runStore mount fix | ~320–380 LOC | Medium-High |

If PR-2 tests push past ~400 lines, split:
- **PR-2a**: `_get_hydrated_entry` lazy-hydration dep + `DELETE /runs/{id}` (409 guard, UUID-validate, rmtree) + 48h sweep at lifespan. ~200–240 LOC.
- **PR-2b**: `POST /runs/{id}/retry` (dir reset, keep PDF+sunat/, re-fire pipeline) + 48h sweep at `GET /runs` + `GET /runs/{id}` cold-load poll path. ~180–210 LOC.

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Persistence core: port, adapter, manifest write, startup index, `GET /runs` | PR-1 | Base: main (post SDD#1) |
| 2 | Lifecycle: lazy hydration, DELETE, retry, 48h sweep | PR-2 | Base: PR-1 merged to main |
| 3 | Frontend: hamburger menu, /historial, store fix, cold-load UX | PR-3 | Base: PR-2 merged to main |

---

## Invariants (anti-patterns enforced throughout all phases)

- **Domain purity**: no SDK/framework/IO import under `domain/`. Run-history concepts MUST NOT enter `domain/`. A heavy import there = auto-reject.
- **`application/pipeline.py` ZERO diff** (D1 design decision — manifest write boundary is the `_run_pipeline_background` wrapper in `routes.py`, NOT the pipeline). Any diff in `pipeline.py` = auto-reject.
- **Manifest failure never fails the run**: write wrapped in `try/except`; on IOError, log warning and continue. Run still completes.
- **Isolated run dirs**: deletion scoped strictly to `{output_dir}/{run_id}/`. `run_id` UUID-validated before any filesystem call. Never delete other dirs.
- **Legacy degrade, never crash**: startup scan per-dir try/except; missing or corrupted manifest → degraded entry (status derived from disk); never hides the run; never aborts startup.
- **48h sweep touches ONLY `status=="error"` runs**: completed runs NEVER auto-deleted.
- **`extraction_cache.json` write-once guard removed for retry**: the retry resets the dir by deleting the cache before re-firing the pipeline. Atomic overwrite (not write-once) is the new semantic.
- **`fecha` NEVER a grouping axis**: R8 grouping key is `(registro, material_canonical, unidad)` — untouched.
- **Units never converted**: KG/TN/RD/Rollo summed independently — untouched.
- **R9/R9b/R9c date invariants untouched**: run-history adds zero date logic to reconciliation.
- **Local-first**: no network calls added. SUNAT fetch remains opt-in/off.
- **Original PDF filename NOT stored** in the manifest (CWE-22 invariant from RH-001-S04).

---

## PR-1 — `feat(run-history): persistence core — port, adapter, manifest write, startup index, GET /runs`

> Scope: `RunHistoryPort` Protocol + `RunManifest` pydantic model; `JsonManifestRunHistoryAdapter`
> (write/scan/sweep/seq-lock); manifest hooks in `_run_pipeline_background` success AND except
> branches; lifespan scan → run_registry merge (hydrated=False); `RunSummaryResponse` schema;
> `GET /runs` endpoint; `extraction_cache` atomic-overwrite semantic change.
>
> Independently shippable: closes the "restart orphans all runs" gap with no UI.
> Depends on: main (post SDD#1 PRs #51–#54).
> Gate: **full dual-blind JD** (persistence core; filesystem + threading lock).

### Phase 1.0 — Pre-work: read current code to confirm seams

- [x] **1.0.1** READ `backend/src/reconciliation/infrastructure/api/routes.py` lines 242–308
  (`_run_pipeline_background` function, both success and except branches).
  Confirm: (a) exact line where `started_at` is set in the registry; (b) the except block
  sets `status="error"` and `error=str(exc)`; (c) no existing manifest-write call present.
  Read-only pre-flight.
  **ANSWER**: (a) `started_at` set at line 279 (`datetime.datetime.now(datetime.UTC).isoformat()`);
  (b) except branch at line 305–307 sets `status="error"` and `error=str(exc)`;
  (c) confirmed no manifest-write call present.

- [x] **1.0.2** READ `backend/src/reconciliation/infrastructure/api/main.py` lines 40–58
  (lifespan function). Confirm: `run_registry` is initialized as empty dict; `config` is stored
  in `app.state`; no existing directory scan. Read-only pre-flight.
  **ANSWER**: All confirmed. `run_registry = {}` at line 52; `app.state.config = config` at line 54;
  `app.state.run_registry = run_registry` at line 55; no directory scan.

- [x] **1.0.3** READ `backend/src/reconciliation/application/pipeline.py` search for
  `_atomic_json_write` — confirm the function exists and is importable from the infrastructure
  layer, OR identify which module owns it. Read-only; confirms the reuse seam for D2.
  **ANSWER**: `_atomic_json_write` is defined in `application/run_context.py` (module-level function,
  line 234). It is NOT in pipeline.py. The adapter imports it from `run_context` — an
  infrastructure-to-application import of a pure stdlib utility function (no IO deps), acceptable.

- [x] **1.0.4** READ `backend/src/reconciliation/infrastructure/container.py` search for
  `_atomic_json_write` definition OR its import. Record the exact module path so the adapter
  can import or replicate the same atomic-write pattern.
  **ANSWER**: `_atomic_json_write` is NOT in container.py. It lives exclusively in
  `application/run_context.py`. The adapter proxies it via:
  `from reconciliation.application.run_context import _atomic_json_write`.

### Phase 1.1 — RED: Write failing tests for PR-1

- [x] **1.1.1** Create `backend/tests/unit/application/test_run_history_port.py`.
  Write failing test `test_run_manifest_schema_version_is_1`:
  Instantiate `RunManifest(schema_version=1, ...)` with required fields.
  FAILS today: `RunManifest` does not exist. Spec: RH-001, D2.

- [x] **1.1.2** Add failing test `test_run_manifest_no_pdf_filename_field`:
  Assert `RunManifest` model has NO field named `pdf_filename` or `filename`.
  FAILS today: model does not exist. Spec: RH-001-S04.

- [x] **1.1.3** Add failing test `test_run_manifest_status_values`:
  Assert `RunManifest` accepts `status="review"` and `status="error"` but not arbitrary strings.
  FAILS today: model does not exist. Spec: D2.

- [x] **1.1.4** Create `backend/tests/unit/infrastructure/test_run_history_adapter.py`.
  Write failing test `test_write_manifest_creates_valid_json`:
  Call `adapter.write_manifest(run_manifest)` with a tmp dir.
  Assert `{tmp_dir}/{run_id}/run_manifest.json` exists and is valid JSON.
  Assert JSON contains `schema_version=1`. FAILS today: adapter does not exist. Spec: RH-001-S01.

- [x] **1.1.5** Add failing test `test_write_manifest_is_atomic_overwrite_not_write_once`:
  Call `adapter.write_manifest(manifest_v1)` then `adapter.write_manifest(manifest_v2)` on the same run_id.
  Assert the file contains `manifest_v2` fields (overwrite succeeded, no error).
  Spec: D2 (atomic overwrite — NOT write-once; retry semantics require this).

- [x] **1.1.6** Add failing test `test_write_manifest_ioerror_does_not_raise`:
  Mock `Path.write_bytes` to raise `OSError`. Call `adapter.write_manifest(...)`.
  Assert NO exception propagates (manifest failure is non-fatal). Spec: RH-001-S02.

- [x] **1.1.7** Add failing test `test_write_failure_manifest_status_is_error`:
  Build a failure manifest via `adapter.write_failure_manifest(run_id, started_at, error_str)`.
  Assert file exists, `status="error"`, `error=error_str`, registro fields null. Spec: RH-001-S03.

- [x] **1.1.8** Add failing test `test_seq_allocation_same_day_increments`:
  Write two manifests in the same tmp dir with the same date prefix.
  Assert first manifest has `seq=1`, second has `seq=2`.
  Spec: RH-004-S01, RH-004-S02, D3.

- [x] **1.1.9** Add failing test `test_seq_allocation_different_days_independent`:
  Write manifest for day D1 (gets seq 1). Write manifest for day D2.
  Assert D2 manifest has `seq=1` (independent). Spec: RH-004-S03.

- [x] **1.1.10** Add failing test `test_seq_allocation_thread_safe`:
  Fire 10 concurrent `threading.Thread` each calling `adapter.write_manifest(...)` with the same date.
  Assert all 10 resulting manifests have unique seq values 1–10 (no duplicates).
  Spec: D3 (mutex-serialized; single-process deployment invariant).

- [x] **1.1.11** Add failing test `test_scan_completed_run_with_manifest`:
  Write a valid manifest for run_id under tmp dir. Call `adapter.scan(tmp_dir)`.
  Assert result contains an entry with `run_id`, `status="review"`, all manifest fields populated,
  `hydrated=False`. Spec: RH-002-S01, D4.

- [x] **1.1.12** Add failing test `test_scan_legacy_run_extraction_cache_present`:
  Create a dir with only `extraction_cache.json` (no manifest). Call `adapter.scan(tmp_dir)`.
  Assert entry has `status="review"`, `degraded=True`, timestamps may be null. Spec: RH-002-S02.

- [x] **1.1.13** Add failing test `test_scan_legacy_run_pdf_only`:
  Create a dir with only `{run_id}.pdf` (no cache, no manifest). Call `adapter.scan(tmp_dir)`.
  Assert entry has `status="error"`, `degraded=True`. Spec: RH-002 (derive from disk).

- [x] **1.1.14** Add failing test `test_scan_corrupted_manifest_skipped`:
  Create three dirs: valid manifest, corrupted JSON, legacy (cache only). Call `adapter.scan(tmp_dir)`.
  Assert result has 2 entries (valid + legacy); corrupted is absent; no exception raised.
  Spec: RH-002-S03.

- [x] **1.1.15** Add failing test `test_scan_empty_output_dir_returns_empty`:
  Call `adapter.scan(empty_tmp_dir)`.
  Assert result is empty list; no exception. Spec: RH-002-S04.

- [x] **1.1.16** Add failing test `test_scan_non_uuid_dirs_ignored`:
  Create tmp dir with subdirs `"not-a-uuid"` and a valid uuid dir with manifest.
  Assert only the uuid dir appears in scan result. Spec: D4 (UUID-named dirs only).

- [x] **1.1.17** Create `backend/tests/unit/infrastructure/test_lifespan_scan.py`.
  Write failing test `test_lifespan_scan_populates_registry`:
  Build a mock app state with 3 run dirs (1 manifest, 1 legacy cache, 1 pdf-only).
  Simulate the lifespan scan call. Assert registry has 3 entries. Spec: RH-002, RH-006-S01.

- [x] **1.1.18** Add failing test `test_get_runs_returns_sorted_newest_first`:
  Patch registry with 3 entries, `started_at` desc ordering.
  `GET /runs` response must be sorted newest first. Spec: RH-003-S01.

- [x] **1.1.19** Add failing test `test_get_runs_failed_run_appears_with_error_flag`:
  Registry has 1 entry `status="error"`. `GET /runs` returns it with error indicator.
  Spec: RH-003-S02.

- [x] **1.1.20** Add failing test `test_get_runs_legacy_run_appears_last`:
  2 manifest runs + 1 legacy (no `started_at`). `GET /runs` puts legacy last. Spec: RH-003-S03.

- [x] **1.1.21** Create `backend/tests/unit/infrastructure/test_background_wrapper_manifest.py`.
  Write failing test `test_manifest_written_on_success`:
  Run `_run_pipeline_background` with a mocked pipeline that returns a result.
  Assert `adapter.write_manifest` called once after success. Spec: RH-001-S01.

- [x] **1.1.22** Add failing test `test_manifest_written_on_pipeline_exception`:
  Run `_run_pipeline_background` with a mocked pipeline that raises.
  Assert `adapter.write_failure_manifest` called once. Spec: RH-001-S03.

- [x] **1.1.23** Add failing test `test_manifest_ioerror_does_not_fail_run`:
  `adapter.write_manifest` raises `IOError`. Pipeline still completes (registry `status="review"`).
  Spec: RH-001-S02, D1 invariant.

### Phase 1.2 — GREEN: Implement PR-1

- [x] **1.2.1** Create `backend/src/reconciliation/application/run_history.py`.
  Define `RunManifest(BaseModel)` with fields from D2:
  `schema_version: int = 1`, `run_id: str`, `status: Literal["review","error"]`,
  `started_at: str`, `completed_at: str | None`, `seq: int`, `registro_min: str | None`,
  `registro_max: str | None`, `row_count: int`, `match_count: int`, `mismatch_count: int`,
  `warnings: list[str]`, `vision_calls_made: int`, `error: str | None = None`.
  No `pdf_filename` field (CWE-22). Spec: RH-001, D2.
  Define `RunHistoryPort` Protocol with methods: `write_manifest`, `write_failure_manifest`,
  `scan`, `sweep_failed`, `delete_run`. Application layer only — zero IO imports.

- [x] **1.2.2** Create `backend/src/reconciliation/infrastructure/run_history_store.py`.
  Implement `JsonManifestRunHistoryAdapter(RunHistoryPort)`:
  - `write_manifest(manifest: RunManifest, output_dir: Path)`: allocates `seq` under
    `threading.Lock` (scan same-day manifests for max seq); writes via `_atomic_json_write`.
  - `write_failure_manifest(run_id, started_at, error_str, output_dir)`: writes `status="error"`,
    registro fields null, counts 0, `completed_at` = now.
  - `scan(output_dir: Path) -> list[dict]`: iterates UUID-named subdirs; reads manifest if
    present (valid → full entry, corrupted → skip + log); no manifest → derive from disk
    (`extraction_cache.json` → `status="review"`, pdf-only → `status="error"`); per-dir
    try/except (never crash). Sets `hydrated=False` on all entries.
  - `sweep_failed(output_dir: Path, cutoff: datetime)`: deletes only `status=="error"` entries
    older than `cutoff`; removes dir + pops from caller's registry dict.
  - `delete_run(run_id: str, output_dir: Path)`: UUID-validates `run_id`; `rmtree` own dir only.
  Design: D3 (seq lock), D4 (scan strategy).

- [x] **1.2.3** Modify `backend/src/reconciliation/infrastructure/api/routes.py`:
  Add manifest write calls to `_run_pipeline_background` (lazy-import the adapter, mirror
  existing lazy-import pattern at lines 262–267):
  - SUCCESS branch (after `registry[run_id].update(...)` at line 302): call
    `run_history.write_manifest(build_run_manifest(result, registry[run_id]), config.output_dir)`
    inside a `try/except OSError: logger.warning(...)` block.
  - EXCEPT branch (after `registry[run_id].update({"status": "error", ...})` at line 307):
    call `run_history.write_failure_manifest(run_id, started_at, str(exc), config.output_dir)`
    inside a `try/except OSError: logger.warning(...)` block.
  Helper `build_run_manifest(result, entry)`: derives `registro_min/max` from
  `result.declared` numeros (int-sort, lexicographic fallback); `seq` allocated by adapter.
  **`pipeline.py` MUST have zero diff — verify before committing.** Design: D1.

- [x] **1.2.4** Modify `backend/src/reconciliation/infrastructure/api/main.py`:
  In lifespan, after `run_registry = {}`:
  Instantiate `JsonManifestRunHistoryAdapter()` → `app.state.run_history`.
  Call `adapter.scan(config.output_dir)` → merge each entry into `run_registry` (hydrated=False).
  Call `adapter.sweep_failed(config.output_dir, cutoff=now-48h)` on already-failed-old entries.
  Per-dir try/except; log counts; never crash startup. Design: D4.

- [x] **1.2.5** Modify `backend/src/reconciliation/infrastructure/api/schemas.py`:
  Add `RunSummaryResponse(BaseModel)`:
  `run_id: str`, `status: str`, `started_at: str | None`, `completed_at: str | None`,
  `seq: int | None`, `registro_min: str | None`, `registro_max: str | None`,
  `row_count: int`, `match_count: int`, `mismatch_count: int`, `warnings_count: int`,
  `vision_calls_made: int`, `degraded: bool`, `error: str | None`. Spec: RH-003.

- [x] **1.2.6** Add `GET /runs` endpoint to `routes.py`:
  Call `adapter.sweep_failed(...)` (lazy 48h sweep); then return registry values as
  `RunSummaryResponse[]` sorted by `started_at` desc (legacy runs with null `started_at` last).
  Spec: RH-003, D5.

- [x] **1.2.7** Change `extraction_cache.json` write from write-once to atomic-overwrite in
  `backend/src/reconciliation/infrastructure/container.py` (or wherever the cache is written).
  Confirm the current guard exists (raises if file already exists). Remove/relax the guard.
  Retry semantics (PR-2) need this: the retry deletes the cache before re-firing, so the
  write-once guard MUST be gone before retry is implemented. Design: D5 (retry dir reset).

- [x] **1.2.8** Run PR-1 test suite:
  ```
  cd backend && uv run pytest \
    tests/unit/application/test_run_history_port.py \
    tests/unit/infrastructure/test_run_history_adapter.py \
    tests/unit/infrastructure/test_lifespan_scan.py \
    tests/unit/infrastructure/test_background_wrapper_manifest.py \
    -v
  ```
  All 1.1.x tests (23 tests) MUST be GREEN.

- [x] **1.2.9** Verify architecture invariants:
  ```
  git diff HEAD -- backend/src/reconciliation/application/pipeline.py
  git diff HEAD -- backend/src/reconciliation/domain/
  ```
  Assert both diffs are EMPTY. Any diff = rollback task 1.2.3 and re-examine seam. Design: D1.

- [x] **1.2.10** Run regression sweep:
  ```
  cd backend && uv run pytest \
    tests/unit/application/test_pipeline_discarded_pages.py \
    tests/unit/application/test_review_service.py \
    tests/unit/infrastructure/test_container.py \
    -v
  ```
  All must remain GREEN.

- [x] **1.2.11** Work-unit commit A: `feat(run-history): RunHistoryPort + JsonManifestRunHistoryAdapter (schema, seq lock, scan, derive-from-disk, sweep)`
  Covers: 1.2.1 + 1.2.2.

- [x] **1.2.12** Work-unit commit B: `feat(run-history): manifest hooks in _run_pipeline_background (success+except, non-fatal); lifespan scan; GET /runs; RunSummaryResponse`
  Covers: 1.2.3 + 1.2.4 + 1.2.5 + 1.2.6. No push (SA-3).

- [x] **1.2.13** Work-unit commit C (if needed): `fix(run-history): extraction_cache atomic-overwrite (remove write-once guard for retry semantics)`
  Covers: 1.2.7. Separate commit only if this file is in a different logical unit.

### Phase 1.3 — Real-data gate (PR-1)

- [x] **1.3.1** Create `backend/tests/integration/test_run_history_legacy_gate.py`.
  Write test `test_scan_legacy_dirs_no_crash`:
  Call `adapter.scan(Path("backend/runs/"))` against the real legacy run dirs on disk.
  Assert: no exception raised; result is a list; each entry has `run_id`, `status`, `degraded`.
  Assert: entries with `extraction_cache.json` have `status="review"`.
  Assert: entries with only a PDF have `status="error"`.
  Assert: count ≥ 1 (the real dirs exist).
  Spec: RH-002, RH-006 (legacy dirs degrade, not crash or hide).
  Run: `cd backend && uv run pytest tests/integration/test_run_history_legacy_gate.py -v`.

- [x] **1.3.2** Simulate restart round-trip (setup for PR-2 gate):
  Start the server locally; `GET /runs` returns the legacy dirs as degraded entries.
  Assert: the 6+ legacy run dirs appear; no 500 error; degraded field is true for entries
  without manifests. This is a manual smoke check — document the curl output as evidence.

### Phase 1.4 — Judgment Day (PR-1)

- [x] **1.4.1** Run dual-blind judgment day on PR-1 diff before push.
  JD must verify:
  - `application/pipeline.py` has zero diff (ZERO — absolute invariant).
  - `domain/` has zero new IO/SDK imports.
  - Manifest write in `_run_pipeline_background` is inside try/except (non-fatal invariant).
  - `seq` allocation under `threading.Lock` (D3 — single-process race prevention).
  - Scan per-dir try/except (startup resilience).
  - `run_manifest.json` contains no `pdf_filename` field (CWE-22).
  - `GET /runs` excludes manifest-missing-key scenario without crashing.
  No push / PR until JD passes. (SA-3)
  **EVIDENCE**: JD FAIL (#3201: suite-RED + tests-can-rmtree-real-runs latent hazard) →
  fixes applied → JD PASS×2. PR #66 merged.

---

## PR-2 — `feat(run-history): lifecycle — lazy hydration, DELETE, retry, 48h sweep`

> Scope: `_get_hydrated_entry` FastAPI dependency replacing `_require_run` on review-service
> endpoints (builds ReviewService lazily on first access, caches into registry entry);
> `DELETE /runs/{id}` (409 on active run, UUID-validate, rmtree own-dir-only);
> `POST /runs/{id}/retry` (409 unless error, reset dir keep PDF+sunat/, same run_id, re-fire);
> 48h sweep at `GET /runs`; cold-load `GET /runs/{id}` polling path (no hydration needed —
> served from manifest fields).
>
> Depends on: PR-1 merged to main.
> Gate: **full dual-blind JD** (filesystem delete + retry dir-reset semantics; 409 guard correctness).
>
> Budget note: if tests push past ~400 lines, split PR-2a (DELETE + guard + sweep at lifespan)
> → PR-2b (retry + sweep at GET /runs + cold-load path).

### Phase 2.0 — Pre-work: confirm hydration seam

- [x] **2.0.1** READ `backend/src/reconciliation/infrastructure/api/routes.py` — search for
  `_require_run` (or the current dependency that validates run readiness and returns the
  registry entry). Identify: (a) its exact function signature; (b) all endpoint decorators
  that use it; (c) whether it already calls `build_review_service` or just looks up the registry.
  Record the exact pattern to mirror for `_get_hydrated_entry`. Read-only pre-flight.
  **EVIDENCE**: PR #67 merged (feat/run-history-lifecycle). `_get_hydrated_entry` dep implemented,
  replacing `_require_run` on all review-service endpoints. JD FAIL×2→fix→PASS×2 (#3208).

- [x] **2.0.2** READ `backend/src/reconciliation/infrastructure/container.py` — confirm
  `build_review_service(ctx)` and `build_reprocess_service(config, ctx, review_service)` signatures.
  These are called by `_get_hydrated_entry` for lazy hydration. Read-only pre-flight.
  **EVIDENCE**: confirmed during PR-2 pre-flight; implemented in PR #67.

### Phase 2.1 — RED: Write failing tests for PR-2

- [x] **2.1.1** Create `backend/tests/unit/infrastructure/test_lazy_hydration.py`.
  Write failing test `test_get_runs_id_no_hydration_served_from_manifest`:
  Registry entry `hydrated=False`, manifest fields populated, no `review_service` key.
  `GET /runs/{id}` should return summary fields (run_id, status, seq, etc.) without
  calling `build_review_service`. Assert response 200 and `review_service` still None in registry.
  FAILS today: `GET /runs/{id}` (status poll) may trigger hydration or 404. Spec: D4.
  **EVIDENCE**: PR #67 merged; all PR-2 RED tests written and passed GREEN.

- [x] **2.1.2** Add failing test `test_get_table_triggers_lazy_hydration`:
  Registry entry `hydrated=False`. `GET /runs/{id}/table` called.
  Assert `build_review_service` called exactly once; registry entry gains `review_service`;
  `hydrated` is True after. Spec: D4, RH-005, RH-011-S01.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.3** Add failing test `test_get_table_second_call_no_rehyd`:
  Registry entry already `hydrated=True` with `review_service`. `GET /runs/{id}/table` called twice.
  Assert `build_review_service` NOT called on second call (cached). Spec: D4.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.4** Create `backend/tests/unit/infrastructure/test_delete_run.py`.
  Write failing test `test_delete_removes_dir_and_registry`:
  Create a tmp run dir. Insert registry entry (status="review"). `DELETE /runs/{run_id}`.
  Assert dir deleted from disk. Assert entry removed from registry. Assert 204 response.
  Spec: RH-009-S01.
  **EVIDENCE**: PR #67 merged; JD FAIL×2 (cold-load 409 + sweep-deletes-mid-retry) → fix → PASS×2 (#3208).

- [x] **2.1.5** Add failing test `test_delete_scoped_to_own_dir`:
  Create two run dirs (A and B). `DELETE /runs/A`.
  Assert dir B still exists. Spec: RH-009-S02.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.6** Add failing test `test_delete_non_uuid_returns_400`:
  `DELETE /runs/../../../etc/passwd`. Assert 400 before any filesystem call.
  Spec: RH-009-S03.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.7** Add failing test `test_delete_unknown_run_returns_404`:
  `DELETE /runs/{unknown_uuid}`. Registry has no such entry. Assert 404. Spec: D5.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.8** Add failing test `test_delete_processing_run_returns_409`:
  Registry entry `status="processing"`. `DELETE /runs/{run_id}`. Assert 409.
  Spec: D5 (409 if pending/processing). Design: D5.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.9** Create `backend/tests/unit/infrastructure/test_retry_run.py`.
  Write failing test `test_retry_reuses_same_run_id`:
  Failed run dir has `{run_id}.pdf`. `POST /runs/{run_id}/retry`. Assert pipeline re-fired
  with the SAME `run_id`. Assert a new manifest will be written (adapter called) when it completes.
  Spec: RH-007-S02, D5.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.10** Add failing test `test_retry_409_unless_error_status`:
  `POST /runs/{run_id}/retry` where run has `status="review"` (completed). Assert 409.
  Spec: D5 (retry only valid on error status).
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.11** Add failing test `test_retry_resets_dir_keeps_pdf_and_sunat`:
  Failed run dir has `{run_id}.pdf`, `extraction_cache.json`, `review.json`, `sunat/` subdir.
  `POST /runs/{run_id}/retry`. Assert `extraction_cache.json` deleted. Assert `review.json`
  deleted. Assert `{run_id}.pdf` still present. Assert `sunat/` still present.
  Spec: D5 (reset dir, keep PDF and sunat/).
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.12** Add failing test `test_retry_409_while_processing`:
  Registry entry `status="processing"`. `POST /runs/{run_id}/retry`. Assert 409.
  Spec: RH-007-S04.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.13** Create `backend/tests/unit/infrastructure/test_48h_sweep.py`.
  Write failing test `test_sweep_deletes_old_failed_run`:
  Failed run dir, `started_at` = 49h ago. Call `adapter.sweep_failed(output_dir, cutoff)`.
  Assert dir deleted. Assert registry entry removed. Spec: RH-008-S01.
  **EVIDENCE**: PR #67 merged; JD PASS×2 (#3208).

- [x] **2.1.14** Add failing test `test_sweep_never_deletes_completed_run`:
  `status="review"`, old timestamp. Call `sweep_failed`. Assert dir intact. Spec: RH-008-S02.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.15** Add failing test `test_sweep_keeps_recent_failed_run`:
  Failed run, `started_at` = 23h ago. Call `sweep_failed`. Assert dir intact. Spec: RH-008-S03.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.16** Add failing test `test_sweep_ignores_non_run_dirs`:
  Failed run (>48h) + unrelated dir in output_dir. Call `sweep_failed`.
  Assert only the failed run dir is removed. Spec: RH-008-S04.
  **EVIDENCE**: PR #67 merged.

- [x] **2.1.17** Add failing test `test_get_runs_triggers_sweep`:
  Failed run dir >48h in registry + on disk. `GET /runs` called.
  Assert sweep ran: dir deleted and entry absent from response. Spec: RH-008, D4.
  **EVIDENCE**: PR #67 merged.

### Phase 2.2 — GREEN: Implement PR-2

- [x] **2.2.1** Add `_get_hydrated_entry(run_id, registry, config)` FastAPI dependency to `routes.py`:
  Looks up registry entry; if `hydrated=False`, calls `build_review_service(entry["ctx"])` +
  `build_reprocess_service(...)`, caches into entry, sets `hydrated=True`. Raises 404 if not found.
  Replaces `_require_run` (or the current guard) on all review-service endpoints
  (`/table`, `/reassign`, `/edit-line`, `/export`, `/reprocess`, etc.). Spec: D4, RH-005.
  **EVIDENCE**: PR #67 merged.

- [x] **2.2.2** Add `DELETE /runs/{run_id}` endpoint to `routes.py`:
  UUID-validate `run_id` (regex `^[0-9a-f]{8}-[0-9a-f]{4}-...`, 400 if invalid).
  404 if not in registry. 409 if `status` in `{"pending","processing"}`.
  Call `adapter.delete_run(run_id, config.output_dir)` (rmtree own dir only).
  Pop registry entry. Return 204. Spec: RH-009, D5.
  **EVIDENCE**: PR #67 merged.

- [x] **2.2.3** Add `POST /runs/{run_id}/retry` endpoint to `routes.py`:
  UUID-validate `run_id`. 404 if not found. 409 if `status != "error"`.
  Reset dir: delete `extraction_cache.json`, `review.json`, `pages/` if present.
  Keep `{run_id}.pdf` and `sunat/` (immutable fetch cache).
  Re-fire `_run_pipeline_background(run_id, pdf_path, config, registry)` as BackgroundTask.
  Set registry `status="processing"`. Return 202. Spec: RH-007-S02, D5.
  **EVIDENCE**: PR #67 merged; JD PASS×2 after fixes (#3208).

- [x] **2.2.4** Wire 48h sweep into `GET /runs` in `routes.py`:
  Before assembling response, call `adapter.sweep_failed(config.output_dir, cutoff=utcnow()-48h)`.
  Spec: RH-008, D4.
  **EVIDENCE**: PR #67 merged.

- [x] **2.2.5** Ensure `GET /runs/{id}` (status poll endpoint) returns manifest-field summary
  WITHOUT triggering hydration (served from the registry entry directly). If that endpoint
  currently calls `_require_run` which triggers hydration, change it to a non-hydrating lookup.
  Spec: D4 (cold-load: GET /runs/{id} polling needs no hydration).
  **EVIDENCE**: PR #67 merged; JD FAIL caught cold-load 409 (mock-theatre with hand-seeded ctx);
  fix verified by both blind judges before PASS×2.

- [x] **2.2.6** Update `backend/src/reconciliation/infrastructure/api/main.py` CORS allow_methods:
  Add `"DELETE"` to `allow_methods` (currently only GET/POST/PATCH/OPTIONS). Required for
  `DELETE /runs/{id}`. Verify CORS policy is not widened beyond delete.
  **EVIDENCE**: PR #67 merged.

- [x] **2.2.7** Run PR-2 test suite:
  ```
  cd backend && uv run pytest \
    tests/unit/infrastructure/test_lazy_hydration.py \
    tests/unit/infrastructure/test_delete_run.py \
    tests/unit/infrastructure/test_retry_run.py \
    tests/unit/infrastructure/test_48h_sweep.py \
    -v
  ```
  All 2.1.x tests (17 tests) MUST be GREEN.
  **EVIDENCE**: all GREEN; PR #67 merged.

- [x] **2.2.8** Verify invariants:
  ```
  git diff HEAD -- backend/src/reconciliation/application/pipeline.py
  git diff HEAD -- backend/src/reconciliation/domain/
  ```
  Both MUST be empty. Confirm DELETE scope: `rmtree` call never uses client input directly.
  **EVIDENCE**: JD×2 confirmed pipeline.py zero-diff + rmtree scoping on PR-2 diff.

- [x] **2.2.9** Run regression sweep:
  ```
  cd backend && uv run pytest \
    tests/unit/application/ \
    tests/unit/infrastructure/test_run_history_adapter.py \
    tests/unit/infrastructure/test_background_wrapper_manifest.py \
    -v
  ```
  All must remain GREEN.
  **EVIDENCE**: PR #67 merged with full regression sweep.

- [x] **2.2.10** Work-unit commit A: `feat(run-history): lazy hydration dep (_get_hydrated_entry); GET /runs/{id} cold-load path (no hydration)`
  Covers: 2.2.1 + 2.2.5.
  **EVIDENCE**: PR #67.

- [x] **2.2.11** Work-unit commit B: `feat(run-history): DELETE /runs/{id} (409 guard, UUID-validate, rmtree own-dir); CORS allow DELETE`
  Covers: 2.2.2 + 2.2.6.
  **EVIDENCE**: PR #67.

- [x] **2.2.12** Work-unit commit C: `feat(run-history): POST /runs/{id}/retry (dir reset keep PDF+sunat/, same run_id, re-fire pipeline); 48h sweep at GET /runs`
  Covers: 2.2.3 + 2.2.4. No push (SA-3).
  **EVIDENCE**: PR #67.

### Phase 2.3 — Real-data gate (PR-2)

- [x] **2.3.1** Create `backend/tests/integration/test_run_history_restart_gate.py`.
  Write test `test_restart_round_trip_manifest_survives`:
  (1) Run pipeline via test fixture → manifest written for run_id.
  (2) Clear the in-memory registry (simulate restart): re-call `adapter.scan(output_dir)`.
  (3) Assert the run reappears in the scan result with `status="review"`, `seq` intact,
      `degraded=False`.
  (4) Simulate lazy hydration: call `build_review_service(ctx)` via the hydration dep.
  (5) Assert the review table is non-empty and matches the original run's row count.
  Spec: RH-006-S01, RH-006-S02.
  **EVIDENCE**: PR #67 merged; SA-5 restart scenario verified (#3215 + sa5-runhist-* screenshots).

- [x] **2.3.2** Write test `test_retry_dir_reset_semantics`:
  (1) Locate a real legacy failed run dir (pdf-only, no cache). Copy to a tmp dir.
  (2) Simulate a retry: delete `extraction_cache.json` (assert it was absent → noop safe).
  (3) Confirm `{run_id}.pdf` still present. Confirm `sunat/` still present if it exists.
  (4) Assert dir state is correct for re-fire. Spec: D5 (retry dir reset).
  Run: `cd backend && uv run pytest tests/integration/test_run_history_restart_gate.py -v`.
  **EVIDENCE**: PR #67 merged.

### Phase 2.4 — Judgment Day (PR-2)

- [x] **2.4.1** Run dual-blind judgment day on PR-2 diff before push.
  JD must verify:
  - `DELETE` UUID-validation is present and fires BEFORE any `rmtree` call.
  - `rmtree` is called with `config.output_dir / run_id` — never with client-supplied path.
  - `POST /runs/{id}/retry` 409 guard correctly covers both `processing` and `review` statuses.
  - Dir reset deletes `extraction_cache.json` and `review.json` only; PDF + sunat/ intact.
  - Lazy hydration caches into registry (second call skips `build_review_service`).
  - 48h sweep touches only `status=="error"` entries — verified line by line.
  - `pipeline.py` zero diff. `domain/` zero diff.
  No push / PR until JD passes. (SA-3)
  **EVIDENCE**: JD FAIL×2 (#3208: cold-load 409 CRITICAL + sweep-deletes-mid-retry CRITICAL,
  both reproduced by both blind judges) → fixes applied → JD PASS×2. PR #67 merged.

---

## PR-3 — `feat(run-history): frontend — hamburger menu, /historial, runStore fix, cold-load UX`

> Scope: `RunHistoryMenu.vue` (new `features/run/` component) mounted in `App.vue` header;
> `RunHistoryPage.vue` new route `/historial` (TanStack Query list, status badges, delete/retry);
> `router.ts` `/historial` route; `frontend/src/api/types.ts` + `frontend/src/api/client.ts`
> (listRuns, deleteRun, retryRun); `ReviewPage.vue` mount fix (`runStore.runId = props.id`
> on setup); `runStore` persistence in localStorage.
>
> Depends on: PR-2 merged to main.
> Gate: **ctr-reviewer + SA-5 Playwright MANDATORY** (visible-UX feature; unit tests alone do not prove runtime behavior — CLAUDE.md §Fix/Feature Discipline #2).

### Phase 3.0 — Pre-work: read existing patterns

- [x] **3.0.1** READ `frontend/src/app/App.vue` — identify the exact header markup structure and
  where a hamburger icon component should be mounted. Confirm the `runStore.runId` reference
  in the template (nav link visibility gate). Read-only pre-flight.
  **ANSWER**: Header is `.app-header__inner` (wordmark + `<nav v-if="runStore.runId">` with
  `margin-left: auto`). Nav gates: "Nueva subida" on `runStore.runId`, "Revisión" on
  `runStore.isReady` (status === 'review'). Menu mounted inside a new `.app-header__right`
  wrapper (nav + menu) carrying the `margin-left: auto` so the always-visible menu stays
  right-aligned when the nav is hidden.

- [x] **3.0.2** READ `frontend/src/features/review/ReviewPage.vue` — identify the `setup()` or
  `onMounted` hook where `runStore.runId` is currently set (or not set from route param).
  Confirm the existing `tableQuery.isFetching` spinner behavior for cold-load UX coverage.
  Read-only pre-flight.
  **ANSWER**: ReviewPage did NOT use runStore at all — it consumed `props.id` only (the gap).
  `<script setup>` runs per mount (App's RouterView keys on `route.fullPath`, so history nav
  remounts). Cold-load UX already covered: "Esperando que el pipeline complete..." block +
  `tableQuery.isFetching` → ReviewGrid `isLoading`. NOTE: RH-011-S03 additionally requires
  mirroring the polled status into the store (`runStore.setStatus`, the store's documented
  mirror hook) or the "Revisión" link (gated on `isReady`) never appears on cold-load.

- [x] **3.0.3** READ `frontend/src/stores/run.ts` (or equivalent runStore) — confirm the current
  `runId` field, whether it is persisted in localStorage today, and the `reset()` method shape.
  Read-only pre-flight.
  **ANSWER**: `runId = ref<string | null>(null)`, NOT persisted anywhere today. `reset()` nulls
  runId/status/uploading/uploadProgress/error. localStorage key-conflict check: `rg localStorage
  frontend/src` → ZERO existing usage — key `"run_id"` is free (Open Question 3: no prefix
  needed). GOTCHA: jsdom 24.1 under Node 26 returns `undefined` from the `window.localStorage`
  getter (sessionStorage works) — vitest suites install a Map-backed stub
  (`__tests__/test-utils/local-storage-stub.ts`); real-browser persistence is SA-5's 3.3.1(f).

### Phase 3.1 — RED: Write failing vitest tests

- [x] **3.1.1** Create `frontend/src/__tests__/features/RunHistoryMenu.test.ts`.
  Write failing test `renders three menu sections: Nuevo batch, Batch actual, Historial`.
  FAILS today: `RunHistoryMenu.vue` does not exist. Spec: RH-010.

- [x] **3.1.2** Add failing test `Nuevo batch resets store and navigates to upload page`:
  Click [Nuevo batch]. Assert `runStore.reset()` called. Assert router pushed to `/`.
  Spec: RH-010-S01.

- [x] **3.1.3** Add failing test `Batch actual disabled when no run is active`:
  `runStore.runId` is null. Assert [Batch actual] is disabled or hidden. Spec: RH-010-S04.

- [x] **3.1.4** Add failing test `Batch actual navigates to current run when active`:
  `runStore.runId = "abc"`. Click [Batch actual]. Assert router pushed to `/runs/abc`.
  Spec: RH-010.

- [x] **3.1.5** Create `frontend/src/__tests__/features/RunHistoryPage.test.ts`.
  Write failing test `renders run list from GET /runs response`:
  Mock `listRuns()` returning 3 entries. Assert 3 rows rendered with label + status badge.
  FAILS today: `RunHistoryPage.vue` does not exist. Spec: RH-010-S02.

- [x] **3.1.6** Add failing test `clicking a history entry navigates to the run`:
  Click entry with `run_id="xyz"`. Assert router pushed to `/runs/xyz`. Spec: RH-010-S03.

- [x] **3.1.7** Add failing test `delete button shows confirm dialog before deletion`:
  Mock `deleteRun`. Click [Eliminar]. Assert confirm dialog appears. Assert `deleteRun` NOT called.
  Confirm → assert `deleteRun("xyz")` called. Spec: RH-009 frontend.

- [x] **3.1.8** Add failing test `retry button is only shown for error-status runs`:
  2 entries: `status="review"` and `status="error"`. Assert [Reintentar] visible only on error entry.
  Spec: RH-007-S01.

- [x] **3.1.9** Add failing test `retry button calls retryRun and invalidates runs query`:
  Click [Reintentar] on error run. Assert `retryRun("xyz")` called. Assert runs query invalidated.
  Spec: RH-007-S02.

- [x] **3.1.10** Create `frontend/src/__tests__/features/ReviewPage.coldload.test.ts`.
  Write failing test `sets runStore.runId from route param on mount`:
  Mount `ReviewPage` with `props.id = "abc123"`, `runStore.runId = null`.
  Assert `runStore.runId === "abc123"` after setup. Spec: RH-011-S01.

- [x] **3.1.11** Add failing test `Revisión nav link appears after cold-load sets runStore.runId`:
  App.vue renders with no active runId. ReviewPage mounts with route param and sets runId.
  Assert nav link becomes visible. Spec: RH-011-S03.

- [x] **3.1.12** Add failing test `runStore.runId persists in localStorage after assignment`:
  `runStore.runId = "abc123"`. Check `localStorage.getItem("run_id") === "abc123"`.
  Spec: RH-011-S02.

### Phase 3.2 — GREEN: Implement PR-3

- [x] **3.2.1** Add to `frontend/src/api/types.ts`:
  `RunSummaryResponse` interface (matching backend `RunSummaryResponse`):
  `run_id`, `status`, `started_at?`, `completed_at?`, `seq?`, `registro_min?`, `registro_max?`,
  `row_count`, `match_count`, `mismatch_count`, `warnings_count`, `vision_calls_made`,
  `degraded`, `error?`. Spec: RH-003.

- [x] **3.2.2** Add to `frontend/src/api/client.ts`:
  `listRuns(): Promise<RunSummaryResponse[]>` → `GET /api/v1/runs`.
  `deleteRun(runId: string): Promise<void>` → `DELETE /api/v1/runs/{runId}`.
  `retryRun(runId: string): Promise<{run_id: string, status: string}>` → `POST /api/v1/runs/{runId}/retry`.
  Design: D6.

- [x] **3.2.3** Create `frontend/src/features/run/RunHistoryMenu.vue`:
  Props: none. Always visible in the App.vue header (not gated on runId).
  Three actions: [Nuevo batch] (`runStore.reset()` + router push `/`),
  [Batch actual] (→ `/runs/{runStore.runId}`; disabled when `runStore.runId` is null),
  [Historial] (→ `/historial`).
  es-PE strings. Design: D6.

- [x] **3.2.4** Create `frontend/src/features/run/RunHistoryPage.vue`:
  Route: `/historial`. TanStack Query `useRunsList` hook calls `listRuns()`.
  Each row: `DD-MM-YYYY · Registros {min}–{max} · #{seq}` label + status badge (green/red/gray).
  [Reintentar] (only for `status="error"`, mutation → `retryRun`, invalidates query).
  [Eliminar] (all statuses, confirm dialog reusing existing dialog pattern → `deleteRun`, invalidates).
  On row click: router push `/runs/{run_id}`.
  Degraded entries: show `—` for unavailable fields. Design: D6.

- [x] **3.2.5** Modify `frontend/src/app/App.vue`:
  Mount `<RunHistoryMenu>` in the header. Spec: RH-010.

- [x] **3.2.6** Modify `frontend/src/app/router.ts`:
  Add route `{ path: '/historial', component: RunHistoryPage }`. Spec: RH-010.

- [x] **3.2.7** Modify `frontend/src/features/review/ReviewPage.vue`:
  In `setup()` (or onMounted if setup is not available): add
  `if (!runStore.runId && props.id) { runStore.runId = props.id }`.
  This enables cold-load: navigating directly to `/runs/{id}` after a restart sets the store.
  The existing `tableQuery.isFetching` spinner already covers the lazy backend hydration.
  Design: D6, Spec: RH-011.

- [x] **3.2.8** Modify `frontend/src/stores/run.ts`:
  Persist `runId` to `localStorage` on set (watch + init from localStorage on store creation).
  On `reset()`, clear localStorage key. Spec: RH-011-S02.

- [x] **3.2.9** Run PR-3 vitest suite:
  ```
  cd frontend && npm test -- RunHistoryMenu RunHistoryPage ReviewPage.coldload
  ```
  All 3.1.x tests (12 tests) MUST be GREEN.

- [x] **3.2.10** Full frontend regression:
  ```
  cd frontend && npm test
  ```
  All prior tests must remain GREEN. `npx vue-tsc --noEmit` → 0 errors.

- [x] **3.2.11** Work-unit commit A: `feat(run-history): RunSummaryResponse types; listRuns/deleteRun/retryRun API client`
  Covers: 3.2.1 + 3.2.2.

- [x] **3.2.12** Work-unit commit B: `feat(run-history): RunHistoryMenu (hamburger: Nuevo/Batch actual/Historial) + /historial route + RunHistoryPage`
  Covers: 3.2.3 + 3.2.4 + 3.2.5 + 3.2.6.

- [x] **3.2.13** Work-unit commit C: `fix(run-history): ReviewPage sets runStore.runId from route param on mount; persist runId to localStorage`
  Covers: 3.2.7 + 3.2.8. No push (SA-3).

### Phase 3.3 — SA-5 Runtime Validation (PR-3 — MANDATORY)

- [x] **3.3.1** Validate against the RUNNING app via Playwright MCP (SA-5 — mandatory gate;
  green vitest alone does NOT prove runtime behavior per CLAUDE.md §Fix/Feature Discipline #2):
  (a) Upload PDF → wait for pipeline → open hamburger menu → click [Historial] → assert run
      listed with correct label (fecha + registro range + #seq) and status badge.
  (b) Click [Nuevo batch] → assert navigated to upload page, runStore.runId cleared.
  (c) Complete a second run → assert it appears at top of /historial list.
  (d) Click [Eliminar] on a run → confirm dialog → run removed from list.
  (e) Navigate directly to `/runs/{run_id}` after server restart (simulate cold-load) →
      assert ReviewPage loads with full reconciliation table.
  (f) Refresh browser on ReviewPage → assert run_id restored from localStorage → table visible.
  STOP if any step fails — do not mark PR-3 done until all 6 sub-checks pass.
  **EVIDENCE**: SA-5 PASS all 6 sub-checks (#3215 + sa5-runhist-* screenshots). SA-5 caught
  two runtime bugs before merge: manifest registro field mismatch (fix PR #68) +
  refetchInterval-ignores-error-state infinite polling (fix PR #69 / commit e1172e4).

### Phase 3.4 — Review (PR-3)

- [x] **3.4.1** Run single-pass `ctr-reviewer` review on PR-3 diff (frontend PR; ctr-reviewer
  sufficient per CLAUDE.md §Fix/Feature Discipline #4 — dual-blind JD for parser/pipeline PRs).
  Reviewer must verify:
  - [Eliminar] confirm dialog prevents accidental deletion.
  - [Reintentar] appears ONLY on error-status runs.
  - Cold-load path does not loop (runStore.runId set once; no watch re-trigger).
  - localStorage key cleared on `reset()` (no stale run after [Nuevo batch]).
  - Degraded runs show `—` for null fields; no "undefined" strings in UI.
  No push / PR until review passes. (SA-3)
  **EVIDENCE**: ctr-reviewer APPROVE (#3214); fixes applied (#3216); PR #66/#68/#69 merged.

---

## Final Tasks

### SDD Verification + Archive

- [x] **F.1** Run `sdd-verify run-history-persistence`:
  Verify all 11 spec requirements (RH-001..RH-011) are satisfied.
  Check: manifests written on success+failure; legacy dirs degrade; listing sorted; seq stable;
  past run re-activatable; restart durable; retry fires; 48h sweep; delete scoped; hamburger;
  cold-load works. Report all CRITICAL / WARNING / SUGGESTION.
  **EVIDENCE**: sdd-verify READY TO ARCHIVE (engram #3218): 0 CRITICAL, 2 doc-state WARNINGs
  (stale tasks.md checkboxes + promote spec) — resolved by this archive pass.

- [x] **F.2** Run `sdd-archive run-history-persistence`:
  Persist archive report to `openspec/changes/archive/run-history-persistence/`.
  Update `docs/HANDOFF.md` status section.
  Conventional commit: `docs(sdd): archive run-history-persistence (SDD#3 complete)`.
  **EVIDENCE**: this archive pass — tasks.md reconciled, spec promoted, folder archived,
  docs updated, archive report saved to engram.

---

## Dependency Graph

```
main (post SDD#2 PR#61–#65)
    │
    ▼
PR-1  feat(run-history): persistence core
      port + adapter + manifest hooks + lifespan scan + GET /runs
      ~300–360 LOC  Gate: dual-blind JD
    │
    ▼
PR-2  feat(run-history): lifecycle
      lazy hydration + DELETE + retry + 48h sweep
      ~350–420 LOC  Gate: dual-blind JD
      [if > 400: split PR-2a (DELETE+guard+sweep-lifespan) → PR-2b (retry+sweep-GET+coldload)]
    │
    ▼
PR-3  feat(run-history): frontend
      hamburger menu + /historial + runStore fix + cold-load UX
      ~320–380 LOC  Gate: ctr-reviewer + SA-5 Playwright
```

All PRs sequential (stacked-to-main). Each must be independently GREEN + gate-passed before the next starts.
Within each PR: write all RED tests first; then GREEN implementation; then gate.

**Parallelism**: NONE across PR boundaries. Within a PR, 1.1.x / 2.1.x / 3.1.x RED tests can be written in any order before the first GREEN task.

---

## Files Created/Modified

| File | PR | Action |
|------|-----|--------|
| `backend/src/reconciliation/application/run_history.py` | PR-1 | CREATE — `RunManifest`, `RunHistoryPort` |
| `backend/src/reconciliation/infrastructure/run_history_store.py` | PR-1 | CREATE — `JsonManifestRunHistoryAdapter` |
| `backend/src/reconciliation/infrastructure/api/routes.py` | PR-1,2 | MODIFY — manifest hooks, GET /runs, DELETE, retry, lazy-hydration dep |
| `backend/src/reconciliation/infrastructure/api/main.py` | PR-1,2 | MODIFY — lifespan scan, run_history state, CORS allow DELETE |
| `backend/src/reconciliation/infrastructure/api/schemas.py` | PR-1 | MODIFY — `RunSummaryResponse` |
| `backend/src/reconciliation/infrastructure/container.py` | PR-1 | MODIFY — remove extraction_cache write-once guard |
| `backend/src/reconciliation/application/pipeline.py` | **None** | ZERO diff — invariant enforced by JD |
| `backend/src/reconciliation/domain/` | **None** | Pure — zero run-history concepts |
| `frontend/src/features/run/RunHistoryMenu.vue` | PR-3 | CREATE |
| `frontend/src/features/run/RunHistoryPage.vue` | PR-3 | CREATE |
| `frontend/src/app/App.vue` | PR-3 | MODIFY — mount RunHistoryMenu |
| `frontend/src/app/router.ts` | PR-3 | MODIFY — /historial route |
| `frontend/src/features/review/ReviewPage.vue` | PR-3 | MODIFY — runStore.runId from route param |
| `frontend/src/stores/run.ts` | PR-3 | MODIFY — localStorage persistence |
| `frontend/src/api/types.ts` | PR-3 | MODIFY — `RunSummaryResponse` |
| `frontend/src/api/client.ts` | PR-3 | MODIFY — listRuns/deleteRun/retryRun |
| `backend/tests/unit/application/test_run_history_port.py` | PR-1 | CREATE |
| `backend/tests/unit/infrastructure/test_run_history_adapter.py` | PR-1 | CREATE |
| `backend/tests/unit/infrastructure/test_lifespan_scan.py` | PR-1 | CREATE |
| `backend/tests/unit/infrastructure/test_background_wrapper_manifest.py` | PR-1 | CREATE |
| `backend/tests/integration/test_run_history_legacy_gate.py` | PR-1 | CREATE — real-data gate |
| `backend/tests/unit/infrastructure/test_lazy_hydration.py` | PR-2 | CREATE |
| `backend/tests/unit/infrastructure/test_delete_run.py` | PR-2 | CREATE |
| `backend/tests/unit/infrastructure/test_retry_run.py` | PR-2 | CREATE |
| `backend/tests/unit/infrastructure/test_48h_sweep.py` | PR-2 | CREATE |
| `backend/tests/integration/test_run_history_restart_gate.py` | PR-2 | CREATE — real-data gate |
| `frontend/src/__tests__/features/RunHistoryMenu.test.ts` | PR-3 | CREATE |
| `frontend/src/__tests__/features/RunHistoryPage.test.ts` | PR-3 | CREATE |
| `frontend/src/__tests__/features/ReviewPage.coldload.test.ts` | PR-3 | CREATE |

---

## Open Questions (SA-2 — flagged before apply)

1. **`_atomic_json_write` location**: task 1.0.3/1.0.4 must confirm the exact module so the
   adapter can import or mirror it. If it lives in `container.py` (internal), the adapter either
   imports it (infrastructure-to-infrastructure, acceptable) or replicates the pattern. Do NOT
   import from `application/` layer.

2. **`_require_run` exact shape**: task 2.0.1 must confirm the existing guard dep name and
   which endpoints use it. If the guard does more than look up the registry (e.g. also calls
   `build_review_service`), the lazy-hydration dep replaces it rather than wraps it.

3. **runStore persistence key**: `localStorage` key proposed as `"run_id"`. If another store
   already uses this key, prefix it (e.g. `"ctr_active_run_id"`). Confirm during 3.0.3 pre-flight.

4. **Retry 409 boundary**: spec RH-007-S04 says "rejected or queued per pipeline rules." Current
   pipeline is single-run-at-a-time. Confirm whether `status="processing"` of ANY run (not just
   the retried run) should block retry. The design says 409 when another run is processing — verify
   the exact guard condition in existing routes before implementing 2.2.3.
