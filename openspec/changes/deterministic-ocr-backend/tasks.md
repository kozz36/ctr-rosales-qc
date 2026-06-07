# Tasks: deterministic-ocr-backend (SDD#1)

**Change**: deterministic-ocr-backend
**Artifact store**: hybrid (engram + openspec)
**Delivery strategy**: ask-on-risk
**Strict TDD**: active (runner: `cd backend && uv run pytest`)
**Date**: 2026-06-06

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~490–690 LOC + uv.lock churn |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR#1 → PR#2 → PR#3 (stacked-to-main) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — orchestrator must ask before apply |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Pure box-row parser + full strict-TDD suite | PR#1 | No SDK, no Docker, no config. Base: main. ~150–200 LOC. |
| 2 | RapidOCRAdapter + factory + config + container wiring + adapter/factory/container tests | PR#2 | Depends on PR#1. Engine default stays `paddle` — no behaviour change. Base: PR#1 branch. ~200–300 LOC. |
| 3 | deps + Docker air-gap + uv.lock + CONT assertions + real-data gate + deploy-default flip | PR#3 | Depends on PR#2. Runtime activation. Base: PR#2 branch. ~140–190 LOC + lockfile. |

---

## Invariants (anti-patterns enforced throughout all phases)

- **Domain purity**: no file under `domain/` imports `rapidocr`, `onnxruntime`, `RapidOCRAdapter`, or `box_row_parser`. Auto-reject.
- **Pipeline zero concrete adapters**: `application/pipeline.py` imports only `ExtractionPort` — never a concrete OCR class. Auto-reject.
- **Lazy heavy deps**: `rapidocr`/`onnxruntime`/`numpy`/`PIL` imported INSIDE methods only, never at module top. `factory.py` imports concrete adapters inside branch bodies only.
- **Provider-agnostic**: selection via `OcrConfig.engine` + `build_ocr_extractor`. Domain/pipeline never reference `RapidOCR` by name.
- **`fecha` never a grouping axis**: `box_row_parser.py` emits `desc/qty/unit` only; no date field. Auto-reject.
- **Units never converted**: `TNE→TN` is a label normalization only — the numeric quantity is NEVER multiplied, divided, or adjusted. `KG/TN/RD/Rollo` unchanged.
- **Reconciliation is the validation gate**: OCR misreads → `status=MISMATCH` + `requires_review=True`. Never auto-corrected.
- **Input PDF read-only**: OCR processing MUST NOT modify the source PDF.
- **Three identifiers**: `#4252` ≠ Registro N° ≠ QR `serie-numero` — never confused.

---

## PR#1 — Pure Box-Row Parser + Strict-TDD Suite

> Scope boundary: `adapters/ocr/box_row_parser.py` + `tests/unit/adapters/test_box_row_parser.py` ONLY.
> No `rapidocr`, no `onnxruntime`, no Docker, no config change. The suite MUST pass with no OCR SDK installed.

### Phase 1.1 — RED: Write failing tests for the pure parser (no implementation yet)

- [x] **1.1.1** Create `backend/tests/unit/adapters/test_box_row_parser.py`.
  Write failing test `test_band_px_scaling` — assert `round(40*dpi/200)` == 40 at 200, 60 at 300, 30 at 150, 20 at 100.
  Spec: EXT-029 / EXT-S029g. Design: §4.

- [x] **1.1.2** Add failing test `test_desc_qty_pairing_multi_row` — 4-cell table at Y centroids [120,160,200,240] (DPI=200, band=40px); assert 4 `MaterialLine`-shaped rows with QTY values {0.008, 0.136, 0.191, 0.041} paired correctly, never cross-band.
  Spec: EXT-029 / EXT-S029a. Design: §2.1.

- [x] **1.1.3** Add failing test `test_unit_cell_association_same_band` — synthetic row with DESC cell at (cx=100,cy=150), QTY cell at (cx=250,cy=152), UNIT cell `TNE` at (cx=300,cy=150). Assert emitted row has `unidad="TN"` (TNE normalized). Also test `KG`, `RD`, `Rollo` remain unchanged.
  Spec: EXT-029 / EXT-S029b. Design: §5 (`_UNIT_RE`). **Design risk #2 mitigation — unit column explicit test.**

