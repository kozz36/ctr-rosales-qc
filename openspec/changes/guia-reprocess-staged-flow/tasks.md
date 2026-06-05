# Tasks — guia-reprocess-staged-flow (PR #1 FOUNDATION)

## Meta
- change: guia-reprocess-staged-flow
- pr_slice: 1 (stacked-to-main, self-contained)
- delivery_strategy: stacked-to-main
- artifact_store: hybrid
- produced: 2026-06-05

---

## Hard Invariants (carry into every work-unit)

These MUST be respected in every task below. Any deviation = SA-2 stop:

- **Domain purity** — no SDK/IO/framework import under `domain/`. `ErroredGuia` stays pure Pydantic BaseModel. Auto-reject if violated.
- **Ports at the boundary** — `application/` imports ZERO concrete adapters. Any concrete-adapter import in `application/` = auto-reject.
- **`fecha` is NEVER a grouping axis** — group key fixed: `(registro, material_canonical, unidad)`.
- **Units NEVER converted** — KG/TN/RD/Rollo summed independently.
- **Additive-only** — errored_guias is a side-channel. MUST NOT alter group key, status, delta, qty, or rows for any correctly-processed guía.
- **Input PDF read-only** — no mutation of source data.
- **Reconciliation = validation gate** — mismatches flagged `requires_review`, never auto-corrected. This slice is read-only; no mutation of ErroredGuia state.

---

## Review Workload Forecast

| Metric | Estimate |
|--------|----------|
| Files changed | 8 |
| Estimated changed lines | ~230 LOC (backend ~160, frontend ~70) |
| New test files | 2 new (backend integration + frontend vitest) |
| Existing test files modified | 1 (test_container.py or test_api_routes.py for integration gate) |
| Chained PRs recommended | No — this IS PR #1 of a pre-planned stack, self-contained |
| 400-line budget risk | Low |
| Decision needed before apply | No |

---

## Dependency Graph

```
T-1 (domain model)
  └── T-2 (ReviewService state)
        └── T-3 (container hydration)
              └── T-4 (API/DTO + integration gate)
                    └── T-5 (TS types)
                          └── T-6 (ErroredGuiasPanel.vue)
                                └── T-7 (ReviewPage.vue mount)
                                      └── T-8 (SA-5 Playwright gate)
```

T-1 through T-4 are sequential (each depends on the prior). T-5 can start in parallel with T-4 once T-4 schema changes are known (they are fixed by spec). T-6 depends on T-5. T-7 depends on T-6. T-8 is the final gate and depends on all prior tasks.

---

## Task List

### T-1 — ErroredGuia.retry_attempted additive flag + cache round-trip

**Spec**: REV-E01  
**Sequential after**: nothing (first task)  
**Test file**: `backend/tests/unit/domain/test_models.py` (new test class) OR new file `backend/tests/unit/domain/test_errored_guia_retry_attempted.py`

**Failing tests to write first (strict-TDD RED gate)**:

1. `test_retry_attempted_defaults_false` — instantiate `ErroredGuia(registro="232", guia_id="T001-001", source_pages=[1])`, assert `eg.retry_attempted is False`.
2. `test_model_dump_includes_retry_attempted` — call `eg.model_dump(mode="json")`, assert key `"retry_attempted"` present and value is `False`; assert existing keys `registro`, `guia_id`, `source_pages` are unaltered.
3. `test_old_cache_without_retry_attempted_loads_gracefully` — call `ErroredGuia.model_validate({"registro": "232", "guia_id": "T001-001", "source_pages": [1]})` (no `retry_attempted` key); assert `eg.retry_attempted is False` (backward-compatible default fill).

**Implementation**:  
`backend/src/reconciliation/domain/models.py` line ~405 — add `retry_attempted: bool = False` to `ErroredGuia` after `source_pages`. No other change.

**Invariant check**: Pure Pydantic BaseModel, no IO import, no grouping key touched.

---

