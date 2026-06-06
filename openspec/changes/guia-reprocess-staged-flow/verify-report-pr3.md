# Verify Report — guia-reprocess-staged-flow PR#3 (Reprocesar con IA)

**Phase**: sdd-verify
**Branch**: feat/guia-reprocess-reprocesar-ia
**Date**: 2026-06-05
**Verdict**: PASS-WITH-WARNINGS
**Scope**: REV-R10..REV-R19 + 25 scenarios (acceptance) vs implementation @ HEAD (5585769)

---

## Executive Summary

All 10 requirements (REV-R10..REV-R19) are implemented and satisfied; 0 CRITICAL.
372 frontend vitest + 272 targeted-backend tests green; vue-tsc clean (exit 0).
Both rewritten risk-guards are MEANINGFUL: the REV-R15 concurrency guard uses a
threading.Event rendezvous (sleep-free) and goes RED if the Semaphore/Lock is removed
(peak>MAX / acquired==0); the REV-R18 reactivity guard uses a never-resolving promise
and goes RED if `reactive()` is swapped for `ref()`. The S02 (vision-on empty → 200
vision_empty) vs S03 (vision-off → 503 vision_disabled) distinction is enforced with
`apply_reprocess.assert_not_awaited()` on the 503 path. Architecture invariants hold:
pipeline.py untouched, reprocess_service ports-only, domain/units.py pure (imports with
anthropic/openai/fitz/PIL blocked), no static bbox crop, lazy heavy deps. 4 WARNINGS are
test-coverage gaps (vision-provenance restart, retry-503-on-vision-only-service), not
behavioral defects. SA-5 vision-enabled Playwright runtime remains the only open gate.

---

## Requirement Results

| Req | Status | Evidence |
|-----|--------|----------|
| REV-R10 | PASS | `read_material_table` in `VisionLLMPort` Protocol (ports.py L87); all 3 adapters implement it; Null→[]; lazy SDK import inside method; parse failure→[]. Tests: test_vision_table.py, test_ports_contract_read_material_table.py. |
| REV-R11 | PASS | Full-page render→`_downscale_image(rendered, reprocess_downscale_max_edge)`; config default 2000 / `gt=0` / env override (config.py L124+, test_config_reprocess.py). No bbox crop (only "no crop" assertion comments). test_downscale_called_when_image_large passes max_edge=1500. |
| REV-R12 | PASS | `_build_recovered_guia_lines_from_vision` forces `requires_review=True` unconditionally (reprocess_service.py L246); ReviewService.add_recovered_guia fail-closed guard rejects requires_review!=True (review_service.py L449). Tests assert override of adapter False. |
| REV-R13 | PASS | `apply_delivery_floor(None, errored.fecha_entrega)` only; NO read_handwritten_date in apply_reprocess. fecha=None without SUNAT, =fecha_entrega when present. ErroredGuia.fecha_entrega persisted (models.py). test_fecha_none_without_sunat / test_fecha_sunat_floor_when_available. |
| REV-R14 | PASS | apply_reprocess → `review_service.add_recovered_guia` sole hook; vision_empty → no call, stays errored, reason="vision_empty"; replace-placeholder + inherit registro + re-reconcile + leaves errored set. test_review_service_recovered_guia_replace.py (5 tests). |
| REV-R15 | PASS | Lazy instance-scoped `asyncio.Semaphore(max_concurrency)` + `asyncio.Lock()`; vision via run_in_executor OUTSIDE lock; commit INSIDE lock. Guard MEANINGFUL: Event-rendezvous semaphore test (peak<=MAX, RED if removed) + _CountingLock asserts acquired==N & max_held==1 (RED if `async with` removed). |
| REV-R16 | PASS | async POST .../reprocess; 200 recovered / 200 vision_empty / 503 vision_disabled / 404 unknown. ReprocessGuiaResponse mirrors RetryGuiaResponse. test_reprocess_endpoint.py: S03 NullVisionAdapter→503 + assert_not_awaited; S02 real-adapter-empty→200 stays distinct. |
| REV-R17 | PASS | build_reprocess_service builds on vision OR sunat (container.py L592); sunat optional; `_require_sunat_on_service` keeps REINTENTAR 503; `_require_vision_on_service` gates reprocess. test_build_reprocess_service.py: vision-only/sunat-only/both, sunat=None on vision-only, config wiring. |
| REV-R18 | PASS | Button BUILT in ErroredGuiasPanel.vue, gated `v-if="guia.retry_attempted"`; `reprocessingIds = reactive(new Set())`; per-guía has() spinner; success emits reprocess-success → table invalidation. Reactivity guard (never-resolving promise) asserts per-guía toggle + independence. Spec wording said `ref<Set>`; tasks T7 corrected to reactive() — impl follows the correct Vue idiom. |
| REV-R19 | PASS (w/ WARN) | Reuses recovered_guia sidecar event (model_dump round-trip); identity_source Literal extended with "vision" (models.py, schemas.py, types.ts). restore_from_sidecar replays SUNAT/vision identically; no re-fetch. Mechanism tested (test_review_service_recovered_guia_sidecar.py) but fixtures use identity_source="qr" — see W1. |

