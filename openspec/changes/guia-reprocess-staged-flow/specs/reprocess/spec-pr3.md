# Delta Spec: guia-reprocess-staged-flow — PR #3 (Reprocesar con IA)

**Change**: guia-reprocess-staged-flow
**Slice**: PR #3 — Reprocesar con IA: errored-guía vision recovery
**Capability modified**: `review` (extends REV-R01..REV-R09 from the PR #2 spec)
**Depends on**: PR #2 MERGED (`ReprocessService` exists; `ReviewService.add_recovered_guia` exists;
`retry_attempted` wired end-to-end; `ErroredGuiasPanel.vue` has REINTENTAR button)
**Date**: 2026-06-05

---

## Scope of This Delta

This spec describes WHAT MUST BE TRUE after PR #3 is applied.
It does NOT cover design or implementation choices (those belong in the design artifact).

PR #3 adds the VISION recovery path for SYSTEMATIC errored guías — those that cannot be
recovered by REINTENTAR (no hashqr_url; no SUNAT) and therefore carry `retry_attempted=True`
after a failed REINTENTAR. The engineer triggers "Reprocesar con IA" per guía; vision reads the
material table from the full guía page; recovered lines are flagged `requires_review` and
rejoined to the reconciliation via the same `add_recovered_guia` hook as PR #2.