- [x] **1.1.4** Add failing test `test_unit_cell_row_scan_fallback` — row with no UNIT cell in the same band; assert `requires_review=True` on the emitted row OR row is dropped (document whichever the implementation chooses and lock it here).
  Design: §2.1 (fallback behaviour). Locks the unit-missing edge case before implementation.

- [x] **1.1.5** Add failing test `test_tne_not_a_numeric_conversion` — a row with unit `TNE`, cantidad `0.136`. Assert `unidad="TN"`, `cantidad==Decimal("0.136")` (value unchanged, multiply by nothing).
  Spec: EXT-029 / EXT-S029b + EXT-032. Domain invariant: units never converted.

- [x] **1.1.6** Add failing test `test_incidental_numbers_not_qty` — cells: `1` (leftmost, no decimal), `1"` (diameter), `408916` (product code), `0.037` (valid decimal). Assert only `0.037` classified as QTY; others are not.
  Spec: EXT-029 / EXT-S029d. Design: §5 (`_QTY_RE` requires `[.,]\d{2,3}`).

- [x] **1.1.7** Add failing test `test_generalized_desc_matcher` — cells with text `FIERRO CORRUGADO 1/2"`, `ALAMBRE NEGRO`, `ACERO DIMENSIONADO`. Assert each classified as DESC (not IGNORED), even without rebar keywords.
  Spec: EXT-029 / EXT-S029e. Design: §5 (≥3-letter alphabetic run rule).

- [x] **1.1.8** Add failing test `test_columnar_table_yields_rows` — synthetic cells arranged as a columnar GRE table where `_LINE_RE` would produce 0 matches (non-contiguous bounding boxes). Assert `≥1` row returned.
  Spec: EXT-029 / EXT-S029c.

- [x] **1.1.9** Add failing test `test_pure_import_no_sdk` — import `box_row_parser` inside a subprocess or with `sys.modules` mock; assert no `rapidocr`, `onnxruntime`, `paddleocr` in `sys.modules` after import.
  Spec: EXT-029 / EXT-S029f + EXT-032 / EXT-S032c.

- [x] **1.1.10** Add failing test `test_count_valid_rows_orientation_oracle` — call `count_valid_rows(cells, dpi=200)`; assert returns `len(parse_box_rows(cells, dpi=200))` for the same synthetic input.
  Design: §2.1 (`count_valid_rows` as orientation oracle). Required by PR#2 adapter retry loop.

- [x] **1.1.11** Add failing test `test_qty_right_of_desc_geometry_guard` — QTY cell at `cx=50` (LEFT of DESC at `cx=200`) in the same band. Assert this pair does NOT associate (qty must be right of desc).
  Design: §2.1 (`qcx > dcx` rule).

- [x] **1.1.12** Add failing test `test_empty_cells_returns_empty_list` — call `parse_box_rows([], dpi=200)`. Assert `[]` returned without exception.
  Spec: EXT-029. Defensive edge case.

### Phase 1.2 — GREEN: Implement `box_row_parser.py`

- [x] **1.2.1** Create `backend/src/reconciliation/adapters/ocr/box_row_parser.py`.
  Implement `Cell` frozen dataclass (`text: str, conf: float, cx: float, cy: float`).
  Implement `_QTY_RE`, `_UNIT_RE`, three-way cell classifier.
  Imports: only `stdlib` (`re`, `decimal`, `dataclasses`) + `reconciliation.domain.models.MaterialLine` + `reconciliation.domain.normalizer.MaterialNormalizer`. Zero SDK imports at module level.
  Spec: EXT-029. Design: §2.1, §5.

