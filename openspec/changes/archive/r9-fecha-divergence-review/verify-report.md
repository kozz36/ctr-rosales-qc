# Verify Report — close-out-rev3b (r8 + r9 + r10)

**Change set**: `r8-material-matching`, `r9-fecha-divergence-review`, `r10-containerized-verification`
**Phase**: sdd-verify (static + green-test confirmation) · **Branch**: `feat/rev2-identity-domain`
**Date**: 2026-06-02 · **Store**: hybrid (engram + this file)

---

## Verdict: PASS-WITH-WARNINGS (with 1 CRITICAL test-suite regression to fix before archive)

| Severity | Count |
|----------|-------|
| CRITICAL | 1 |
| WARNING  | 4 |
| SUGGESTION | 3 |

Executive summary: implementation faithfully encodes the R8/R9/R10 specs and all domain
invariants; the prior r8 W1 (`fecha` in the group key) is now RESOLVED by r9. One real test
regression was found (r10 broke an r9 gate assertion) and the four known-open issues
(KI-1..KI-4) are confirmed present and deferred to judgment-day.

---

## Test results (commands actually run, real exit status)

| Suite | Command | Result |
|-------|---------|--------|
| Backend unit (per-dir) | `cd backend && uv run pytest tests/unit/domain tests/unit/application tests/unit/adapters tests/unit/infrastructure` | **855 passed**, 0 failed, exit 0 |
| Backend R8+R9 gates | `uv run pytest tests/integration/test_pipeline_r8_gate.py tests/integration/test_pipeline_r9_gate.py` | **20 passed, 1 FAILED, 9 skipped** (PDF-gated), exit 1 |
| Frontend | `cd frontend && npm test` | **188 passed** (17 files), exit 0 |

- The monolithic `uv run pytest -q` HANGS (paddle import in collection of e2e modules) — killed
  after ~10 min at 99% CPU. Per-directory is the correct path (documented in r9/r10 apply-progress).
  The claimed "~766" backend count is now **855** (grew across r9/r10); claim was stale-low, not wrong.
- Frontend **188** matches the claim exactly.
- The real-PDF / cloud-vision e2e (R8/R9 item 4, R10.10) was NOT run — gated to judgment-day,
  blocked by external cloud/SUNAT throttling. Correct per scope.

---

## CRITICAL

### C1 — r10 broke an r9 gate test; the suite is RED, not green as claimed
- **Evidence**: `tests/integration/test_pipeline_r9_gate.py:141`
  `TestR9PipelineConfigGate::test_protocolo_crop_present_and_disabled_by_default` asserts
  `cfg.vision.protocolo_crop.enabled is False`. r10 task R10.5 + the R10.9 calibration changed the
  default to an ENABLED box `(0.60, 0.14, 1.00, 0.22)` at `config.py:104-106`, so the assertion now
  fails (`assert True is False`).
- **Why undetected**: r10 apply ran only the four unit dirs ("817 passed, all directories green");
  it never re-ran the r9 *integration* gate, so the cross-slice regression slipped through.
- **Operational impact**: at runtime nothing breaks — the enabled crop is the *correct* calibrated
  production value (R9.5's disabled zero-box was a conservative placeholder; R10.9 tuned it and proved
  `2026-05-28 @ conf=1.00`). The damage is purely that the committed test suite is RED, which
  invalidates the "766+188 green" gate the close-out depends on.
- **Fix (1 line)**: update the r9 gate test to assert the calibrated box is now enabled
  (`protocolo_crop.enabled is True` with the `(0.60,0.14,1.00,0.22)` coordinates), or rename the test
  to `test_protocolo_crop_present_and_calibrated`. Do NOT revert config — the enabled crop is correct.

---

## WARNINGS

### W1 (KI-1) — Vision cap raises instead of degrading gracefully
- **Evidence**: `pipeline.py:1078, 1092, 1109` — `raise VisionCapExceededError` on cap exhaustion in
  both batch and sequential paths. Sequential path builds guías for processed blocks (1090-1091) then
  still raises, aborting the run.
- **Impact**: hitting `vision.max_vision_calls` aborts the whole pipeline instead of stopping vision
  calls and leaving remaining `fecha=None` (already a valid, flag-able state per FDR-006). A large PDF
  that exceeds the cap fails the run rather than producing a partial-but-usable reconciliation.
- **Fix**: convert the raise into graceful degradation — stop issuing vision calls, leave unread guías
  with `fecha=None` + a `requires_review`/`vision_cap_reached` warning, and let reconciliation complete.
- **Status**: deferred to judgment-day fix phase (KI-1).

### W2 (KI-2) — No inter-call pacing on cloud vision (asymmetric vs SUNAT)
- **Evidence**: `adapters/vision/openai_compatible.py` has NO per-call pacing — the only `sleep` is
  OpenAI-batch polling (`:414`). SUNAT, by contrast, paces every download via `_pace_request`
  (`descargaqr.py:369-376`, `_FETCH_PACING_S=0.5`) plus exponential backoff. Rapid sequential
  `qwen3.5:397b-cloud` calls have no throttle.
