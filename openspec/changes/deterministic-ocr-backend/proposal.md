# Proposal — deterministic-ocr-backend (SDD#1)

> Status: **APPROVED plan** (user + empirical probe). This proposal formalizes already-locked
> decisions; it does not re-open them. Artifact store: hybrid (engram + openspec).

## 1. Intent / Problem

**Root cause of issue #40 (vision quantity inaccuracy): deterministic OCR is OFF in the deployed
runtime.** The production image is paddle-free and runs with
`RECONCILIATION__OCR__ENABLED=false`, so the **vision LLM carries 100% of guía quantity
extraction**. The #40 eval proved no single vision model is reliable for printed GRE tables
(kimi-k2.5:cloud 83.1% > qwen3.5:397b-cloud 76.9%; neither trustworthy alone), and every
vision-recovered line must be flagged `requires_review`. Vision is the wrong tool for a
**deterministic, printed** columnar table.

**Why now**: an empirical probe confirmed a paddle-free deterministic OCR path is viable —
RapidOCR PP-OCRv5-server (ONNXRuntime, no paddlepaddle) read GT page 156 with **4/4 ground-truth
quantities exact** at 3.4 s/page. Re-enabling deterministic OCR as the PRIMARY quantity extractor
demotes vision to its correct role: a **rare fallback** for date reads and illegible pages.

**Success looks like**: in the deployed paddle-free image, scanned guía printed tables are read
deterministically and reconciled EXACT against the trusted digital declared side; vision is no
longer on the critical path for printed-table quantities; the #40 accuracy problem is eliminated
for printed tables; no domain rule, grouping axis, or reconciliation behavior changes.

## 2. Scope

### In scope (SDD#1 — backend only)
- **New `RapidOCRAdapter`** implementing `ExtractionPort.extract_printed_table(image: bytes) ->
  list[MaterialLine]` (`domain/ports.py:61`); `extract_declared` is a no-op `[]`. Lazy-imports
  `rapidocr`/`onnxruntime`/`numpy` INSIDE methods.
- **Provider-agnostic OCR factory** `adapters/ocr/factory.py::build_ocr_extractor(cfg) ->
  ExtractionPort`, mirroring `adapters/vision/factory.py::build_vision_adapter`. Sole place that
  imports concrete OCR adapters; selection by config; lazy import inside the body.
- **Config selector**: `OcrConfig.engine: Literal["paddle","rapidocr"] = "paddle"` (additive;
  `OcrConfig` already has `extra="allow"`, config.py:236). Backward-compat default `paddle`.
- **Container wiring** at the existing OCR decision point (`container.py:378-392`):
  `enabled=False → NullOcrExtractor`; `engine=rapidocr → RapidOCRAdapter`;
  `engine=paddle → PrintedTableAdapter`.
- **Layout-aware box-row parser** — a PURE function unit (no RapidOCR dependency) replacing the
  failing `paddle_table.py _LINE_RE` one-liner. Centroid-per-box → DESC/QTY classification →
  nearest-QTY-to-the-right within a **DPI-scaled** row band (40px @ 200 DPI baseline) → `TNE→TN`.
  Generalize the corpus-specific `_DESC_RE` descriptor matcher and `_QTY_RE` amount detector.
- **Self-scoring orientation auto-fix** (parser-as-orientation-oracle): default rotate **-90°**
  (the known reg227 invariant), run the box-row parser; **if 0 valid lines → retry 0/90/180/270
  and pick the rotation that yields the most valid rows**. Applies ONLY in the OCR/guía path;
  Protocolo / non-guía pages stay upright (never force-rotated). No new model/dependency.
- **Deps + Docker air-gap**: new `[project.optional-dependencies] ocr =
  ["rapidocr","onnxruntime","Pillow>=10.0","numpy>=1.26"]`; Dockerfile builder adds `--extra ocr`;
  `uv.lock` updated; runtime CONT assertion `import rapidocr`; existing paddle-absence assertion
  retained (rapidocr does NOT pull paddlepaddle). **Model bundling**: v5-server `.onnx` weights
  (~165 MB: det 84 MB + rec 81 MB) must be present in the air-gapped image via a build-time
  warm-up `RapidOCR(params=...)` OR a COPY of pre-downloaded weights to the exact venv-relative
  `rapidocr/models/` path.