- [x] **1.2.2** Implement `parse_box_rows(cells: list[Cell], dpi: int) -> list[MaterialLine]`:
  - `band_px = round(40 * dpi / 200)`
  - Group cells into row bands by centroid Y
  - For each QTY cell, find nearest DESC in same band with `dcx < qcx`
  - Find UNIT cell in same band (or fallback with `requires_review=True`)
  - Normalize `TNE→TN` (label only; qty unchanged)
  - Emit `MaterialLine` per valid pair; include `description_canonical = MaterialNormalizer.canonicalize(desc_raw)`
  - Return `[]` on no valid pairs (orientation oracle reads this as 0 valid rows)
  Design: §2.1, §4, §5.

- [x] **1.2.3** Implement `count_valid_rows(cells: list[Cell], dpi: int) -> int` as `len(parse_box_rows(cells, dpi))`.
  Design: §2.1.

- [x] **1.2.4** Run `cd backend && uv run pytest tests/unit/adapters/test_box_row_parser.py -v` — all 12 tests must be GREEN. If any fail, fix before proceeding.
  Spec: all EXT-029 scenarios, EXT-032 / S032c (domain purity). **No SDK installed — this is the purity gate.**

- [x] **1.2.5** Verify domain/ untouched: `git diff --name-only HEAD | grep domain/` must be empty.
  Spec: EXT-032 / EXT-S032c. Domain invariant check.

- [x] **1.2.6** Commit work-unit: `feat(ocr): add pure box-row parser with strict-TDD suite (PR#1)`.
  Conventional commit. No push (orchestrator-only — SA-3).

---

## PR#2 — RapidOCRAdapter + Factory + Config + Container Wiring

> Scope boundary: `rapid_table.py`, `factory.py`, `OcrConfig.engine` in `config.py`, `container.py:378-392` three-branch + `deskew=None`, plus all adapter/factory/container/config tests.
> Engine default stays `"paddle"` — no runtime behaviour change until PR#3.

### Phase 2.1 — RED: Write failing tests (adapter, factory, config, container)

- [x] **2.1.1** Create `backend/tests/unit/adapters/test_rapid_table.py`.
  Write failing tests (using injected `_engine` mock mirroring `test_paddle_table.py::_make_ocr`):
  - `test_extract_declared_returns_empty_list` — EXT-028 / S028a.
  - `test_lazy_import_not_triggered_at_init` — assert `_engine is None` after `RapidOCRAdapter()` construction. EXT-028 / S028b.
  - `test_engine_failure_returns_empty_not_raises` — mock engine raises on `__call__`; assert `[]` returned and `_ocr_failed=True`. EXT-028 / design §2.2 graceful degradation.
  Design: §2.2, §9.

- [x] **2.1.2** Add failing tests for orientation logic (mocked engine):
  - `test_default_minus90_applied_first` — mock `_engine` returns boxes for -90° only; assert rows returned without retry.
  - `test_retry_triggered_on_zero_valid_rows` — mock returns boxes for 0° only; assert retry loop runs {0,90,180,270} and picks 0°.
  - `test_max_valid_rows_wins_on_retry` — mock: 90° yields 2 rows, 180° yields 4 rows; assert 4-row candidate returned.
  Spec: EXT-030 / S030a-c. Design: §6.

- [x] **2.1.3** Add failing test `test_confidence_below_threshold_sets_requires_review` — mock `_engine` returns a cell with `score=0.75` (< 0.85); assert emitted `MaterialLine` has `requires_review=True`.
  Spec: EXT-004 (confidence gate retained). Design: §2.2.

- [x] **2.1.4** Create `backend/tests/unit/adapters/test_ocr_factory.py`.
  Write failing tests:
  - `test_rapidocr_engine_resolves_to_rapidocr_adapter` — EXT-027 / S027a.
  - `test_paddle_engine_resolves_to_printed_table_adapter` — EXT-027 / S027b.
  - `test_enabled_false_resolves_to_null_extractor` — EXT-027 / S027c.
  - `test_unknown_engine_raises_value_error`.
  - `test_factory_module_imports_without_rapidocr_installed` — EXT-027 / S027d; assert no SDK in `sys.modules` at factory import time.
  Spec: EXT-027. Design: §2.3.