### T-2 — ReviewService: trailing errored_guias param on __init__ and restore_from_sidecar + read-only property

**Spec**: REV-E03  
**Sequential after**: T-1 (ErroredGuia must carry retry_attempted before service stores it)  
**Test file**: `backend/tests/unit/application/test_review_service.py` — add new test class `TestReviewServiceErroredGuias`

**Failing tests to write first**:

1. `test_init_stores_errored_guias` — construct `ReviewService(declared=[], guias=[], rows=[], ctx=mock_ctx, errored_guias=[eg1, eg2])`; assert `service.errored_guias == [eg1, eg2]`.
2. `test_init_defaults_errored_guias_to_empty` — construct without `errored_guias` kwarg; assert `service.errored_guias == []` (trailing optional, no existing caller breaks).
3. `test_errored_guias_property_is_read_only` — assert `ReviewService` exposes `.errored_guias` property; assert no `add_recovered_guia`, `apply_retry`, `apply_reprocess` method exists (out-of-scope guard).
4. `test_restore_from_sidecar_preserves_errored_guias` — call `ReviewService.restore_from_sidecar(declared=[], guias=[], rows=[], ctx=ctx_with_empty_sidecar, errored_guias=[eg1])`; assert `service.errored_guias == [eg1]` (sidecar edits replay leaves errored_guias intact because they originate from cache, not sidecar events).

**Implementation**:
- `backend/src/reconciliation/application/review_service.py` L110 `__init__`: add trailing `errored_guias: list[ErroredGuia] | None = None`; store `self._errored_guias: list[ErroredGuia] = list(errored_guias or [])`.
- `backend/src/reconciliation/application/review_service.py` L410 `restore_from_sidecar`: add matching trailing param; thread through to `cls(...)` call at L435 (pass `errored_guias=errored_guias`).
- Add `@property def errored_guias(self) -> list[ErroredGuia]: return list(self._errored_guias)`.
- Add `ErroredGuia` to lazy-import block at top of method if not already imported at module level (check if `ErroredGuia` is already imported; if not, lazy-import inside the method body or import from `domain.models` at module top since it is a domain type with no heavy deps).

**Invariant check**: Application layer only imports from `domain.models` (pure) — no concrete adapter. Trailing param preserves all 4-arg call sites for existing callers.

---

### T-3 — build_review_service hydrates errored_guias from extraction cache

**Spec**: REV-E02  
**Sequential after**: T-2 (ReviewService must accept errored_guias before container passes it)  
**Test file**: `backend/tests/unit/infrastructure/test_container.py` — add new test class `TestBuildReviewServiceErroredGuias`

**Failing tests to write first**:

1. `test_build_review_service_hydrates_errored_guias_from_cache` — mock `ctx.read_extraction_cache()` to return `{"declared": [], "guias": [], "rows": [], "errored_guias": [{"registro": "232", "guia_id": "T001-001", "source_pages": [5], "retry_attempted": False}]}`; call `build_review_service(ctx)`; assert `service.errored_guias` has 1 entry with `guia_id == "T001-001"`.
2. `test_build_review_service_absent_key_defaults_to_empty` — mock cache without `errored_guias` key; assert `service.errored_guias == []` with no exception.
3. `test_build_review_service_restart_preservation` — mock cache with 2 errored guías AND a non-empty sidecar (one field_edit); assert after `build_review_service` returns, `service.errored_guias` has 2 entries AND the sidecar edit is replayed on rows (i.e., RESTART-PRESERVATION: errored_guias survive alongside sidecar replay). This is the hard gate.

**Implementation**:
- `backend/src/reconciliation/infrastructure/container.py` lines 589–599:
  - After `rows = [...]` line, add: `errored_guias = [ErroredGuia.model_validate(e) for e in cache.get("errored_guias", [])]`
  - Add `ErroredGuia` to the lazy import block at L583.
  - Pass `errored_guias=errored_guias` to `ReviewService.restore_from_sidecar(...)`.

