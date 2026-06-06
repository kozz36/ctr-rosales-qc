# Proposal: guia-reprocess-staged-flow — PR #3 (Reprocesar con IA)

## Intent

REINTENTAR (PR #2) recovers **TRANSIENT** errored guías deterministically (re-decode →
SUNAT). **SYSTEMATIC** errored guías cannot be recovered that way: the supplier prints only a
compact non-SUNAT QR, so there is no `hashqr_url` and no SUNAT line items (keystone analysis:
reg227, all 24 guías). After a failed REINTENTAR these guías carry `retry_attempted=True` — the
gate PR #2 wired. PR #3 adds the **vision recovery path** on top of that gate: read the guía's
**material table** via `VisionLLMPort` and surface the lines as a recovered guía flagged
`requires_review`. This is the LAST slice of the change; it closes the staged-recovery loop.

## Scope

### In Scope
- `VisionLLMPort.read_material_table(image, hint=None) -> list[MaterialLine]` — NEW Protocol
  method (port extension), implemented in **all three** vision adapters:
  `AnthropicVisionAdapter`, `OpenAICompatibleVisionAdapter` (OpenAI cloud + Ollama via
  `base_url`), and `NullVisionAdapter` (stub `-> []`, graceful in `vision.enabled=false` mode).
  Lazy-imports `anthropic`/`openai` INSIDE the method body.