- [x] **2.1.5** Extend `backend/tests/unit/application/test_config.py`:
  - `test_ocr_config_engine_default_is_paddle` — `OcrConfig().engine == "paddle"`.
  - `test_ocr_config_engine_from_env` — env `RECONCILIATION__OCR__ENGINE=rapidocr` → `engine="rapidocr"`. EXT-027 / S027e.
  Spec: EXT-027. Design: §8.

- [x] **2.1.6** Extend `backend/tests/unit/infrastructure/test_container.py`:
  - `test_rapidocr_engine_wires_rapidocr_adapter` — build container with `engine=rapidocr`; assert `_ocr_adapter` is `RapidOCRAdapter` instance.
  - `test_rapidocr_engine_sets_deskew_none` — same config; assert `_deskew is None`.
  - `test_paddle_engine_unchanged` — build with `engine=paddle`; assert `_ocr_adapter` is `PrintedTableAdapter`, deskew is `DeskewAdapter`.
  - `test_enabled_false_still_null_extractor` — regression guard.
  Spec: EXT-027. Design: §8.

- [x] **2.1.7** Add failing test `test_pipeline_imports_zero_concrete_adapters` (extend `test_pipeline.py` or add to `test_config.py`):
  Import `backend.src.reconciliation.application.pipeline`; assert `RapidOCRAdapter` and `PrintedTableAdapter` NOT in the module's namespace.
  Spec: EXT-027 / S027d. Domain invariant.

### Phase 2.2 — GREEN: Implement adapter, factory, config, container changes

- [x] **2.2.1** Add `engine: Literal["paddle", "rapidocr"] = "paddle"` to `OcrConfig` in `backend/src/reconciliation/application/config.py`.
  Ensure `extra="allow"` (already present). Additive only.
  Spec: EXT-027. Design: §8.

- [x] **2.2.2** Create `backend/src/reconciliation/adapters/ocr/factory.py`.
  Implement `build_ocr_extractor(cfg) -> ExtractionPort` with lazy-import branches per design §2.3.
  Note: factory does NOT handle `enabled=False` — that stays in `container.py`.
  Spec: EXT-027. Design: §2.3.

- [x] **2.2.3** Create `backend/src/reconciliation/adapters/ocr/rapid_table.py`.
  Implement `RapidOCRAdapter(dpi=200, _engine=None)`:
  - `extract_declared` → `[]`
  - `_get_engine()` with `_INIT_LOCK` double-checked lazy init per design §3
  - `_ocr_cells(engine, img_array) -> list[Cell]` converting `RapidOCROutput` to `Cell` objects (centroid from polygon mean)
  - `_rotate(image_bytes, deg) -> ndarray` via lazy Pillow/numpy
  - `extract_printed_table(image: bytes)` orientation loop per design §6 (default -90°, retry on 0 rows, max-valid-rows)
  Spec: EXT-028 + EXT-030. Design: §2.2, §3, §6.

- [x] **2.2.4** Update `backend/src/reconciliation/infrastructure/container.py` (lines 378-392):
  Replace two-branch with three-branch logic per design §8.
  `enabled=False` → `NullOcrExtractor` (unchanged).
  `enabled=True` → `build_ocr_extractor(config)` via `__new__` bypass, inject `_declared_adapter` + `_ocr_adapter`.
  Wire `deskew=None` when `engine=rapidocr`; keep `DeskewAdapter` for `engine=paddle`.
  **INVARIANT**: `pipeline.py` must not be touched.
  Spec: EXT-027. Design: §8.

- [x] **2.2.5** Run full adapter+factory+config+container test suite:
  `cd backend && uv run pytest tests/unit/adapters/test_rapid_table.py tests/unit/adapters/test_ocr_factory.py tests/unit/application/test_config.py tests/unit/infrastructure/test_container.py -v`
  All must be GREEN. No SDK installed requirement (injected mock).

- [x] **2.2.6** Run regression sweep on existing paddle tests:
  `cd backend && uv run pytest tests/unit/adapters/test_paddle_table.py tests/unit/adapters/test_null_extractor.py -v`
  Must remain GREEN. PR#2 is additive; paddle path unchanged.

