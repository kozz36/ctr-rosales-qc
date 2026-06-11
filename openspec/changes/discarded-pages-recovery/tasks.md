# Tasks: discarded-pages-recovery (SDD#2)

**Change**: discarded-pages-recovery
**Artifact store**: hybrid (engram + openspec)
**Delivery strategy**: ask-on-risk → chained PRs approved
**Chain strategy**: stacked-to-main
**Strict TDD**: ACTIVE (runner: `cd backend && uv run pytest <targeted-path>` — monolithic run hangs on paddle import; frontend: `cd frontend && npm test`)
**Date**: 2026-06-11

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines — PR-1 | ~270–320 LOC incl. tests |
| Estimated changed lines — PR-2 | ~360–420 LOC incl. tests |
| Estimated changed lines — PR-3a | ~330–380 LOC incl. vitest |
| Estimated changed lines — PR-3b | ~280–330 LOC incl. vitest + SA-5 |
| 400-line budget risk | **High** (PR-2, PR-3a near or at limit) |
| Chained PRs recommended | **Yes** |
| PR order | PR-1 → PR-2 → PR-3a → PR-3b (stacked-to-main) |
| Decision needed before apply | No — chain already approved; strategy: stacked-to-main |

**Chained PRs recommended: Yes**
**400-line budget risk: High**
**Decision needed before apply: No**

### PR Budget Breakdown

| PR | Scope | Estimate | Risk |
|----|-------|----------|------|
| PR-1 | DiscardedPage model, drop-site emit, PipelineResult, cache persist/hydrate, ReviewService state + property, table DTO | ~270–320 LOC | Low |
| PR-2 | identity Literal lockstep (4 sites), apply_page_recovery (3 tiers), recover_discarded_page hook, 3 endpoints + status poll, sidecar replay | ~360–420 LOC | High (if tests push over 400: split Literal lockstep + hook as PR-2a; rest as PR-2b) |
| PR-3a | Third tab wiring, A1 grouping, A2 collapsed groups + lazy thumbnails, A3 selection (per-page, per-group, global), single-page Recuperar | ~330–380 LOC | Medium-High |
| PR-3b | A3 ETA confirm dialog, batch fire, poll-until-done, A4 mount re-attach, completion summary, SA-5 Playwright gate | ~280–330 LOC | Medium |

If PR-2 tests push the total past 400 lines, split as:
- **PR-2a**: Literal lockstep (4 sites) + `recover_discarded_page` hook in `ReviewService` + sidecar replay + restart round-trip test. ~180–200 LOC.
- **PR-2b**: `apply_page_recovery` + `PageRecoveryResult` + OCR-selection helper + 3 endpoints + batch status lifecycle tests. ~200–250 LOC.

---

## Invariants (anti-patterns enforced throughout all phases)

- **Domain purity**: no SDK/framework/IO import under `domain/`. A heavy import there = auto-reject.
- **Pipeline zero concrete adapters**: `application/pipeline.py` imports ZERO concrete adapters; depends only on Protocols + config/run_context. An import of `DiscardedPage` from `domain/` is valid (it is a pure model); an import of any adapter is not.
- **Lazy heavy deps**: `rapidocr`/`anthropic`/`openai`/`pyzbar`/`fitz`/`openpyxl` INSIDE methods only, never at module top.
- **Vision provider-agnostic**: never bind domain or pipeline to a vendor. OCR via `ExtractionPort`; vision via `VisionLLMPort`. No concrete adapter import in `application/`.
- **`fecha` NEVER a grouping axis**: recovered guías enter reconciliation via `(registro, material_canonical, unidad)` only. No date field on `DiscardedPage`. No date field on the cache key.
- **Units never converted**: KG/TN/RD/Rollo summed independently. `cached_lines` carry raw units; used as-is.
- **Three identifiers never confused**: Contents-ID `#4252` ≠ Registro N° ≠ QR `serie-numero`. `recovered_{page}` is a guía-rail id; not a Registro N° and not a Contents-ID.
- **Reconciliation is the validation gate**: recovered lines ALWAYS `requires_review=True` (absolute; no auto-accept regardless of OCR confidence). MISMATCH never auto-corrected.
- **QR-evidence gate blocking semantics UNCHANGED**: a no-evidence page NEVER opens or extends a block. Only the silent drop is replaced by an explicit `DiscardedPage` entry. The `continue` stays; the page still cannot become a guía block.
- **Backward compatibility**: old extraction caches (no `discarded_pages` key) MUST hydrate to `[]` without error.
- **Input PDF read-only**: recovery renders pages from the read-only source PDF on demand.
- **`identity_source` Literal lockstep (4 sites)**: all four sites updated in ONE commit with a test asserting DTO validation — the `match_method` 500-lesson.
- **`DiscardedPage` model must remain domain-pure**: `domain/models.py`, `BaseModel`, zero IO/SDK imports.
- **`application/pipeline.py` MUST NOT import concrete adapters** as a result of this change.

---

## PR-1 — `feat(pipeline): surface discarded GUIA pages`