- **Deploy runtime defaults**: `RECONCILIATION__OCR__ENABLED=true` +
  `RECONCILIATION__OCR__ENGINE=rapidocr`.
- **strict-TDD validation** (project `strict_tdd:true`; runner `cd backend && uv run pytest`):
  failing-test-first, in order — (a) pure box-row parser unit tests; (b) `RapidOCRAdapter` unit
  tests with an injected `_engine` mock (mirror `tests/unit/adapters/test_paddle_table.py` `_ocr`
  injection); (c) real-data integration gate vs GT pages 0148/0156/0160
  (`docs/eval/ground_truth.md`), `@pytest.mark.slow` keyed on `CTR_PDF_PATH`.

### Explicit non-goals (deferred or out of scope)
- **Issue #50 sentinel-emit / API / UI** → **SDD#2**. SDD#1 stays backend-only: NO API/schema
  changes, NO new domain model surfaced to the API. With OCR on, more GUIA pages get
  `len(lines)>0`, so fewer hit the silent drop at `pipeline.py:976-982` — this is an **implicit
  improvement only**, not the full fix.
- **Cross-model vision consensus (#44)** — out of scope.
- **No domain-rule changes**: grouping key stays `(registro, material_canonical, unidad)`; `fecha`
  is NEVER a grouping axis; units never converted; reconciliation tolerance stays EXACT.
- **No reconciliation / grade-matching changes**: the parser output feeds the EXISTING canonical
  matching (Tier-1 dual-spec + Tier-2 grade-tolerant) unchanged. OCR is upstream of, and does not
  alter, `material-canonical-matching`.
- **Paddle path NOT removed** — retained as a dev/optional engine (`ml` extra) for parity.

## 3. Approach (architectural altitude — hexagonal placement)

- **Adapter (driven, OCR boundary)**: `RapidOCRAdapter` lives under `adapters/ocr/`, behind the
  existing `ExtractionPort`. It is the only thing that touches the RapidOCR/ONNX SDK, lazily. It
  owns the side-effectful concerns — engine instantiation and the self-scoring orientation retry
  loop (an adapter concern, not domain).
- **Factory (composition seam)**: `adapters/ocr/factory.py::build_ocr_extractor(cfg)` is the
  provider-agnostic selector — the direct structural mirror of the vision factory. It is the ONLY
  module importing concrete OCR adapters; `container.py` calls it at the current OCR decision
  point. Selection is pure config (`ocr.enabled`, `ocr.engine`) — never vendor-bound.
- **Pure parser unit**: the box-row reconstruction is a standalone PURE function (input: list of
  `(box, txt, score)` cells; output: `list[MaterialLine]`-shaped rows). It has ZERO RapidOCR
  dependency, so it is unit-tested in isolation and reused regardless of OCR engine. This is the
  Humble Object pattern — push logic out of the hard-to-test SDK boundary into a pure core.
- **Self-scoring orientation as an adapter strategy**: because `page.rotation == 0` for all
  scanned pages (rotation lives in the bitmap, not PDF metadata) and RapidOCR `Cls` only fixes
  0/180, orientation is resolved by **using the parser itself as the oracle** — rotate, parse,
  score by valid-row count. This keeps orientation a self-correcting adapter concern with no model
  dependency and cost only on the rare mis-oriented page.
- **Domain / application untouched**: `application/pipeline.py` imports ZERO concrete adapters
  (depends only on `ExtractionPort`); `domain/` stays pure. The parser output flows into the
  existing extract stage → canonical matching → reconciliation gate with no behavioral change.
- **Reconciliation remains the validation gate**: OCR reads are reconciled EXACT vs the trusted
  digital declared side; mismatches are flagged `requires_review`, NEVER auto-corrected.

## 4. Risks / Tradeoffs

| Risk | Trigger | Impact | Mitigation |
|------|---------|--------|------------|
| Air-gap model bundling | Runtime has no network; weights not in venv | First OCR call fails (auto-download blocked) | Build-time warm-up `RapidOCR(params=...)` OR COPY pre-downloaded `.onnx` to exact venv-relative `models/` path; add `import rapidocr` CONT assertion |
| DPI / row-band calibration | Pages rendered at non-200-DPI | Wrong DESC↔QTY pairing if 40px band hardcoded | Scale the row band by DPI; do not hardcode for 200 DPI |
| Corpus-specific descriptor regex | Materials outside `BARR/ACERO/A615/A706` | DESC cells not recognized → dropped rows | Generalize the descriptor matcher beyond the PoC `_DESC_RE` |
| Full-PDF orientation unvalidated | Pages outside reg227's 165 (only those proven uniform -90°) | Mis-oriented page parses to garbage | Self-scoring retry (0/90/180/270, most-valid-rows wins) self-corrects per page |
| Vision still needed for dates / illegible pages | Printed table unreadable or date read | Vision remains on a narrow path | Intended — vision demoted to rare fallback, `requires_review` retained |
| Paddle path drift | Dev keeps `engine=paddle` | Two engines to maintain | Retain paddle as optional dev engine only; deploy default is rapidocr |

## 5. First reviewable slice boundary + changed-line forecast

**Recommended first slice (PR #1) — the pure, dependency-light core:**
the **box-row parser pure unit + its strict-TDD test suite**, with NO RapidOCR/ONNX/Docker
coupling. It is independently reviewable, fully testable without the heavy SDK, and de-risks the
algorithmic heart (DESC↔QTY pairing, DPI-scaled band, `TNE→TN`) before any deps land.

**Subsequent slices** (likely separate PRs given dep + air-gap weight):
- PR #2 — `RapidOCRAdapter` + `build_ocr_extractor` factory + `OcrConfig.engine` + container wiring
  + adapter unit tests (injected `_engine` mock).
- PR #3 — deps/extras + Dockerfile `--extra ocr` + model bundling/warm-up + `uv.lock` + CONT
  assertions + real-data integration gate + deploy-default flip.

**Rough changed-line forecast (planning signal for tasks Review Workload Forecast / ask-on-risk):**

| Area | Est. changed LOC |
|------|------------------|
| Box-row parser (pure) + tests | ~180–240 |
| RapidOCRAdapter + adapter tests | ~160–220 |
| Factory + config selector + container wiring | ~60–90 |
| Deps/Dockerfile/model-bundling/CONT + integration gate | ~90–140 |
| **Total** | **~490–690** |

> Forecast **exceeds the 400-line single-PR budget** → **chained/stacked PRs recommended** along
> the slice boundary above. The orchestrator should resolve this against the cached
> `delivery_strategy` at the Review Workload Guard before apply.

## 6. Rejected alternatives (do not re-propose)
- **Hardcoded -90° with no self-correction** — brittle across the full 493-page PDF (only reg227's
  165 pages validated uniform -90°).
- **Vision-only quantity extraction (status quo)** — the #40 root cause being fixed here.
- **Removing the paddle path** — kept as an optional dev engine for parity.

## 6b. Documented future upgrade path (NOT in SDD#1 scope)
- **ONNX doc-orientation model (escalation, not rejected).** Deferred — NOT implemented now. The
  self-scoring orientation auto-fix resolves mixed orientations **reactively** (per-page retry
  0/90/180/270). The ONNX doc-orientation classifier (same `onnxruntime`, no paddle) is the
  **proactive** escalation if guías start arriving in **diverse/unpredictable orientations** and the
  reactive retry becomes a **recurring cost** rather than a rare-page exception.
  **Trigger to revisit**: a measurable share of guía pages need a non-`-90°` rotation (i.e. the
  self-scoring fallback fires frequently in production), making a single up-front orientation read
  cheaper than the 4-way retry. Until that signal appears, self-scoring is the correct, dep-free
  default. Captured per user request (2026-06-06).

## 7. Open / partial items (SA-2 — flagged, not improvised)
None blocking. Two calibration parameters are intentionally deferred to spec/design rather than
guessed here:
- The exact **DPI-scaling formula** for the row band (baseline 40px @ 200 DPI) — to be fixed in
  spec against the real render DPI.
- The **generalized descriptor-matcher** definition beyond the corpus `_DESC_RE` — to be specified
  in design so it does not silently drop non-rebar materials.
These are design-detail, not architectural unknowns, so this proposal is **`done`**, not partial.