- [x] **2.2.7** Verify `git diff HEAD -- backend/src/reconciliation/domain/` is empty (domain purity).
  Verify `git diff HEAD -- backend/src/reconciliation/application/pipeline.py` is empty (pipeline zero concrete adapters).
  Spec: EXT-032 / S032c. Architecture invariant gate.

- [x] **2.2.8** Commit work-unit: `feat(ocr): add RapidOCRAdapter, factory, config engine field, container wiring (PR#2)`.
  No push. (SA-3)

---

## PR#3 — Dependencies + Docker Air-Gap + uv.lock + CONT Assertions + Real-Data Gate + Deploy Flip

> Scope boundary: `pyproject.toml` `[ocr]` extra, `Dockerfile` builder/runtime/test stages, `uv.lock`, `docker-compose.yml` env, `tests/integration/test_rapidocr_gate.py`.
> This is the activation PR — behaviour changes in deploy when merged.

### Phase 3.1 — RED: Write failing real-data integration gate

- [ ] **3.1.1** Create `backend/tests/integration/test_rapidocr_gate.py` with `@pytest.mark.slow`.
  Write failing test `test_page_0156_4_rows_exact` — reads page 0156 from `CTR_PDF_PATH` (skipped if unset), runs REAL `RapidOCRAdapter`, asserts 4 rows with exact multiset `{(0.008,TN),(0.136,TN),(0.191,TN),(0.041,TN)}`.
  Spec: EXT-031 / S031a. Design: §9.

- [ ] **3.1.2** Add failing test `test_page_0148_3_rows_exact` — page 0148, 3 rows: `{(0.037,TN),(0.014,TN),(0.102,TN)}`.
  Spec: EXT-031 / S031b.

- [ ] **3.1.3** Add failing test `test_page_0160_4_rows_acero_dimensionado_exact` — page 0160, 4 rows: `{(1.616,TN),(0.238,TN),(1.643,TN),(0.121,TN)}`.
  Spec: EXT-031 / S031c.

- [ ] **3.1.4** Add failing test `test_domain_invariants_e2e_no_unit_conversion` — run a 2-row table with KG and TN rows through real adapter + mock reconciler; assert KG and TN sums are independent (no conversion).
  Spec: EXT-032 / S032b. Domain invariant end-to-end.

### Phase 3.2 — GREEN: Deps + Docker + uv.lock + deploy flip

- [ ] **3.2.1** Add `[project.optional-dependencies]` group `ocr` to `backend/pyproject.toml`:
  `rapidocr>=3.8.1,<3.9`, `onnxruntime`, `Pillow>=10.0`, `numpy>=1.26`.
  Run `cd backend && uv lock --extra ocr` to update `uv.lock`. Commit lockfile.
  Spec: EXT-033. Design: §7.

- [ ] **3.2.2** Update `Dockerfile` builder stage: change `uv sync` line to `uv sync --frozen --no-dev --extra identity --extra llm --extra ocr`.
  Add PP-OCRv5-server build-time warm-up `RUN` step immediately after the sync (per design §7 Dockerfile sketch). Network available at build time — this bakes the 165MB weights into `.venv/site-packages/rapidocr/models/`.
  Spec: EXT-033. Design: §7.

- [ ] **3.2.3** Add runtime CONT assertion to Dockerfile runtime stage (alongside existing paddle-absence assertion at L52):
  Construct `RapidOCR(params=...)` offline — must succeed (weights baked).
  Retain existing paddle-absence assertion (Dockerfile:55-58) unchanged.
  Spec: EXT-033 / S033a-c. Design: §7.

- [ ] **3.2.4** Add the same `--extra ocr` + warm-up + offline assert to the Dockerfile **test stage** (L84-102) so in-container pytest can run the real-data gate offline.
  Design: §7.

- [ ] **3.2.5** Update `docker-compose.yml`: add `RECONCILIATION__OCR__ENABLED=true` and `RECONCILIATION__OCR__ENGINE=rapidocr` to the backend service env.
  Spec: EXT-027 (deploy defaults). Design: §8. **This is the deploy-flip — engine activates in production.**