---

## Test Results (actual)

- Targeted backend (PR3 touched paths): **272 passed** across 13 files
  (vision_table, config_reprocess, reprocess_service_vision, reprocess_service,
  review_service, recovered_guia_replace, recovered_guia_sidecar, mark_retry_attempted,
  ports, ports_contract_read_material_table, build_reprocess_service, reprocess_endpoint,
  api_routes, full_page_image, table_errored_guias).
- REINTENTAR retry regression: **PASS** (test_api_routes.py::TestRetryEndpoint incl.
  test_retry_503_when_sunat_disabled, test_failed_retry_marks_remaining_errored_attempted).
- Frontend vitest: **272 passed (21 files)** — includes 14 new reprocess tests.
- vue-tsc --noEmit: **exit 0 (clean)**.
- Domain purity proof: `domain/units.py` + `domain/ports.py` import with
  anthropic/openai/fitz/PIL/paddleocr/requests forced to None → OK.

Note: monolithic `pytest -q` intentionally NOT run (hangs on paddle import) — targeted
paths only, per project convention.

---

## CRITICAL (0)

None.

---

## WARNINGS (4 — coverage gaps, non-blocking)

- **W1 (REV-R19)**: No test asserts a `identity_source="vision"` recovered guía survives
  restart with provenance intact. The sidecar replay suite proves the mechanism but uses
  `identity_source="qr"` fixtures. Mechanism is provably identical (model_dump round-trip),
  so this is a coverage gap, not a defect. Recommend adding a vision-provenance replay test.
- **W2 (REV-R17-S03)**: No endpoint test asserts REINTENTAR 503s when the service was built
  VISION-ONLY (`_sunat is None` but service present) — the precise purpose of the new
  `_require_sunat_on_service` guard. Existing test only covers `reprocess_service is None`.
  Guard code is present and correct; add the vision-only-service retry-503 regression.
- **W3 (REV-R11-S03)**: No explicit assertion that the source PDF file is byte-unmodified
  across apply_reprocess. Path provably never writes (render_page returns bytes; read-only
  by DocumentSourcePort contract), so the invariant holds by construction.
- **W4 (REV-R18 spec drift)**: spec-pr3.md REV-R18 literally specifies `ref<Set<string>>`;
  the correct Vue 3 idiom (and the tasks T7 correction) is `reactive(new Set())`. Impl is
  correct; the SPEC TEXT is wrong and should be amended to avoid future confusion.

---

## Remaining Gates

- **SA-5**: Playwright runtime validation on a VISION-ENABLED run
  (`RECONCILIATION__VISION__ENABLED=true`; app default is vision OFF). Orchestrator-run,
  NOT part of this verify. Upload → review → click "Reprocesar con IA" on a
  retry_attempted guía → confirm spinner, table refresh, recovered rows requires_review.

---

## Artifacts

- engram: `sdd/guia-reprocess-staged-flow/verify-report-pr3`
- openspec: `openspec/changes/guia-reprocess-staged-flow/verify-report-pr3.md`