- `ReprocessService.apply_reprocess(guia_id)` — vision recovery orchestration: render
  `source_pages` from `ctx.pdf_path` as a **full page** (read-only fitz) → **downscale to a max
  long-edge** → `read_material_table` → normalize lines (MaterialKeyResolver) → date via the
  EXISTING date-authority chain (NO new vision date call) → `add_recovered_guia` (reused from
  PR #2; replaces the 0-line placeholder, inherits the registro).
- **Bounded-concurrency** for the vision recovery path: a config `reprocess_max_concurrency`
  (NEW; default **3**, tracking Ollama-cloud-Pro's 3-concurrent-model limit; degrades to 1 for
  local/single-model). Vision calls run up to N-wide via an `asyncio.Semaphore` (the semaphore
  IS the bounded queue — no separate queue structure). **Parallel-I/O, serialized-commit**: the
  state mutation (`add_recovered_guia` + `reconcile`) runs behind an `asyncio.Lock` so concurrent
  completions cannot corrupt shared `ReviewService` `_guias`/`_rows`.
- Endpoint `POST /api/v1/runs/{run_id}/errored-guias/{guia_id}/reprocess` + response DTO
  (`ReprocessGuiaResponse`: updated rows + errored_guias).
- Frontend: wire the **"Reprocesar con IA"** button (already gated by `retry_attempted` from
  PR #2) in `ErroredGuiasPanel.vue` to the endpoint, with **N independent per-guía async
  progress states** (pending / running / done / error). This per-guía progress is the main added
  UX surface and likely justifies a `size:exception` backend+frontend PR.

### Out of Scope / Non-Goals (explicit)
- **Handwritten-date recovery in the reprocess path.** `read_material_table` reads ONLY the
  material table (SRP). Reading the guía stamp date stays an INDEPENDENT pipeline concern
  (`read_handwritten_date` on the stamp crop) — NOT coupled into this path.
- **Per-Registro batch reprocess** (a "reprocess all 24" loop) — deferred follow-up.
- **REINTENTAR → vision auto-fallback** (one button silently chaining into the other) — decided
  NO; "Reprocesar con IA" is a distinct, operator-initiated action.
- **A separate queue data structure** — the `asyncio.Semaphore` covers bounded queueing.

## Capabilities

### New Capabilities
None — vision recovery extends the existing `reprocess` capability surface introduced by PR #2.

### Modified Capabilities
- `reprocess`: add the vision recovery requirement (SYSTEMATIC errored guía with
  `retry_attempted=True` → full-page render + downscale → `read_material_table` → recovered guía
  flagged `requires_review`, joined by `(registro, material_canonical, unidad)`).

## Approach

Approach B (locked — `ReprocessService` is the adapter orchestrator; `ReviewService` keeps SRP).
PR #3 extends that orchestrator with a vision path that reuses PR #2's `add_recovered_guia` pure
mutation. Named patterns:

- **Hexagonal port extension** — `read_material_table` is added to the `VisionLLMPort` Protocol
  (domain), bound only at the adapter boundary. The domain never sees a vendor.
- **Bounded-concurrency worker pool** — an `asyncio.Semaphore(reprocess_max_concurrency)` caps
  concurrent vision calls; excess requests queue ON the semaphore. No bespoke queue.
- **Parallel-I/O, serialized-commit** — the vision I/O fans out concurrently, but every
  `add_recovered_guia` + `reconcile` mutation is serialized behind a single `asyncio.Lock`. This
  is the correctness keystone: concurrent completions writing to shared `_guias`/`_rows`
  unguarded would corrupt the reconciliation state.

**Vision crop decision (locked): FULL-PAGE + downscale, NOT a configurable `table_crop`.** A
`table_crop` bbox is premature optimization with an asymmetric downside — a misconfigured bbox
silently crops out material rows = silent data loss, and supplier layouts vary (reg227 ≠ others).
Full page + a structured-extraction prompt + the `requires_review` gate is the safe default; the
downscale max-edge governs token cost without risking row loss.

**Date decision (locked): follow the EXISTING date-authority chain, NO new vision date call.** If
`fecha_entrega` (SUNAT) exists → the R9b floor supplies it. But reg227 is SYSTEMATIC (no QR → no
SUNAT) → `fecha=None` → `requires_review`; the operator assigns it on review. The declared side is
untouched (still the digital Protocolo parse). No conflict with reception-date-authority.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `domain/ports.py` (`VisionLLMPort`) | Modified | Add `read_material_table(image, hint=None) -> list[MaterialLine]` Protocol method |
| `adapters/vision/anthropic_vision.py` | Modified | Implement `read_material_table` (table-extraction prompt; lazy `anthropic`) |
| `adapters/vision/openai_compatible.py` | Modified | Implement `read_material_table` (OpenAI + Ollama; lazy `openai`) |
| `adapters/vision/null_vision.py` | Modified | Stub `read_material_table -> []` (graceful vision-off) |
| `application/reprocess_service.py` | Modified | `apply_reprocess(guia_id)` + Semaphore + commit Lock (async) |
| `application/review_service.py` | Reference | Reuse PR #2 `add_recovered_guia` (no change) |
| `application/config.py` (`VisionConfig`) | Modified | NEW `reprocess_max_concurrency` field (default 3) |
| `infrastructure/container.py` | Modified | `build_reprocess_service` injects the vision adapter + max_concurrency |
| `infrastructure/api/routes.py` | Modified | NEW `reprocess` endpoint (mirrors the `retry` route) |
| `infrastructure/api/schemas.py` | Modified | `ReprocessGuiaResponse` DTO |
| `frontend/.../ErroredGuiasPanel.vue` + api client/types | Modified | Wire "Reprocesar con IA" + N per-guía progress states |

## Constraints (architecture invariants — AUTO-REJECT anti-patterns)

- **Domain purity** — NO SDK/framework/IO import under `domain/`. `read_material_table` is a
  Protocol method (no concrete binding); `ErroredGuia`/`MaterialLine` stay pure Pydantic.
- **Ports at the boundary** — `ReprocessService` imports adapters ONLY through Protocols;
  `application/pipeline.py` is NOT modified for the reprocess path; ZERO concrete-adapter import
  in `application/`.
- **Lazy heavy deps** — the new adapter methods lazy-import `anthropic`/`openai` INSIDE the method
  body (suite runs with them uninstalled).
- **Vision provider-agnostic** — implemented in all three adapters; selection is config
  (`provider:`) behind `VisionLLMPort`. Never bind domain or pipeline to a vendor.
- **`fecha` is NEVER a grouping axis** — recovered guía joins by `(registro, material_canonical,
  unidad)`.
- **Units never converted** (KG/TN/RD/Rollo summed independently); **three identifiers never
  confused** (`#4252` section ≠ Registro N° ≠ QR `serie-numero`; group by Registro N°).
- **Reconciliation is the validation gate** — recovered lines are ALWAYS `requires_review=True`,
  NEVER auto-accepted.
- **Input PDF read-only** — re-render is a read-only fitz open of `ctx.pdf_path`; isolated output
  dir per run; **local-first** — vision is config-driven (local Ollama or cloud), never hard-coded.

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| **State-consistency race** — concurrent vision completions mutate shared `_guias`/`_rows` (explore Risk 1) | High (without guard) | Parallel-I/O, serialized-commit: all `add_recovered_guia` + `reconcile` behind one `asyncio.Lock`; `add_recovered_guia` stays the SOLE entry point |
| **Sidecar replay gap on restart** — recovered/reprocess state lost on restart (explore Risk 2) | Med | Reuse PR #2's `review_sidecar.json` recovery event (OQ-1: same event vs new key) |
| **Vision cost cap** — reprocess vision calls run OUTSIDE the pipeline `vision.n` cap (explore Risk 3) | Med | Now bounded by `reprocess_max_concurrency`; OQ-3: reconcile interplay with the `vision.n` cost cap |
| **NullVisionAdapter contract** — "Reprocesar con IA" in `vision.enabled=false` mode must degrade, not crash (explore Risk 4) | Med | `read_material_table -> []` stub → no lines recovered, guía stays errored; surface a clear "vision disabled" state |
| **DPI re-render timeout** — full-page render on the 493-page PDF, esp. many guías (explore Risk 5) | Med | Async bounded concurrency + downscale long-edge cap; sync timeout pressure removed |
| **`read_material_table` accuracy** — full-table vision is less reliable than a stamp date; hallucinated qty slipping through (explore Risk 6) | Med | `requires_review=True` always; reconciliation-vs-declared is the validation gate; structured-extraction prompt |

## Rollback Plan

Revert the PR. Pure additive: `read_material_table` unused (Protocol method + stubs), `apply_reprocess`
unwired, endpoint removed, button reverts to gated-but-inert. No cache/schema migration.
`reprocess_max_concurrency` defaults are inert when the path is unwired. Recovered guías are
`requires_review` — existing reassign/edit corrects any wrongly-recovered guía.

## Open Questions (flag for design — do NOT decide here; SA-2 no-invent)

- **OQ-1 (downscale max-edge value)**: exact long-edge cap (e.g. ≤2000px) balancing token cost
  vs. table legibility. Design must pick a concrete value + justify.
- **OQ-2 (sidecar event)**: reuse PR #2's existing `review_sidecar.json` `recovered_guia` event,
  or a new `reprocess` event key? Reuse keeps the MERGE/replay contract simple; a new key
  separates provenance (vision vs SUNAT recovery).
- **OQ-3 (cost-cap interplay)**: how `reprocess_max_concurrency` interacts with the existing
  pipeline `vision.n` cost cap — shared token, separate budget, or unbounded reprocess count
  bounded only by concurrency.
- **OQ-4 (per-guía progress transport)**: polling vs SSE for the N independent per-guía progress
  states; and whether a true cancel/skip control ships in PR #3 or is deferred.

## Dependencies

PR #2 (MERGED): `ReprocessService` exists with `apply_retry`; `ReviewService.add_recovered_guia`
exists (replace 0-line placeholder, inherit registro); `retry_attempted` wired end-to-end;
`ErroredGuiasPanel.vue` exists with the REINTENTAR button; the "Reprocesar con IA" button is
ALREADY gated by `retry_attempted`. Vision must be enabled (`vision.enabled=true`) for recovery
to produce lines; in `vision.enabled=false` mode the NullVisionAdapter stub degrades gracefully.

## Apply-Phase Gates (mandatory — noted for the later phase, not this proposal)

- **Strict-TDD**: a failing test FIRST for each `*.py` / `frontend/src/**` change (would fail
  without the change), then green.
- **`npx vue-tsc --noEmit`** clean on the frontend.
- **SA-5 runtime validation against the RUNNING app via Playwright on a VISION-ENABLED run**
  (the deterministic app mode has vision OFF, so SA-5 specifically needs a vision-enabled run:
  upload → review → trigger "Reprocesar con IA" → observe per-guía progress + recovered lines
  flagged `requires_review`).

## Success Criteria

- [ ] "Reprocesar con IA" on a SYSTEMATIC errored guía (`retry_attempted=True`) reads the material
      table via vision, recovers lines (flagged `requires_review`), replaces the 0-line placeholder,
      inherits the registro, removes the guía from the errored set, and re-reconciles.
- [ ] Concurrent reprocess runs are bounded by `reprocess_max_concurrency`; the commit is
      serialized (no `_guias`/`_rows` corruption under concurrent completions).
- [ ] `read_material_table` implemented in all three adapters; `vision.enabled=false` degrades
      gracefully (NullVisionAdapter `-> []`, no crash).
- [ ] `ReprocessService` imports ZERO concrete adapters; domain stays pure; heavy deps lazy.
- [ ] Recovered lines never auto-accepted (always `requires_review`); grouping key + units
      untouched; input PDF read-only.
- [ ] Frontend shows N independent per-guía progress states (pending/running/done/error).