**Invariant check**: Lazy import inside function body (Adapter convention). Pure hydration — no IO beyond the existing `ctx.read_extraction_cache()` call. No change to rows/guias/declared hydration.

---

### T-4 — ReconciliationTableResponse.errored_guias + get_table mapping + integration gate

**Spec**: REV-E04  
**Sequential after**: T-3  
**Test files**:  
  - New: `backend/tests/unit/infrastructure/test_table_errored_guias.py` (unit — DTO + route mapping)  
  - Extend: `backend/tests/unit/infrastructure/test_api_routes.py` — add integration test class `TestGetTableErroredGuias`

**Failing tests to write first**:

Unit (DTO):
1. `test_reconciliation_table_response_errored_guias_field_defaults_empty` — instantiate `ReconciliationTableResponse(run_id="x", rows=[])`, assert `resp.errored_guias == []`.
2. `test_reconciliation_table_response_carries_errored_guias` — instantiate with `errored_guias=[ErroredGuiaResponse(registro="232", guia_id="T001-001", source_pages=[5])]`; assert round-trips correctly.
3. `test_existing_consumers_unaffected` — parse `{"run_id": "x", "rows": [], "unresolved_guias": []}` (no `errored_guias` key); assert no deserialization error, `errored_guias == []` (backward-compat).

Integration (route):
4. `test_get_table_returns_errored_guias` — build a mock `ReviewService` with 2 errored guías; inject into a test `RunRegistry`; call `GET /runs/{run_id}/table`; assert response JSON includes `errored_guias` with 2 entries matching `registro/guia_id/source_pages`.
5. `test_get_table_no_errored_guias_returns_empty_list` — `ReviewService.errored_guias == []`; assert `errored_guias: []` in response, HTTP 200.
6. `test_get_table_additive_rows_status_delta_qty_unchanged` — construct a `ReviewService` with 1 reconciliation row (status MATCH, summed_qty=100) AND 1 errored guía; call `/table`; assert row's status/qty/delta unchanged (additive-only gate).

**Implementation**:
- `backend/src/reconciliation/infrastructure/api/schemas.py` L303 `ReconciliationTableResponse`: add `errored_guias: list[ErroredGuiaResponse] = Field(default_factory=list, description="Guías that resolved to 0 material lines (REV-E04).")`. `ErroredGuiaResponse` is already at L137 — reuse as-is (note: `retry_attempted` is intentionally NOT added to `ErroredGuiaResponse` this slice per design decision §5).
- `backend/src/reconciliation/infrastructure/api/routes.py` L366 `get_table`: after the `unresolved_guias` list comprehension, add: `errored_guias = [ErroredGuiaResponse(registro=e.registro, guia_id=e.guia_id, source_pages=e.source_pages) for e in review_service.errored_guias]`; include `errored_guias=errored_guias` in the `ReconciliationTableResponse(...)` constructor.

**Invariant check**: `ErroredGuiaResponse` already existed in schemas.py (L137) — no new DTO class. Additive field with `default_factory=list` ensures zero backward-compat breakage for any client ignoring the field.

---

### T-5 — Frontend TS types: ErroredGuiaResponse interface + ReconciliationTableResponse field

**Spec**: REV-E05 (frontend types dependency)  
**Sequential after**: T-4 schema is fixed (spec-driven, not runtime-dependent — can proceed in parallel with T-4 since field names are fully specified)  
**Parallel with**: T-4 (independent layer — no runtime dependency)  
**Test file**: No dedicated test — type correctness is enforced by TypeScript compiler during T-6 vitest run. Optionally add a type-only test in `frontend/src/__tests__/api/client.test.ts` as a compile-time guard.

**Failing gate**: TypeScript compile error if `ErroredGuiaResponse` is referenced in T-6 before it is declared here.

