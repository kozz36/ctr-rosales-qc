# Evaluation Results — Vision Quantity-Accuracy & OCR Engine

Engineering record for two sequential evaluations run on 2026-06-05/06, both using real
reg227 guía pages from the production document. Full raw artifacts are in `docs/eval/`
(gitignored — contains real QC data; do not commit).

---

## 1. Vision Quantity-Accuracy Eval (#40)

**Date**: 2026-06-05 / 06. **Issue**: [#40](../../issues/40). **Status**: CLOSED.

### Goal

Select the cloud vision model for `read_material_table` (guía reprocess). The prior pipeline
had `RECONCILIATION__OCR__ENABLED=false` and no decodable QR in reg227, making vision the
sole extractor. An early run observed kimi reading `0.091` for a line where the GRE table
printed `0.191`. The eval determines whether kimi or qwen3.5:397b-cloud is more accurate, and
whether the mandatory `requires_review=True` gate on every recovered line is sufficient.

### Methodology

- **N = 5 runs** per model per guía. Same input image per run (no re-render between runs).
- **Image rendering**: 300 DPI, max edge 2000px — the same resolution the production
  pipeline renders (faithful, not a higher-quality test set).
- **5 curated guías** from 3 GRE series covering different guía sizes (1-line and 4-line
  tables). Ground truth read from 300-DPI zoomed crops of the printed GRE tables;
  cross-validated by model agreement.
- **Metric**: exact quantity match per (guía × diameter × run) = per line-run. Total pool:
  5 guías × up to 4 lines × 5 runs = 65 line-runs per model (some guías have 1 line only).
- **Driven from the frontend** (SA-5): upload → review → Reprocesar con IA → rendered DOM
  inspection. API-only backend pass used to establish the harness; frontend pass is the gate.
- **Models under test**: `qwen3.5:397b-cloud` and `kimi-k2.5:cloud` via
  `OpenAICompatibleVisionAdapter` through Ollama cloud base_url.

### Ground Truth (curated guías — anonymized page refs)

| Guía | Page idx | Lines | Quantities (TNE) |
|------|----------|-------|-----------------|
| A (8MM series) | 0141 | 1 | 2.489 |
| B (3-line) | 0148 | 3 | 0.037 / 0.014 / 0.102 |
| C (4-line) | 0156 | 4 | 0.008 / 0.136 / 0.191 / 0.041 |
| D (ACERO DIMENSIONADO, 4-line) | 0160 | 4 | 1.616 / 0.238 / 1.643 / 0.121 |
| E (1-line) | 0164 | 1 | 0.213 |

### Per-guía Results

#### qwen3.5:397b-cloud

| Guía | Line | GT | Exact | Values (5 runs) | Failure mode |
|------|------|----|-------|-----------------|--------------|
| A — 8MM | 8MM | 2.489 | 5/5 | 2.489×5 | — |
| B — 3/8" | 3/8" | 0.037 | 5/5 | 0.037×5 | — |
| B — 1/2" | 1/2" | 0.014 | 5/5 | 0.014×5 | — |
| B — 5/8" | 5/8" | 0.102 | 5/5 | 0.102×5 | — |
| C — 3/8" | 3/8" | 0.008 | **0/5** | 0.608×5 | Systematic misread (decimal shift) |
| C — 1/2" | 1/2" | 0.136 | 5/5 | 0.136×5 | — |
| C — 5/8" | 5/8" | 0.191 | 4/5 | 0.191×4, 0.151×1 | Flaky |
| C — 3/4" | 3/4" | 0.041 | 5/5 | 0.041×5 | — |
| D — 3/8" | 3/8" | 1.616 | 2/5 | 1.615×3, 1.616×2 | Sub-digit rounding |
| D — 5/8" | 5/8" | 0.238 | 5/5 | 0.238×5 | — |
| D — 3/4" | 3/4" | 1.643 | **0/5** | 1.843×5 | Systematic misread (0.200 over) |
| D — 1" | 1" | 0.121 | 5/5 | 0.121×5 | — |
| E — 3/8" | 3/8" | 0.213 | 4/5 | 0.213×4, null×1 | Empty return |

**qwen summary: 50/65 = 76.9%**. Avg elapsed: 24–36 s/call.

#### kimi-k2.5:cloud

| Guía | Line | GT | Exact | Values (5 runs) | Failure mode |
|------|------|----|-------|-----------------|--------------|
| A — 8MM | 8MM | 2.489 | 4/5 | 2.489×4, null×1 | Empty return |
| B — 3/8" | 3/8" | 0.037 | 1/5 | 0.037×1, null×3, 0.637×1 | Empty + leading-digit hallucination |
| B — 1/2" | 1/2" | 0.014 | 3/5 | 0.014×3, null×1, 0.314×1 | Empty + hallucination |
| B — 5/8" | 5/8" | 0.102 | 4/5 | 0.102×4, null×1 | Empty return |
| C — 3/8" | 3/8" | 0.008 | 5/5 | 0.008×5 | — |
| C — 1/2" | 1/2" | 0.136 | 5/5 | 0.136×5 | — |
| C — 5/8" | 5/8" | 0.191 | 4/5 | 0.191×4, 0.151×1 | Flaky |
| C — 3/4" | 3/4" | 0.041 | 5/5 | 0.041×5 | — |
| D — 3/8" | 3/8" | 1.616 | 5/5 | 1.616×5 | — |
| D — 5/8" | 5/8" | 0.238 | 5/5 | 0.238×5 | — |
| D — 3/4" | 3/4" | 1.643 | 5/5 | 1.643×5 | — |
| D — 1" | 1" | 0.121 | 5/5 | 0.121×5 | — |
| E — 3/8" | 3/8" | 0.213 | 3/5 | 0.213×3, null×2 | Empty return |

**kimi summary: 54/65 = 83.1%**. Avg elapsed: 11–17 s/call.

### Comparative Summary

| Metric | qwen3.5:397b-cloud | kimi-k2.5:cloud |
|--------|-------------------|-----------------|
| Exact line-runs | 50/65 | **54/65** |
| Accuracy | 76.9% | **83.1%** |
| Avg call latency | 24–36 s | **11–17 s** |
| Empty returns | Rare (1 case) | Frequent (~20–40% on some pages) |
| Systematic misreads | **Yes** (0.608 for 0.008; 1.843 for 1.643) | No |
| Flaky hallucinations | No | Yes (0.637, 0.314 on guía B) |

### Failure Mode Characterization

**qwen** errors are **deterministic and non-recoverable by retry**: the same wrong value is
returned across all 5 runs (e.g. guía C 3/8" returns 0.608 in 5/5 runs; guía D 3/4" returns
1.843 in 5/5 runs). The discordance is qualitatively distinguishable from kimi on those same
lines (kimi returns 0.008 and 1.643 respectively — correct).

**kimi** errors are **stochastic empty-returns**: the model returns `[]` rather than a wrong
value. A retry has a non-zero probability of success. The occasional leading-digit
hallucination (0.637, 0.314) on guía B is isolated and non-systematic.

**Cross-model diagnostic**: the two models fail on different lines with qualitatively
complementary error patterns. Where qwen errs systematically, kimi is correct and they
disagree — this is the basis for the consensus upgrade path (#44).

### Verdict

**Neither model alone is reliable enough to accept results without human review.** The
`requires_review=True` gate on every recovered/grade_tolerant line is **mandatory and
non-negotiable** — it is the only safety net against silent quantity errors.

**Selected model: kimi-k2.5:cloud** (faster, no systematic misreads, higher accuracy).
Recommended config: `provider=ollama, OLLAMA__MODEL=kimi-k2.5:cloud, DEADLINE_S=60`.
qwen3.5:397b-cloud requires `DEADLINE_S≥45` due to latency.

**Upgrade path**: cross-model consensus reprocess (#44) — run both models per guía, accept
on agreement (within tolerance 0), flag `requires_review` on any disagreement or empty.
Catches all single-model systematic errors. Deferred until deterministic OCR (#SDD1) is
deployed (OCR eliminates the quantity-accuracy problem for most guías).

### SA-5 Frontend Validation

Frontend-driven: kimi recovered guía C (page 0156, 4 lines) correctly via the Reprocesar
con IA button. Rendered DOM confirmed `requires_review` badges on all recovered rows.
`reprocess` button gated on `retry_attempted`. Table invalidation on reprocess-success.
Run ID e575f4fd.

---

## 2. OCR Engine Eval (pre-SDD)

**Date**: 2026-06-06. **Engram**: `ocr-engine-eval` (#3023). **Status**: COMPLETE.

### Goal

Determine whether deterministic OCR is viable for guía quantity extraction, and which
engine to adopt for SDD#1 (replacing the `RECONCILIATION__OCR__ENABLED=false` + vision-only
deploy configuration).

### Why vision was carrying extraction

Every reg227 guía page has `text_len≈159` — only the Forma report header/footer is present as
PDF text. The GRE table (printed quantities) is inside an embedded raster image. PDF-text
parsing cannot read it. The deployed app has `RECONCILIATION__OCR__ENABLED=false` and paddle
is NOT in the runtime image (`Dockerfile` target=`runtime` is paddle-free), making vision the
sole extractor for all 24 reg227 guías. This is the structural root cause of #40: vision is
a poor substitute for OCR on printed raster text.

### Methodology

- **3 guías** from the #40 ground-truth set (pages 0148, 0156, 0160) — chosen because their
  quantities are confirmed. The eval feeds the same page images used in the vision eval.
- Paddle ran on the host `uv` dev environment (dev-only, not the runtime image).
- RapidOCR tests run in ONNX mode (no paddlepaddle dependency).
- **Box reconstruction** (PoC): for RapidOCR, a layout-aware parser was prototyped to
  associate DETALLE + UNIDAD + CANTIDAD cells by y-center bounding-box row.
- De-rotation applied via explicit -90° rotation before OCR on the final RapidOCR run (guías
  are scanned sideways in the PDF).

### Engine Comparison

| Engine | Config | Page 0148 (3 lines) | Page 0156 (4 lines) | Page 0160 (4 lines) | Cells | Speed | Deployable |
|--------|--------|---------------------|---------------------|---------------------|-------|-------|------------|
| Paddle PP-OCRv5 server | lang=es, host dev env | **3/3** | 0/4* | **4/4** | 112–116 | ~4 s | **No** (excluded from runtime image) |
| RapidOCR PP-OCRv4 mobile | ONNX, default | 0/3 | 0/4 | 0/4 | 66–81 | ~1.2 s | Yes |
| RapidOCR PP-OCRv5 server | ONNX, no de-rotate | 0/3 | 1/4 | 3/4 | 67–78 | ~3 s | Yes |
| **RapidOCR PP-OCRv5 server** | **ONNX + de-rotate** | **3/3** | **4/4** | **4/4** | 107–117 | **~3 s** | **Yes** |

\* Paddle on page 0156 reads all cells correctly but the current `_LINE_RE` parser extracts
0/4 quantity lines (parser limitation, not engine limitation — see Blocker 1 below).

### Key Finding

**Deterministic OCR reads the printed GRE tables exactly when properly oriented.** The
quantities read by RapidOCR PP-OCRv5-server + de-rotate are:

- Page 0148: 0.037 / 0.014 / 0.102 (3/3 exact)
- Page 0156: 0.008 / 0.136 / 0.191 / 0.041 (4/4 exact)
- Page 0160: 1.616 / 0.238 / 1.643 / 0.121 (4/4 exact)

The vision quantity-accuracy problem (#40, kimi 83% / qwen 77%) is largely an artifact of
running OCR-off. Deterministic OCR is the correct extraction path for printed GRE tables.

### Real Blockers (neither is the OCR engine itself)

**Blocker 1 — Parser not layout-aware.**
`paddle_table.py::_LINE_RE` expects `<desc> <qty> <TN|KG|RD|Rollo>` on a single line. The
GRE table is **columnar** (DETALLE, UNIDAD, CANTIDAD in separate bounding-box cells), and the
unit printed is `TNE` (not `TN`). The parser extracts 0 material lines even when OCR reads
all cells. A layout-aware parser is required: associate cells by y-center row, accept `TNE→TN`.
The PoC bounding-box row associator in `docs/eval/ocr_compare.py` already works for this.

**Blocker 2 — Page orientation.**
Guías are scanned sideways in the PDF (native 90° rotation). OCR needs an upright image.
Paddle handles this internally (PP-LCNet doc-orientation + UVDoc unwarp + textline-orientation).
A lean ONNX engine needs an explicit page-level de-rotation step before inference. Note:
RapidOCR `cls` mode is textline-level only — page-level document orientation detection is a
separate step (e.g. RapidOCR doc-orientation model, or a lightweight 4-way scorer).
**Do not hardcode -90°**; production needs auto-orientation per page.

### Deploy Recommendation

**RapidOCR (PP-OCRv5 server, ONNXRuntime)** + auto page-orientation/deskew + layout-aware
box parser.

Install: `pip install rapidocr onnxruntime` (enums `OCRVersion.PPOCRV5`, `ModelType.SERVER`).
No `paddlepaddle` required → fits the runtime image → re-enable `RECONCILIATION__OCR__ENABLED=true`.

With OCR re-enabled:
- Deterministic quantity extraction for all guías with printed GRE tables.
- Vision becomes the **rare fallback** (illegible pages, other-provider flows, date reads only).
- The #40 vision accuracy problem substantially reduced (OCR reads printed text exactly; vision
  is no longer the primary quantity extractor).
- #50 impact reduced: when OCR provides a quantity-line evidence, identity inference has more
  signal even if QR fails.

### How to Reproduce

All scripts are in `docs/eval/` (gitignored):

```
docs/eval/
  ocr_probe.py         # paddle raw proof — reads GRE tables on the host dev env
  ocr_probe.log        # probe output (raw OCR cells)
  ocr_compare.py       # engine comparison + box-based row reconstruction (PoC parser)
  ocr_compare.json     # engine comparison results (paddle + rapidocr variants)
  ground_truth.md      # confirmed GT quantities for the 5 curated guías
  eval_results.json    # vision N=5 per-model results (full run_raw)
  run_eval.py          # vision harness (feeds model the same image the pipeline renders)
  pages_hires/         # 300 DPI renders for visual verification
```

To re-run the vision harness against a different model, set `OLLAMA_BASE_URL` and
`OLLAMA_MODEL` and invoke `run_eval.py`. No re-billing needed for analysis: raw values are
saved in `eval_results.json`.

---

## 3. Summary & Next Action

| Finding | Conclusion |
|---------|-----------|
| kimi 83.1% vs qwen 76.9% quantity accuracy | kimi selected; neither reliable without `requires_review` gate |
| qwen errors are deterministic; kimi errors are stochastic empty-returns | Cross-model consensus (#44) is the accuracy upgrade |
| Deterministic OCR reads GRE tables exactly (RapidOCR ONNX PP-OCRv5 + de-rotate) | OCR is viable — blockers are parser + orientation, not the engine |
| OCR off in deploy is the structural root cause of the vision accuracy problem | SDD#1: re-enable OCR with RapidOCR ONNX + layout-aware parser |
| Vision still needed for handwritten stamp dates | Vision remains, but scope narrows to date reads + illegible-page fallback |

**Next work**: SDD#1 — deterministic OCR backend (RapidOCR ONNX adapter + auto-orientation +
layout-aware parser + re-enable OCR path). See `docs/HANDOFF.md` §SDD-plan.
