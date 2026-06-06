# Design: guia-reprocess-staged-flow — PR #3 (Reprocesar con IA)

## Technical Approach
Approach B (locked, last slice). Extend the provider-agnostic `VisionLLMPort` with a NEW
Protocol method `read_material_table` (**hexagonal port extension** — bound only at the adapter
boundary), implemented in all three adapters (`AnthropicVisionAdapter`,
`OpenAICompatibleVisionAdapter`, `NullVisionAdapter` → `[]`) with **lazy heavy-dep imports**.
`ReprocessService` gains an `async` method `apply_reprocess(guia_id, source_pages)`: render the
full guía page (read-only fitz), **downscale to a max long-edge**, call `read_material_table`,
normalize through the SAME `MaterialKeyResolver` the pipeline uses, stamp `requires_review=True`,
resolve fecha via the EXISTING date-authority chain (NO new vision date call), then hand the
recovered guía to `ReviewService.add_recovered_guia` (the SOLE mutation hook, REUSED from PR#2).

Concurrency uses **bounded-concurrency** (`asyncio.Semaphore(reprocess_max_concurrency)`) +
**parallel-I/O-serialized-commit**: the vision calls fan out N-wide, but every
`add_recovered_guia` + `reconcile` is serialized behind one `asyncio.Lock` so concurrent
completions can never corrupt the shared `ReviewService._guias`/`_rows`.

## Open-Question Resolutions

| OQ | Decision | Rationale |
|----|----------|-----------|
| **OQ-1** Downscale long-edge | **`reprocess_downscale_max_edge: int = 2000`** (config on `VisionConfig`). Render full page at the existing `DPI=300`, then downscale so the longest edge ≤ 2000 px (no upscaling). | 2000 px on the long edge keeps the guía table's small handwriting/print legible (≈ a US-Letter page at ~170 DPI effective) while staying well under provider input ceilings — Anthropic resizes anything > 1568 px long-edge server-side anyway (so 2000 → ~1568 effective with no quality loss the model would have kept), and OpenAI's high-detail tiling caps the short edge at 768 / long edge at 2000. Cheap to make config (one `Field`), so it is config — operators can raise it for a hard-to-read supplier without a code change. Full-page (not `table_crop`) is the locked input: a misconfigured bbox silently crops material rows = silent data loss. |
| **OQ-2** Sidecar event | **REUSE the PR#2 `recovered_guia` EditEvent.** No new `reprocess` key. | `add_recovered_guia` already emits `recovered_guia` with `new_value = guia.model_dump(mode="json")` — the **fully-normalized** guía model. The MERGE-safe `_persist()` and the `restore_from_sidecar` `recovered_guia` branch (`GuiaDeRemision.model_validate` → `add_recovered_guia`, no re-fetch) rebuild state identically whether the lines came from SUNAT or vision. The event shape already carries everything needed to replay — adding a `reprocess` key would duplicate the replay branch for zero new information. **Provenance** (which guías were vision-recovered) is captured by `identity_source="vision"` on the persisted `GuiaDeRemision`, so it survives the round-trip without a new event kind. |
| **OQ-3** Concurrency vs vision cost cap | **Two SEPARATE governors.** `reprocess_max_concurrency` (default 3) bounds *in-flight* reprocess vision calls; it does NOT share the pipeline's `max_vision_calls` (500) budget, which was consumed by the original pass and is a *total-calls* cap, not a concurrency cap. Reprocess is **manual, per-guía, operator-initiated** — each click = exactly one vision call — so the runaway risk is bounded by the size of the errored set, not by an automatic loop. | The two caps answer different questions: `max_vision_calls` = "did the pipeline blow its total token budget" (pipeline-scoped, already spent); `reprocess_max_concurrency` = "how many reprocess calls run at once" (throughput/throttle, tracks the Ollama-cloud-Pro 3-concurrent-model limit). Coupling them would let a long pipeline run starve reprocess, or let reprocess silently exhaust a shared counter mid-review. reg227 = 24 guías: even if the operator clicks all 24, that is 24 deliberate single calls throttled to 3-wide — no runaway. No new total-calls cap is added for reprocess in PR#3 (manual gate is the governor); a future per-Registro batch button (deferred) would revisit a total cap. |
| **OQ-4** Progress transport | **COLLAPSED — no new transport.** Make the reprocess endpoint **`async def`** and the service method `async`. Each "Reprocesar con IA" click is its own independent HTTP request that `await`s its own vision result; the `asyncio.Semaphore` lives backend-side and merely *delays the await* when > N are in flight. The frontend fires **N independent TanStack `useMutation` calls**, each resolving when its guía's request returns. Per-guía progress = the per-mutation `isPending`/`isError`/`isSuccess` state, keyed by `guia_id` (a `Set`/`Map` of in-flight ids, mirroring PR#2's `retryingId` but plural). **No SSE, no polling, no server-side status store.** | The mutation-per-guía model gives N independent in-flight states for free — the backend holds no per-guía status because the request *is* the status. SSE/polling would only be needed if one request fanned out to many background units the client had to observe; here the request *is* the unit. **Cancel/skip is DEFERRED** from PR#3: it needs request-abort plumbing (AbortController + backend cancellation of an in-flight vision call) that adds scope without a proven need for the manual, fast-feedback flow; the operator can simply not click. |