**Implementation**:
- `frontend/src/api/types.ts` after `UnresolvedGuiaResponse` interface (~L168):
  ```ts
  /**
   * A guía that resolved to 0 material lines during the pipeline run (REV-E04/REV-E05).
   * Read-only this slice — no action buttons.
   * Mirrors backend ErroredGuiaResponse (schemas.py).
   */
  export interface ErroredGuiaResponse {
    registro: string | null
    guia_id: string
    source_pages: number[]
  }
  ```
- `frontend/src/api/types.ts` `ReconciliationTableResponse` interface (~L175): add `errored_guias: ErroredGuiaResponse[]` field.

**Invariant check**: Additive field — existing consumers destructuring `rows` and `unresolved_guias` are unaffected. No action fields (retry_attempted intentionally omitted from DTO this slice).

---

### T-6 — ErroredGuiasPanel.vue: read-only collapsible panel + vitest

**Spec**: REV-E05  
**Sequential after**: T-5  
**Test file**: `frontend/src/__tests__/features/ErroredGuiasPanel.test.ts` (new file, mirror of `UnresolvedGuiasPanel.test.ts`)

**Failing tests to write first** (vitest RED gate):

1. `renders nothing when erroredGuias is empty` — mount `ErroredGuiasPanel` with `erroredGuias: []`; assert `.errored-panel` does not exist in DOM (`v-if="erroredGuias.length > 0"`).
2. `renders per-Registro entries when erroredGuias has items` — mount with 2 entries (`registro: "227", pages: [5,6]` and `registro: "230", pages: [11]`); assert panel section exists; assert both `guia_id` values are visible in the DOM.
3. `does not render REINTENTAR or Reprocesar buttons` — mount with 1 entry; assert no `button` with text matching `REINTENTAR` or `Reprocesar` exists (out-of-scope guard for PR #2/#3).
4. `panel is collapsible — header toggle shows/hides body` — click header toggle; assert panel body visibility changes.
5. `displays source pages in entry` — mount with entry `source_pages: [3, 4]`; assert "3" and "4" are present in rendered output.

**Implementation**: Create `frontend/src/features/review/ErroredGuiasPanel.vue`.
- Mirror `UnresolvedGuiasPanel.vue` structure (collapsible `v-if="erroredGuias.length > 0"`, header with count badge, body list).
- Props: `erroredGuias: ErroredGuiaResponse[]`.
- Each item shows: `guia_id`, `registro` (or "Sin registro" if null), `source_pages` formatted as "Págs. X, Y".
- NO buttons of any kind (no assign, no retry, no reprocess). Read-only display.
- BEM CSS class prefix: `errored-panel` (distinguishes from `unresolved-panel`).
- `isOpen = ref(true)` default (matches existing panel behavior).

**Invariant check**: No emit events. No mutation of erroredGuias. Component is purely presentational.

---

### T-7 — ReviewPage.vue: mount ErroredGuiasPanel above grid

**Spec**: REV-E05  
**Sequential after**: T-6  
**Test file**: No new test file. Existing `frontend/src/__tests__/features/smoke_rev2.test.ts` or `smoke.test.ts` — verify no import error and component mounts. Alternatively, add a mounting smoke test in `frontend/src/__tests__/features/ReviewPage.test.ts` if it exists; if not, the vitest compile pass from T-6 is sufficient for this slice.

**Implementation**:
- `frontend/src/features/review/ReviewPage.vue`:
  1. Add import: `import ErroredGuiasPanel from './ErroredGuiasPanel.vue'`
  2. Add to `<script setup>` imports from `@/api/types`: `ErroredGuiaResponse` (already imported via `types.ts` changes in T-5).
  3. Add computed: `const erroredGuias = computed<ErroredGuiaResponse[]>(() => tableQuery.data.value?.errored_guias ?? [])`
  4. Mount in `<template>` between `<UnresolvedGuiasPanel .../>` and `<ReviewGrid .../>` (line ~45-47):
     ```html
     <ErroredGuiasPanel
       v-if="isReady && erroredGuias.length > 0"
       :errored-guias="erroredGuias"
     />
     ```

**Invariant check**: No `@assign` or action event wired. Read-only prop binding only. `v-if="isReady"` guard consistent with existing panel mount pattern.

---

### T-8 — SA-5 Playwright MCP runtime validation (MANDATORY gate)

**Spec**: REV-E05 (SA-5 rule — visible-UX feature)  
**Sequential after**: T-7 (all backend + frontend tasks complete)  
**Blocking**: YES — this task MUST complete before marking the slice "done"

**Validation steps** (must all pass):
1. Upload the real PDF through the UI (or use an existing completed run with known errored guías).
2. Navigate to the review page for a run that has at least 1 errored guía in the extraction cache.
3. Assert `ErroredGuiasPanel` is visible above the grid with correct per-Registro entries and page references.
4. Assert zero JS console errors.
5. Assert no REINTENTAR/Reprocesar buttons rendered.
6. Assert existing reconciliation rows (MATCH/MISMATCH status, qty, delta) are unchanged.

**If no errored guías exist in the current test run**: Inject a synthetic errored guía into the extraction cache JSON for an existing run (manual edit to `errored_guias: [...]`), restart the backend, and re-navigate to the review page. This is a valid validation path for this read-only slice.

---

## Execution Order Summary

| Step | Task | Can parallelize |
|------|------|----------------|
| 1 | T-1 domain model | Start here |
| 2 | T-2 ReviewService | After T-1 |
| 3 | T-3 container | After T-2 |
| 4 | T-4 API/DTO + integration | After T-3; T-5 can start in parallel |
| 4b | T-5 TS types | Parallel with T-4 |
| 5 | T-6 Panel.vue + vitest | After T-5 |
| 6 | T-7 ReviewPage mount | After T-6 |
| 7 | T-8 Playwright gate | After T-7 (BLOCKING) |

## Test Commands

```bash
# Backend (targeted path — monolithic pytest hangs on paddle import)
cd backend && uv run pytest tests/unit/domain/ tests/unit/application/ tests/unit/infrastructure/ -q

# Frontend
cd frontend && npm test
```

## Files Expected to Change

| File | Action | Task |
|------|--------|------|
| `backend/src/reconciliation/domain/models.py` | Modify — add `retry_attempted: bool = False` | T-1 |
| `backend/src/reconciliation/application/review_service.py` | Modify — trailing param + property | T-2 |
| `backend/src/reconciliation/infrastructure/container.py` | Modify — hydrate errored_guias | T-3 |
| `backend/src/reconciliation/infrastructure/api/schemas.py` | Modify — add field to ReconciliationTableResponse | T-4 |
| `backend/src/reconciliation/infrastructure/api/routes.py` | Modify — map errored_guias in get_table | T-4 |
| `backend/tests/unit/domain/test_errored_guia_retry_attempted.py` (or test_models.py) | New/extend | T-1 |
| `backend/tests/unit/application/test_review_service.py` | Extend | T-2 |
| `backend/tests/unit/infrastructure/test_container.py` | Extend | T-3 |
| `backend/tests/unit/infrastructure/test_table_errored_guias.py` | New | T-4 |
| `frontend/src/api/types.ts` | Modify — add interface + field | T-5 |
| `frontend/src/features/review/ErroredGuiasPanel.vue` | Create | T-6 |
| `frontend/src/features/review/ReviewPage.vue` | Modify — import + computed + mount | T-7 |
| `frontend/src/__tests__/features/ErroredGuiasPanel.test.ts` | Create | T-6 |

Total: 8 production files + 4 test files = 12 files. ~230 LOC.

## Out of Scope (explicit boundary)

- ReprocessService, `read_material_table`, `apply_retry`, `apply_reprocess`, `add_recovered_guia`
- POST `/errored-guias/{guia_id}/retry` or `/reprocess` endpoints
- REINTENTAR / Reprocesar action buttons in frontend
- `retry_attempted` field on `ErroredGuiaResponse` DTO (reserved for PR #2)
- Transient vs systematic classification
- Any mutation of ErroredGuia state
