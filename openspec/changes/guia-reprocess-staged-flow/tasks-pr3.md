# Tasks — guia-reprocess-staged-flow (PR #3 Reprocesar con IA)

## Meta
- change: guia-reprocess-staged-flow
- pr_slice: 3 of N (stacked-to-main onto PR#2)
- delivery_strategy: stacked-to-main / size:exception (backend+frontend must ship together for SA-5 gate)
- artifact_store: hybrid
- produced: 2026-06-05
- spec_ref: sdd/guia-reprocess-staged-flow/spec-pr3 (#2978) + openspec/changes/guia-reprocess-staged-flow/specs/reprocess/spec-pr3.md
- design_ref: sdd/guia-reprocess-staged-flow/design-pr3 (#2976) + openspec/changes/guia-reprocess-staged-flow/design-pr3.md
- test_runner_backend: `cd backend && uv run pytest`
- test_runner_frontend: `cd frontend && npm test`

---

## Review Workload Forecast

| Metric | Estimate |
|--------|----------|
| New files | 0 (all modifications to existing files) |
| Modified files | 8 backend + 3 frontend = 11 total |
| Estimated backend LOC changed | ~320 (ports.py +8; anthropic_vision.py +60; openai_compatible.py +60; null_vision.py +5; reprocess_service.py +90; config.py +8; container.py +30; routes.py +45; schemas.py +15) |
| Estimated frontend LOC changed | ~110 (ErroredGuiasPanel.vue +75; client.ts +15; types.ts +20) |
| **Total estimated delta** | **~430 LOC** |
| 400-line budget risk | **Medium** — 7% over budget; all within one tightly-coupled vertical slice (vision port → adapters → service → container → endpoint → frontend); splitting would break SA-5 |
| Chained PRs recommended | **No** |
| Decision needed before apply | **No** |

**Recommended delivery**: Single PR with `size:exception` — the vision path (adapters → service → endpoint) and the frontend button are a semantically inseparable vertical slice. Splitting backend and frontend would leave the frontend without a reprocess endpoint to target and make SA-5 impossible until both halves merge. The 30-LOC overage does not warrant the coordination overhead of a stacked split.

---

## Hard Invariants (carry into every work-unit — auto-reject if violated)

- **Domain pure**: zero SDK/framework/IO import under `domain/` — `read_material_table` is a Protocol method in `domain/ports.py`, no concrete logic.
- **Ports at the boundary**: `application/reprocess_service.py` imports ZERO concrete adapters at module level. `application/pipeline.py` is NOT modified for the reprocess path.
- **Lazy heavy deps**: `anthropic` and `openai` imported INSIDE `read_material_table` method bodies only (same pattern as existing `read_handwritten_date`).
- **Vision provider-agnostic**: all three adapters satisfy the protocol; selection via `provider:` config, never a hard-coded vendor import in application or domain.
- **`fecha` is NEVER a grouping axis**: reconciliation key is `(registro, material_canonical, unidad)` — `fecha=None` on a systematic guía is correct and expected.
- **Units never converted**: non-domain units skipped in `_build_recovered_guia_lines_from_vision`; KG/TN/RD/Rollo summed independently.
- **`requires_review` always True on vision-recovered lines**: every `MaterialLine` produced by the vision path MUST have `requires_review=True`, set by the SERVICE (not the adapter or the model default).
- **No new vision date call**: `read_material_table` reads ONLY the material table. `read_handwritten_date` is NOT called in `apply_reprocess`.
- **`add_recovered_guia` is the sole mutation hook**: `apply_reprocess` MUST pass the recovered guía through `ReviewService.add_recovered_guia` only — no direct mutation of `_guias`/`_rows`/`_errored_guias`.
- **Input PDF read-only**: `ctx.pdf_path` opened read-only; no write, truncate, or rename.
- **Full-page render; no static bbox crop**: downscaled full-page image sent to vision; hard-coded `table_crop` bbox is PROHIBITED (silent row-loss invariant).
- **REINTENTAR gate unchanged**: `apply_retry` still 503s when `sunat.enabled=False` (REV-R17 decouples ONLY the reprocess path).

---

## Dependency Graph

```
T1 (VisionLLMPort port method) ──┐
T2 (Config: 2 new VisionConfig fields) ─┤ (both can start in parallel)
                                  ├──► T3 (adapters: Anthropic + OpenAI-compat + Null)
                                  │
                                  └──► T4 (ReprocessService.apply_reprocess + helpers + lazy prims)
                                            │
                                   T5 ──────┘ (container: SUNAT-gate decouple + vision inject)
                                   │
                          T6 ──────┘ (API: async reprocess endpoint + ReprocessGuiaResponse schema)
                                   │
                          T7 ──────┘ (Frontend: button + reprocessingIds + client + types)
                                   │
                          T8 ──────┘ (Cross-cutting gates: vue-tsc + targeted suites + SA-5 Playwright)
```

T1 and T2 are parallel entry points — neither depends on the other.
T3 depends on T1 (adapter must satisfy the new Protocol method).
T4 depends on T1 + T2 (service uses the port + config fields; async primitives need config).
T5 depends on T4 (container wires the fully-built service).
T6 depends on T5 (route calls service built by container).
T7 depends on T6 (frontend calls the endpoint + types come from the schema).
T8 depends on T7 (all layers must be present for SA-5 vision-enabled run).

---

## STRICT TDD MODE — NON-NEGOTIABLE

Every task that touches `*.py` or `frontend/src/**` MUST follow this sequence:
1. **Write the failing test** (it must fail without the change — verify this explicitly).
2. **Implement the change** to make the test pass.
3. **Run the suite green**: `cd backend && uv run pytest <targeted path>` / `cd frontend && npm test`.

Do NOT write implementation first and tests after. Do NOT mark a task done without a green targeted run.
Test runner — backend: `cd backend && uv run pytest` · frontend: `cd frontend && npm test`.

---

## Work-Unit Checklist

---

### T1 — Add `read_material_table` to `VisionLLMPort` Protocol
**Spec**: REV-R10 | **Parallel entry point — can start immediately**

**STRICT TDD: write failing test FIRST, then implement.**

**Failing test** (`tests/unit/test_ports_contract.py` — new section or extend):
```
# test_vision_llm_port_has_read_material_table:
#   assert hasattr(VisionLLMPort, 'read_material_table')
#   assert the method's __annotations__ reflect image:bytes, hint:str|None, return list[MaterialLine]
#   (structural check; fails before the method is added)

# test_null_adapter_satisfies_vision_port_with_table_method:
#   from reconciliation.adapters.vision.null_vision import NullVisionAdapter
#   assert isinstance(NullVisionAdapter(), VisionLLMPort)  # Protocol structural check
#   (fails until NullVisionAdapter defines read_material_table)
```

**Implementation**:
1. In `domain/ports.py`, add to `VisionLLMPort` Protocol AFTER `read_handwritten_date_batch`:
   ```python
   def read_material_table(self, image: bytes, hint: str | None = None) -> list[MaterialLine]: ...
   ```
   `MaterialLine` is already imported in `domain/ports.py` — no new import.
2. No logic, no vendor import, no concrete binding. Protocol declaration only.

**Invariant check**: no SDK/IO import enters `domain/`. Auto-reject if violated.

**Files**: `backend/src/reconciliation/domain/ports.py`
**Test file**: `backend/tests/unit/test_ports_contract.py`
**Commit message**: `feat(domain): add read_material_table to VisionLLMPort protocol`

---

### T2 — Add `reprocess_max_concurrency` and `reprocess_downscale_max_edge` to `VisionConfig`
**Spec**: REV-R11, REV-R15 | **Parallel entry point — can start immediately (independent of T1)**

**STRICT TDD: write failing test FIRST, then implement.**

**Failing test** (`tests/unit/test_config.py` — new section or extend):
```
# test_vision_config_has_reprocess_max_concurrency:
#   c = VisionConfig()
#   assert c.reprocess_max_concurrency == 3
#   assert c.reprocess_downscale_max_edge == 2000
#   (fails before fields are added)

# test_vision_config_env_override_concurrency:
#   with patch.dict(os.environ, {"RECONCILIATION__VISION__REPROCESS_MAX_CONCURRENCY": "5"}):
#       c = VisionConfig()
#       assert c.reprocess_max_concurrency == 5

# test_vision_config_env_override_downscale:
#   with patch.dict(os.environ, {"RECONCILIATION__VISION__REPROCESS_DOWNSCALE_MAX_EDGE": "1500"}):
#       c = VisionConfig()
#       assert c.reprocess_downscale_max_edge == 1500

# test_vision_config_rejects_zero_concurrency:
#   with pytest.raises(ValidationError):
#       VisionConfig(reprocess_max_concurrency=0)
```

**Implementation**:
In `application/config.py`, inside `VisionConfig`:
```python
reprocess_max_concurrency: int = Field(default=3, gt=0,
    description="Max concurrent vision calls during Reprocesar con IA.")
reprocess_downscale_max_edge: int = Field(default=2000, gt=0,
    description="Long-edge px cap for full-page downscale before vision table read.")
```
Env vars: `RECONCILIATION__VISION__REPROCESS_MAX_CONCURRENCY`, `RECONCILIATION__VISION__REPROCESS_DOWNSCALE_MAX_EDGE`.

**Files**: `backend/src/reconciliation/application/config.py`
**Test file**: `backend/tests/unit/test_config.py`
**Commit message**: `feat(config): add reprocess_max_concurrency + reprocess_downscale_max_edge to VisionConfig`

---

### T3 — Implement `read_material_table` in all three vision adapters
**Spec**: REV-R10, REV-R11 (image prep lives here) | **Sequential after T1** (must satisfy the protocol)

**STRICT TDD: write failing tests FIRST, then implement.**

**Failing tests** (three new test files / sections):
```
# tests/unit/test_null_vision_table.py (or extend test_null_vision.py):
# test_null_read_material_table_returns_empty:
#   adapter = NullVisionAdapter()
#   result = adapter.read_material_table(b"fake-image-bytes")
#   assert result == []
#   (fails before method exists)

# tests/unit/test_anthropic_vision_table.py (new):
# test_anthropic_read_material_table_success:
#   FAKE client returning JSON: '{"lines": [{"descripcion":"ALAMBRE N°16","cantidad":50,"unidad":"KG"}],"confidence":0.95}'
#   result = adapter.read_material_table(b"image")
#   assert len(result) == 1
#   assert result[0].description_raw == "ALAMBRE N°16"
#   assert result[0].cantidad == 50
#   assert result[0].unidad == "KG"

# test_anthropic_read_material_table_malformed_json:
#   FAKE client returning 'not json at all'
#   result = adapter.read_material_table(b"image")
#   assert result == []  # never raises

# test_anthropic_read_material_table_missing_lines_key:
#   FAKE client returning '{"confidence": 0.5}'  (no "lines" key)
#   result = adapter.read_material_table(b"image")
#   assert result == []

# test_anthropic_read_material_table_sdk_exception:
#   FAKE client raises APIError
#   result = adapter.read_material_table(b"image")
#   assert result == []  # errors always return [], never raise

# (Mirror the above 4 scenarios for OpenAICompatibleVisionAdapter)
# test_openai_read_material_table_success
# test_openai_read_material_table_malformed_json
# test_openai_read_material_table_think_block_stripped (defensive: model returns <think>...</think> prefix)
# test_openai_read_material_table_sdk_exception
```

**Implementation**:

1. **`NullVisionAdapter`** — add:
   ```python
   def read_material_table(self, image: bytes, hint: str | None = None) -> list[MaterialLine]:
       return []
   ```

2. **`AnthropicVisionAdapter`** — add `_TABLE_SYSTEM_PROMPT` (module-level constant) and method:
   - Prompt: instruct the model to extract EVERY material row from the guía de remisión table and return ONLY strict JSON `{"lines": [{"descripcion": str, "cantidad": number, "unidad": str}], "confidence": 0..1}`.
   - Import `anthropic` INSIDE the method body (lazy — reuse `_get_client()` pattern).
   - Send `image` as a base64-encoded PNG/JPEG vision message (full page — no crop).
   - Parse response: strip markdown fences, extract JSON, map each entry to `MaterialLine(description_raw=..., description_canonical=..., cantidad=..., unidad=..., confidence=envelope_confidence)`.
   - On any `Exception` (SDK, JSON parse, key error): return `[]`.

3. **`OpenAICompatibleVisionAdapter`** — identical structure:
   - Same `_TABLE_SYSTEM_PROMPT` (copy; both adapters use the same extraction contract).
   - Strip `<think>…</think>` blocks before JSON parse (reuse existing defensive strip).
   - Lazy `openai` import INSIDE the method body.
   - Same error-isolation: any failure → `[]`.

**Invariant check**: `anthropic`/`openai` imported INSIDE method, never at module top. Full-page image passed — no `table_crop` bbox ever applied here. Adapter errors → `[]`, never raises.

**Files**: `backend/src/reconciliation/adapters/vision/null_vision.py`, `backend/src/reconciliation/adapters/vision/anthropic_vision.py`, `backend/src/reconciliation/adapters/vision/openai_compatible.py`
**Test files**: `backend/tests/unit/test_null_vision.py`, `backend/tests/unit/test_anthropic_vision_table.py`, `backend/tests/unit/test_openai_vision_table.py`
**Commit message**: `feat(adapters): implement read_material_table in Anthropic, OpenAI-compat, and Null vision adapters`

---

### T4 — `ReprocessService.apply_reprocess` + helpers + lazy concurrency primitives
**Spec**: REV-R11, REV-R12, REV-R13, REV-R14, REV-R15, REV-R19 | **Sequential after T1 + T2**

> **RISK GUARD REV-R15 (MANDATORY)**: The concurrency test MUST use an `asyncio.Event` rendezvous to FORCE call interleaving — two (or three) vision fakes BOTH enter, BOTH block on the event, release together — then assert `add_recovered_guia` was called one-at-a-time (lock held). A `sleep`-based ordering test is FLAKY and FORBIDDEN.

**STRICT TDD: write failing tests FIRST, then implement.**

**Failing tests** (`backend/tests/unit/test_reprocess_service_vision.py` — new file):
```
# test_build_recovered_guia_lines_from_vision_requires_review_always_true:
#   vision returns [MaterialLine(description_raw="X", confidence=0.99, requires_review=False)]
#   after _build_recovered_guia_lines_from_vision, every line has requires_review=True
#   (fails without the service-side stamp)

# test_build_recovered_guia_lines_from_vision_key_parity:
#   same description+unidad as a known pipeline-normalized line
#   assert: (group_token, unidad) == pipeline canonical key for the same input
#   (normalization parity crux — fails if key_resolver not called)

# test_build_recovered_guia_lines_from_vision_skips_non_domain_unit:
#   vision returns a line with unidad="PAQUETE" (not in domain set)
#   assert: result list is empty (skip, don't crash)

# test_apply_reprocess_success_async:
#   FAKE vision → 2 MaterialLines
#   FAKE doc_source.render_page → b"image"
#   FAKE downscale (monkeypatch _downscale_image or pass small image already ≤ max_edge)
#   call: await service.apply_reprocess("T227-0001", [10])
#   assert: result.recovered == True
#   assert: add_recovered_guia called exactly once with a GuiaDeRemision
#   assert: all lines on the recovered guía have requires_review=True
#   assert: guía identity_source == "vision"
#   assert: guía fecha == None (no SUNAT in this test)

# test_apply_reprocess_vision_empty:
#   FAKE vision → []
#   call: await service.apply_reprocess("T227-0001", [10])
#   assert: result.recovered == False
#   assert: result.reason == "vision_empty"
#   assert: add_recovered_guia NOT called
#   assert: errored_guias still contains T227-0001

# test_apply_reprocess_unknown_guia_id:
#   call apply_reprocess with guia_id not in errored_guias
#   assert: raises ValueError (or result.recovered=False, reason="not_found")

# test_apply_reprocess_downscale_long_edge:
#   synthesize image bytes > 2000px long edge (mock render_page)
#   assert: image passed to read_material_table has long-edge ≤ 2000
#   (fails before downscale logic exists)

# test_apply_reprocess_fecha_sunat_floor_when_available:
#   service has SUNAT; errored guía has fecha_entrega=2026-05-28
#   FAKE vision → 1 MaterialLine
#   await apply_reprocess(...)
#   assert: recovered guía fecha == date(2026, 5, 28)  (R9b floor as reception)
#   assert: requires_review=True on the guía

# === RISK GUARD REV-R15: asyncio.Event rendezvous test (MANDATORY, SLEEP-FREE) ===
# test_apply_reprocess_concurrent_commits_serialized:
#   gate = asyncio.Event()
#   call_order = []
#   async def fake_vision_blocking(image, hint=None):
#       await gate.wait()  # block until released
#       call_order.append("vision")
#       return [MaterialLine(...)]
#   service._vision = FakeVisionWithEvent(fake_vision_blocking)
#   tasks = [asyncio.create_task(service.apply_reprocess(gid, [10])) for gid in ["g1", "g2", "g3"]]
#   # let all 3 tasks start and block on gate
#   await asyncio.sleep(0)  # yield
#   assert len(call_order) == 0  # all 3 blocked
#   gate.set()  # release all simultaneously
#   results = await asyncio.gather(*tasks)
#   # assert no lost updates: errored_guias shrunk by 3, rows consistent
#   assert sum(1 for r in results if r.recovered) == 3
#   # assert add_recovered_guia was never interleaved (mock spy: calls in sequence, no overlap)
```

**Implementation**:

In `application/reprocess_service.py` (modify existing file from PR#2):

1. Add `vision: VisionLLMPort` parameter to `ReprocessService.__init__`; make `sunat: SunatGreFetchPort | None = None` (REV-R17 prep for T5). Add `max_concurrency` and `downscale_max_edge` ints from config (or read from `config.vision`).

2. Add lazy-init properties on the instance (NEVER at construction time — service built in sync context):
   ```python
   @property
   def _sem(self) -> asyncio.Semaphore:
       if self.__sem is None:
           self.__sem = asyncio.Semaphore(self._max_concurrency)
       return self.__sem

   @property
   def _commit_lock(self) -> asyncio.Lock:
       if self.__lock is None:
           self.__lock = asyncio.Lock()
       return self.__lock
   ```

3. Add module-level helper `_downscale_image(image_bytes: bytes, max_edge: int) -> bytes`: open with PIL/fitz, compute scale ratio, resize if long-edge > max_edge, return PNG bytes unchanged if already ≤ max_edge. **No import at module top** — import PIL/fitz inside the function.

4. Add `_build_recovered_guia_lines_from_vision(lines: list[MaterialLine], source_page: int, key_resolver: MaterialKeyResolver) -> list[MaterialLine]`: for each raw line from the adapter, normalize `unidad` against domain set (skip non-domain), call `key_resolver.resolve(description_raw, unidad)`, set `description_canonical=key.group_token`, `match_method=key.method`, `requires_review=True` (ALWAYS — service policy, not adapter), keep `confidence` from the model envelope.

5. Implement `async apply_reprocess(self, guia_id: str, source_pages: list[int]) -> ReprocessResult`:
   ```python
   async def apply_reprocess(self, guia_id, source_pages):
       errored = self._find_errored(guia_id)  # raises if not found
       async with self._sem:
           # render + downscale (read-only fitz)
           image = self._doc_source.render_page(source_pages[0], dpi=300)
           image = _downscale_image(image, self._downscale_max_edge)
           # dispatch sync vision SDK to threadpool
           loop = asyncio.get_event_loop()
           raw_lines = await loop.run_in_executor(
               None, self._vision.read_material_table, image
           )
           if not raw_lines:
               return ReprocessResult(recovered=False, reason="vision_empty", ...)
           lines = _build_recovered_guia_lines_from_vision(raw_lines, source_pages[0], self._key_resolver)
           if not lines:
               return ReprocessResult(recovered=False, reason="vision_empty", ...)
           # date: R9b floor or None (no new vision date call)
           fecha = None
           if self._sunat is not None and errored.fecha_entrega is not None:
               fecha, _ = apply_delivery_floor(None, errored.fecha_entrega)
           guia = GuiaDeRemision(
               guia_id=guia_id, registro=errored.registro, fecha=fecha,
               lines=lines, source_pages=source_pages,
               identity_source="vision", requires_review=True,
               fecha_entrega=errored.fecha_entrega,
           )
           async with self._commit_lock:
               rows = self._review_service.add_recovered_guia(guia)
       return ReprocessResult(
           recovered=True, reason=None, rows=rows,
           errored_guias=self._review_service.errored_guias,
       )
   ```

6. Add `ReprocessResult` dataclass alongside `RetryResult`:
   ```python
   @dataclass
   class ReprocessResult:
       guia_id: str
       recovered: bool
       reason: str | None   # "vision_empty" | None
       rows: list[ReconciliationRow]
       errored_guias: list[ErroredGuia]
   ```

**Invariants**: `apply_reprocess` does NOT call `read_handwritten_date`. `requires_review=True` stamped by the service AFTER adapter returns, not inside adapter. Vision I/O runs OUTSIDE the commit lock. Sync SDK via `run_in_executor`. Lazy semaphore and lock bind to the running loop.

**Files**: `backend/src/reconciliation/application/reprocess_service.py`
**Test file**: `backend/tests/unit/test_reprocess_service_vision.py`
**Commit message**: `feat(service): add apply_reprocess async method with semaphore+lock concurrency`

---

### T5 — Decouple `build_reprocess_service` from SUNAT gate; inject vision adapter
**Spec**: REV-R17 | **Sequential after T4**

> **RISK GUARD REV-R17 (MANDATORY)**: This task MUST add a test that the reprocess service builds when `sunat.enabled=False` + `vision.enabled=True`. It MUST also run the existing retry/503 unit test as a regression check in the same work-unit (verify `apply_retry` still 503s when SUNAT off). Do NOT break the PR#2 SUNAT-only path.

**STRICT TDD: write failing tests FIRST, then implement.**

**Failing tests** (`backend/tests/unit/test_container_reprocess.py` — extend existing):
```
# test_build_reprocess_service_vision_only_sunat_off:
#   config: sunat.enabled=False, vision.enabled=True
#   result = build_reprocess_service(ctx, config, review_service)
#   assert result is not None  # service MUST be built
#   assert isinstance(result, ReprocessService)
#   (fails before the SUNAT gate is decoupled)

# test_build_reprocess_service_both_enabled:
#   config: sunat.enabled=True, vision.enabled=True
#   result = build_reprocess_service(ctx, config, review_service)
#   assert result is not None
#   assert result._sunat is not None   # sunat injected
#   assert result._vision is not None  # vision injected

# test_build_reprocess_service_vision_off_sunat_off:
#   config: sunat.enabled=False, vision.enabled=False
#   result = build_reprocess_service(ctx, config, review_service)
#   assert result is None  # or returns a service whose apply_reprocess 503s — either is acceptable

# === REGRESSION CHECK (MANDATORY) ===
# test_apply_retry_still_503_when_sunat_off:
#   (import or re-run from test_container_reprocess.py or test_routes_retry.py)
#   POST .../retry with sunat.enabled=False → reprocess_service None (or sunat=None)
#   assert: 503 (REINTENTAR gate unchanged)
#   Confirms REV-R17 decoupling did NOT break the PR#2 apply_retry path.
```

**Implementation**:
In `infrastructure/container.py`, modify `build_reprocess_service`:

```python
def build_reprocess_service(ctx, config, review_service) -> ReprocessService | None:
    vision_ok = config.vision.enabled
    sunat_ok  = config.sunat.enabled
    if not vision_ok and not sunat_ok:
        log.info("build_reprocess_service: both vision and SUNAT disabled — returning None")
        return None
    # Always build + inject vision adapter (may be Null if vision.enabled=False but sunat on)
    vision = build_vision_adapter(config)       # returns NullVisionAdapter when vision.enabled=False
    sunat  = build_sunat_adapter(config) if sunat_ok else None
    # lazy-import heavy deps inside method (existing pattern)
    from reconciliation.adapters.source.pdf_structure import PdfStructureAdapter
    from reconciliation.adapters.identity.qr_barcode import QrBarcodeExtractionAdapter
    from reconciliation.adapters.inference.material_key_resolver import MaterialKeyResolver
    from reconciliation.adapters.inference.material_key_normalizer import MaterialKeyNormalizer
    doc_source   = PdfStructureAdapter(ctx.pdf_path)
    identity     = QrBarcodeExtractionAdapter(ctx.pdf_path)
    normalizer   = MaterialKeyNormalizer()
    key_resolver = MaterialKeyResolver(normalizer)
    return ReprocessService(
        doc_source=doc_source, identity=identity, sunat=sunat,
        vision=vision, key_resolver=key_resolver,
        review_service=review_service,
        max_concurrency=config.vision.reprocess_max_concurrency,
        downscale_max_edge=config.vision.reprocess_downscale_max_edge,
    )
```

The route helper for REINTENTAR (`_require_reprocess_service` or equivalent) is UNCHANGED — it still returns 503 when the service is None or when `sunat` is None on the service instance. The new `reprocess` route helper (`_require_vision_reprocess` — added in T6) gates on `vision.enabled`.

**Files**: `backend/src/reconciliation/infrastructure/container.py`
**Test file**: `backend/tests/unit/test_container_reprocess.py`
**Commit message**: `feat(container): decouple build_reprocess_service from SUNAT gate; inject vision adapter (REV-R17)`

---

### T6 — Async `POST .../reprocess` endpoint + `ReprocessGuiaResponse` schema
**Spec**: REV-R16 | **Sequential after T5**

**STRICT TDD: write failing tests FIRST, then implement.**

**Failing tests** (`backend/tests/unit/test_routes_reprocess.py` — new file, using `TestClient`):
```
# test_reprocess_guia_success_200:
#   mock registry entry: reprocess_service.apply_reprocess → ReprocessResult(recovered=True, rows=..., errored_guias=[])
#   POST /api/v1/runs/{run_id}/errored-guias/T227-0001/reprocess
#   assert: status 200
#   assert: body.recovered == True
#   assert: body.reason is None
#   assert: len(body.rows) > 0

# test_reprocess_guia_vision_empty_200:
#   mock apply_reprocess → ReprocessResult(recovered=False, reason="vision_empty", errored_guias=[guia])
#   assert: status 200
#   assert: body.recovered == False
#   assert: body.reason == "vision_empty"
#   assert: errored_guias contains the guía

# test_reprocess_guia_vision_disabled_503:
#   vision.enabled=False in config (or reprocess_service built with NullVisionAdapter)
#   assert: status 503
#   assert: body detail contains "vision" or "IA"

# test_reprocess_guia_unknown_id_404:
#   guia_id not in errored_guias
#   assert: status 404

# test_reprocess_guia_unknown_run_404:
#   run_id not in registry
#   assert: status 404

# test_reprocess_endpoint_is_async_route:
#   inspect route handler: assert asyncio.iscoroutinefunction(route_handler) == True
#   (the reprocess route is the FIRST async def mutation route — verify this explicitly)
```

**Implementation**:

1. In `infrastructure/api/schemas.py`, add:
   ```python
   class ReprocessGuiaResponse(BaseModel):
       run_id: str
       guia_id: str
       recovered: bool
       reason: str | None = None
       rows: list[ReconciliationRowResponse]
       errored_guias: list[ErroredGuiaResponse]
   ```

2. In `infrastructure/api/routes.py`, add:
   ```python
   def _require_vision_reprocess(reprocess_service, config: AppConfig) -> ReprocessService:
       """503 when vision is disabled (NullVisionAdapter path returns [])."""
       if reprocess_service is None or not config.vision.enabled:
           raise HTTPException(status_code=503, detail="Vision IA no disponible; vision.enabled=False.")
       return reprocess_service

   @router.post(
       "/runs/{run_id}/errored-guias/{guia_id}/reprocess",
       response_model=ReprocessGuiaResponse,
   )
   async def reprocess_guia(
       run_id: str, guia_id: str,
       registry: RunRegistry, config: AppConfigDep,
   ) -> ReprocessGuiaResponse:
       entry = _require_run(registry, run_id)
       review_service = _require_review_service(entry, run_id)
       service = _require_vision_reprocess(entry.get("reprocess_service"), config)
       if not any(e.guia_id == guia_id for e in review_service.errored_guias):
           raise HTTPException(status_code=404, detail=f"Errored guía '{guia_id}' not found.")
       result = await service.apply_reprocess(guia_id, _get_source_pages(review_service, guia_id))
       return ReprocessGuiaResponse(
           run_id=run_id, guia_id=guia_id,
           recovered=result.recovered, reason=result.reason,
           rows=[_row_to_response(r) for r in result.rows],
           errored_guias=[_errored_to_response(e) for e in result.errored_guias],
       )
   ```

   **Note**: `async def` makes this the FIRST async mutation route — confirm FastAPI handles mixed sync/async routes (it does; no global change needed).

**Files**: `backend/src/reconciliation/infrastructure/api/schemas.py`, `backend/src/reconciliation/infrastructure/api/routes.py`
**Test file**: `backend/tests/unit/test_routes_reprocess.py`
**Commit message**: `feat(api): add async POST reprocess endpoint and ReprocessGuiaResponse schema (REV-R16)`

---

### T7 — Frontend: "Reprocesar con IA" button + `reprocessingIds` Set + `reprocessGuia` client
**Spec**: REV-R18 | **Sequential after T6**

> **RISK GUARD REV-R18 (MANDATORY)**: Use `reactive(new Set<string>())` (or a manual trigger pattern), NOT `ref(new Set())`. Vue 3's `ref` wraps the Set but does NOT detect `.add()`/`.delete()` mutations — the template will not re-render. The vitest MUST assert that the per-guía `isPending` state (Set membership) actually toggles when a click fires, proving reactivity works.

**STRICT TDD: write failing tests FIRST, then implement (vitest).**

**Failing tests** (`frontend/src/features/review/__tests__/ErroredGuiasPanel.spec.ts` — extend):
```
// --- types.ts ---
// test_reprocess_response_type_has_correct_fields:
//   const r: ReprocessGuiaResponse = { run_id: '', guia_id: '', recovered: true, reason: null, rows: [], errored_guias: [] }
//   (TS compile error if fields missing — vue-tsc catches this)

// --- ErroredGuiasPanel.vue ---
// test_reprocesar_button_absent_before_retry_attempted:
//   mount with [{ guia_id: 'X', retry_attempted: false }]
//   expect(wrapper.find('[data-testid="reprocesar-X"]').exists()).toBe(false)
//   OR button present but disabled — per spec: hidden OR disabled before retry_attempted

// test_reprocesar_button_present_after_retry_attempted:
//   mount with [{ guia_id: 'X', retry_attempted: true }]
//   expect(wrapper.find('[data-testid="reprocesar-X"]').exists()).toBe(true)
//   expect(wrapper.find('[data-testid="reprocesar-X"]').attributes('disabled')).toBeUndefined()

// test_reprocesar_button_click_calls_reprocessGuia:
//   mock client.reprocessGuia to return resolved promise
//   click '[data-testid="reprocesar-X"]'
//   expect(client.reprocessGuia).toHaveBeenCalledWith(runId, 'X')

// test_reprocesar_per_guia_spinner_reactivity (RISK GUARD REV-R18):
//   mount component; stub reprocessGuia to return a never-resolving promise
//   click reprocesar for guia 'X'
//   await nextTick()
//   expect(wrapper.find('[data-testid="reprocesar-spinner-X"]').exists()).toBe(true)
//   // Proves reprocessingIds Set mutation triggers re-render (reactive, not ref<Set>)

// test_reprocesar_n_independent_in_flight_states:
//   mount with [{ guia_id: 'X', retry_attempted: true }, { guia_id: 'Y', retry_attempted: true }]
//   stub: guia X never resolves; guia Y resolves immediately
//   click reprocesar for BOTH
//   await nextTick()
//   expect(spinnerX.exists()).toBe(true)   // X still in-flight
//   expect(spinnerY.exists()).toBe(false)  // Y resolved
//   // Confirms per-guia state independence (no cross-contamination)

// test_reprocesar_success_invalidates_table_query:
//   stub reprocessGuia to return { recovered: true, rows: [], errored_guias: [] }
//   click reprocesar for 'X'
//   await flushPromises()
//   expect(queryClient.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['table', runId] })

// test_reprocesar_vision_empty_shows_readable_message:
//   stub returns { recovered: false, reason: 'vision_empty' }
//   click reprocesar for 'X'
//   await flushPromises()
//   expect(wrapper.text()).toContain('No se pudo leer')  // human-readable, not raw JSON
//   expect(wrapper.find('[data-testid="reprocesar-X"]').attributes('disabled')).toBeDefined()
```

**Implementation**:

1. **`frontend/src/api/types.ts`** — add:
   ```typescript
   export interface ReprocessGuiaResponse {
     run_id: string
     guia_id: string
     recovered: boolean
     reason: string | null
     rows: ReconciliationRowResponse[]
     errored_guias: ErroredGuiaResponse[]
   }
   ```

2. **`frontend/src/api/client.ts`** — add:
   ```typescript
   reprocessGuia(runId: string, guiaId: string): Promise<ReprocessGuiaResponse> {
     return this.post(`/runs/${runId}/errored-guias/${guiaId}/reprocess`)
   }
   ```

3. **`frontend/src/features/review/ErroredGuiasPanel.vue`** — extend:
   - Add `reactive(new Set<string>())` as `reprocessingIds` (NOT `ref<Set>()` — see Risk Guard).
   - Add per-guía `useMutation` (TanStack) for `reprocessGuia`:
     - `onMutate: (guiaId) => reprocessingIds.add(guiaId)` (or equivalent tracking)
     - `onSettled: (_, __, guiaId) => reprocessingIds.delete(guiaId)`
     - `onSuccess: () => queryClient.invalidateQueries({ queryKey: ['table', runId] })`
   - Per-guía "Reprocesar con IA" button:
     - `v-if="entry.retry_attempted"` (or `v-show` + `:disabled="!entry.retry_attempted"`)
     - `:disabled="reprocessingIds.has(entry.guia_id)"`
     - `data-testid="reprocesar-{entry.guia_id}"` (enables vitest + Playwright targeting)
   - Per-guía spinner: `data-testid="reprocesar-spinner-{entry.guia_id}"` shown while `reprocessingIds.has(guia_id)`.
   - On vision-empty (`recovered: false, reason: "vision_empty"`): show "No se pudo leer la tabla de materiales" (human-readable); disable button.
   - On 503: show "IA no disponible"; disable button.

**Files**: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/features/review/ErroredGuiasPanel.vue`
**Test file**: `frontend/src/features/review/__tests__/ErroredGuiasPanel.spec.ts`
**Commit message**: `feat(frontend): add Reprocesar con IA button with per-guía reprocessingIds reactive state (REV-R18)`

---

### T8 — Cross-cutting gates: vue-tsc + targeted suites green + SA-5 Playwright (vision-enabled run)
**Spec**: REV-R10..REV-R19 (final gate) | **Sequential after T7**

> **SA-5 — MANDATORY, NON-NEGOTIABLE**: The app runs in deterministic mode by default (`vision.enabled=False`). SA-5 requires a **vision-enabled run** — start the app with `RECONCILIATION__VISION__ENABLED=true` and a real (or Ollama-local) provider. Without a vision-enabled run, the "Reprocesar con IA" feature CANNOT be marked done. This gate is orchestrator-driven.

**Gate sequence (all must pass before PR#3 is marked done)**:

1. **`npx vue-tsc --noEmit`** from `frontend/` — TypeScript strict compile. Catches type mismatches (`ReprocessGuiaResponse` fields, `reprocessingIds` Set type) that vitest happy-path mocks miss.

2. **Backend targeted suite green**:
   ```
   cd backend && uv run pytest tests/unit/test_ports_contract.py \
     tests/unit/test_config.py \
     tests/unit/test_null_vision.py \
     tests/unit/test_anthropic_vision_table.py \
     tests/unit/test_openai_vision_table.py \
     tests/unit/test_reprocess_service_vision.py \
     tests/unit/test_container_reprocess.py \
     tests/unit/test_routes_reprocess.py \
     tests/unit/test_routes_retry.py  -q
   ```
   (Include `test_routes_retry.py` to confirm the PR#2 REINTENTAR 503 regression does not fire.)

3. **Frontend suite green**: `cd frontend && npm test -- --run`

4. **SA-5 Playwright runtime gate (VISION-ENABLED, MANDATORY)**:
   - Start backend with `RECONCILIATION__VISION__ENABLED=true` pointing to a real or Ollama-local vision provider.
   - Upload the real PDF → pipeline completes → ReviewPage renders.
   - Identify a systematic errored guía (`retry_attempted=true`, in ErroredGuiasPanel).
   - If a live errored guía with `retry_attempted=true` exists: click "Reprocesar con IA".
     - Assert: per-guía spinner appears.
     - Assert: on completion, the guía leaves `ErroredGuiasPanel` (recovered=True path) OR a "No se pudo leer" message appears (vision-empty path — still counts as SA-5 pass if the button fired and the UI responded).
     - Assert: the reconciliation grid re-renders (TanStack invalidation fired).
     - Assert: 0 browser console errors.
   - Fallback (no live errored guía with `retry_attempted=true`): use a synthetic run with a known-errored guía via Playwright page injection or a mocked endpoint — SA-5 must still run; never skip.

**Note**: T8 is a validation task, not an implementation task. It produces no new production code. The orchestrator drives the SA-5 session.

---

## Parallelism Summary

| Wave | Tasks | Dependency |
|------|-------|------------|
| Wave 1 | **T1** (port), **T2** (config) | None — parallel entry points |
| Wave 2 | **T3** (adapters) | T1 done |
| Wave 3 | **T4** (apply_reprocess + helpers + concurrency) | T1 + T2 done |
| Wave 4 | **T5** (container SUNAT-gate decouple) | T4 done |
| Wave 5 | **T6** (API endpoint + schema) | T5 done |
| Wave 6 | **T7** (frontend button + state) | T6 done |
| Wave 7 | **T8** (cross-cutting gates + SA-5) | T7 + running app |

T1 and T2 are the two parallel entry points. No other wave can be parallelized safely without its predecessor's API contract.

---

## Risks and Bottlenecks

1. **Normalization parity (T4 crux)**: `_build_recovered_guia_lines_from_vision` must produce the SAME `(registro, group_token, unidad)` key as the pipeline `_norm_line` for the same description+unit input. Divergence → ghost `DECLARED_MISSING` row instead of updating `MISMATCH`. Mitigated by the T4 parity test.

2. **asyncio.Event rendezvous (T4 REV-R15)**: a `sleep`-based concurrency test will pass non-deterministically. The rendezvous test (mandatory) must force all three tasks to block simultaneously before releasing — verifies the lock truly serializes commits rather than passing by lucky timing.

3. **`ref<Set>` Vue reactivity miss (T7 REV-R18)**: `ref(new Set())` does not trigger reactivity on `.add()`/`.delete()` mutations. Using `reactive(new Set())` (or a trigger counter) is mandatory. The vitest spinner-reactivity test validates this; if it passes with `ref<Set>`, something is wrong with the test.

4. **`build_reprocess_service` SUNAT-gate (T5 REV-R17)**: this is the load-bearing architectural change — without it, reg227 (no SUNAT, the keystone systematic case) returns 503 for every reprocess call. The regression test on `apply_retry` 503 confirms the REINTENTAR gate is not accidentally widened.

5. **SA-5 requires a vision-enabled run (T8)**: the app default is `vision.enabled=False` (deterministic mode). SA-5 cannot be satisfied by running the default app. The orchestrator must configure and start a vision-enabled instance. If no vision provider is available, use Ollama-local (ollama:qwen3.5:397b-cloud or equivalent configured in `docker-compose.yml`).

6. **async route is the first `async def` mutation route (T6)**: FastAPI handles mixed sync/async routing via its internal threadpool — no global change needed, but the implementor should confirm no middleware assumes all mutation routes are sync (e.g., no synchronous lock held across the entire request lifecycle at the framework layer).

7. **Sidecar replay (REV-R19 — covered by PR#2 T3/T4)**: `add_recovered_guia` already emits `recovered_guia` with `new_value = guia.model_dump(mode="json")`. The `identity_source="vision"` field is a new attribute on `GuiaDeRemision`; confirm `GuiaDeRemision` allows this field (add if absent as `identity_source: str | None = None`). Replay is deterministic (no re-vision call) — covered by the existing `restore_from_sidecar` branch from PR#2 without modification.

---

## Files Modified

| File | Action | Task |
|------|--------|------|
| `backend/src/reconciliation/domain/ports.py` | Modify | T1 |
| `backend/src/reconciliation/application/config.py` | Modify | T2 |
| `backend/src/reconciliation/adapters/vision/null_vision.py` | Modify | T3 |
| `backend/src/reconciliation/adapters/vision/anthropic_vision.py` | Modify | T3 |
| `backend/src/reconciliation/adapters/vision/openai_compatible.py` | Modify | T3 |
| `backend/src/reconciliation/application/reprocess_service.py` | Modify | T4 |
| `backend/src/reconciliation/infrastructure/container.py` | Modify | T5 |
| `backend/src/reconciliation/infrastructure/api/schemas.py` | Modify | T6 |
| `backend/src/reconciliation/infrastructure/api/routes.py` | Modify | T6 |
| `frontend/src/api/types.ts` | Modify | T7 |
| `frontend/src/api/client.ts` | Modify | T7 |
| `frontend/src/features/review/ErroredGuiasPanel.vue` | Modify | T7 |