## Architecture Decisions (confirmed vs real code)

| # | Decision | Choice | Rationale / rejected |
|---|----------|--------|----------------------|
| 1 | Port method | Add to `VisionLLMPort` (ports.py L67): `def read_material_table(self, image: bytes, hint: str \| None = None) -> list[MaterialLine]`. Returns the existing `MaterialLine` domain model (NOT a new `VisionTableResult`). | The pipeline and `_build_recovered_guia_lines` already speak `MaterialLine`; reusing it keeps the recovered-guía build path identical to PR#2's SUNAT path. A new result type would force a translation layer for zero gain — the per-line fields needed (description, cantidad, unidad) are exactly `MaterialLine`'s. The port stays provider-agnostic; no vendor binding. |
| 2 | `requires_review` stamping | Stamp **at the service level** in a new `_build_recovered_guia_lines_from_vision` helper (mirror of PR#2's `_build_recovered_guia_lines`), NOT inside the adapter / domain `MaterialLine` default. The adapter returns raw `MaterialLine`s (whatever it read); the service forces `requires_review=True` on every line after `key_resolver.resolve`. | The invariant "reprocess results are ALWAYS `requires_review`" is a **reconciliation-gate policy**, which belongs to the application layer, not to the vision adapter (an adapter must not encode domain policy) nor to the model default (other call sites legitimately produce non-review lines). Keeping the adapter dumb also means a buggy/hallucinating model cannot accidentally mark a line trusted. |
| 3 | Vision normalization parity | New module-level helper `_build_recovered_guia_lines_from_vision(lines: list[MaterialLine], source_page: int, key_resolver) -> list[MaterialLine]`: for each line, re-derive `unidad` against the domain set (skip non-domain units, same filter as SUNAT path), then `key = key_resolver.resolve(line.description_raw, line.unidad)` → `description_canonical=key.group_token`, `match_method=key.method`, `requires_review=True`, `confidence` = the model's confidence (kept for the review surface, never used as a gate). | Reuse the SAME `MaterialKeyResolver` so a vision-recovered guía groups into the IDENTICAL `(registro, group_token, unidad)` key as a pipelined or SUNAT-recovered one. Sharing the normalization keeps three recovery sources (pipeline / SUNAT / vision) on one key path — no MATCH drift. |
| 4 | Date handling (LOCKED) | `apply_delivery_floor(None, official.fecha_entrega)` **when `fecha_entrega` exists** (SUNAT-enabled run), else `fecha=None`. NO new `read_handwritten_date` call in the reprocess path. reg227 (systematic, no SUNAT) → `fecha=None` → `requires_review` → operator assigns. | `read_material_table` reads ONLY the table (SRP) — the handwritten stamp date stays an independent `read_handwritten_date` concern, not coupled in. The declared side is untouched (digital Protocolo parse). For the systematic class there is no SUNAT and no in-path date read by design; the null fecha is a flagged, operator-resolved state, never a silently-wrong date. Reception-date authority skill: floor is a lower bound used AS reception — safe because the line is `requires_review`. |
| 5 | SUNAT-gate relaxation (CRITICAL, see Risks) | `build_reprocess_service` currently returns `None` (→ 503) when `sunat.enabled=False`, gating the WHOLE service. PR#3 **decouples the vision path from that gate**: build the `ReprocessService` whenever **vision OR SUNAT** is usable; inject the vision adapter (via `build_vision_adapter`) ALWAYS; keep `sunat` optional (`SunatGreFetchPort \| None`). The `retry` route keeps its SUNAT 503 (`_require_reprocess_service` stays for retry); the new `reprocess` route gates on **vision**, returning 503 only when `vision.enabled=False` (Null adapter → `[]`). | reg227 (the keystone systematic case) has NO SUNAT — if reprocess inherited the SUNAT 503 it could never recover the very guías it exists for. Vision and SUNAT are orthogonal data sources; the service must be constructible with either. `ReprocessService.__init__` gains `vision: VisionLLMPort` and makes `sunat: SunatGreFetchPort \| None = None`. |
| 6 | Concurrency primitives placement | `asyncio.Semaphore(reprocess_max_concurrency)` and `asyncio.Lock` are created **lazily on the `ReprocessService` instance** (one service per run, lives in the run registry entry for the run's lifetime), guarded so they bind to the running loop on first `await`. `apply_reprocess` does: `async with self._sem:` → `await loop.run_in_executor(None, self._vision_read, image)` (vision SDK is sync → run in threadpool) → `async with self._commit_lock:` → `self._review_service.add_recovered_guia(guia)`. | The semaphore bounds N concurrent vision reads; the lock wraps ONLY the `add_recovered_guia` + reconcile critical section (the shared-state mutation). The vision read (the slow part) stays OUTSIDE the lock so commits serialize without serializing I/O. Instance-scoped (not per-call) so all concurrent requests for the same run share one semaphore/lock. Created lazily to avoid binding a loop at construction (service is built in a sync route context). |
| 7 | API | `POST /runs/{run_id}/errored-guias/{guia_id}/reprocess` (**async**, single) → `ReprocessGuiaResponse{run_id, guia_id, recovered, reason, rows, errored_guias}` (mirror of `RetryGuiaResponse`). 503 when `vision.enabled=False`; 404 when `guia_id` not in errored set; `recovered=False, reason="vision_empty"` when the model returns `[]`; `reason="vision_disabled"` is folded into the 503. | Mirrors the retry route + DTO exactly for frontend symmetry. Single guía = one vision call → returns updated rows + remaining errored inline (the async + semaphore handle the N-concurrent case across separate requests — no batch endpoint in PR#3). Per-Registro batch reprocess is deferred. |
| 8 | Frontend | **ADD** the "Reprocesar con IA" button to `ErroredGuiasPanel.vue` (it does NOT exist yet — see SA-2 note), gated by `guia.retry_attempted` (only shown/enabled after a failed REINTENTAR, per PR#2). `client.ts::reprocessGuia(runId, guiaId)`. Per-guía in-flight state via a `reprocessingIds = ref<Set<string>>` (N independent) instead of PR#2's single `retryingId`; on success invalidate the `GET /table` TanStack query. | The proposal said the button was "ALREADY present" — it is NOT (the PR#1 read-only test asserts its absence). Non-blocking: PR#3 BUILDS it. A `Set` of in-flight ids gives N independent per-guía spinners (OQ-4). Query invalidation keeps grid + panel consistent. |

## Adapter Design (`read_material_table`)

- **Prompt strategy** (both real adapters, mirroring the date-read `_SYSTEM_PROMPT`): a new
  `_TABLE_SYSTEM_PROMPT` instructing the model to extract EVERY material row from a Peruvian
  guía de remisión / GRE table and return **ONLY** strict JSON:
  `{"lines": [{"descripcion": str, "cantidad": number, "unidad": str}], "confidence": 0..1}`.
  Defensive parsing reuses the existing pattern: strip markdown fences and `<think>…</think>`
  blocks (OpenAI-compat), map malformed/empty → `[]`. Each parsed row becomes a `MaterialLine`
  with `description_raw=descripcion`, `description_canonical=descripcion` (placeholder, overwritten
  by the service-side resolver), `unidad` (raw — service normalizes/filters), `cantidad`,
  `confidence` from the envelope. **The image is the FULL page** (downscaled), never a crop.
- **Lazy import**: `anthropic` / `openai` imported INSIDE the method body (reuse `_get_client`).
- **Error isolation**: any SDK/parse failure returns `[]` (never raises) — same contract as the
  date methods returning `confidence=0`.
- **`NullVisionAdapter.read_material_table` → `[]`** (vision-off graceful stub; no LLM, no IO).
- **`supports_batch` is irrelevant** here — reprocess is one-image-per-call; no batch path.

## Concurrency Sequence (3 concurrent reprocess requests, same run)

```
client: 3× useMutation(reprocessGuia)  → 3 async HTTP requests (g1,g2,g3)

req g1 ─ apply_reprocess(g1) ─┐
req g2 ─ apply_reprocess(g2) ─┤  (Semaphore(3) → all 3 acquire)
req g3 ─ apply_reprocess(g3) ─┘
            │ async with self._sem:                 # bounded queue; 4th would wait here
            │   render full page (read-only fitz) + downscale ≤2000px
            │   await run_in_executor → read_material_table   # PARALLEL vision I/O (sync SDK in threadpool)
            │   _build_..._from_vision → MaterialLine[] requires_review=True
            │   fecha = apply_delivery_floor(None, fecha_entrega?)  # or None
            │   ┌─ async with self._commit_lock:     # SERIALIZED critical section
g1 commits ─┤   │   review_service.add_recovered_guia(g1)  → reconcile + persist
g2 waits ───┤   │   review_service.add_recovered_guia(g2)  → reconcile + persist
g3 waits ───┘   │   review_service.add_recovered_guia(g3)  → reconcile + persist
                └─ release lock → release sem
each request ◄─ ReprocessGuiaResponse{recovered, rows, errored_guias}
```
The lock wraps ONLY `add_recovered_guia` (append `_guias` → drop `_errored_guias` → reconcile →
sidecar persist). Vision reads run concurrently; commits never interleave → `_guias`/`_rows`
stay consistent.

## Data Flow
```
POST /errored-guias/{id}/reprocess  (async)
  └─ route: get ReprocessService from registry entry (built at run end; vision injected)
       └─ await apply_reprocess(guia_id, source_pages)
            async with sem:
              render_page(page, 300) → downscale(max_edge=2000)
              await executor → vision.read_material_table(image) → list[MaterialLine]
                ├─ [] → reason="vision_empty", stay errored, mark_retry_attempted? NO (already attempted) ─┐
                └─ lines → _build_recovered_guia_lines_from_vision → key_resolver.resolve → group_token
                     → apply_delivery_floor(None, fecha_entrega?)  (None when no SUNAT)
                     → GuiaDeRemision(identity_source="vision", requires_review lines)
                     async with commit_lock:
                       → ReviewService.add_recovered_guia(guia)
                            append _guias; drop _errored_guias; reconcile(_delivery_dates());
                            emit recovered_guia sidecar event; _persist()
       ◄─ ReprocessGuiaResponse{recovered, rows, errored_guias}
restart ─ build_review_service ─ hydrate _errored_guias(cache) ─ restore_from_sidecar
            replay recovered_guia → add_recovered_guia (model already normalized; no re-read)
```

## File Changes
| File | Action | Description |
|------|--------|-------------|
| `domain/ports.py` | Modify | Add `read_material_table` to `VisionLLMPort` Protocol |
| `adapters/vision/anthropic_vision.py` | Modify | `read_material_table` + `_TABLE_SYSTEM_PROMPT` + table-JSON parse (lazy `anthropic`) |
| `adapters/vision/openai_compatible.py` | Modify | `read_material_table` + `_TABLE_SYSTEM_PROMPT` + table-JSON parse (lazy `openai`; reuse think-strip) |
| `adapters/vision/null_vision.py` | Modify | `read_material_table → []` |
| `application/reprocess_service.py` | Modify | `vision` ctor dep (+ `sunat` optional); async `apply_reprocess`; lazy `Semaphore`+`Lock`; `_build_recovered_guia_lines_from_vision`; `ReprocessResult` (or reuse `RetryResult` shape) |
| `application/config.py` `VisionConfig` | Modify | `reprocess_max_concurrency: int = 3`; `reprocess_downscale_max_edge: int = 2000` |
| `infrastructure/container.py` `build_reprocess_service` | Modify | Inject `build_vision_adapter(config)`; make `sunat` optional; build whenever vision OR sunat usable (decouple from SUNAT 503) |
| `infrastructure/api/routes.py` | Modify | `+reprocess` (async) endpoint; vision-gate helper (`_require_vision_reprocess`) |
| `infrastructure/api/schemas.py` | Modify | `ReprocessGuiaResponse` (mirror `RetryGuiaResponse`) |
| `frontend/src/api/client.ts` + `types.ts` | Modify | `reprocessGuia` + `ReprocessGuiaResponse` type |
| `frontend/src/features/review/ErroredGuiasPanel.vue` | Modify | ADD "Reprocesar con IA" button (gated by `retry_attempted`); `reprocessingIds` Set state |

## Interfaces
```python
class VisionLLMPort(Protocol):
    supports_batch: bool
    def read_handwritten_date(self, image: bytes, hint: str | None = None) -> VisionResult: ...
    def read_handwritten_date_batch(self, images: list[bytes]) -> list[VisionResult]: ...
    def read_material_table(self, image: bytes, hint: str | None = None) -> list[MaterialLine]: ...  # NEW

class ReprocessService:
    def __init__(self, doc_source, identity, sunat: SunatGreFetchPort | None,
                 key_resolver, review_service, vision: VisionLLMPort,
                 max_concurrency: int = 3, downscale_max_edge: int = 2000) -> None: ...
    def apply_retry(self, guia_id: str, source_pages: list[int]) -> RetryResult: ...      # PR#2, sync
    async def apply_reprocess(self, guia_id: str, source_pages: list[int]) -> ReprocessResult: ...  # NEW
```

## Config
```python
class VisionConfig(BaseSettings):
    ...
    reprocess_max_concurrency: int = Field(default=3, gt=0)      # bounded-concurrency governor
    reprocess_downscale_max_edge: int = Field(default=2000, gt=0)  # OQ-1; long-edge px cap
```
Env: `RECONCILIATION__VISION__REPROCESS_MAX_CONCURRENCY`,
`RECONCILIATION__VISION__REPROCESS_DOWNSCALE_MAX_EDGE`.

## Failure / Restart
- `read_material_table` returns `[]` (adapter never raises) → `recovered=False, reason="vision_empty"`,
  guía stays errored. Already `retry_attempted=True` (PR#2 precondition for showing the button), so no
  state change beyond staying errored.
- `vision.enabled=False` → NullVisionAdapter → `[]`; the route 503s BEFORE calling so the operator
  gets "IA no disponible" rather than a silent empty result.
- **Restart replay = unchanged from PR#2** (OQ-2 reuse): the `recovered_guia` sidecar event carries the
  fully-normalized `GuiaDeRemision` (with `identity_source="vision"`); `restore_from_sidecar` replays it
  via `add_recovered_guia` with NO vision re-read — deterministic and air-gap-safe on restart.

## Testing Strategy (Strict-TDD, failing-first — apply phase, NOT now)
| Layer | Test | Approach |
|-------|------|----------|
| Unit | `read_material_table` parse | FAKE client returning JSON `{"lines":[…]}` → asserts `MaterialLine[]`; malformed/`<think>`/fences → `[]` |
| Unit | NullVisionAdapter | `read_material_table → []` |
| Unit | `_build_..._from_vision` parity | vision lines → SAME `(registro, group_token, unidad)` key as pipeline/SUNAT; all `requires_review=True`; non-domain unit skipped |
| Unit | `apply_reprocess` success (async) | FAKE vision → lines; FAKE doc_source render; assert recovered, lines requires_review, fecha=None when no SUNAT |
| Unit | `apply_reprocess` empty | FAKE vision → `[]` → `recovered=False, reason="vision_empty"`, no mutation |
| Unit | bounded-concurrency + serialized commit | drive 3 concurrent `apply_reprocess` with a vision fake that sleeps; assert ≤ N in vision concurrently AND `add_recovered_guia` calls never interleave (lock held); rows consistent |
| Unit | SUNAT-off constructibility | `build_reprocess_service` with `sunat.enabled=False, vision.enabled=True` → service built (NOT None) |
| API | reprocess endpoint | async route returns recovered rows + remaining errored; 503 when vision disabled; 404 unknown guia |
| Frontend (vitest) | Reprocesar button | shown only when `retry_attempted`; click → `reprocessGuia`, per-guía spinner via `reprocessingIds` Set, query invalidated; N independent in-flight states |
| **Real-precondition** | NOT mock-only | PR#2 lesson: 6 real bugs slipped past green mock tests (guía line-edit HTTP 422, etc.) — pair happy-path mocks with a real-data/runtime check. Run `npx vue-tsc --noEmit`. |
| **E2E (SA-5, REQUIRED, VISION-ENABLED)** | Playwright | App default has vision OFF → SA-5 needs a **vision-enabled** run: upload → review → fail REINTENTAR on a systematic errored guía → "Reprocesar con IA" → per-guía progress → recovered lines `requires_review` → 0 console errors |

## Constraints (AUTO-REJECT anti-patterns — restate)
Domain purity (no SDK/IO under `domain/`; `read_material_table` is a Protocol method; `MaterialLine`
stays pure). Ports at the boundary (`ReprocessService` imports only Protocols + config; `pipeline.py`
NOT modified; ZERO concrete-adapter import in `application/`). Lazy heavy deps (`anthropic`/`openai`
imported INSIDE the new adapter methods). Vision provider-agnostic (3 adapters; selection via
`provider:` behind the port; never bind to a vendor). `fecha` is NEVER a grouping axis (key =
`(registro, material_canonical, unidad)`). Units never converted (KG/TN/RD/Rollo summed
independently; non-domain units skipped). Three identifiers never confused (group by Registro N°).
Reconciliation is the validation gate (recovered vision lines ALWAYS `requires_review`, never
auto-accepted). Input PDF read-only (render_page = read-only fitz open). Local-first (vision can be
Ollama-local or cloud; config-driven, never hard-coded).

## Open Questions
None blocking. SA-2 NOTES (actual code vs locked inputs):
1. **Frontend button is NOT "already present".** The locked inputs/proposal state the "Reprocesar con
   IA" button is "ALREADY gated by `retry_attempted` from PR#2". The real `ErroredGuiasPanel.vue` has
   only the REINTENTAR button, and `ErroredGuiasPanel.test.ts` explicitly asserts the Reprocesar button
   is ABSENT (PR#3 scope). Resolution (non-blocking): PR#3 BUILDS the button + its `reprocessingIds`
   state and updates that test; the `retry_attempted` GATE plumbing IS already wired end-to-end (PR#2).
2. **`build_reprocess_service` is SUNAT-gated** (returns None → 503 when `sunat.enabled=False`),
   gating the ENTIRE service including the future vision path. Resolution (decision #5): decouple the
   vision reprocess path from the SUNAT gate — build the service whenever vision OR SUNAT is usable,
   inject vision always, keep `sunat` optional. WITHOUT this, reg227 (no SUNAT — the keystone systematic
   case) could never be reprocessed. This is the load-bearing architectural change of PR#3.
3. **Routes are sync `def`** (threadpool) today; the reprocess route is the FIRST `async def` mutation
   route. This is the mechanism that collapses OQ-4 (N independent awaiting requests + backend
   semaphore) — confirmed feasible with FastAPI's mixed sync/async routing.