- **Impact**: bursty cloud-vision calls can trip provider rate limits / thinking-blowups (the very
  stall the per-call wall-clock deadline in commit 48bb268 was added to bound). No backoff means a
  429-equivalent is not smoothed.
- **Fix**: add a small inter-call pacing/backoff to the vision adapter mirroring the SUNAT pattern.
- **Status**: deferred to judgment-day (KI-2).

### W3 (KI-3) — SUNAT subset re-fetch timeouts under load; cross-run cache only via the named volume
- **Evidence**: `fetch_many` (`descargaqr.py:179-236`) bounds concurrency with `asyncio.Semaphore`
  and a best-effort N-shrink after 3 consecutive `None`s, but has no true 429 backoff between threads;
  retries+backoff live only in the per-URL sync `fetch()`. Cross-run cache exists ONLY when
  `SunatConfig.cache_dir` is set (R10.8 → the `/data/sunat-cache` named Docker volume); the default
  `cache_dir=None` is per-run only.
- **Impact**: outside the container (no mounted volume) repeated runs re-fetch from scratch; under
  load the subset re-fetch can intermittently time out. The N-shrink is a guard, not a guarantee.
- **Fix**: strengthen 429 backoff in the concurrent path; document the container volume as the only
  durable cross-run cache.
- **Status**: deferred to judgment-day (KI-3).

### W4 (carried from r8 #2787 W2) — export missing `requires_review` column
- **Evidence**: `xlsx_report.py` adds the `"Método"` (match_method) column but not a
  `requires_review` column. MAT-008 says BOTH fields MUST be in the export; MAT-S10 asserts each
  export row includes a `requires_review` column.
- **Impact**: the engineer's xlsx/csv export omits the review-needed flag; a reviewer working from
  the export alone cannot see which rows need human attention.
- **Fix**: add a `"Revisión"` column (Sí/"") or formally document the deviation in the spec.
- **Status**: open from r8 verify; not addressed by r9/r10.

---

## SUGGESTIONS

- S1 (KI-4 process gap) — The full faithful e2e (R8 MATCH #4252=4.124 TN + R9 Registro-232
  divergence, R10.10) has NEVER been captured. R10.10 and R10.11 are unchecked `[ ]` in r10
  tasks.md and explicitly listed as remaining in apply-progress #2808. This is a process gap, not a
  code defect: the smoke gate R10.9 passed (crop calibrated, 2026-05-28 @ conf=1.00, 646-717 tokens),
  but the end-to-end gate that proves the whole pipeline is the trusted gate per CLAUDE.md and is
  still pending a non-throttled cloud/SUNAT window. Run `make verify` before final archive/push.