REINTENTAR (PR #2), batch per-Registro reprocess, and rollback/undo are OUT OF SCOPE here.

---

## Added Requirements

### REV-R10 — VisionLLMPort.read_material_table Protocol method

`VisionLLMPort` in `domain/ports.py` MUST declare a `read_material_table` method with
signature:

```python
def read_material_table(self, image: bytes, hint: str | None = None) -> list[MaterialLine]: ...
```

The method is a Protocol declaration only — no logic, no concrete binding, no vendor import
in the domain.

`read_material_table` MUST be implemented by ALL three adapters:

- `AnthropicVisionAdapter` — table-extraction prompt; lazy `anthropic` import inside the method.
- `OpenAICompatibleVisionAdapter` — same prompt; lazy `openai` import inside the method.
- `NullVisionAdapter` — returns `[]` unconditionally (vision-off graceful stub).

Every adapter implementation MUST:
- Accept `image` as raw PNG/JPEG bytes (full page, no crop).
- On any SDK error, parse failure, or empty model response: return `[]` (NEVER raise).
- Lazy-import `anthropic` or `openai` INSIDE the method body (suite runs with them uninstalled).

#### Scenario REV-R10-S01: Protocol declared; adapters conform

- GIVEN the `VisionLLMPort` Protocol
- WHEN a conforming adapter is passed where the port is expected
- THEN static type-checking accepts it without error
- AND all three adapters (`AnthropicVisionAdapter`, `OpenAICompatibleVisionAdapter`,
  `NullVisionAdapter`) satisfy the Protocol without structural change

#### Scenario REV-R10-S02: NullVisionAdapter returns empty list — no crash

- GIVEN `vision.enabled=False` (NullVisionAdapter injected)
- WHEN `null_adapter.read_material_table(image=b"...")` is called
- THEN the return value is `[]`
- AND no exception is raised
- AND no LLM call or IO operation is performed

#### Scenario REV-R10-S03: Adapter parse failure returns empty list

- GIVEN an `AnthropicVisionAdapter` whose SDK returns a malformed JSON body (missing `lines` key)
- WHEN `read_material_table(image=b"...")` is called
- THEN the return value is `[]`
- AND no exception propagates to the caller

---

### REV-R11 — Full-page render with configurable downscale (no crop)

When `ReprocessService.apply_reprocess` prepares the image for `read_material_table`, it MUST:

1. Open `ctx.pdf_path` read-only using PyMuPDF (fitz) — no write, no truncate, no rename.
2. Render each page in `errored_guia.source_pages` at DPI 300 (same as pipeline).
3. Downscale the rendered image so the longest edge does not exceed
   `reprocess_downscale_max_edge` (config on `VisionConfig`, default 2000 px, `Field(gt=0)`).
   If the longest edge is already ≤ `reprocess_downscale_max_edge`, no scaling is applied.
4. Pass the full-page image (downscaled or unchanged) to `read_material_table`.

A fixed-bbox table crop MUST NOT be used. Cropping to a static table region silently drops
material rows when the supplier's layout deviates — this is a silent data-loss invariant.

`reprocess_downscale_max_edge` MUST be exposed as an env-configurable field:
`RECONCILIATION__VISION__REPROCESS_DOWNSCALE_MAX_EDGE`.

#### Scenario REV-R11-S01: Page longer than max edge is downscaled

- GIVEN `reprocess_downscale_max_edge = 2000` (default)
- AND a guía page renders at 300 DPI to 2479 × 3508 pixels (A4 portrait)
- WHEN `apply_reprocess` prepares the image
- THEN the rendered image passed to `read_material_table` has long-edge ≤ 2000 px
- AND no table-crop bbox is applied

#### Scenario REV-R11-S02: Page shorter than max edge is passed unchanged

- GIVEN `reprocess_downscale_max_edge = 2000`
- AND a guía page renders to 1240 × 1754 pixels (long-edge 1754 < 2000)
- WHEN `apply_reprocess` prepares the image
- THEN the image passed to `read_material_table` has the same dimensions (no upscale, no crop)

#### Scenario REV-R11-S03: Source PDF opened read-only

- GIVEN a run where `apply_reprocess` is called for any errored guía
- WHEN the service renders the page
- THEN the source PDF at `ctx.pdf_path` is NOT modified, truncated, or renamed

---

### REV-R12 — Recovered vision lines are ALWAYS requires_review=True

Every `MaterialLine` produced by the vision recovery path MUST have `requires_review=True`.
This MUST be set by `ReprocessService` (application layer), NOT by the adapter.
The vision adapter returns raw `MaterialLine` objects; the service MUST force `requires_review=True`
on every line AFTER key resolution, regardless of model confidence.

Reconciliation-vs-declared is the accuracy gate; no model confidence score grants auto-accept.

#### Scenario REV-R12-S01: High-confidence model result still lands requires_review=True

- GIVEN the vision adapter returns 3 `MaterialLine` objects with `confidence = 0.99`
- WHEN `apply_reprocess` builds the recovered guía
- THEN every `MaterialLine` in the recovered `GuiaDeRemision` has `requires_review=True`
- AND no line has `requires_review=False`

#### Scenario REV-R12-S02: adapter requires_review=False overridden by service

- GIVEN the vision adapter (via a fake) sets `requires_review=False` on a returned line
- WHEN the service processes that line
- THEN the line stored in the recovered guía has `requires_review=True`

---

### REV-R13 — Fecha follows existing date-authority chain; NO new vision date call

The vision recovery path MUST NOT invoke any additional vision date call.
`read_material_table` reads ONLY the material table — it MUST NOT read handwritten stamp dates.

Date resolution for a reprocessed guía MUST follow the existing date-authority chain:

1. If SUNAT is enabled and the errored guía's `fecha_entrega` is available on the
   `OfficialGre` / `GuiaDeRemision` record:
   `apply_delivery_floor(None, fecha_entrega)` → resolved date is the SUNAT delivery date
   (R9b floor used as reception; non-blocking `requires_review` flag; same as SUNAT recovery).
2. If SUNAT is disabled OR `fecha_entrega` is unavailable (systematic class, no SUNAT):
   `fecha = None` on the recovered `GuiaDeRemision` → `requires_review=True` → operator
   assigns in review.

The Protocolo declared date is not affected. Grouping key is
`(registro, material_canonical, unidad)` — `fecha` is NOT in the key.

#### Scenario REV-R13-S01: Systematic guía (no SUNAT) — fecha=None, requires_review

- GIVEN `sunat.enabled=False` (systematic class, reg227 archetype)
- AND an errored guía `T227-0001` (registro=227) with no `fecha_entrega`
- WHEN `apply_reprocess` resolves the date
- THEN the recovered `GuiaDeRemision` has `fecha = None`
- AND `requires_review=True` is set on the guía
- AND no vision date call is made (no `read_handwritten_date` invocation)

#### Scenario REV-R13-S02: SUNAT-enabled guía — R9b floor applied as reception date

- GIVEN `sunat.enabled=True`
- AND an errored guía with `fecha_entrega = 2026-05-28`
- WHEN `apply_reprocess` resolves the date
- THEN `apply_delivery_floor(None, fecha_entrega=2026-05-28)` is applied
- AND the recovered guía's `fecha` is `2026-05-28`
- AND `requires_review=True` is set (floor-as-reception is a non-blocking flag)

---

### REV-R14 — apply_reprocess reuses add_recovered_guia; recovered guía leaves errored set

`ReprocessService.apply_reprocess(guia_id, source_pages)` MUST recover the guía by passing
the fully-normalised `GuiaDeRemision` (vision-recovered, `requires_review` lines, resolved
fecha) to `ReviewService.add_recovered_guia` — the same sole mutation hook used by PR #2's
REINTENTAR path. No parallel mutation path may exist.

After a successful `add_recovered_guia`:
- The guía MUST be removed from `_errored_guias`.
- The guía MUST be present in `_guias`.
- `ReviewService._reconciler.reconcile` MUST re-run.
- The updated reconciliation rows MUST reflect the recovered material lines.
- The recovered guía MUST inherit the same `registro` as the original errored entry.

When vision returns `[]` (no lines):
- `add_recovered_guia` MUST NOT be called.
- The guía MUST remain in `_errored_guias`.
- The response MUST communicate `recovered=False, reason="vision_empty"`.
- No reconciliation mutation occurs.

#### Scenario REV-R14-S01: Successful reprocess — errored shrinks, rows update

- GIVEN a run with an errored guía `T227-0001` (registro=227, source_pages=[10])
- AND the vision adapter returns 2 `MaterialLine` objects for that page
- WHEN `apply_reprocess("T227-0001", [10])` is called
- THEN `add_recovered_guia` is called exactly once with the recovered `GuiaDeRemision`
- AND `T227-0001` is removed from `errored_guias`
- AND the reconciliation rows for registro 227 include the recovered material
- AND the recovered guía's `registro` equals 227

#### Scenario REV-R14-S02: Vision returns empty — guía stays errored, no mutation

- GIVEN the vision adapter returns `[]` for the rendered page
- WHEN `apply_reprocess` is called
- THEN `add_recovered_guia` is NOT called
- AND the guía remains in `errored_guias`
- AND no reconciliation row is altered

---

### REV-R15 — Bounded concurrency and serialized commit

`ReprocessService` MUST manage concurrent `apply_reprocess` calls using two primitives:

1. **`asyncio.Semaphore(reprocess_max_concurrency)`** — bounds the number of in-flight vision
   calls. Config field `VisionConfig.reprocess_max_concurrency: int = Field(default=3, gt=0)`,
   env `RECONCILIATION__VISION__REPROCESS_MAX_CONCURRENCY`. When more than N reprocess
   requests arrive concurrently for the same run, excess requests wait on the semaphore.
2. **`asyncio.Lock()`** — serializes the critical section: `add_recovered_guia` + reconcile +
   `_persist`. The vision I/O (the slow part) runs OUTSIDE the lock so N requests may be in
   flight concurrently; only state mutations are serialized.

Both primitives MUST be instance-scoped (one per `ReprocessService` instance, i.e. one per run)
and created lazily on the first `await` to avoid loop-binding at construction time.

The sync vision SDK call (`read_material_table`) MUST be dispatched via
`loop.run_in_executor(None, ...)` so it does not block the event loop.

#### Scenario REV-R15-S01: Concurrent requests bounded by semaphore; commits serialized

- GIVEN `reprocess_max_concurrency = 3`
- AND 3 concurrent `POST .../reprocess` requests for guías g1, g2, g3 in the same run
- WHEN all 3 requests execute
- THEN all 3 vision calls (via `run_in_executor`) may run concurrently (within the semaphore)
- AND `add_recovered_guia` is called for each guía one at a time (never interleaved)
- AND after all 3 complete: `errored_guias` shrinks by 3, reconciliation rows reflect all 3
  recovered guías, and no qty or status value is corrupted by a lost update

#### Scenario REV-R15-S02: 4th concurrent request waits on semaphore

- GIVEN `reprocess_max_concurrency = 3`
- AND 4 concurrent reprocess requests
- WHEN the 4th request arrives while 3 are in-flight
- THEN the 4th request's vision call is queued (does not error; waits for a semaphore slot)
- AND eventually completes successfully when a slot is released

---

### REV-R16 — Reprocess API endpoint

`POST /api/v1/runs/{run_id}/errored-guias/{guia_id}/reprocess` MUST:

- Be an `async def` route (the first async mutation route in the API).
- Return `200 OK` with `ReprocessGuiaResponse` on both recovered and vision-empty outcomes:
  ```
  ReprocessGuiaResponse {
    run_id: str
    guia_id: str
    recovered: bool
    reason: str | None        # "vision_empty" when recovered=False; null when recovered=True
    rows: list[ReconciliationRowResponse]
    errored_guias: list[ErroredGuiaResponse]
  }
  ```
  (Mirrors `RetryGuiaResponse` from REV-R08 for frontend symmetry.)
- Return `503 Service Unavailable` when `vision.enabled=False` (NullVisionAdapter; reason:
  "vision_disabled"). The operator gets a clear "IA no disponible" signal rather than a
  silent empty result.
- Return `404 Not Found` when `run_id` is unknown OR `guia_id` is not in `errored_guias`.
- NOT modify the input PDF.

#### Scenario REV-R16-S01: Successful reprocess — 200 with updated rows and shrunk errored

- GIVEN a valid `run_id` and an errored guía `T227-0001` with `retry_attempted=True`
- AND vision is enabled and returns material lines
- WHEN `POST .../errored-guias/T227-0001/reprocess` is called
- THEN the response is `200 OK` with `recovered=True`
- AND `rows` reflects the reconciliation rows including the recovered material
- AND `errored_guias` does not contain `T227-0001`
- AND the response body includes `run_id` and `guia_id`

#### Scenario REV-R16-S02: Vision returns empty — 200, recovered=False, guía stays errored

- GIVEN vision is enabled but returns `[]` for the rendered page
- WHEN `POST .../errored-guias/T227-0001/reprocess` is called
- THEN the response is `200 OK` with `recovered=False, reason="vision_empty"`
- AND `errored_guias` still contains `T227-0001`
- AND no reconciliation row is altered

#### Scenario REV-R16-S03: Vision disabled — 503

- GIVEN `vision.enabled=False` in the active config
- WHEN `POST .../errored-guias/T227-0001/reprocess` is called
- THEN the response is `503 Service Unavailable`

#### Scenario REV-R16-S04: Unknown guia_id — 404

- GIVEN a valid `run_id` with no errored guía having `guia_id="UNKNOWN-0099"`
- WHEN `POST .../errored-guias/UNKNOWN-0099/reprocess` is called
- THEN the response is `404 Not Found`

---

### REV-R17 — SUNAT-gate decoupling: ReprocessService builds when vision OR SUNAT available

`build_reprocess_service` in `infrastructure/container.py` MUST build and return a
`ReprocessService` instance whenever **vision is usable (`vision.enabled=True`) OR SUNAT is
enabled**, not exclusively when SUNAT is enabled.

When SUNAT is disabled and vision is enabled:
- `ReprocessService.__init__` MUST accept `sunat: SunatGreFetchPort | None = None`.
- The `apply_reprocess` path MUST be reachable (endpoint returns 200, not 503).
- The REINTENTAR path (`apply_retry`) retains its existing 503 gate (REV-R08).

When both vision AND SUNAT are disabled: `build_reprocess_service` MAY return None or the
endpoint MAY 503 — the `AppConfig` fail-fast (`vision.enabled=False` + `sunat.enabled=False`)
already prevents this combination in production.

The decoupling MUST NOT break the existing SUNAT-only recovery path (REINTENTAR, PR #2):
`apply_retry` still requires SUNAT and still 503s when `sunat.enabled=False`.

#### Scenario REV-R17-S01: SUNAT off + vision on — reprocess endpoint reachable

- GIVEN `sunat.enabled=False` and `vision.enabled=True`
- WHEN `build_reprocess_service` is called during container setup
- THEN a `ReprocessService` instance is returned (NOT None)
- AND `POST .../errored-guias/{id}/reprocess` returns 200 or 200 (vision-empty)
  (NOT 503, NOT 404 due to service absence)

#### Scenario REV-R17-S02: SUNAT on + vision on — both paths available

- GIVEN `sunat.enabled=True` and `vision.enabled=True`
- THEN `POST .../retry` is reachable (REV-R08)
- AND `POST .../reprocess` is reachable (REV-R16)
- AND both may be called independently for the same guía

#### Scenario REV-R17-S03: SUNAT on + vision off — retry reachable, reprocess 503

- GIVEN `sunat.enabled=True` and `vision.enabled=False`
- THEN `POST .../retry` is reachable
- AND `POST .../reprocess` returns `503 Service Unavailable`

---

### REV-R18 — Reprocesar con IA button in ErroredGuiasPanel.vue

`ErroredGuiasPanel.vue` MUST be extended to include a "Reprocesar con IA" button per
errored-guía entry. This button does NOT exist in PR #2; PR #3 BUILDS it.

The button MUST:
- Be shown and enabled ONLY when `entry.retry_attempted === true` (the guía has already failed
  a REINTENTAR). Before `retry_attempted` is set, the button MUST be hidden or disabled.
- On click, fire an independent async request via `client.ts::reprocessGuia(runId, guiaId)`
  targeting `POST .../errored-guias/{guia_id}/reprocess`.
- Maintain per-guía in-flight state via a `reprocessingIds = reactive(new Set<string>())`. N independent
  guía reprocess requests MUST each have their own `isPending` / `isError` / `isSuccess` state,
  keyed by `guia_id`. (This mirrors PR #2's `retryingId` but as a Set for N concurrency.)
- On success (`recovered: true`): invalidate the `GET /table` TanStack Query cache so the
  reconciliation grid re-renders with updated rows and the panel refreshes (entry removed or
  marked recovered).
- On failure (`recovered: false`, reason="vision_empty"): show a human-readable status string
  ("No se pudo leer la tabla de materiales" or equivalent) and disable the button.
- On 503 (vision disabled): show "IA no disponible" and disable the button.
- Show a per-guía loading spinner while the request is in-flight (via `reprocessingIds`
  membership check).

`ReprocessGuiaResponse` MUST be typed in `types.ts` (fields: `run_id`, `guia_id`,
`recovered`, `reason`, `rows`, `errored_guias`).

#### Scenario REV-R18-S01: Button hidden before retry_attempted

- GIVEN an errored guía with `retry_attempted: false`
- WHEN `ErroredGuiasPanel` renders
- THEN the "Reprocesar con IA" button is NOT visible or is disabled for that entry
- AND no reprocess API call is triggered

#### Scenario REV-R18-S02: Button shown and enabled after retry_attempted=true

- GIVEN an errored guía with `retry_attempted: true`
- WHEN `ErroredGuiasPanel` renders
- THEN the "Reprocesar con IA" button is visible and enabled for that entry

#### Scenario REV-R18-S03: Click triggers per-guía spinner; success refreshes table

- GIVEN guía `T227-0001` with `retry_attempted: true`
- WHEN the engineer clicks "Reprocesar con IA"
- THEN `T227-0001` is added to `reprocessingIds` (spinner shown)
- AND `reprocessGuia(runId, "T227-0001")` is called
- WHEN the API returns `{"recovered": true, ...}`
- THEN the `GET /table` TanStack Query is invalidated
- AND `T227-0001` is removed from `reprocessingIds`
- AND the reconciliation grid updates with recovered rows
- AND the panel no longer shows `T227-0001` (or shows it as recovered)

#### Scenario REV-R18-S04: N independent in-flight states — no cross-guía interference

- GIVEN guías `T227-0001` and `T227-0002` both with `retry_attempted: true`
- WHEN the engineer clicks "Reprocesar con IA" for both simultaneously
- THEN both guías appear in `reprocessingIds` with independent spinners
- AND the completion of one does NOT affect the loading state of the other
- AND the table is invalidated when each completes independently

#### Scenario REV-R18-S05: Vision-empty failure shows readable status

- GIVEN guía `T227-0003` with `retry_attempted: true`
- WHEN the engineer clicks "Reprocesar con IA"
- AND the API returns `{"recovered": false, "reason": "vision_empty"}`
- THEN a human-readable message is displayed (NOT a raw JSON string)
- AND the button is disabled (not clickable again without page reload)

---

### REV-R19 — Sidecar replay: vision-recovered guía survives restart

A `GuiaDeRemision` recovered via `apply_reprocess` MUST survive an application restart using
the EXISTING `recovered_guia` sidecar event mechanism (REV-R06 from PR #2). No new event
kind is required.

`identity_source="vision"` on the recovered `GuiaDeRemision` provides provenance —
distinguishing vision-recovered guías from SUNAT-recovered ones without a new event key.

`ReviewService.restore_from_sidecar` already replays `recovered_guia` events by calling
`add_recovered_guia` with the stored model data; this MUST work identically whether the stored
guía was produced by REINTENTAR (SUNAT lines) or Reprocesar-con-IA (vision lines), because
`new_value` carries the fully-normalized `GuiaDeRemision.model_dump(mode="json")`.

The replayed guía MUST:
- Be present in `_guias` after replay.
- Be absent from `_errored_guias` after replay.
- Carry `requires_review=True` on every `MaterialLine` (serialised and restored from JSON).
- NOT trigger a new vision call during replay (the guía model is already normalized).

#### Scenario REV-R19-S01: Restart after Reprocesar — recovery survives

- GIVEN a run where `T227-0001` (registro=227) was recovered via Reprocesar con IA
- AND the `recovered_guia` event with `identity_source="vision"` was written to
  `review_sidecar.json`
- WHEN the application is restarted and `restore_from_sidecar` is executed
- THEN `errored_guias` does NOT contain `T227-0001`
- AND `_guias` contains the recovered `GuiaDeRemision` for `T227-0001` with `identity_source="vision"`
- AND every `MaterialLine` on the restored guía has `requires_review=True`
- AND `GET /runs/{run_id}/table` returns rows that include the recovered material
- AND no vision call was made during replay

#### Scenario REV-R19-S02: Mixed restart — SUNAT-recovered and vision-recovered guías coexist

- GIVEN a run where `T228-0001` was recovered via REINTENTAR (identity_source="QR") and
  `T227-0001` via Reprocesar (identity_source="vision")
- WHEN the application restarts and replays both `recovered_guia` events
- THEN both guías are present in `_guias` with their respective `identity_source`
- AND neither guía appears in `errored_guias`
- AND reconciliation rows reflect both recovered guías

---

## MUST-NOT Invariants (hard; reject any implementation that violates these)

| Invariant | Binding rule |
|-----------|-------------|
| Domain purity | No SDK, framework, or IO import under `domain/`. `read_material_table` is a pure Protocol method; `MaterialLine` stays pure Pydantic. |
| Ports at the boundary | `ReprocessService` imports ZERO concrete adapters at module level. `application/pipeline.py` is NOT modified for the reprocess path. |
| Lazy heavy deps | `anthropic` and `openai` MUST be imported INSIDE `read_material_table` method bodies only. |
| Vision provider-agnostic | Implemented in all three adapters (`AnthropicVisionAdapter`, `OpenAICompatibleVisionAdapter`, `NullVisionAdapter`). Selection via config `provider:` behind `VisionLLMPort`. Never bind domain or pipeline to a vendor. |
| fecha is NEVER a grouping axis | Reconciliation key is `(registro, material_canonical, unidad)`. `fecha=None` on a reprocessed systematic guía is permitted and expected. |
| Units never converted | KG, TN, RD, Rollo summed independently per unit type. Non-domain units are skipped in the normalization helper. |
| requires_review always True on vision recovery | Every `MaterialLine` from a vision-recovered guía MUST have `requires_review=True`. No model confidence score grants auto-accept. |
| No new vision date call in reprocess path | `read_material_table` reads ONLY the material table. `read_handwritten_date` is NOT called by `apply_reprocess`. |
| add_recovered_guia is the sole mutation hook | `apply_reprocess` MUST pass the recovered guía through `ReviewService.add_recovered_guia` only. No direct mutation of `_guias`/`_errored_guias`. |
| Input PDF read-only | `ctx.pdf_path` is opened read-only. No write, truncate, or rename of the PDF. |
| Full-page render; no static bbox crop | Full-page (downscaled) image is sent to vision. A hard-coded `table_crop` bbox is PROHIBITED. |
| Existing rows/keys immutable | `apply_reprocess` MUST NOT alter the `status`, `delta`, `qty`, or `canonical_key` of any row that does not involve the recovered guía. |
| REINTENTAR gate unchanged | `apply_retry` (PR #2) still 503s when `sunat.enabled=False`. Decoupling (REV-R17) affects ONLY the `apply_reprocess` path. |

---

## Out of Scope (explicit — absence is a conformance requirement)

- Per-Registro batch reprocess ("reprocess all N errored guías in one click") — deferred.
- REINTENTAR → vision auto-fallback (chaining the two buttons silently) — decided NO.
- A separate vision date call (`read_handwritten_date`) inside `apply_reprocess` — NO.
- Cancel / abort of an in-flight vision call from the frontend — deferred.
- Any modification to `application/pipeline.py` for the reprocess path.
- Changes to REINTENTAR (`apply_retry`) semantics.

---

## Acceptance Summary

| Req | Core scenario(s) | Pass condition |
|-----|-----------------|----------------|
| REV-R10 | Protocol declared; adapters conform; Null → [] | `read_material_table` in Protocol; all 3 adapters satisfy; parse errors return [] |
| REV-R11 | Full-page downscale; no crop; read-only PDF | Long-edge capped at `reprocess_downscale_max_edge`; shorter pages unchanged; PDF unmodified |
| REV-R12 | requires_review=True always | High-confidence result still requires_review; adapter override corrected by service |
| REV-R13 | fecha chain; no vision date call | Systematic guía → fecha=None; SUNAT floor applied when available; no read_handwritten_date |
| REV-R14 | add_recovered_guia reused; vision-empty stays errored | Errored shrinks on success; no mutation on vision_empty |
| REV-R15 | Bounded concurrency; serialized commit | ≤N vision calls concurrently; commits never interleaved; no lost update under 3 concurrent |
| REV-R16 | Reprocess API: 200/503/404 semantics | recovered/vision_empty/vision_disabled/unknown-id cases all correct |
| REV-R17 | SUNAT-off + vision-on → service builds and endpoint reachable | 200 (not 503) when sunat=off+vision=on; retry 503 gate unchanged |
| REV-R18 | Frontend button: gated, per-guía spinners, success invalidates | Hidden before retry_attempted; N independent in-flight states; table refreshes |
| REV-R19 | Sidecar replay survives restart; identity_source=vision | Vision-recovered guía in _guias post-restart; requires_review=True preserved; no re-fetch |
