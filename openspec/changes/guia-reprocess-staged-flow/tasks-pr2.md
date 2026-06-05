# Tasks — guia-reprocess-staged-flow (PR #2 REINTENTAR)

## Meta
- change: guia-reprocess-staged-flow
- pr_slice: 2 of N (stacked-to-main onto PR#1)
- delivery_strategy: stacked-to-main
- artifact_store: hybrid
- produced: 2026-06-05
- spec_ref: sdd/guia-reprocess-staged-flow/spec-pr2 (#2959) + openspec/changes/guia-reprocess-staged-flow/specs/reprocess/spec-pr2.md
- design_ref: sdd/guia-reprocess-staged-flow/design-pr2 (#2960) + openspec/changes/guia-reprocess-staged-flow/design-pr2.md

---

## Review Workload Forecast

| Metric | Estimate |
|--------|----------|
| New files | 1 (reprocess_service.py) |
| Modified files | 7 backend + 3 frontend = 10 total |
| Estimated backend LOC changed | ~320 (reprocess_service ~120, review_service +50, ports.py +8, container.py +35, routes.py +60, schemas.py +30, pipeline.py -3) |
| Estimated frontend LOC changed | ~120 (ErroredGuiasPanel.vue +70, client.ts +20, types.ts +30) |
| **Total estimated delta** | **~440 LOC** |
| 400-line budget risk | **Medium** — just over budget but all in one coherent vertical slice |
| Chained PRs recommended | **No** — PR#2 is already one of the planned chain; backend+frontend split would leave the panel non-functional and break SA-5 gate |
| Decision needed before apply | **No** — stacked-to-main already resolved; backend+frontend must ship together for SA-5 Playwright gate to be runnable |

---

## Hard Invariants (carry into every work-unit)

- **Domain pure**: zero SDK/framework/IO import under `domain/` — auto-reject if violated.
- **Application ports-only**: `application/reprocess_service.py` and `application/pipeline.py` import ZERO concrete adapters; depend only on Protocols + config. A concrete-adapter import in `application/` = auto-reject.
- **Lazy heavy deps**: adapters import `fitz`/`pyzbar`/`zxing-cpp`/`requests` INSIDE methods, never at module top.
- **`fecha` is NEVER a grouping axis**: key is `(registro, material_canonical, unidad)` — invariant unchanged.
- **Units never converted**: `_normalize_sunat_unit` maps to domain set, sums are unit-independent.
- **Reconciliation is the validation gate**: recovered lines carry `requires_review=True`, NEVER auto-accepted. No auto-correction.
- **`add_recovered_guia` is the sole mutation hook**: ReprocessService MUST call only this method; it never mutates `_guias`/`_rows`/`_errored_guias` directly.
- **Input PDF read-only**: `render_page` is a read-only fitz open; no write path into the source PDF.
- **SUNAT gate**: endpoints return 503 when `config.sunat.enabled=False` (no date source for recovery).

---

## Dependency Graph

```
T1 (ports) ──► T2 (helper) ──► T3 (add_recovered_guia) ──► T4 (sidecar replay)
                                                                   │
                                        T5 (ReprocessService) ─────┘
                                              │
                           T6 (container) ────┘
                                │
                     T7 (API) ──┘
                                │
                     T8 (Frontend) ──► T9 (Playwright SA-5 gate)
```

T1 and T2 can start in parallel once T0 (branch setup) is done.
T3 depends on T2 (uses helper). T4 depends on T3 (extends sidecar replay).
T5 depends on T1 + T2 + T3 (uses port + helper + add_recovered_guia).
T6 depends on T5. T7 depends on T6 (endpoint uses ReprocessService from container).
T8 depends on T7 (types + endpoints must exist). T9 depends on T8 (runtime gate).

---

## Work-Unit Checklist

### T1 — Promote `decode_hashqr_url` to `IdentityExtractionPort`
**Spec**: REV-R01 | **Sequential after**: — (can start immediately)

**Failing test first**:
```
# test: protocol contract — IdentityExtractionPort.decode_hashqr_url required
# assert: isinstance(QrBarcodeExtractionAdapter(), IdentityExtractionPort)
# assert: hasattr(IdentityExtractionPort, 'decode_hashqr_url') (structural check)
# assert: pipeline.py:510 hasattr guard is GONE (grep test)
```

**Implementation steps**:
1. In `domain/ports.py`, add `decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None` to `IdentityExtractionPort` (after `decode_identity`).
2. In `application/pipeline.py` line ~510, remove the `hasattr(self._identity, "decode_hashqr_url")` guard; call `self._identity.decode_hashqr_url(...)` directly.
3. Verify `QrBarcodeExtractionAdapter` at `adapters/identity/qr_barcode.py:259` already satisfies the new signature (no implementation change needed — just confirm it).

**Files**: `domain/ports.py`, `application/pipeline.py`
**Test file**: `tests/unit/test_ports_contract.py` (new or extend existing)

---

### T2 — Shared `_build_recovered_guia` normalization helper
**Spec**: REV-R03 (normalization parity crux) | **Sequential after**: T1

**Failing test first**:
```
# test: helper builds MaterialLine list with identical (registro, material_canonical, unidad)
#       as a pipeline-produced line for the same SUNAT descripcion+unidad input
# assert: key_resolver.resolve() called with same args as pipeline _norm_line
# assert: line.requires_review == True (always, regardless of key.requires_review)
# assert: line.confidence == 1.0
# assert: line.source_page == first errored_page
```

**Implementation steps**:
Create module-level (or class-static) helper `_build_recovered_guia_lines(official: OfficialGre, errored_guia: ErroredGuia, key_resolver: MaterialKeyResolver, config: AppConfig) -> list[MaterialLine]` in `application/reprocess_service.py` (not yet the full service — just the helper + RetryResult dataclass and the file scaffold). The helper must:
- Mirror pipeline `_stage_sunat_fetch` MaterialLine construction (pipeline.py L1216-1226): `description_raw=descripcion`, `description_canonical=descripcion` (placeholder), `unidad=_normalize_sunat_unit(item.unidad)` filtered to domain set, `cantidad=item.cantidad`, `confidence=1.0`, `source_page=errored_guia.source_pages[0]`, `requires_review=True`.
- Then call `key_resolver.resolve(description_raw, unidad)` → set `description_canonical=key.group_token`, `match_method=key.method`, `requires_review=True` (unconditional per REV-R03, overrides key.requires_review). Mirror pipeline `_norm_line` (pipeline.py L1553-1558).
- Filter lines where `_normalize_sunat_unit` returns a value not in the domain set (same skip-with-warning as pipeline).
- Return empty list if no valid lines.

Also define `RetryResult` dataclass in the same file:
```python
@dataclass
class RetryResult:
    guia_id: str
    recovered: bool
    rows: list[ReconciliationRow]
    errored_guias: list[ErroredGuia]
    reason: str | None  # "no_hashqr_url" | "sunat_empty" | None
    retry_attempted: bool
```

**Files**: `application/reprocess_service.py` (new), `domain/material_key_resolver.py` (read-only reference)
**Test file**: `tests/unit/test_reprocess_helper.py` (new)

---

### T3 — `ReviewService.add_recovered_guia`
**Spec**: REV-R05 | **Sequential after**: T2

**Failing tests first**:
```
# test_add_recovered_guia_reconciles_correctly:
#   given declared=1 Registro with 1 line, guias=[], errored_guias=[ErroredGuia matching that registro]
#   when add_recovered_guia(recovered_guia) called
#   then rows contain the recovered guía's contribution, status may now be MATCH
#   and errored_guias no longer contains the guia_id

# test_add_recovered_guia_additive_isolation:
#   other registro rows are NOT changed by the mutation

# test_add_recovered_guia_idempotent:
#   calling add_recovered_guia twice with the same guia_id → second call returns same rows,
#   no new sidecar event, no duplicate in _guias

# test_add_recovered_guia_rejects_non_review_lines:
#   guia with any line.requires_review=False → raises ValueError
```

**Implementation steps**:
Add `add_recovered_guia(self, guia: GuiaDeRemision) -> list[ReconciliationRow]` to `ReviewService`:
1. Guard: all lines must have `requires_review=True`; raise `ValueError` if any line has `requires_review=False`.
2. Idempotency: if `guia.guia_id` already in `self._guias` (guia_id match), return current `self._rows` (no event, no mutate).
3. Append `guia` to `self._guias`.
4. Remove any `ErroredGuia` with matching `guia_id` from `self._errored_guias`.
5. Re-reconcile: `self._rows = self._reconciler.reconcile(self._declared, self._guias, delivery_dates=self._delivery_dates())`.
6. Emit `EditEvent(kind="recovered_guia", target={"guia_id": guia.guia_id}, field=None, old_value=None, new_value=guia.model_dump(mode="json"))`.
7. Append event to `self._audit_trail`. Call `self._persist()`.
8. Return `list(self._rows)`.

**Files**: `application/review_service.py`
**Test file**: `tests/unit/test_review_service_recovery.py` (new)

---

### T4 — `recovered_guia` sidecar replay in `restore_from_sidecar`
**Spec**: REV-R06 | **Sequential after**: T3

**Failing test first**:
```
# test_restart_replay_recovered_guia:
#   build ReviewService with errored_guias=[ErroredGuia(guia_id="X")]
#   call add_recovered_guia → sidecar written with recovered_guia event
#   call ReviewService.restore_from_sidecar with same initial state + same ctx
#   assert: resulting service._guias contains the recovered guía
#   assert: resulting service._errored_guias does NOT contain guia_id="X"
#   assert: no external fetch/decode called during replay (FAKE ctx)
```

**Implementation steps**:
In `ReviewService.restore_from_sidecar`, add a `elif kind == "recovered_guia":` branch AFTER the existing `reassignment` branch:
```python
elif kind == "recovered_guia":
    raw = edit.get("new_value", {})
    if isinstance(raw, dict):
        try:
            from reconciliation.domain.models import GuiaDeRemision  # noqa: PLC0415
            guia = GuiaDeRemision.model_validate(raw)
            service.add_recovered_guia(guia)
        except Exception:
            pass  # tolerate replay errors (mirrors existing pattern)
```

Note: `restore_from_sidecar` hydrates `_errored_guias` from the extraction cache FIRST (constructor argument), then the replay moves any recovered ones back to `_guias` (same as apply_reassignment removes from errored during replay). The order is correct because add_recovered_guia's idempotency guard prevents double-insertion.

**Files**: `application/review_service.py`
**Test file**: `tests/unit/test_review_service_recovery.py` (extend T3 test file)

---

### T5 — `ReprocessService.apply_retry`
**Spec**: REV-R02, REV-R03, REV-R04, REV-R07 | **Sequential after**: T1 + T2 + T3 (all required)

**Failing tests first**:
```
# test_apply_retry_transient_success:
#   FAKE doc_source.render_page → bytes
#   FAKE identity.decode_identity → GuiaIdentity; .decode_hashqr_url → "https://sunat/..."
#   FAKE sunat.fetch → OfficialGre(lines=[...], fecha_entrega=date(...))
#   call apply_retry(guia_id)
#   assert: result.recovered == True
#   assert: result.retry_attempted == True
#   assert: len(result.errored_guias) == original - 1  (guia removed)
#   assert: result.rows contains the guía's contribution
#   assert: all recovered lines have requires_review=True
#   assert: guía key == key produced by pipeline _norm_line for same input

# test_apply_retry_no_hashqr_url:
#   FAKE identity.decode_hashqr_url → None
#   assert: result.recovered == False
#   assert: result.reason == "no_hashqr_url"
#   assert: result.retry_attempted == True
#   assert: errored_guias unchanged (no partial guía added)

# test_apply_retry_sunat_empty:
#   FAKE sunat.fetch → OfficialGre(lines=[], fecha_entrega=...)
#   assert: result.recovered == False
#   assert: result.reason == "sunat_empty"
#   assert: errored_guias unchanged

# test_apply_retry_unknown_guia_id:
#   apply_retry with guia_id not in errored_guias
#   assert: raises ValueError (or returns recovered=False, reason="not_found")
```

**Implementation steps**:
In `application/reprocess_service.py`, implement `ReprocessService`:
```python
class ReprocessService:
    def __init__(
        self,
        doc_source: DocumentSourcePort,
        identity: IdentityExtractionPort,
        sunat: SunatGreFetchPort,
        key_resolver: MaterialKeyResolver,
        review_service: ReviewService,
        config: AppConfig,
    ) -> None: ...

    def apply_retry(self, guia_id: str) -> RetryResult:
        # 1. Find ErroredGuia by guia_id in review_service.errored_guias → 404 if not found
        # 2. Re-render first source_page at DPI=300: doc_source.render_page(page_idx, dpi=300)
        # 3. Decode identity: identity.decode_identity(image, page_idx)
        # 4. Decode hashqr_url: identity.decode_hashqr_url(image, page_idx)
        #    → if None: return RetryResult(recovered=False, reason="no_hashqr_url", retry_attempted=True, ...)
        # 5. SUNAT fetch: sunat.fetch(hashqr_url)
        #    → if None or no lines after filtering: return RetryResult(recovered=False, reason="sunat_empty", retry_attempted=True, ...)
        # 6. Build lines: _build_recovered_guia_lines(official, errored_guia, key_resolver, config)
        #    → if empty after filtering: return RetryResult(recovered=False, reason="sunat_empty", ...)
        # 7. Build GuiaDeRemision:
        #    - guia_id from GuiaIdentity.guia_id (or errored_guia.guia_id as fallback)
        #    - registro = errored_guia.registro
        #    - fecha = apply_delivery_floor(None, official.fecha_entrega)[0]  (R9b: date=fecha_entrega when vision=None)
        #    - fecha_entrega = official.fecha_entrega (persist for floor/ceiling survival)
        #    - source_pages = errored_guia.source_pages
        #    - identity_source = "qr" (came from QR decode)
        #    - lines from step 6
        # 8. Call review_service.add_recovered_guia(guia) → returns rows
        # 9. Return RetryResult(recovered=True, retry_attempted=True, rows=rows,
        #                       errored_guias=review_service.errored_guias, reason=None)

    def apply_retry_registro(self, registro: str) -> list[RetryResult]:
        # Iterate errored guías for registro; call apply_retry per guía
        # Individual failures do not abort remaining (REV-R07)
        ...
```

**Note**: No vision call. Date = `apply_delivery_floor(None, official.fecha_entrega)` which returns `(fecha_entrega, True)` when first arg is None (R9b Rule-2 floor — SUNAT-authoritative date mode). Import `apply_delivery_floor` from `domain/date_floor.py` (domain import is allowed from application layer via port/domain).

**Files**: `application/reprocess_service.py`
**Test file**: `tests/unit/test_reprocess_service.py` (new)

---

### T6 — `build_reprocess_service` in `container.py`
**Spec**: REV-R02 (wiring) | **Sequential after**: T5

**Failing test first**:
```
# test_build_reprocess_service_wires_correctly:
#   build with a real AppConfig (sunat.enabled=True) and a fake ctx
#   assert: returns ReprocessService instance (isinstance check)
#   assert: ReprocessService has doc_source, identity, sunat, key_resolver attrs (structural)

# test_build_reprocess_service_sunat_disabled:
#   build with config.sunat.enabled=False
#   assert: returns None (or raises — design decision below)
```

**Design decision (SA-2)**: When SUNAT is disabled, `build_reprocess_service` returns `None` (not a ReprocessService). The API route checks for `None` and returns 503 without calling the service. This matches the `sunat_adapter = None` pattern already in `build_pipeline`.

**Implementation steps**:
Add `build_reprocess_service(ctx: RunContext, config: AppConfig, review_service: ReviewService) -> ReprocessService | None` to `container.py`:
- If `not config.sunat.enabled`: log + return `None`.
- Otherwise lazy-import `PdfStructureAdapter`, `QrBarcodeExtractionAdapter`, `SunatDescargaqrAdapter`, `MaterialKeyResolver`, `MaterialKeyNormalizer`, `build_inference_adapter` (mirror `build_pipeline` pattern).
- Construct and return `ReprocessService(doc_source, identity, sunat, key_resolver, review_service, config)`.
- PDF path comes from `ctx.pdf_path`.

**Files**: `infrastructure/container.py`
**Test file**: `tests/unit/test_container_reprocess.py` (new)

---

### T7 — API endpoints: sync single-retry + background per-Registro retry
**Spec**: REV-R08 | **Sequential after**: T6

**Failing tests first**:
```
# test_retry_guia_success:
#   POST /api/v1/runs/{run_id}/errored-guias/{guia_id}/retry
#   mock review_service.errored_guias has guia_id
#   mock reprocess_service.apply_retry → RetryResult(recovered=True, rows=..., errored_guias=[])
#   assert: 200, RetryGuiaResponse{run_id, recovered=True, rows=..., errored_guias=[]}

# test_retry_guia_not_found:
#   guia_id not in errored_guias → 404

# test_retry_guia_sunat_disabled:
#   reprocess_service is None in registry → 503

# test_retry_guia_failure_reason:
#   apply_retry → RetryResult(recovered=False, reason="no_hashqr_url")
#   assert: 200, RetryGuiaResponse{recovered=False, reason="no_hashqr_url"}

# test_retry_registro_background:
#   POST /api/v1/runs/{run_id}/registros/{registro}/retry
#   assert: 202, RetryBatchResponse{run_id, task="queued", count=N}
```

**Implementation steps**:
1. Add to `infrastructure/api/schemas.py`:
```python
class RetryGuiaResponse(BaseModel):
    run_id: str
    recovered: bool
    reason: str | None = None
    rows: list[ReconciliationRowResponse]
    errored_guias: list[ErroredGuiaResponse]

class RetryBatchResponse(BaseModel):
    run_id: str
    task: Literal["queued"]
    count: int  # number of errored guías for the registro
```
Also extend `ErroredGuiaResponse` to include `retry_attempted: bool = False` (read from `ErroredGuia.retry_attempted` — see T3 note below).

2. Add to `infrastructure/api/routes.py`:
```python
@router.post("/runs/{run_id}/errored-guias/{guia_id}/retry", response_model=RetryGuiaResponse)
def retry_guia(run_id: str, guia_id: str, registry: RunRegistry, config: AppConfigDep) -> RetryGuiaResponse:
    entry = _require_run(registry, run_id)
    review_service = _require_review_service(entry, run_id)
    reprocess_service = entry.get("reprocess_service")
    if reprocess_service is None:
        raise HTTPException(status_code=503, detail="SUNAT fetch is disabled; REINTENTAR is unavailable.")
    # Check guia_id exists in errored_guias (404 if not found)
    if not any(e.guia_id == guia_id for e in review_service.errored_guias):
        raise HTTPException(status_code=404, detail=f"Errored guía '{guia_id}' not found in run '{run_id}'.")
    result = reprocess_service.apply_retry(guia_id)
    rows_resp = [_row_to_response(r) for r in result.rows]
    errored_resp = [ErroredGuiaResponse(...) for e in result.errored_guias]
    return RetryGuiaResponse(run_id=run_id, recovered=result.recovered, reason=result.reason,
                             rows=rows_resp, errored_guias=errored_resp)

@router.post("/runs/{run_id}/registros/{registro}/retry", response_model=RetryBatchResponse, status_code=202)
def retry_registro(run_id: str, registro: str, background_tasks: BackgroundTasks,
                   registry: RunRegistry, config: AppConfigDep) -> RetryBatchResponse:
    entry = _require_run(registry, run_id)
    review_service = _require_review_service(entry, run_id)
    reprocess_service = entry.get("reprocess_service")
    if reprocess_service is None:
        raise HTTPException(status_code=503, detail="SUNAT fetch is disabled.")
    count = sum(1 for e in review_service.errored_guias if e.registro == registro)
    background_tasks.add_task(reprocess_service.apply_retry_registro, registro)
    return RetryBatchResponse(run_id=run_id, task="queued", count=count)
```
3. Wire `reprocess_service` into the registry entry in the pipeline background runner (after `review_service` is built): call `build_reprocess_service(ctx, config, review_service)` and store as `registry[run_id]["reprocess_service"]`.

**Note on `ErroredGuia.retry_attempted`**: The `ErroredGuia` domain model needs a `retry_attempted: bool = False` field so the API and frontend can gate the REINTENTAR button. Add this field to `domain/models.py::ErroredGuia` as part of this task. `ReprocessService.apply_retry` sets it via `errored_guia.model_copy(update={"retry_attempted": True})` BEFORE deciding success/failure — guaranteed per REV-R03/REV-R04.

**Files**: `infrastructure/api/schemas.py`, `infrastructure/api/routes.py`, `infrastructure/container.py` (wire reprocess_service), `domain/models.py` (ErroredGuia.retry_attempted field)
**Test file**: `tests/unit/test_routes_retry.py` (new, using TestClient)

---

### T8 — Frontend: REINTENTAR button + per-Registro retry
**Spec**: REV-R09 | **Sequential after**: T7 (endpoints + types must exist)

**Failing tests first (vitest)**:
```
# test_reintentar_button_renders_when_not_attempted:
#   mount ErroredGuiasPanel with erroredGuias=[{...retry_attempted: false}]
#   assert: REINTENTAR button exists and is NOT disabled

# test_reintentar_button_disabled_after_attempted:
#   mount with retry_attempted=true
#   assert: button is disabled + shows secondary label (e.g. "SUNAT no disponible" or "Reintentado")

# test_reintentar_calls_retryGuia_mutation:
#   mock useMutation; click REINTENTAR
#   assert: mutate called with { runId, guia_id }

# test_reintentar_loading_state:
#   while retryingId.value == guia_id → button shows loading state / disabled

# test_reintentar_success_invalidates_table:
#   on mutation success → queryClient.invalidateQueries called for GET /table key

# test_registro_retry_button_triggers_batch:
#   per-Registro group header button → calls retryRegistro mutation
```

**Implementation steps**:
1. **`frontend/src/api/types.ts`**: Add:
```typescript
export interface RetryGuiaResponse {
  run_id: string
  recovered: boolean
  reason: string | null
  rows: ReconciliationRowResponse[]
  errored_guias: ErroredGuiaResponse[]
}

export interface RetryBatchResponse {
  run_id: string
  task: 'queued'
  count: number
}
```
Extend `ErroredGuiaResponse` to include `retry_attempted: boolean` (default `false`).

2. **`frontend/src/api/client.ts`**: Add:
```typescript
retryGuia(runId: string, guiaId: string): Promise<RetryGuiaResponse>
retryRegistro(runId: string, registro: string): Promise<RetryBatchResponse>
```

3. **`frontend/src/features/review/ErroredGuiasPanel.vue`**: Refactor from read-only to interactive:
   - Accept `runId: string` prop (in addition to existing `erroredGuias`).
   - Add `retryingId = ref<string | null>(null)` for per-item loading state.
   - Add TanStack `useMutation` for `retryGuia`; `onSuccess` calls `queryClient.invalidateQueries({ queryKey: ['table', runId] })`.
   - Per-item REINTENTAR button: `disabled` when `entry.retry_attempted || retryingId === entry.guia_id`; shows spinner when loading.
   - Group items by `registro`; add per-Registro REINTENTAR button that calls `retryRegistro` mutation (202 → show "batch queued" toast).
   - Failure path: disable button + show `reason` as a human-readable label below the guia_id.

**Files**: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/features/review/ErroredGuiasPanel.vue`
**Test file**: `frontend/src/features/review/__tests__/ErroredGuiasPanel.spec.ts` (new or extend existing)

---

### T9 — SA-5 Playwright runtime gate (MANDATORY)
**Spec**: REV-R09 + CLAUDE.md SA-5 | **Sequential after**: T8 (running app required)

**Gate criteria (all must pass)**:
1. Upload the real PDF → run completes → ReviewPage renders.
2. If any errored guías exist in the panel: click REINTENTAR on one.
3. Assert: the guía leaves ErroredGuiasPanel (no longer in list).
4. Assert: the recovered guía's row appears in the reconciliation table (or existing row status changes).
5. Assert: 0 browser console errors throughout.
6. Fallback (if no live errored guía): assert the REINTENTAR button renders in disabled state when `retry_attempted=true` (screenshot sufficient for SA-5 compliance without a live SUNAT call).

**Note**: This is a validation task, not an implementation task. It closes the PR#2 slice. Do NOT mark T8 "done" without T9 passing.

---

## Parallelism Summary

| Phase | Tasks | Dependency |
|-------|-------|------------|
| Wave 1 | T1 (ports), T2 (helper scaffold) | None — parallel |
| Wave 2 | T3 (add_recovered_guia) | T2 done |
| Wave 3 | T4 (sidecar replay) | T3 done |
| Wave 4 | T5 (ReprocessService) | T1 + T2 + T3 done |
| Wave 5 | T6 (container wiring) | T5 done |
| Wave 6 | T7 (API) | T6 done |
| Wave 7 | T8 (Frontend) | T7 done |
| Wave 8 | T9 (Playwright gate) | T8 + running app |

No task can safely run ahead of its wave — each wave introduces an API contract consumed by the next.

---

## Risks and Bottlenecks

1. **Normalization parity (T2/T5 crux)**: if `_build_recovered_guia_lines` diverges from `_norm_line` (pipeline.py L1553), the recovered guía will produce a different canonical key, causing a DECLARED_MISSING ghost row instead of updating the MISMATCH. This is the highest-risk correctness bug. Mitigated by the T2 parity test that compares keys directly.

2. **`ErroredGuia.retry_attempted` field (T7)**: adding this field to `domain/models.py` propagates to the extraction cache schema. Old cache files (pre-PR#2) will not have this field; Pydantic's `default=False` handles backward-compat gracefully. No migration needed.

3. **Sidecar replay ordering (T4)**: `restore_from_sidecar` hydrates `_errored_guias` from the extraction cache first (constructor), then replays `recovered_guia` events which remove guías from that list. If the extraction cache is not refreshed to persist the `retry_attempted=True` flag after a retry, a restart re-adds the guía to `_errored_guias` (with `retry_attempted=False`) and then the sidecar replay removes it again (correct behavior). However, the `retry_attempted` badge would reset to `false` on the freshly-hydrated ErroredGuia. **Mitigation**: persist `retry_attempted=True` back to the extraction cache in `add_recovered_guia` OR set it during sidecar replay. SA-2: flag to orchestrator — implementor should choose the simpler path (replay sets it) and note it in the commit.

4. **Background per-Registro retry (T7/T8)**: the batch endpoint returns 202 immediately; the frontend must re-poll GET /table to see results. There is no push/websocket notification in this PR. The client polls via TanStack's `refetchInterval` already in place. SA-2: if the review team considers this UX insufficient, a follow-up PR can add a status subscription — out of scope here.

5. **T9 Playwright gate with no live errored guía**: if the real PDF used in the e2e test produces no errored guías (all guías decode cleanly), the gate cannot exercise the happy path. Fallback: use a synthetic test run with a known-errored guía (mocked SUNAT). SA-5 requires runtime validation — do not skip even if the fallback is needed.