- S2 (from r8 #2787 S1) — `ReconciliationRowResponse.match_method` Literal omits `"codigo_sunat"`
  (schemas.py) while domain `MatchMethod` reserves it; a future `codigo_sunat` row would fail Pydantic
  validation at the API boundary. Keep the literals in sync or comment.

- S3 (from r8 #2787 S2) — Hallucination guard validates `diametro` + `presentacion` only; LLM `grado`
  and `familia` are accepted verbatim (mitigated by `requires_review=True`). Consider validating
  `grado` against the known collapse set.

---

## Requirement coverage (req → impl → evidence)

### R8 (MAT-001..MAT-013) — all IMPLEMENTED
- MAT-001 → `_GroupKey(registro, material_canonical, unidad)` — **`fecha` REMOVED** (reconciliation.py:44-51,
  94-98). **Prior r8 W1 RESOLVED by r9 commit e3ed8c5.** This is the single most important re-confirmation.
- MAT-002..MAT-005 → `material_key.py` CanonicalKey VO (frozen, 9M≠DOB), `material_key_normalizer.py`
  (grade collapse, compound-first diameter, 9M/DOB-or-None) — verified.
- MAT-006/007/012 → `MaterialInferencePort` (pure-domain Protocol), `OllamaMaterialInferenceAdapter`
  (lazy `openai` import, temp=0, `<think>` strip, graceful None) — verified.
- MAT-008 → `match_method` worst-wins aggregation (reconciliation.py:182-201) → schema/route/xlsx.
  Export PARTIAL (W4: no `requires_review` column).
- MAT-009/010 → normaliser pure, module-level regex; `unidad` outside CanonicalKey, separate axis,
  never converted — verified.
- MAT-011 → MATCH/MISMATCH/DECLARED_MISSING/GUIA_MISSING with EXACT(0) (`status = "MATCH" if delta ==
  Decimal(0) else "MISMATCH"`, reconciliation.py:246) — verified.
- MAT-013 → #4252 deterministic MATCH 4.124 TN — `test_pipeline_r8_gate.py` (passed; real-PDF item gated).

### R9 (FDR-001..FDR-011) — all IMPLEMENTED
- FDR-001/002 → handwritten Protocolo date via `VisionLLMPort`; `Registro.fecha_authoritative`
  (handwritten-first, electronic fallback); bounded year inference. Display fecha now sourced from
  `fecha_authoritative` (reconciliation.py:101). Verified.
- FDR-003 → `date_divergence.check_fecha_divergence` compares `(month, day)` only, tol 0, either-None
  → False (date_divergence.py:39-52). Year-only ≠ divergence. Verified.
- FDR-004/011 → divergence is a post-grouping side-channel; `requires_review` OR-only; group key,
  status, delta, summed_qty untouched (reconciliation.py:214 comment + code). Verified.
- FDR-005/007 → null / `< 0.85` confidence declared date → `fecha_declarada_handwritten=None`, registro
  flagged, no per-guía false divergence (pipeline.py:1186-1208; confidence locked 0.85, config.py:239). Verified.
- FDR-006 → null guía date → not divergent (predicate null-safety). Verified.
- FDR-008 → API: `GuiaContributionResponse.{fecha,fecha_divergence,divergence_reason}` +
  `ReconciliationRowResponse.has_fecha_divergence` + `source_pages`. Verified.
- FDR-009 → frontend RED highlight: `GuiaDrillDown.vue` `--divergent` class + `FechaDivergenceBadge`
  per guía (:22,:93); `ReconciliationRow.vue` group badge on `has_fecha_divergence` (:100). Verified.
- FDR-010 → `date_divergence.py` pure (stdlib datetime/typing/dataclasses only); no new port. Verified.

### R10 (CONT-001..CONT-008) — IMPLEMENTED through R10.9; R10.10/R10.11 OPEN
- CONT-001/002 → paddle-free multi-stage Dockerfile, `uv --frozen`, non-root, baked paddle-absence
  assertion (Dockerfile:56-58). Verified by build (apply-progress) + static read.
- CONT-003/005 (CONT-S05 interpretation) → vision cloud is config-only; **r10 itself touches NO domain
  file** (its commits hit config.py/openai_compatible.py/descargaqr.py/container.py/pipeline.py). The
  `git diff main -- domain/` shows large deltas, but those are the cumulative r8+r9 changes on the
  branch, NOT r10. CONT-S05 holds for the r10 change in isolation. Token metering `_TokenMeter` added.
- CONT-004 → `ocr.enabled=false` → `NullOcrExtractor`, paddle never imported (container.py:372-385). Verified.
- CONT-006 → `fetch_many` bounded concurrency + N-shrink + per-URL backoff + `cache_dir` cross-run cache
  (see W3 for the gaps). Implemented.
- CONT-007 (R8+R9 in-container gate) → **NOT captured** — R10.10 open (KI-4 / S1).
- CONT-008 → air-gap default preserved (sunat off / provider default); cloud is opt-in compose env. Verified.

---

## Domain invariants (from skills) — all ENCODED
- Group by `(registro, material_canonical, unidad)`, `fecha` NOT an axis — `_GroupKey` (reconciliation.py:44-51). ✓
- Units summed independently, never converted — `unidad` outside CanonicalKey, separate axis. ✓
- MATCH tolerance EXACT(0) — `delta == Decimal(0)` (reconciliation.py:246). ✓
- Confidence auto-flag 0.85 — `ConfidenceConfig.threshold` frozen at 0.85 (config.py:239-246). ✓
- Reception date = handwritten Protocolo date — `fecha_authoritative` handwritten-first (models.py / reconciliation.py:101). ✓
- Day-month divergence → non-blocking WARNING, `requires_review` + page number — `check_fecha_divergence`
  + side-channel wiring + `source_pages` in DTO. ✓
- Domain purity — zero heavy-SDK imports in `domain/`; `openai` lazy-imported only in the adapter. ✓

---

## KI confirmation summary

| KI | Confirmed | Severity | Evidence |
|----|-----------|----------|----------|
| KI-1 vision cap raises (no graceful degrade) | YES | WARNING (W1) | pipeline.py:1078,1092,1109 |
| KI-2 no inter-call cloud-vision pacing | YES | WARNING (W2) | openai_compatible.py (no per-call sleep) vs descargaqr.py:369-376 |
| KI-3 SUNAT re-fetch timeout / cache only via volume | YES | WARNING (W3) | descargaqr.py:179-236 (no thread-level 429 backoff), R10.8 cache_dir |
| KI-4 full e2e never captured | YES | SUGGESTION (S1) | r10 tasks.md R10.10/R10.11 `[ ]`; apply-progress #2808 |

---

## Next recommended
`sdd-archive` is BLOCKED until **C1** (the RED r9 gate test) is fixed — the green-test gate the
close-out claims does not currently hold. Recommended path: **judgment-day** (fixes C1 + KI-1..KI-4),
then re-run the per-directory unit suite + r8/r9 gates green, then `make verify` (R10.10 full e2e),
then `sdd-archive`, then push.