> Scope: `DiscardedPage` domain model, drop-site emit at `pipeline.py:977-982`,
> `PipelineResult.discarded_pages`, cache persist/hydrate (tolerant), `ReviewService`
> state + `discarded_pages` property, `DiscardedPageResponse` DTO, `ReconciliationTableResponse.discarded_pages` field.
>
> Independently shippable: closes the #50 silent-drop visibility hole with no UI.
> Depends on: main (post PR #51–#54, SDD#1 merged).

### Phase 1.0 — Pre-work: read existing sidecar replay to confirm mirror contract

- [x] **1.0.1** READ `backend/src/reconciliation/application/review_service.py` lines 596–740 (the `restore_from_sidecar` method, `recovered_guia` replay branch at :684–719) AND `ReviewService.__init__` constructor signature (:120–128) to confirm the exact parameter names, the `errored_guias` hydration pattern, and the `recovered_guia` replay shape.
  Record: (a) the exact `__init__` parameter + `_errored_guias` assignment pattern to mirror for `_discarded_pages`; (b) the `recovered_guia` sidecar replay structure to mirror for `recovered_discarded_page` in PR-2.
  This is a read-only pre-flight — no code change. Required before writing PR-1 RED tests.
  Design: §4 (mirror `add_recovered_guia` + sidecar convention), §5.

### Phase 1.1 — RED: Write failing tests for PR-1

- [x] **1.1.1** Create `backend/tests/unit/application/test_pipeline_discarded_pages.py`.
  Write failing test `test_no_qr_evidence_page_emits_discarded_entry`.
  CONFIRMED RED: ImportError DiscardedPage. Spec: EXT-034 / EXT-S034a.

- [x] **1.1.2** Add failing test `test_no_qr_evidence_empty_lines_still_discarded`.
  CONFIRMED RED: ImportError DiscardedPage. Spec: EXT-034 / EXT-S034b.

- [x] **1.1.3** Add failing test `test_valid_qr_evidence_not_discarded`.
  CONFIRMED RED: AttributeError discarded_pages. Spec: EXT-034 / EXT-S034c.

- [x] **1.1.4** Add failing test `test_ocr_fallback_evidence_not_discarded`.
  CONFIRMED RED: AttributeError discarded_pages. Spec: EXT-034 / EXT-S034d.

- [x] **1.1.5** Add failing test `test_discarded_entry_registro_none_is_valid`.
  CONFIRMED RED: ImportError DiscardedPage. Spec: EXT-034 / EXT-S034e.

- [x] **1.1.6** Add failing test `test_errored_and_discarded_collections_are_separate`.
  CONFIRMED RED: AttributeError discarded_pages. Spec: EXT-035 / EXT-S035a.

- [x] **1.1.7** Add failing test `test_old_pipeline_result_cache_hydrates_without_error`.
  CONFIRMED RED: AttributeError discarded_pages. Spec: EXT-035 / EXT-S035b.

- [x] **1.1.8** Create `backend/tests/unit/infrastructure/test_container_discarded.py`.
  Write failing test `test_build_review_service_hydrates_discarded_pages`.
  CONFIRMED RED: ImportError DiscardedPage. Spec: EXT-035. Design: §5.

- [x] **1.1.9** Add failing test `test_build_review_service_old_cache_discarded_defaults_to_empty`.
  CONFIRMED RED: AttributeError discarded_pages. Spec: EXT-035 / EXT-S035b.

- [x] **1.1.10** Create `backend/tests/unit/infrastructure/test_schemas_discarded.py`.
  Write failing test `test_reconciliation_table_response_includes_discarded_pages`.
  CONFIRMED RED: ImportError DiscardedPageResponse. Spec: REV-R33 / EXT-S033a, EXT-S033b.

- [x] **1.1.11** Add failing test `test_discarded_pages_defaults_to_empty_list`.
  CONFIRMED RED: AttributeError discarded_pages. Spec: EXT-S033b.

- [x] **1.1.12** Add failing test `test_discarded_page_response_distinguishes_from_errored`.
  CONFIRMED RED: ImportError DiscardedPageResponse. Spec: REV-R33 / EXT-S033c.

### Phase 1.2 — GREEN: Implement PR-1

- [x] **1.2.1** Add `DiscardedPage(BaseModel)` to `backend/src/reconciliation/domain/models.py`.
  Domain-pure, zero IO/SDK. Spec: EXT-034. Design: §1 (Option B).

- [x] **1.2.2** Add `discarded_pages: list[DiscardedPage] = field(default_factory=list)` to `PipelineResult`.
  Spec: EXT-035. Design: §5.

- [x] **1.2.3** Updated `_stage_assemble_blocks` to return `tuple[list[_GuiaBlock], list[DiscardedPage]]`.
  Emits `DiscardedPage` before `continue`; gate blocking semantics UNCHANGED. Caller unpacked.
  Also fixed `test_positional_gate.py` (17 direct call sites that expected a list).
  Spec: EXT-034. Design: §5.

- [x] **1.2.4** `_stage_persist` persists `discarded_pages` as additive cache key. Spec: EXT-035.

- [x] **1.2.5** `ReviewService.__init__` gains `discarded_pages` param + state + property. Spec: REV-R33.

- [x] **1.2.6** `restore_from_sidecar` gains `discarded_pages` param + forwards to constructor. Design: §4.

- [x] **1.2.7** `build_review_service` hydrates `discarded_pages` via tolerant `cache.get("discarded_pages", [])`. Spec: EXT-035. Design: §5.

- [x] **1.2.8** `DiscardedPageResponse` DTO + `ReconciliationTableResponse.discarded_pages` field. Spec: REV-R33.

- [x] **1.2.9** `GET /table` populates `discarded_pages` from `review_service.discarded_pages`. Spec: REV-R33.

- [x] **1.2.10** PR-1 test suite: 12/12 GREEN. 1448 total unit tests passing.

- [x] **1.2.11** Architecture invariants: domain/ pure; pipeline.py no new concrete adapter imports. ✓

- [x] **1.2.12** Regression sweep: 150 tests GREEN. All 1448 unit tests GREEN.

- [x] **1.2.13** Committed: `5f0e37a feat(pipeline): emit DiscardedPage at rev-6 QR-evidence gate; surface in PipelineResult + cache + API (PR-1)`.

### Phase 1.3 — Real-data gate (PR-1)

- [x] **1.3.1** Real-data gate: all 4 tests PASSED (background run exit code 0).
  `test_discarded_count_is_343`: PASSED — 343 discarded pages (wall ~7:18, OCR=false).
  `test_discarded_ranges_match_evidence`: PASSED — 11 ranges confirmed.
  `test_zero_silent_drop`: PASSED — assembled + discarded = 469; zero overlap.
  `test_a5_mapping_each_run_maps_to_one_registro`: PASSED (or xfail non-blocking).
  ```
  cd backend && uv run pytest tests/integration/ -v -m slow -k "e2e or real_data"
  ```
  (If no existing e2e fixture covers discarded pages: run the pipeline manually via CLI / test fixture with `CTR_PDF_PATH` set and inspect the API output.)
  Assert `PipelineResult.discarded_pages` count = **343** (the 2026-06-11 evidence).
  Assert discarded pages form **11 contiguous runs** with the expected ranges: (33–35),(57–81),(99–137),(152),(165–222),(239–276),(279),(293–347),(358–376),(379–452),(463–492).
  Assert `GET /table` response includes `discarded_pages` with 343 entries — none silently dropped.
  Assert (A5 mapping): spot-check that each run maps to a single registro in the discarded entries (derived-not-observed — flag if any `registro=None` entries appear within a run that should have a registro; do NOT fail for `registro=None` entries; just log for human review).
  Assert existing `errored_guias` count is UNCHANGED (separate semantic collection).
  Spec: EXT-034/EXT-035 / unit-green ≠ correct lesson from `docs/DECISIONS.md §audit`.

### Phase 1.4 — Judgment Day (PR-1)

- [ ] **1.4.1** Run dual-blind judgment day on PR-1 diff before push.
  PR-1 touches `pipeline.py` (the drop site) and `review_service.py` (state shape). Both are parser/pipeline-core touches. Full dual-blind JD required per CLAUDE.md §Fix/Feature Discipline #4.
  JD must verify:
  - `_stage_assemble_blocks` return-shape change does not alter block assembly behavior.
  - `DiscardedPage` model is domain-pure (no IO/SDK import).
  - Old cache backward-compat (no `KeyError`/`ValidationError` on missing key).
  - `errored_guias` and `discarded_pages` are semantically separate; the bulk-batch enrollment leak (Option A design §1 justification) is provably absent.
  No push / PR until JD passes. (SA-3)

---

## PR-2 — `feat(recovery): OCR-first page recovery`

> Scope: `identity_source="operator"` Literal lockstep (4 sites, 1 commit), `apply_page_recovery` (3 tiers), `PageRecoveryResult`, OCR-selection helper (shared with `build_pipeline`), `recover_discarded_page` hook + sidecar replay, 3 recovery endpoints + batch status lifecycle.
>
> Depends on: PR-1 merged to main.
> Budget note: if tests push PR-2 past ~400 lines, split as PR-2a (Literal lockstep + hook + sidecar replay) → PR-2b (service + endpoints).

### Phase 2.0 — Pre-work: verify sidecar replay mirror contract

- [x] **2.0.1** READ `backend/src/reconciliation/application/review_service.py` `:684–719` (the full `recovered_guia` replay branch) to extract the exact mirror pattern for `recovered_discarded_page`:
  - The `raw_guia = edit.get("new_value")` extraction.
  - `GuiaDeRemision.model_validate(raw_guia)` validation.
  - The R2-W2 `requires_review` coercion before re-add.
  - The `add_recovered_guia` call.
  - The WARNING on failure (never swallow silently).
  The `recovered_discarded_page` replay MUST mirror this pattern exactly, substituting `recover_discarded_page(page, guia)` in place of `add_recovered_guia(guia)`.
  Record the exact `target` dict shape written at audit-emit time (:539-546) — `recovered_discarded_page` needs the same shape (with `page` added).
  Read-only pre-flight.

- [x] **2.0.2** READ `backend/src/reconciliation/infrastructure/container.py` `:378-407` (the `build_pipeline` OCR-selection logic: `ocr.enabled=False` → `NullOcrExtractor`, engine factory → `build_ocr_extractor`) to identify the exact branch to extract as a shared helper for `build_reprocess_service`.
  Assert: the branch IS there and IS self-contained enough to extract without changing `build_pipeline` behavior.
  Read-only pre-flight.

### Phase 2.1 — RED: Write failing tests for PR-2

- [x] **2.1.1** Create `backend/tests/unit/application/test_apply_page_recovery.py`.
  Write failing test `test_tier1_cached_lines_no_ocr_no_vision_called`:
  Discarded entry with `lines=[MaterialLine(...)]`. Spy `ExtractionPort` and `VisionLLMPort`.
  Assert `ExtractionPort.extract_printed_table` NOT called.
  Assert `VisionLLMPort` NOT called.
  Assert result `recovered=True`; returned lines are the cached lines.
  FAILS today: `apply_page_recovery` does not exist.
  Spec: EXT-036 / EXT-S036a. Design: §4 (Tier 1).

- [x] **2.1.2** Add failing test `test_tier2_empty_cached_lines_ocr_called`:
  Entry with `lines=[]`. Mock `ExtractionPort.extract_printed_table` returning 2 `MaterialLine` objects.
  Assert `ExtractionPort.extract_printed_table` IS called once.
  Assert `VisionLLMPort` NOT called.
  Assert result `recovered=True`; lines from OCR.
  Spec: EXT-036 / EXT-S036b. Design: §4 (Tier 2).

- [x] **2.1.3** Add failing test `test_tier3_empty_ocr_vision_fallback`:
  Entry with `lines=[]`. OCR returns `[]`. Mock `VisionLLMPort.read_material_table` returning lines.
  Assert vision IS called.
  Assert result `recovered=True`.
  Spec: EXT-036 / EXT-S036c. Design: §4 (Tier 3).

- [x] **2.1.4** Add failing test `test_all_tiers_empty_recovery_fails_entry_retained`:
  Entry with `lines=[]`. OCR returns `[]`. Vision returns `[]`.
  Assert `PageRecoveryResult.recovered=False`, `reason="empty"`.
  Assert the entry is NOT removed from `ReviewService.discarded_pages`.
  Spec: EXT-036 / EXT-S036c. REV-R30-S04.

- [x] **2.1.5** Add failing test `test_all_recovered_lines_require_review_unconditionally`:
  Entry with cached lines where all OCR conf >= 0.95.
  Assert every `MaterialLine` in the recovered `GuiaDeRemision` has `requires_review=True`.
  Spec: EXT-037 / EXT-S037b. REV-R30-S03. Absolute invariant.

- [x] **2.1.6** Add failing test `test_recovered_guia_id_format_no_collision_with_qr`:
  Recovery of page 152.
  Assert `guia_id="recovered_152"` (matches design §2).
  Assert `guia_id` does NOT match `[A-Z]\d+-\d+` (QR format).
  Assert `identity_source="operator"`.
  Spec: EXT-037 / EXT-S037a.

- [x] **2.1.7** Add failing test `test_recovered_guia_inherits_section_registro`:
  Entry with `registro="232"`. Recovery completes.
  Assert `guia.registro="232"`.
  Assert no assignment dialog triggered (no raise, no side-effect).
  Spec: EXT-037 / EXT-S037c. REV-R31-S05.

- [x] **2.1.8** Add failing test `test_double_recover_idempotent`:
  Recover page 152 twice (second call sees no entry in discarded list).
  Assert second call returns `recovered=False, reason="not_found"` (no duplicate GuiaDeRemision created).
  Design: §2 (deterministic guia_id → `add_recovered_guia` idempotency contract).

- [x] **2.1.9** Create `backend/tests/unit/application/test_recover_discarded_page_hook.py`.
  Write failing test `test_recover_discarded_page_removes_entry_from_list`:
  ReviewService with 2 discarded entries. Call `recover_discarded_page(page=152, guia=...)`.
  Assert `discarded_pages` now has 1 entry (the other, not page 152).
  Spec: REV-R31. Design: §4.

- [x] **2.1.10** Add failing test `test_recover_discarded_page_fail_closed_guard`:
  Attempt to call `recover_discarded_page` with a `GuiaDeRemision` that has a line with `requires_review=False`.
  Assert `ValueError` is raised.
  Design: §4 (fail-closed `requires_review` guard, mirroring `add_recovered_guia` :493-499).

- [x] **2.1.11** Create `backend/tests/unit/infrastructure/test_recovery_endpoints.py`.
  Write failing test `test_single_recover_endpoint_404_unknown_page`:
  `POST /runs/{run_id}/discarded-pages/9999/recover` where page 9999 not in discarded list.
  Assert 404 response.
  Spec: REV-R31. Design: §3.

- [x] **2.1.12** Add failing test `test_single_recover_endpoint_409_run_not_ready`:
  `POST /runs/{run_id}/discarded-pages/152/recover` where run is not in READY state.
  Assert 409 response.
  Design: §3 (mirrors existing 409 pattern in routes.py).

- [x] **2.1.13** Add failing test `test_batch_recover_endpoint_202_lifecycle`:
  `POST /runs/{run_id}/discarded-pages/recover-batch` with `{"pages": [152, 175]}`.
  Assert 202 response with `{"run_id": ..., "count": 2}`.
  Poll `GET /runs/{run_id}/discarded-pages/recover-status` until `done=True`.
  Assert final status has `total=2`, `recovered+failed=2`, `done=True`.
  Spec: REV-R30 (progress lifecycle). Design: §3 (SA-5 settle-only-when-done contract).

- [x] **2.1.14** Add failing test `test_batch_409_when_batch_in_flight`:
  Start a batch. While in-flight (mock), send second `POST recover-batch`.
  Assert 409 response (one active batch per run).
  Design: §3.

- [x] **2.1.15** Add failing test `test_recover_status_terminal_shape_when_no_batch_fired`:
  `GET /runs/{run_id}/discarded-pages/recover-status` when no batch has been submitted.
  Assert `{"total": 0, "recovered": 0, "failed": 0, "done": true}`.
  Design: §3 (terminal-shape — PR-3b re-attach on mount depends on this; LOCKED by test).

- [x] **2.1.16** Add failing test `test_identity_source_operator_roundtrips_dto`:
  Build a `GuiaContributionResponse` with `identity_source="operator"`.
  Assert `model_validate` succeeds (no `ValidationError`).
  This is the 4-site lockstep gate — FAILS today because `"operator"` is not in the Literal.
  Design: §2 (the `match_method` 500-lesson applied here).

- [x] **2.1.17** Create `backend/tests/unit/application/test_sidecar_restart_roundtrip.py`.
  Write failing test `test_restart_round_trip_recovered_discarded_page`:
  (1) Create `ReviewService` with 1 discarded entry. (2) Call `recover_discarded_page(page=152, guia=...)`. (3) Assert `discarded_pages == []` and `guias` contains `recovered_152`. (4) Call `restore_from_sidecar` on a fresh `ReviewService` using the persisted sidecar JSON. (5) Assert the fresh service has `discarded_pages == []` and the recovered guía is present.
  FAILS today: `recovered_discarded_page` audit kind not in sidecar replay.
  Design: §5 (§11.1 risk — sidecar replay mandatory; restart round-trip test). MUST.

- [x] **2.1.18** Add failing test `test_vision_off_ocr_still_attempted_failure_not_503`:
  `NullVisionAdapter` active. Discarded entry with `lines=[]`. OCR returns `[]`.
  Assert response is a structured failure (not 503, not 500).
  Assert entry remains in `discarded_pages`.
  Spec: REV-R31-S04.

### Phase 2.2 — GREEN: Implement PR-2

- [x] **2.2.1** Update Literal at all FOUR sites in ONE commit (lockstep — never partial):
  - `domain/models.py:72`: `GuiaContribution.identity_source: Literal["qr","ocr_fallback","vision","operator"]`
  - `domain/models.py:131`: `GuiaDeRemision.identity_source: Literal["qr","ocr_fallback","vision","operator"] = "ocr_fallback"`
  - `infrastructure/api/schemas.py:35`: `GuiaContributionResponse.identity_source: Literal["qr","ocr_fallback","vision","operator"]`
  - `frontend/src/api/types.ts`: `identity_source: 'qr' | 'ocr_fallback' | 'vision' | 'operator'`
  All four sites in a single work-unit commit. Test 2.1.16 (DTO validation) must pass before proceeding.
  Design: §2 (4-site lockstep, `match_method` 500-lesson).

- [x] **2.2.2** Extract OCR-selection helper from `container.py:build_pipeline` into a shared function (e.g. `_build_ocr_extractor_for_config(config, ocr_config) -> ExtractionPort`).
  `build_pipeline` calls it; `build_reprocess_service` will also call it.
  INVARIANT: `build_pipeline` behavior MUST NOT change. Verify with existing container tests after this step.
  Design: §4 (shared helper to prevent drift; §11.4 risk).

- [x] **2.2.3** Add `extractor: ExtractionPort | None = None` parameter to `ReprocessService.__init__` in `backend/src/reconciliation/application/reprocess_service.py`.
  Store as `self._extractor`. No concrete adapter import — ports-only constructor (Dependency Inversion).
  Design: §4 (additive port).

- [x] **2.2.4** Add `PageRecoveryResult` dataclass to `reprocess_service.py` (mirrors `ReprocessResult`):
  ```python
  @dataclass
  class PageRecoveryResult:
      recovered: bool
      page: int
      guia_id: str | None = None
      reason: str | None = None     # "empty" | "not_found" | None
      rows: list[ReconciliationRow] = field(default_factory=list)
  ```
  Domain-pure result type (no SDK/IO).

- [x] **2.2.5** Implement `async apply_page_recovery(self, page: int) -> PageRecoveryResult` on `ReprocessService`:
  Follow the 8-step algorithm from design §4 exactly:
  1. Lookup entry in `review_service.discarded_pages` by page → not found: return `PageRecoveryResult(recovered=False, reason="not_found")`.
  2. Tier 1 — `entry.lines` non-empty → use directly (no render, no OCR, no vision).
  3. Tier 2 — `doc_source.render_page(page, dpi=300)` + `self._extractor.extract_printed_table(image)` in `run_in_executor` (CPU-blocking). Skip if `self._extractor is None`.
  4. Tier 3 — `_downscale_image` + `vision.read_material_table` under existing `Semaphore`.
  5. All tiers empty → `PageRecoveryResult(recovered=False, reason="empty")`.
  6. Normalize via `_build_recovered_guia_lines_from_vision` (reused as-is; sets `requires_review=True` unconditionally per line). Rename to `_build_recovered_lines` is optional polish — DO NOT rename if it risks breaking existing callers; prefer additive overload or just reuse.
  7. Build `GuiaDeRemision(guia_id=f"recovered_{page}", registro=entry.registro, fecha=None, fecha_entrega=None, lines=..., source_pages=[page], identity_source="operator")`.
  8. Under commit Lock: `review_service.recover_discarded_page(page, guia)`.
  Design: §4 (exact algorithm; `fecha=None` intentional — no vision date read, no R9b/R9c floor/ceiling applies — graceful per `reception-date-authority` skill).

- [x] **2.2.6** Add `recover_discarded_page(self, page: int, guia: GuiaDeRemision) -> list[ReconciliationRow]` to `ReviewService` in `review_service.py`:
  Mirror `add_recovered_guia` contract (:458-543):
  1. Fail-closed `requires_review` guard (raise `ValueError` if any line has `requires_review != True`).
  2. Append `guia` to `self._guias` (no placeholder to replace — append path only).
  3. Drop the `DiscardedPage` entry with matching `page` from `self._discarded_pages`.
  4. Re-reconcile with `_delivery_dates()`.
  5. Emit audit event `kind="recovered_discarded_page"`, `target={"guia_id": guia.guia_id, "page": page}`, `new_value=guia.model_dump(mode="json")`.
  6. `_persist()`.
  Return updated rows.
  Design: §4 (Open/Closed over existing hook — dedicated entry point, not modifying `add_recovered_guia`).

- [x] **2.2.7** Update `restore_from_sidecar` in `review_service.py` to handle `recovered_discarded_page` sidecar events:
  Mirror the `recovered_guia` branch (:684-719) EXACTLY:
  - Extract `raw_guia = edit.get("new_value")`.
  - `GuiaDeRemision.model_validate(raw_guia)`.
  - R2-W2 coercion: coerce lines to `requires_review=True` before re-add.
  - Call `service.recover_discarded_page(page=target.get("page"), guia=guia)`.
  - Log WARNING on failure (never swallow silently — the same silent-data-loss guard as :715-719).
  Note: `target` must carry `"page"` — this is set by the audit emit in 2.2.6.
  Design: §5 (§11.1 risk now resolved — sidecar replay mirrors the existing `recovered_guia` pattern).

- [x] **2.2.8** Update `build_reprocess_service` in `container.py`:
  Use the shared OCR-selection helper from 2.2.2 to build the `ExtractionPort` and pass it as `extractor=...`.
  Design: §4 (shared helper — `build_reprocess_service` and `build_pipeline` use the same OCR-selection logic).

- [x] **2.2.9** Add 3 recovery endpoints to `routes.py` (mirroring the `_run_reprocess_batch` / `ReprocessBatchStatusResponse` pattern from :1110-1268):
  - `POST /runs/{run_id}/discarded-pages/{page}/recover` → single-page, calls `apply_page_recovery(page)`, returns `RecoverPageResponse`.
  - `POST /runs/{run_id}/discarded-pages/recover-batch` → body `{pages: list[int]}`, returns 202 `{run_id, count}`; status record in registry under `"discarded"` key in `discarded_batches`. 409 if batch in-flight.
  - `GET /runs/{run_id}/discarded-pages/recover-status` → `{total, recovered, failed, done}`. Terminal shape when no batch: `{total: 0, recovered: 0, failed: 0, done: true}`.
  Add corresponding `RecoverPageResponse`, `DiscardedBatchStatusResponse` (alias `ReprocessBatchStatusResponse` shape) DTOs to `schemas.py`.
  Design: §3 (endpoint contracts + SA-5 settle-only-on-done pattern).

- [x] **2.2.10** Run PR-2 test suite:
  ```
  cd backend && uv run pytest \
    tests/unit/application/test_apply_page_recovery.py \
    tests/unit/application/test_recover_discarded_page_hook.py \
    tests/unit/application/test_sidecar_restart_roundtrip.py \
    tests/unit/infrastructure/test_recovery_endpoints.py \
    tests/unit/infrastructure/test_schemas_discarded.py \
    -v
  ```
  All tests (2.1.1–2.1.18) MUST be GREEN.

- [x] **2.2.11** Verify architecture invariants:
  ```
  git diff HEAD -- backend/src/reconciliation/domain/ | grep "^+.*import"
  git diff HEAD -- backend/src/reconciliation/application/pipeline.py | grep "^+.*import"
  git diff HEAD -- backend/src/reconciliation/application/reprocess_service.py | grep "^+.*import"
  ```
  Assert: `reprocess_service.py` imports only `ExtractionPort` (port), never a concrete adapter. `pipeline.py` unchanged. `domain/` stays pure.

- [x] **2.2.12** Run regression sweep:
  ```
  cd backend && uv run pytest \
    tests/unit/application/test_reprocess_service.py \
    tests/unit/application/test_review_service.py \
    tests/unit/infrastructure/test_container.py \
    tests/unit/adapters/ \
    -v
  ```
  All must remain GREEN.

- [x] **2.2.13** Commit work-unit A: `feat(recovery): add identity_source="operator" Literal lockstep (4 sites); recover_discarded_page hook + sidecar replay (PR-2)`
  Covers: 2.2.1 + 2.2.6 + 2.2.7.

- [x] **2.2.14** Commit work-unit B: `feat(recovery): apply_page_recovery (3-tier OCR-first), OCR-selection helper, batch endpoints + status poll (PR-2)`
  Covers: 2.2.2 + 2.2.3 + 2.2.4 + 2.2.5 + 2.2.8 + 2.2.9.
  No push (SA-3).

### Phase 2.3 — Real-data gate (PR-2)

- [x] **2.3.1** Run real-data recovery test against the section PDF (`docs/eval/reg227_section.pdf`, OCR=rapidocr, vision capped at 0):
  Page 152 (registro='227', 3 cached lines) selected as Tier-1 target.
  Assert OCR NOT called (Tier 1 path): PASS — OCR spy call count = 0.
  Assert `recovered=True`: PASS.
  Assert ALL recovered lines `requires_review=True` (3/3 lines): PASS.
  Assert entry REMOVED from `discarded_pages` (1→0): PASS.
  Re-reconciliation: 53 rows returned. Spec: EXT-036 / EXT-S036a + REV-R32 / REV-S032a.
  Gate test: `backend/tests/integration/test_discarded_recovery_gate.py::TestDiscardedRecoveryRealDataGate::test_2_3_1_recovery_chain` — 2 passed in 193s.

- [x] **2.3.2** Verify sidecar restart round-trip with real data:
  Sidecar edits=1. Event `recovered_discarded_page` present: PASS.
  Event target `{'guia_id': 'recovered_152', 'page': 152}`: PASS.
  Event `new_value` is dict (GuiaDeRemision model_dump): PASS.
  `restore_from_sidecar` on fresh service: guía `recovered_152` present: PASS.
  `discarded_pages` entry for page 152 absent in fresh service: PASS.
  Design: §5 (§11.1 risk resolved).
  Gate test: `backend/tests/integration/test_discarded_recovery_gate.py::TestDiscardedRecoveryRealDataGate::test_2_3_2_sidecar_restart_roundtrip` — PASS.

### Phase 2.4 — Judgment Day (PR-2)

- [ ] **2.4.1** Run dual-blind judgment day on PR-2 diff before push.
  PR-2 touches `reprocess_service.py`, `review_service.py`, `routes.py`, `schemas.py`, `container.py` — multi-file pipeline-touching change. Full dual-blind JD required.
  JD must verify:
  - 4-site Literal lockstep is complete (no 5th site missed).
  - `recover_discarded_page` fail-closed guard is equivalent in strength to `add_recovered_guia` guard.
  - Sidecar replay for `recovered_discarded_page` mirrors `recovered_guia` exactly (R2-W2 coercion present).
  - Batch endpoint never settles `done=True` prematurely (PR-49 SA-5 lesson).
  - `fecha=None` on recovered guía is intentional and does not crash the reconciliation (R9b/R9c graceful off).
  No push / PR until JD passes. (SA-3)

---

## PR-3a — `feat(review): Descartadas tab — grouped list + selection`

> Scope: third tab wiring (`TAB_ORDER` extension), A1 grouping computed property,
> A2 collapsed groups + lazy thumbnails (`<img loading="lazy">`), A3 per-page +
> per-group + global selection, single-page "Recuperar" button (calls single-page endpoint).
>
> Independently shippable and SA-5-checkable on its own.
> Depends on: PR-2 merged to main (discarded entries in the table response; single-page endpoint).

### Phase 3a.1 — RED: Write failing vitest tests

- [x] **3a.1.1** Created `frontend/src/__tests__/features/ReviewPage.descartadasTab.test.ts` (project convention is `src/__tests__/features/`, not `src/features/review/__tests__/` — convention-match authorized by task text).
  Test `renders three tabs in TAB_ORDER with Descartadas appended; default is Reconciliación`.
  CONFIRMED RED: `expected [...] to have a length of 3 but got 2`. Spec: REV-R27 / REV-R27-S01.

- [x] **3a.1.2** Test `shows the discarded count badge on the Descartadas tab` (2 entries → "2").
  CONFIRMED RED: descartadas tab undefined. Spec: REV-R27 / REV-R27-S01.

- [x] **3a.1.3** Test `keeps the Descartadas tab present with zero entries; badge is hidden or "0"`.
  CONFIRMED RED: 2 tabs instead of 3. Spec: REV-R27 / REV-R27-S02.

- [x] **3a.1.4** Test `preserves existing tab indices and Pendientes behavior`.
  Regression LOCK — passes against current code by design (locks REV-R27-S03 existing behavior; cannot fail-first by nature). Spec: REV-R27 / REV-R27-S03.

- [x] **3a.1.5** Created `frontend/src/__tests__/features/DescartadasTab.test.ts` (component unit tests).
  Test `groups discarded entries into contiguous page runs` (57,58,59 | 81,82 → 2 groups).
  CONFIRMED RED: `Failed to resolve import "@/features/review/DescartadasTab.vue"` (whole suite — same ImportError RED convention as PR-1/PR-2). Spec: A1.

- [x] **3a.1.6** Test `splits a contiguous run when the registro changes` ([57] | [58,59]).
  CONFIRMED RED: suite-level import failure. Design: A1.

- [x] **3a.1.7** Test `renders all groups collapsed by default — no <img> elements exist`.
  CONFIRMED RED: suite-level import failure. Design: A2.

- [x] **3a.1.8** Test `expanding a group renders <img loading="lazy"> with thumbnail URLs`
  (asserts `/runs/run-123/pages/{57,58}/thumbnail`). CONFIRMED RED: suite import failure. Design: A2.

- [x] **3a.1.9** Test `checking one per-page checkbox selects only that page`
  (+ group header goes indeterminate). CONFIRMED RED: suite import failure. Spec: REV-R29.

- [x] **3a.1.10** Test `group header checkbox selects all pages of the run without expanding`
  (tri-state usable COLLAPSED; asserts zero `<img>` forced). CONFIRMED RED: suite import failure. Design: A3.

- [x] **3a.1.11** Test `global "Seleccionar todas (N)" selects every page across groups` (5 pages, 2 groups).
  CONFIRMED RED: suite import failure. Spec: REV-R29-S01. Design: A3.

- [x] **3a.1.12** Test `global control toggles back to deselect all` (bulk disabled after).
  CONFIRMED RED: suite import failure. Spec: REV-R29-S02.

- [x] **3a.1.13** Test `disables "Recuperar seleccionadas" when nothing is selected`.
  CONFIRMED RED: suite import failure. Spec: REV-R29.

- [x] **3a.1.14** Test `clicking "Recuperar" calls recoverDiscardedPage and emits refetch on success`
  (asserts `recoverDiscardedPage('run-123', 152)`). PLUS extra honesty lock
  `shows the failure reason honestly when recovered=false` (reason="empty" surfaced, no refetch).
  CONFIRMED RED: suite import failure. Spec: REV-R31 UI.

- [x] **3a.1.15** Test `renders the empty-state message with no checkboxes or thumbnails`.
  CONFIRMED RED: suite import failure. Spec: REV-R28-S05.

- [x] **3a.1.16** Test `shows a "sin registro" label for entries without registro`.
  CONFIRMED RED: suite import failure. Spec: REV-R28-S03.

- [x] **3a.1.17** Test `never renders a REINTENTAR / SUNAT-retry surface`.
  CONFIRMED RED: suite import failure. Spec: REV-R33 MUST-NOT.

### Phase 3a.2 — GREEN: Implement PR-3a

- [x] **3a.2.1** VERIFIED already satisfied by PR-2 commit (4-site Literal lockstep): `types.ts` has `DiscardedPageResponse`, `RecoverPageResponse`, `DiscardedBatchResponse`, `DiscardedRecoverStatusResponse`, `ReconciliationTableResponse.discarded_pages` and `'operator'` in both `identity_source` unions. No change needed in PR-3a. Original spec follows:
  Add `DiscardedPageResponse`, `RecoverPageResponse` + batch request/status TypeScript types to `frontend/src/api/types.ts`:
  ```typescript
  export interface DiscardedPageResponse {
    page: number
    registro: string | null
    has_cached_lines: boolean
  }
  export interface RecoverPageResponse {
    recovered: boolean
    page: number
    guia_id: string | null
    reason: string | null
    rows: ReconciliationRow[]
    discarded_pages: DiscardedPageResponse[]
  }
  ```
  Also add `discarded_pages: DiscardedPageResponse[]` to `ReconciliationTableResponse`.
  Verify `identity_source` union in `types.ts` already has `'operator'` (set in 2.2.1).
  Design: §6 (D2 lockstep, frontend union).

- [x] **3a.2.2** DONE — three client functions added following the module's per-endpoint conventions (single-page recover uses a 120 s timeout: Tier-2 OCR/Tier-3 vision exceed the 30 s default). Original spec follows:
  Add API client functions to `frontend/src/api/client.ts`:
  `recoverDiscardedPage(runId: string, page: number): Promise<RecoverPageResponse>` → `POST /runs/{runId}/discarded-pages/{page}/recover`.
  `recoverDiscardedBatch(runId: string, pages: number[]): Promise<{run_id: string, count: number}>` → `POST /runs/{runId}/discarded-pages/recover-batch`.
  `getDiscardedRecoverStatus(runId: string): Promise<{total: number, recovered: number, failed: number, done: boolean}>` → `GET /runs/{runId}/discarded-pages/recover-status`.
  Design: §6.

- [x] **3a.2.3** DONE — TabKey + TAB_ORDER extended, `descartadasTabEl` ref + `tabElFor` switch, third tab button (role=tab, aria-selected, aria-controls, roving tabindex, badge mirrors `erroredCount` v-if pattern), `discardedPages`/`discardedCount` computeds, `tabpanel-descartadas` panel. `onTabKeydown` verified generic over 3 elements (modular arithmetic — zero logic change). Two pre-existing 2-tab assertions updated per REV-R27 [MODIFIED: REV-R23]: `ReviewPage.tabs.test.ts` (tab count 2→3) + `ReviewPage.tabsKeyboard.test.ts` (End → Descartadas is now the last tab; WAI-ARIA semantics unchanged). Original spec follows:
  Extend `ReviewPage.vue` (`features/review/ReviewPage.vue:242-276`):
  Update `type TabKey` to include `'descartadas'`.
  Update `TAB_ORDER = ['reconciliacion', 'pendientes', 'descartadas']`.
  Add `descartadasTabEl` ref + `tabElFor` branch.
  Add third tab button with `role="tab"`, `aria-selected`, `aria-controls`, roving tabindex, count badge (mirrors `erroredCount` pattern).
  `onTabKeydown` already uses `TAB_ORDER` modular arithmetic — verify it works with 3 elements without change.
  Spec: REV-R27. Design: §6 (D6).

- [x] **3a.2.4** DONE — created per design A1/A2/A3 + D6. Deviations (noted): (a) `expanded` lives in a separate `Set<number>` keyed by group first-page, NOT a field on the computed group objects (a computed is re-derived — state on its items would reset; same derived-view-model intent); (b) bulk button is rendered with live count but gated by `BULK_FLOW_READY=false` so no dead-click path ships before PR-3b wires the confirm/batch flow (orchestrator-authorized "disabled/hidden pending PR-3b"); (c) honest failure reasons mapped es-PE for `empty` / `not_found` / `already_recovered` + network errors. Original spec follows:
  Create `frontend/src/features/review/DescartadasTab.vue`:
  Props: `{ discardedPages: DiscardedPageResponse[], runId: string }`.
  Emits: `'refetch'`.
  Computed `groups`: O(n) pass over sorted `discardedPages`, break on page-index gap OR registro change. Returns `Array<{ registro: string|null, pages: DiscardedPageResponse[], expanded: boolean }>`.
  Render: collapsed group headers (page range, count badge, registro label). On expand: `<img loading="lazy" :src="thumbnailUrl(page)">` per page inside `v-if`.
  Per-page checkbox: `selected: Set<number>` reactive state.
  Per-group header checkbox: tri-state (all/some/none), toggles group pages in `selected`.
  Global "Seleccionar todas (N)" control: selects all pages across all groups.
  "Recuperar seleccionadas" button: disabled when `selected.size === 0`; label shows count.
  Single-page "Recuperar" button per page entry.
  Empty-state message when `discardedPages.length === 0`.
  "sin registro" label when `entry.registro === null`.
  Reuse `PageSheetViewer.vue` (PR#48) for per-page sheet viewer action.
  Design: §6 (D6), A1, A2, A3.

- [x] **3a.2.5** PR-3a vitest suite GREEN: `cd frontend && npm test -- DescartadasTab` (vitest positional filter, not jest `--testPathPattern`) → 2 files, **18/18 passed** (17 planned + 1 extra honesty lock on recovered=false).

- [x] **3a.2.6** Full frontend regression GREEN: `cd frontend && npm test` → **340/340 passed** (322 prior + 18 new). `npm run type-check` (vue-tsc --noEmit) → 0 errors.

- [x] **3a.2.7** Committed work-unit on branch `feat/discarded-pages-tab`:
  `feat(review): Descartadas tab — grouped list, collapsed thumbnails, tri-state selection, single-page recover (PR-3a)`
  No push (SA-3).

### Phase 3a.3 — SA-5 runtime validation (PR-3a)

- [ ] **3a.3.1** Validate against the RUNNING app via Playwright MCP (SA-5 — mandatory before marking PR-3a done):
  Upload the full PDF → wait for pipeline to complete → navigate to ReviewPage → click "Descartadas para revisión" tab → assert tab is visible and active → assert count badge shows the expected discarded count → assert groups are collapsed by default → expand one group → assert thumbnails render (img elements visible) → click a single entry's "Recuperar" button → assert the entry disappears from the tab OR shows a result → assert the Reconciliación tab reflects the recovered line flagged for review.
  Green unit tests alone do NOT prove runtime behavior (SA-5 principle + PR#49 lesson).

### Phase 3a.4 — Judgment Day (PR-3a)

- [ ] **3a.4.1** Run single-pass `ctr-reviewer` review on PR-3a diff (frontend PRs: ctr-reviewer is sufficient per CLAUDE.md §Fix/Feature Discipline #4; dual-blind JD is for parser/pipeline-touching PRs).
  Reviewer must verify:
  - `TAB_ORDER` extension preserves existing indices (Reconciliación=0, Pendientes=1).
  - A1 grouping breaks on registro change (A5 structural guarantee).
  - A2 collapse default: zero `<img>` rendered on mount (343 × img requests avoided).
  - REINTENTAR button is structurally absent from `DescartadasTab`.
  - `selected` state is ephemeral (no backend call to maintain selection).
  No push / PR until review passes. (SA-3)

---

## PR-3b — `feat(review): bulk recovery at scale`

> Scope: A3 ETA confirm dialog (with OCR-empty count + ~10 s/page label + conditional vision cost warning), batch fire (`recoverDiscardedBatch`), poll-until-done (settle ONLY on `done=true`), A4 mount re-attach (poll once on tab mount; if `done=false` resume polling, disable buttons), completion summary.
>
> SA-5 Playwright runtime validation (mandatory gate for this PR): upload → Descartadas tab → expand group → select subset → recover → progress settles → flagged row in Reconciliación.
>
> Depends on: PR-3a merged to main.

### Phase 3b.1 — RED: Write failing vitest tests

> All 3b.1.x created in `frontend/src/__tests__/features/DescartadasTab.bulk.test.ts`
> (same `src/__tests__/features/` convention as PR-3a). CONFIRMED RED as a batch:
> 11/11 failing before the implementation (no dialog element, `recoverBatchMock`
> never called, mount poll count 0 — each test failed on its own assertion, not a
> suite-level import error, because DescartadasTab.vue already exists from PR-3a).

- [x] **3b.1.1** Test `shows the confirm dialog and does NOT fire the batch until confirmed`:
  2 pages selected → click → dialog rendered, `recoverBatchMock` NOT called; confirm →
  called with `('run-123', [57, 58])`. CONFIRMED RED: no `.descartadas-tab__dialog` element.
  Spec: REV-R30 / REV-R30-S01.

- [x] **3b.1.2** Test `shows an approximate ETA and the vision-cost warning when OCR-empty pages are selected`:
  K=2 `has_cached_lines=false` → asserts `/≈\s*\d+\s*min/` AND "~10 s" AND
  `.descartadas-tab__dialog-warning` mentioning IA/visión. CONFIRMED RED. Design: A3.

- [x] **3b.1.3** Test `does NOT show the vision-cost warning when every selected page has cached lines`:
  3 cached pages → warning absent; count "3" in dialog. CONFIRMED RED. Design: A3.

- [x] **3b.1.4** Test `disables "Recuperar seleccionadas" while the batch is in-flight`
  (mock `done=false` after fire). CONFIRMED RED. Spec: REV-R30 / REV-R30-S05.

- [x] **3b.1.5** Test `removes recovered pages incrementally and never settles before done=true`:
  status sequence `{3,1,0,false}` → `{3,2,0,false}` → `{3,2,1,true}`; asserts incremental
  'refetch' BEFORE done (list shrinks via parent prop refresh — 2 groups remain), the
  completion summary `.descartadas-tab__batch-summary` is ABSENT after `recovered=1` AND
  after `recovered=2` (SA-5 premature-settlement regression lock, PR#49 lesson), appears
  with "2 recuperadas"/"1 falló" only after `done=true`, and the failed page stays listed.
  CONFIRMED RED. Spec: REV-R30-S02 + REV-R30-S04 + REV-R30-S06.

- [x] **3b.1.6** Test `shows the completion summary "2 recuperadas / 1 falló" after done=true`.
  CONFIRMED RED. Spec: REV-R30 (completion summary).

- [x] **3b.1.7** Test `re-attaches to an in-flight batch on mount: resumes polling and disables the bulk button`:
  mount status `done:false` → bulk disabled + status called again after a 2.5 s tick.
  CONFIRMED RED: poll count stayed 0. Design: A4.

- [x] **3b.1.8** Test `does not poll after mount when the status is the terminal no-batch shape`:
  `{total:0, done:true}` → exactly 1 status call after 5 s; bulk enabled once selected.
  CONFIRMED RED: mount poll count 0 (no on-mount poll existed). Design: A4 (terminal
  shape locked by backend test 2.1.15).

- [x] **3b.1.9** Test `disables the per-page "Recuperar" buttons while a batch is in-flight`
  (re-attach `done:false` → per-page button disabled). CONFIRMED RED. Spec: REV-R30-S05 spirit / A4.

- [x] **3b.1.10** Test `emits refetch after the batch completes so the parent refreshes the grid`
  (`done=true` → 'refetch' emitted). CONFIRMED RED. Spec: REV-R32.

- [x] **3b.1.11** (ADDED — ctr-reviewer PR-3a carry-over, orchestrator-mandated) Test
  `excludes a singly-recovered page from the bulk payload after the list refreshes`:
  pages {152,153} selected → page 152 recovered singly → parent prop refresh removes it →
  bulk count shows 1 and `recoverBatchMock` called with `('run-123', [153])` — the stale
  selected page NEVER reaches the payload. CONFIRMED RED: dialog element missing (and the
  PR-3a code had no selected∩discardedPages intersection). Spec: REV-R30.9 spirit (only
  pages from the discarded collection are submitted).

### Phase 3b.2 — GREEN: Implement PR-3b

- [x] **3b.2.1** DONE — inline confirm section in `DescartadasTab.vue` (task text authorized
  "or inline"; mirrors the Pendientes dialog verbatim — backdrop, role="dialog",
  aria-modal, Esc close, Tab focus-trap `onDialogTab`, W2 trigger-focus restore).
  Count prominent in title (`¿Recuperar N páginas?`) + confirm button (`Confirmar (N)`);
  body "OCR-primero, IA como último recurso." (REV-R30-S01 wording); ETA line
  `≈ ceil(K×10s/60) min` labeled "aproximado, ~10 s por página sin OCR en caché"
  (K=0 → "recuperación casi instantánea"); `.descartadas-tab__dialog-warning` only when
  K > 0. Counts derive from `selectedLive`/`ocrEmptyCount` computeds. Design: A3.

- [x] **3b.2.2** DONE — bulk fire + poll-until-done in `DescartadasTab.vue`:
  confirm → `recoverDiscardedBatch(runId, selectedLive)` (202; 409 → honest es-PE error,
  batch never marked in-flight) → `setInterval` poll of `getDiscardedRecoverStatus` every
  2 s with an immediate kick. Each tick: `recovered+failed` delta vs. last-known → emit
  'refetch' (recovered pages leave INCREMENTALLY via the parent's refreshed prop; failed
  pages stay — backend keeps them in `discarded_pages`). Settlement EXCLUSIVELY on
  `status.done === true` (PR#49 SA-5 lesson ×3: STRICT — no timing heuristic; the
  30 s/page hard cap is ONLY a hung-batch failsafe, mirroring Pendientes). `done=true` →
  `.descartadas-tab__batch-summary` "N recuperadas / M falló|fallaron" from the REAL
  backend counts + final 'refetch' + buttons re-enabled. **Carry-over fix**: the payload
  is `selectedLive` — `selected` ∩ current `discardedPages` (computed used by ALL
  selection consumers) + a `watch` pruning the raw Set on prop refresh, so a
  singly-recovered page never reaches the batch payload. Design: A4 + §6 (D6).

- [x] **3b.2.3** DONE — `onMounted → reattachBatch()`: one `getDiscardedRecoverStatus`
  call; `done === false` (strict check — tolerates undefined from partial test mocks) →
  batch state seeded from the backend `{total, recovered, failed}` + polling resumed
  (no immediate kick — the status was just read) + all Recuperar buttons (bulk AND
  per-page) disabled; terminal `{total:0, done:true}` → no polling, buttons enabled.
  `onBeforeUnmount` clears the timer. Design: A4 (1 h batch survivability).

- [x] **3b.2.4** DONE — `npx vitest run DescartadasTab`: **29/29 GREEN**
  (14 PR-3a unit + 11 PR-3b bulk + 4 ReviewPage.descartadasTab).

- [x] **3b.2.5** DONE — full `npm test`: **351/351 GREEN** (35 files; 340 prior + 11 new).
  `npx vue-tsc --noEmit`: 0 errors.

- [x] **3b.2.6** DONE — committed as
  `feat(review): bulk recovery — ETA confirm, batch fire, poll-until-done, mount re-attach, completion summary (PR-3b)`.
  No push (SA-3 — ship is orchestrator-only).

### Phase 3b.3 — SA-5 runtime validation (PR-3b — MANDATORY)

- [ ] **3b.3.1** Validate against the RUNNING app via Playwright MCP (SA-5 — mandatory gate before marking PR-3b done):
  Full flow: upload PDF → wait for pipeline → navigate to ReviewPage → click "Descartadas para revisión" tab → verify count badge matches expected discarded count → expand a group → thumbnails render lazy → select a subset via per-page checkboxes → click "Recuperar seleccionadas" → confirm dialog appears (assert ETA line present) → confirm → progress updates incrementally (assert intermediate state before `done`) → batch completes → completion summary shown → verified recovered row appears in Reconciliación tab flagged `requires_review`.
  STOP if any step fails — do not mark PR-3b done until this gate passes. (SA-5 principle, CLAUDE.md §Fix/Feature Discipline #2 — real data over mock theatre).

### Phase 3b.4 — Judgment Day (PR-3b)

- [ ] **3b.4.1** Run single-pass `ctr-reviewer` review on PR-3b diff.
  Reviewer must verify:
  - Bulk settle strictly on `done=true` (PR#49 SA-5 lesson — line-by-line check in the poll loop).
  - Mount re-attach uses the locked terminal shape (safe to call on every mount).
  - `recoverDiscardedBatch` payload is the operator-selected subset only (not all 343 pages).
  - `emit('refetch')` fires after batch completion so Reconciliación grid updates.
  - Failed pages remain in the list (never silently removed).
  No push / PR until review passes. (SA-3)

---

## Final Tasks

### SDD Verification + Archive

- [ ] **F.1** Run `sdd-verify discarded-pages-recovery`:
  Verify all 14 delta requirements (EXT-034..EXT-037, REV-R27..REV-R33) are satisfied.
  Expected: all tests GREEN, real-data gate passed (343 discarded entries surfaced), SA-5 Playwright evidence collected, no CRITICAL or WARNING from verify.

- [ ] **F.2** Run `sdd-archive discarded-pages-recovery`:
  Persist archive report to `openspec/changes/archive/discarded-pages-recovery/`.
  Update `docs/HANDOFF.md` status section (branch `main`, all PRs merged, SDD#2 closed).
  Conventional commit: `docs(handoff): SDD#2 discarded-pages-recovery closed`.

---

## Dependency Graph

```
main (post SDD#1 PR#51-#54)
    │
    ▼
PR-1  feat(pipeline): surface discarded GUIA pages
    │  ~270–320 LOC
    │
    ▼
PR-2  feat(recovery): OCR-first page recovery
    │  ~360–420 LOC  [if > 400: split PR-2a (hook/sidecar) → PR-2b (service/endpoints)]
    │
    ▼
PR-3a feat(review): Descartadas tab — grouped list + selection
    │  ~330–380 LOC
    │
    ▼
PR-3b feat(review): bulk recovery at scale
       ~280–330 LOC

All PRs sequential (stacked-to-main). Each must be independently GREEN before the next starts.
Within each PR: RED tasks first (all failing tests written), then GREEN (implementation).
```

**Parallelism**: NONE across PR boundaries (Option B parallel side-channel avoids the Option A enrollment-leak risk at the cost of explicit serial dependency through the chain). Within a PR, RED test tasks can be written in any order before the first GREEN task.

---

## Files Created/Modified

| File | PR | Action |
|------|-----|--------|
| `backend/src/reconciliation/domain/models.py` | PR-1 | MODIFY — add `DiscardedPage`; PR-2 — add `"operator"` to identity_source Literal (×2 sites) |
| `backend/src/reconciliation/application/pipeline.py` | PR-1 | MODIFY — `PipelineResult.discarded_pages`, drop-site emit, `_stage_persist` |
| `backend/src/reconciliation/application/review_service.py` | PR-1 | MODIFY — `_discarded_pages` state + property; PR-2 — `recover_discarded_page` hook + sidecar replay |
| `backend/src/reconciliation/application/reprocess_service.py` | PR-2 | MODIFY — `extractor` port, `apply_page_recovery`, `PageRecoveryResult` |
| `backend/src/reconciliation/infrastructure/container.py` | PR-1 | MODIFY — `build_review_service` hydration; PR-2 — OCR-selection shared helper + `build_reprocess_service` wiring |
| `backend/src/reconciliation/infrastructure/api/schemas.py` | PR-1 | MODIFY — `DiscardedPageResponse`, `ReconciliationTableResponse.discarded_pages`; PR-2 — `RecoverPageResponse`, batch DTOs, `identity_source Literal` |
| `backend/src/reconciliation/infrastructure/api/routes.py` | PR-2 | MODIFY — 3 recovery endpoints |
| `frontend/src/api/types.ts` | PR-2 | MODIFY — `DiscardedPageResponse`, `RecoverPageResponse`, batch types, `identity_source 'operator'` |
| `frontend/src/api/client.ts` | PR-3a | MODIFY — `recoverDiscardedPage`, `recoverDiscardedBatch`, `getDiscardedRecoverStatus` |
| `frontend/src/features/review/ReviewPage.vue` | PR-3a | MODIFY — `TAB_ORDER` extension, third tab wiring |
| `frontend/src/features/review/DescartadasTab.vue` | PR-3a | CREATE — grouped list + selection + single-page recover |
| `frontend/src/features/review/DescartadasTab.vue` | PR-3b | MODIFY — ETA confirm dialog, bulk fire, poll-until-done, mount re-attach |
| `backend/tests/unit/application/test_pipeline_discarded_pages.py` | PR-1 | CREATE |
| `backend/tests/unit/infrastructure/test_container_discarded.py` | PR-1 | CREATE |
| `backend/tests/unit/infrastructure/test_schemas_discarded.py` | PR-1 | CREATE (extended in PR-2) |
| `backend/tests/unit/application/test_apply_page_recovery.py` | PR-2 | CREATE |
| `backend/tests/unit/application/test_recover_discarded_page_hook.py` | PR-2 | CREATE |
| `backend/tests/unit/application/test_sidecar_restart_roundtrip.py` | PR-2 | CREATE |
| `backend/tests/unit/infrastructure/test_recovery_endpoints.py` | PR-2 | CREATE |
| `frontend/src/features/review/__tests__/DescartadasTab.test.ts` | PR-3a | CREATE |
| `frontend/src/features/review/__tests__/DescartadasTab.unit.test.ts` | PR-3a | CREATE |

**Domain/ new models**: `DiscardedPage` only — domain purity invariant maintained.
**pipeline.py concrete adapter imports**: ZERO added — Dependency Inversion invariant maintained.

---

## Open Questions (SA-2 — flagged, not invented)

1. **`_build_recovered_guia_lines_from_vision` rename**: design §4 notes `rename to _build_recovered_lines is optional polish`. Implementation must decide: rename (additive refactor) or reuse as-is. If renaming, verify NO OTHER callers break (grep before rename). If not renaming, reuse verbatim. Both are valid; decision must be made before implementation starts. Assumption (state before apply): **reuse as-is** (safe default; rename is optional polish, not a requirement).

2. **`_stage_assemble_blocks` return shape**: currently returns a single value (or tuple). Adding discarded pages to the return changes the internal call signature. Task 1.2.3 specifies a tuple return — implementation must verify no other caller destructures the current return and would break. Pre-flight read of `_run()` call site is required before 1.2.3 (or bundle with 1.0.1).

3. **`has_cached_lines` indicator vs. `cached_lines` count**: design §3 says the DTO exposes `has_cached_lines: bool` (NOT raw `MaterialLine` objects). The ETA confirm (A3) needs the count of OCR-empty pages (those where `has_cached_lines=false`). Confirm the boolean is sufficient for the ETA calculation — yes it is (`K = count of entries where has_cached_lines=false`). No change needed.

4. **Playwright evidence path**: SA-5 evidence from 3a.3.1 and 3b.3.1 should be saved to `docs/playwright/sdd2-descartadas-recovery-{date}.png` (gitignored per existing convention). Verify the path convention before 3a.3.1.

5. **PR-2 budget guard**: if the combined LOC of 2.2.1–2.2.9 + tests 2.1.1–2.1.18 exceeds ~400 lines, split into PR-2a and PR-2b as described in the Review Workload Forecast. The split boundary is after task 2.2.7 (PR-2a closes: Literal lockstep + hook + sidecar replay). PR-2b starts fresh from 2.2.8. Track with `git diff --stat` before committing.