- [ ] **3.2.6** Run real-data gate with `CTR_PDF_PATH` set:
  `cd backend && uv run pytest tests/integration/test_rapidocr_gate.py -v -m slow`
  All 4 tests must be GREEN against the real PDF. This is the proof-of-correctness (CLAUDE.md Fix Discipline #2 — real data over mock theatre).

- [ ] **3.2.7** [Design risk #1 — full-PDF orientation validation] Before merging PR#3, run a wider orientation check: render a broader sample of guía pages (not just 3 GT pages) through `RapidOCRAdapter` with the real PDF, inspect row-count distribution. If any pages return 0 rows from all orientations, log and investigate before the deploy flip. This is a SA-5-style runtime check.

- [ ] **3.2.8** Run containerized verify gate (`make verify` or Compose-based): build Docker image, run CONT assertions inline. Both CONT-S0x (rapidocr import + offline weights) and CONT-S02 (paddle absence) must pass.
  Spec: EXT-033 / S033a-c.

- [ ] **3.2.9** Run full test regression (non-slow, paddle-free):
  `cd backend && uv run pytest tests/unit/ -v --ignore=tests/integration`
  All existing tests must remain GREEN. No regression from container wiring changes.

- [ ] **3.2.10** Commit work-unit: `feat(ocr): add rapidocr deps, Docker air-gap bundling, CONT assertions, real-data gate, deploy flip (PR#3)`.
  No push. (SA-3)

---

## Dependency Graph

```
PR#1 tasks (1.1.x → 1.2.x)
    ↓
PR#2 tasks (2.1.x → 2.2.x)  [depends on PR#1 parser being importable]
    ↓
PR#3 tasks (3.1.x → 3.2.x)  [depends on PR#2 RapidOCRAdapter being wired]
```

All tasks within each PR phase are sequential. Tasks across PRs are sequential (no parallelism across PR boundaries — each PR must be GREEN before the next starts).

**Within PR#1**: tasks 1.1.1–1.1.12 (RED) can be written in any order within the test file, but all must exist before 1.2.1 (GREEN).
**Within PR#2**: tasks 2.1.x (RED) before 2.2.x (GREEN). Config (2.2.1) before factory (2.2.2) before adapter (2.2.3) before container (2.2.4).
**Within PR#3**: task 3.2.1 (deps/lockfile) before 3.2.2–3.2.5 (Dockerfile/compose). Real-data gate (3.1.x + 3.2.6) before the deploy flip review (3.2.7) before containerized verify (3.2.8).

---

## Files Created/Modified

| File | PR | Action |
|------|-----|--------|
| `backend/src/reconciliation/adapters/ocr/box_row_parser.py` | #1 | CREATE |
| `backend/tests/unit/adapters/test_box_row_parser.py` | #1 | CREATE |
| `backend/src/reconciliation/application/config.py` | #2 | MODIFY (add `engine` field) |
| `backend/src/reconciliation/adapters/ocr/factory.py` | #2 | CREATE |
| `backend/src/reconciliation/adapters/ocr/rapid_table.py` | #2 | CREATE |
| `backend/src/reconciliation/infrastructure/container.py` | #2 | MODIFY (lines 378-392) |
| `backend/tests/unit/adapters/test_rapid_table.py` | #2 | CREATE |
| `backend/tests/unit/adapters/test_ocr_factory.py` | #2 | CREATE |
| `backend/tests/unit/application/test_config.py` | #2 | MODIFY (extend) |
| `backend/tests/unit/infrastructure/test_container.py` | #2 | MODIFY (extend) |
| `backend/pyproject.toml` | #3 | MODIFY (add `[ocr]` extra) |
| `backend/uv.lock` | #3 | UPDATE (pin rapidocr 3.8.x) |
| `backend/Dockerfile` | #3 | MODIFY (builder + runtime + test stages) |
| `docker-compose.yml` | #3 | MODIFY (deploy env flip) |
| `backend/tests/integration/test_rapidocr_gate.py` | #3 | CREATE |

**Domain/ files**: ZERO modifications (domain purity invariant).
**pipeline.py**: ZERO modifications (pipeline zero-concrete-adapters invariant).
