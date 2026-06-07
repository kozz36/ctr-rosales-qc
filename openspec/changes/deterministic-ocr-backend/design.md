# Design — deterministic-ocr-backend (SDD#1)

> Technical design for the user-APPROVED SDD#1. Architecture is LOCKED in the proposal
> (`sdd/deterministic-ocr-backend/proposal`). This document is the concrete HOW: module
> layout, signatures, the two deferred calibration decisions, and the air-gap bundling
> mechanism. It does NOT re-open any locked decision.

## 0. Architecture recap (locked — not re-opened)

Hexagonal / Ports & Adapters. `ExtractionPort` (`domain/ports.py:54`) is the driven port.
RapidOCR is a new **driven adapter** behind it; a provider-agnostic **factory** is the only
concrete-OCR importer; the box→row association is a **pure Humble Object** with zero RapidOCR
dependency. `pipeline.py` imports zero concrete adapters; `domain/` stays pure. Vision (LLM)
is demoted to the rare fallback (dates + illegible pages); no domain-rule, key, tolerance,
unit, or reconciliation change.

---

## 1. Module layout

```
backend/src/reconciliation/adapters/ocr/
  box_row_parser.py     # NEW — PURE. No rapidocr/onnxruntime import. The Humble Object.
  rapid_table.py        # NEW — RapidOCRAdapter (ExtractionPort). Sole RapidOCR SDK toucher (lazy).
  factory.py            # NEW — build_ocr_extractor(cfg). Sole concrete-OCR importer. Mirrors vision/factory.py.
  paddle_table.py       # RETAINED — PrintedTableAdapter, optional dev engine (paddle path).
  null_extractor.py     # RETAINED — NullOcrExtractor (enabled=False).
```

### 1.1 Where the pure parser lives — and why

`box_row_parser.py` is placed under `adapters/ocr/` as a **pure module**, NOT under `domain/`.

**Justification (two competing constraints, both satisfied):**

- It must be **importable without rapidocr/onnxruntime** so PR#1 unit tests run on the
  paddle-free, rapidocr-free CI and the test image. → it imports ONLY stdlib + `domain.models`
  (`MaterialLine`) + `domain.normalizer` (`MaterialNormalizer`). Zero heavy deps at module top.
  This already satisfies the "pure, isolated, unit-testable" requirement.
- It is **OCR-layout logic, not a domain invariant.** Box centroids, DPI bands, and DESC/QTY
  geometry are an OCR-engine output-shape concern. Putting geometry under `domain/` would leak
  an adapter-shaped concept (pixel boxes) into the pure core — the inverse anti-pattern. The
  domain core already owns the *real* invariant: the canonical key + tolerance, via
  `MaterialNormalizer` and the reconciler. The parser's only job is to produce the SAME
  `description_raw` text shape the existing normalizer already consumes.

So: **pure module, adapter package, depends only on domain models + normalizer.** This mirrors
how `paddle_table.py::_parse_lines` is already a pure method living in the adapter package — we
extract that responsibility into a standalone, engine-independent, fully unit-tested module.

**Domain-purity invariant check:** `box_row_parser.py` imports `domain.models.MaterialLine` and
`domain.normalizer.MaterialNormalizer` (both already pure). It imports NO SDK/IO. `rapid_table.py`
imports rapidocr/onnxruntime/numpy/PIL **inside methods only**. `factory.py` imports
`rapid_table`/`paddle_table` **inside the function body only**. `pipeline.py` unchanged — still
zero concrete adapters. PASS.

---

## 2. Signatures

### 2.1 Pure parser — `box_row_parser.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

from reconciliation.domain.models import MaterialLine
from reconciliation.domain.normalizer import MaterialNormalizer

_NORMALIZER = MaterialNormalizer()  # pure, no deps

@dataclass(frozen=True, slots=True)
class Cell:
    """One OCR text box, engine-independent. Built from RapidOCROutput by the adapter.

    cx/cy are the polygon centroid (mean of the 4 corner xs/ys), in PIXELS at the
    render DPI. The adapter computes these from res.boxes; the parser never sees a
    raw RapidOCR object — that keeps this module SDK-free.
    """
    text: str
    conf: float
    cx: float
    cy: float

def parse_box_rows(cells: list[Cell], dpi: int) -> list[MaterialLine]:
    """Associate each DESC cell to its nearest QTY cell on the same row.

    PURE. No IO, no SDK. Algorithm (generalized from docs/eval/ocr_compare.py:57-76):
      1. Classify each cell as QTY (amount-shape) or DESC (the rest) — see §5.
      2. band_px = row-band threshold scaled by dpi — see §4.
      3. For each DESC (sorted by cy), pick the nearest UNUSED QTY where
         |qcy - dcy| < band_px AND qcx > dcx (qty column is right of detalle).
      4. Emit MaterialLine(description_raw=desc, canonical=normalize(desc),
         unidad, cantidad, confidence=min(desc_conf, qty_conf), requires_review).
    Returns [] when no DESC/QTY pair associates (the orientation oracle reads this).
    """

def count_valid_rows(cells: list[Cell], dpi: int) -> int:
    """Orientation-oracle score: number of valid associated rows (== len(parse_box_rows)).

    Exposed separately so the adapter's rotation loop scores a candidate WITHOUT
    re-running normalization side-effects redundantly; implemented as
    len(parse_box_rows(cells, dpi)). 'Valid row' definition in §6.
    """
```

`unidad` handling: the unit column (TNE/TN/KG/RD/Rollo) is itself a cell. The parser locates
the unit cell on the same row band as the QTY (the unit column sits between or beside the qty)
and normalizes the **label** `TNE → TN` (a LABEL normalization, NOT a quantity conversion — the
units-never-converted invariant is preserved; we only fix the OCR spelling of the unit name).
When no unit cell is found on the row, the line is still emitted with `requires_review=True`
and the unit inferred as `TN` only if a confident neighbour exists; otherwise the row is dropped
(no silent fabricated unit). Default carries `requires_review` per the confidence gate.

### 2.2 Adapter — `rapid_table.py`

```python
class RapidOCRAdapter:
    """Deterministic OCR extractor for printed guía tables (ExtractionPort).

    Sole RapidOCR SDK toucher. Lazy-imports rapidocr/onnxruntime/numpy/PIL inside
    methods. Owns the self-scoring orientation retry. extract_declared is a no-op.

    Args:
        dpi:     render DPI of the incoming image bytes (default 200, the pipeline
                 render DPI at pipeline.py:813). Drives the parser band scaling.
        _engine: optional pre-built RapidOCR instance injected for tests — mirrors
                 PrintedTableAdapter's `_ocr` seam (test_paddle_table.py). When set,
                 the lazy-load path and the 165MB model load are skipped entirely.
    """
    def __init__(self, dpi: int = 200, _engine: object | None = None) -> None: ...

    def extract_declared(self, text: str) -> list[MaterialLine]:  # no-op
        return []

    def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
        """Run RapidOCR + self-scoring orientation, return associated material lines.

        Graceful degradation identical to PrintedTableAdapter: on any load/inference
        failure, log a warning, set self._ocr_failed=True, return []. NEVER raises
        (domain rule: flag mismatches, never abort the run). Mirrors the
        _unavailable / _ocr_failed flags so the pipeline drop-gate and
        'OCR unavailable' log path (pipeline.py:824-829) work unchanged.
        """
```

Internal seams (private, all pure-ish helpers calling the lazy engine):

```python
def _get_engine(self) -> object: ...                 # lazy RapidOCR(params=...), §3
def _ocr_cells(self, engine, img_array) -> list[Cell]: ...   # RapidOCROutput → list[Cell]
def _rotate(self, image_bytes, deg) -> ndarray: ...  # Pillow rotate, lazy PIL/numpy
# orientation loop lives in extract_printed_table, scoring via box_row_parser.count_valid_rows
```

### 2.3 Factory — `factory.py` (mirrors `vision/factory.py` exactly)

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from reconciliation.application.config import AppConfig
    from reconciliation.domain.ports import ExtractionPort

def build_ocr_extractor(cfg: "AppConfig") -> "ExtractionPort":
    """Construct the configured ExtractionPort OCR engine.

    Selection by cfg.ocr.engine (Literal["paddle","rapidocr"]). Imports the
    concrete adapter INSIDE the branch so the factory module imports with no SDK.
    Raises ValueError on an unknown engine (mirrors build_vision_adapter).
    """
    engine = cfg.ocr.engine
    if engine == "rapidocr":
        from reconciliation.adapters.ocr.rapid_table import RapidOCRAdapter  # noqa: PLC0415
        return RapidOCRAdapter()
    if engine == "paddle":
        from reconciliation.adapters.ocr.paddle_table import PrintedTableAdapter  # noqa: PLC0415
        return PrintedTableAdapter()
    raise ValueError(
        f"Unknown OCR engine: {engine!r}. Expected one of: 'paddle', 'rapidocr'."
    )
```

Note: the factory returns the OCR **slot** adapter only. The declared slot
(`DigitalTextExtractionAdapter`) is unaffected and remains wired by the composite. The factory
deliberately does NOT take `enabled` into account — `enabled=False` is handled one level up in
`container.py` (NullOcrExtractor branch, §8), exactly as today.

---

## 3. RapidOCR instantiation (confirmed API, rapidocr 3.8.1)

Lazy, inside `_get_engine`:

```python
def _get_engine(self) -> object:
    if self._engine is not None:
        return self._engine
    with _INIT_LOCK:                     # module-level threading.Lock, like paddle_table
        if self._engine is not None:
            return self._engine
        from rapidocr import (           # noqa: PLC0415  — lazy, never module top
            RapidOCR, OCRVersion, ModelType,
        )
        self._engine = RapidOCR(params={
            "Det.ocr_version": OCRVersion.PPOCRV5, "Det.model_type": ModelType.SERVER,
            "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.SERVER,
        })
        return self._engine
```

**Instance lifecycle: construct ONCE per adapter, cache on `self._engine`** (NOT per call).
The PP-OCRv5-server weights are ~165MB (det 84MB + rec 81MB) and the ONNXRuntime session build
dominates first-call latency; per-call construction would reload 165MB on every guía page (469
pages) — unacceptable. The adapter is built once by the factory and lives for the whole run, so
one cached engine per run is correct. The `_INIT_LOCK` double-checked guard mirrors
`paddle_table.py:44,170` for thread safety. Per-page cost after warm-up: ~3.4 s/page (probed).

**Calling convention** (PoC `ocr_compare.py:100-112`): `res = engine(img_array)` →
`RapidOCROutput` with `res.boxes` (list of 4-point polygons), `res.txts` (list[str]),
`res.scores` (list[float]). The adapter maps each `(box, txt, score)` to a `Cell`
(cx/cy = polygon centroid), then hands `list[Cell]` to the pure parser. `res` or `res.boxes`
can be `None` → treat as zero cells.

---

## 4. DPI-scaling formula (deferred calibration #1)

Baseline (PoC, `ocr_compare.py:71`): row-band threshold = **40 px at 200 DPI**.

**Formula (locked):**

```python
band_px = round(40 * dpi / 200)   # == round(0.2 * dpi)
```

This is a **linear scale in DPI** because the row-band is a physical page distance (the vertical
gap between table rows in millimetres) rendered to pixels; pixels-per-physical-unit scales
linearly with DPI. At the pipeline's render DPI of 200, `band_px == 40` (byte-identical to the
PoC). At 300 DPI it becomes 60; at 150 DPI, 30.

**Where DPI comes from:** the render stage. The pipeline renders at a fixed `dpi=200`
(`pipeline.py:813 self._doc.render_page(cls.page, dpi=200)`). The adapter therefore takes
`dpi` as a constructor arg defaulting to **200**, and threads it into `parse_box_rows(cells, dpi)`.
This makes the band **explicit and testable**: a parser unit test asserts
`band_px(200)==40`, `band_px(300)==60`, `band_px(100)==20`, and that two cells 50 px apart
associate at 300 DPI but NOT at 200 DPI. No magic constant buried in a comparison.

> Forward note (not in scope): if a future change makes render DPI configurable, the factory
> would read `cfg`-derived DPI and pass it to `RapidOCRAdapter(dpi=...)`. Today it is a constant
> seam, so we hardcode the default 200 and keep the formula explicit.

---

## 5. Generalized descriptor matcher (deferred calibration #2)

**Problem:** the PoC `_DESC_RE = BARR|ACERO|A615|A706` is a rebar-keyword allowlist. It DROPS any
non-rebar material (alambre, clavos, malla, other suppliers) because their descriptions don't
contain those tokens — a silent data loss the moment the corpus widens.

**Design — classify by POSITIVE quantity-shape + column geometry, NOT by a keyword allowlist.**

```python
# Decimal qty: any-digit integer + any-digit fraction. NO artificial caps —
# admits 2.5 (one fractional digit, real declared data), 0.008, 5800.00 (>=1000),
# 1234.56. Aligned with the declared-side _MATERIAL_LINE_RE: (\d+(?:[.,]\d+)?).
_QTY_DECIMAL_RE = re.compile(r"^\d+[.,]\d+$")
# Bare integer: only a QTY when an adjacent UNIT cell disambiguates it.
_QTY_INTEGER_RE = re.compile(r"^\d+$")
_UNIT_RE = re.compile(r"^(TNE|TN|KG|RD|Rollo)$", re.IGNORECASE)  # unit-column cell (TNE label-normalized)
```

Three-way cell classification:

1. **QTY cell** ⇔ EITHER (a) `_QTY_DECIMAL_RE.fullmatch(text)` — a bare decimal amount with
   one-or-more integer digits and one-or-more fractional digits (NO `{1,3}`/`{2,3}` caps), OR
   (b) `_QTY_INTEGER_RE.fullmatch(text)` (a bare integer) AND a UNIT cell sits in its row band
   (the unit-suffix disambiguator). This is a *positive* shape test, not a material keyword.
2. **UNIT cell** ⇔ `_UNIT_RE.fullmatch(text)`.
3. **DESC cell** ⇔ "the rest": a cell that is NEITHER a QTY nor a UNIT cell AND contains at least
   one alphabetic run of ≥3 letters (`re.search(r"[A-Za-z]{3,}", text)`). The ≥3-letter run
   rejects stray punctuation/number-only header cells from being mistaken for descriptions.

**Why any-digit (JD CRITICAL FIX):** the prior `^\d{1,3}[.,]\d{2,3}$` SILENTLY DROPPED real
declared data — `2.5 TN` (one fractional digit, pages 378-379) failed the `\d{2,3}` minimum, and
`5800.00 KG` (>=1000) failed the `\d{1,3}` cap. The OCR side MUST mirror the declared-side
`(\d+(?:[.,]\d+)?)` shape or reconciliation produces false MISMATCH/drops. Empirically (177 real
qty tokens, full 493-page PDF) NO thousands separators exist — `.` is always the decimal
separator, so `,`→`.` (`replace(",", ".")`) treats a comma as a DECIMAL separator (evidence-backed,
not an assumption). A malformed token is dropped-with-log, never raised.

**Incidental-number guard (the critical correctness property):** `lote 119`, guía codes, and
diameter leads `1"`, `1 3/8"` MUST NOT be read as quantities.

- `lote 119` → `119` is a BARE integer with NO adjacent unit → NOT promoted to QTY. The whole
  `lote 119` cell contains a ≥3-letter run (`lote`) → classified as DESC text, fed intact to the
  normalizer. The canonical matcher already ignores `lote NNN` (incidental number, per the
  material-canonical-matching skill §"incidental numbers like lote 119 are NOT grade contexts").
  A standalone `408916` (guía code) is likewise a bare integer with no adjacent unit → dropped. PASS.
- `1"` / `1 3/8"` → the `"` and fraction slash break BOTH qty shapes (decimal and bare-integer)
  → never a quantity. They live inside the DESC cell as diameter text → the canonical matcher's
  diameter normalization consumes `1 3/8"` correctly (the `{2,3}` quantifier in the grade detector
  already excludes single-digit diameter leads). PASS.

**Unit-fallback guard (JD CRITICAL FIX):** the preferred unit pick (same row band, RIGHT of the
qty column) yields a CONFIDENT line. A relaxed/out-of-column unit pick violates positional evidence
→ the line is emitted with `requires_review=True`, NEVER confident (consistent with the no-unit
path). A unit is claimed only by the desc that OWNS it (band-nearest desc) and exactly once, so a
unit is never STOLEN across rows packed tighter than the band.

**Geometry refinement (qty column):** beyond shape, the association in §2.1 already requires
`qcx > dcx` (the quantity column is physically to the RIGHT of the detalle column) and same row
band. So even if a description accidentally contains an amount-shaped substring as a *separate*
cell, only an amount cell in the right-hand column band associates. Shape + geometry together
are the discriminator; the keyword allowlist is removed entirely.

**Canonical-matching cross-check (verified against the skill):** the parser emits
`description_raw` = the **full DESC cell text** (e.g. `BARRA A615A706 G60 3/4" DOB APL`), then
sets `description_canonical = MaterialNormalizer.canonicalize(desc_raw)` — exactly what
`paddle_table.py:267` does today. This means the downstream Tier-1 dual-spec normalization
(`a615a706 ≡ A615/A706`) and Tier-2 grade-tolerant recovery receive the IDENTICAL text shape
they already handle. We do NOT pre-clean grade/diameter in the parser (that would duplicate and
risk diverging from the canonical matcher). The parser's contract is: emit faithful raw DESC
text + the associated qty + unit. No reconciliation/grade-matching change (proposal non-goal). PASS.

---

## 6. Self-scoring orientation (parser-as-orientation-oracle)

Locked decision #1: AUTO-FIX by self-scoring, default −90° first, retry the full set on failure,
pick max valid rows. Zero new model/dep.

**"Valid row" definition (precise):** one element of `parse_box_rows(cells, dpi)` output — i.e. a
DESC cell that successfully associated to a QTY cell within the DPI-scaled band, to its right,
yielding a parseable `Decimal` cantidad. `count_valid_rows == len(parse_box_rows(...))`.

**Retry loop (inside `extract_printed_table`, after a successful OCR engine load):**

```python
def extract_printed_table(self, image: bytes) -> list[MaterialLine]:
    self._ocr_failed = False
    try:
        engine = self._get_engine()
    except Exception:                      # load failure → graceful []
        self._unavailable = True; self._ocr_failed = True; return []
    try:
        # 1. Default rotation first (the uniform reg227 invariant): -90 CW.
        cells = self._ocr_cells(engine, self._rotate(image, -90))
        lines = parse_box_rows(cells, self._dpi)
        if lines:                          # got valid rows on the default → done
            return lines
        # 2. Zero valid rows → the page may be a different orientation. Retry the set.
        best_lines: list[MaterialLine] = []
        for deg in (0, 90, 180, 270):      # -90 already tried; full coverage
            cells = self._ocr_cells(engine, self._rotate(image, deg))
            cand = parse_box_rows(cells, self._dpi)
            if len(cand) > len(best_lines):
                best_lines = cand
        return best_lines                  # max-valid-rows wins; [] if all fail
    except Exception as exc:               # inference failure → graceful []
        self._ocr_failed = True
        # persistent-capability short-circuit mirrors paddle_table (set _unavailable)
        return []
```

**Cost:** the common case (correctly −90°) runs OCR **once**; only a mis-oriented page pays the
4-extra-rotation cost. Self-corrects on the full 493-page PDF without a hardcoded blind angle
(the rejected option) and without a doc-orientation ONNX model (the other rejected option).

**Boundary — does NOT run for non-guía / Protocolo pages:** the orientation retry lives ENTIRELY
inside `extract_printed_table`, which the pipeline calls **only for `cls.kind == "GUIA"`** pages
(`pipeline.py:804-824` — the loop `if cls.kind != "GUIA": continue`). Protocolo and other
non-guía pages never reach this adapter method, so they are never rotated. The guard is the
**call-site classification**, documented here as the boundary: the adapter is a printed-table
extractor; it trusts the caller to only feed it guía images. No new orientation port is added —
the existing `_deskew` slot (`pipeline.py:816`) becomes a no-op/`None` for the rapidocr engine
(RapidOCR owns its own orientation), avoiding the paddle-only `DeskewAdapter`.

> Deskew-slot wiring detail: when `engine == rapidocr`, `container.py` injects `deskew=None`
> (the paddle `DeskewAdapter` is paddle-only and must not load). RapidOCR's self-scoring replaces
> it. This is additive to the existing `if self._deskew is not None` guard at pipeline.py:816 —
> no pipeline edit needed.

---

## 7. Air-gap model bundling (infra design)

Two candidates:
- **(a) Build-time warm-up:** run `RapidOCR(params=...)` once in the Dockerfile builder (network
  available) to trigger the auto-download into the venv-relative `rapidocr/models/` dir.
- **(b) COPY pre-downloaded `.onnx`:** vendor `det 84MB + rec 81MB` into the repo and `COPY` them
  to the exact venv-relative path.

**RECOMMENDATION: (a) build-time warm-up.** Rationale:
- (b) adds 165MB of binary weights to the git repo (or an LFS dependency) and pins us to a manual
  re-download whenever rapidocr bumps the bundled model version — a maintenance and repo-bloat
  cost. (a) keeps the weights out of git; the lockfile + warm-up reproduces them deterministically.
- The builder stage already has network (it runs `uv sync` against PyPI), so warm-up adds no new
  network capability — the **runtime** stage stays air-gapped (no egress), which is the actual
  invariant. The 165MB lands in `.venv` in the builder and is copied via the existing
  `COPY --from=builder /app/.venv ./.venv` (Dockerfile:44) — no new COPY path to maintain.
- The venv-relative cache path is **non-obvious** (NOT `~/.cache`): rapidocr resolves models to a
  package-relative `…/site-packages/rapidocr/models/` inside the venv. Warm-up populates exactly
  that path, so the runtime import finds them with zero path configuration. (b) would require us
  to hardcode that fragile internal path in a COPY.

**Dockerfile sketch (builder stage, after the project install at line 30):**

```dockerfile
# Install the ocr extra (rapidocr + onnxruntime) alongside identity + llm
RUN uv sync --frozen --no-dev --extra identity --extra llm --extra ocr

# AIR-GAP warm-up: download the PP-OCRv5-server weights INTO the venv at build time
# (network available here; the runtime stage has none). Populates the venv-relative
# rapidocr/models/ dir so the runtime import is fully offline.
RUN /app/.venv/bin/python -c "\
from rapidocr import RapidOCR, OCRVersion, ModelType; \
RapidOCR(params={'Det.ocr_version': OCRVersion.PPOCRV5, 'Det.model_type': ModelType.SERVER, \
'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.SERVER}); \
print('rapidocr v5-server weights warmed')"
```

**Runtime CONT assertion (Dockerfile runtime stage, alongside the existing import checks at L52):**

```dockerfile
# CONT-S0x: rapidocr importable AND its v5-server weights are present offline.
# Constructing the engine with NO network must succeed (weights baked into the venv).
RUN /app/.venv/bin/python -c "\
from rapidocr import RapidOCR, OCRVersion, ModelType; \
RapidOCR(params={'Det.ocr_version': OCRVersion.PPOCRV5, 'Det.model_type': ModelType.SERVER, \
'Rec.ocr_version': OCRVersion.PPOCRV5, 'Rec.model_type': ModelType.SERVER}); \
print('rapidocr offline-ready — CONT OK')"
```

The existing **paddle-absence assertion (Dockerfile:55-58) is RETAINED unchanged** — CONT-S02 still
holds (rapidocr is ONNX-based, no paddlepaddle). The same `--extra ocr` + warm-up + offline
assertion is added to the **test stage** (Dockerfile:84-102) so in-container pytest can run the
real-data gate offline.

> Build-cost note: warm-up runs an ONNX session build at image-build time (one-off, ~seconds) and
> adds ~165MB to the final image. Acceptable for a deterministic, air-gapped deploy.

---

## 8. Container wiring (`container.py:378-392`)

The OCR slot is selected by the factory and injected into `_ocr_adapter`. Because
`CompositeExtractionAdapter.__init__` (container.py:85-96) imports `PrintedTableAdapter`
unconditionally, the rapidocr path must bypass that `__init__` and inject the factory-built
adapter — exactly the `__new__` pattern the `enabled=False` branch already uses.

**Before** (current — two branches):

```python
if not config.ocr.enabled:                       # NullOcrExtractor branch
    extractor = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
    extractor._declared_adapter = DigitalTextExtractionAdapter()
    extractor._ocr_adapter = NullOcrExtractor()
else:                                             # always PrintedTableAdapter (paddle)
    extractor = CompositeExtractionAdapter()
```

**After** (three branches — additive; default engine "paddle" keeps byte-identical behaviour):

```python
if not config.ocr.enabled:                       # (1) OFF — unchanged
    from reconciliation.adapters.ocr.null_extractor import NullOcrExtractor
    from reconciliation.adapters.pdf.digital_text_extractor import DigitalTextExtractionAdapter
    extractor = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
    extractor._declared_adapter = DigitalTextExtractionAdapter()
    extractor._ocr_adapter = NullOcrExtractor()
else:                                             # (2)+(3) ON — engine via factory
    from reconciliation.adapters.ocr.factory import build_ocr_extractor
    from reconciliation.adapters.pdf.digital_text_extractor import DigitalTextExtractionAdapter
    extractor = CompositeExtractionAdapter.__new__(CompositeExtractionAdapter)
    extractor._declared_adapter = DigitalTextExtractionAdapter()
    extractor._ocr_adapter = build_ocr_extractor(config)   # paddle → PrintedTableAdapter
                                                           # rapidocr → RapidOCRAdapter
```

Plus, where the deskew slot is wired (container.py builds `_deskew`): when
`config.ocr.engine == "rapidocr"`, inject `deskew=None` (RapidOCR self-scores; the paddle
`DeskewAdapter` must not load). `engine == "paddle"` keeps the existing `DeskewAdapter` wiring.

**Config change (additive, `OcrConfig` config.py:216-238):**

```python
class OcrConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="allow")
    enabled: bool = True
    engine: Literal["paddle", "rapidocr"] = "paddle"   # NEW — default keeps current behaviour
```

`extra="allow"` (L236) already tolerates unknown keys, so this is backward-compatible. Deploy
runtime sets `RECONCILIATION__OCR__ENABLED=true` + `RECONCILIATION__OCR__ENGINE=rapidocr`
(docker-compose env), flipping the default to rapidocr only in deploy — local/dev stays on the
coded default unless overridden.

**Invariant check:** `pipeline.py` still imports ZERO concrete adapters; only `container.py` and
`factory.py` touch concrete OCR adapters; `factory.py` lazy-imports inside branches. PASS.

---

## 9. strict-TDD test plan (failing-test-first, runner: `cd backend && uv run pytest`)

| Unit | Test file | First failing test → green |
|---|---|---|
| Pure parser (PR#1) | `tests/unit/adapters/test_box_row_parser.py` (NEW) | `band_px` scaling (200→40, 300→60, 100→20); DESC↔QTY association by row+right-column; `lote 119` NOT a qty; `1 3/8"` diameter NOT a qty; TNE→TN label normalize; multi-row table; empty cells → []; orientation-oracle `count_valid_rows`. All run with NO rapidocr installed. |
| RapidOCRAdapter (PR#2) | `tests/unit/adapters/test_rapid_table.py` (NEW) | inject `_engine` mock returning a fake `RapidOCROutput` (boxes/txts/scores) — mirror `test_paddle_table.py::_make_ocr`. Assert: well-formed table → MaterialLines; default −90° tried first; 0 rows → retries {0,90,180,270} and picks max; confidence<0.85 → `requires_review`; engine raise → `[]` + `_ocr_failed`; `_unavailable` short-circuit; `extract_declared` no-op; lazy-load not triggered at init (`_engine is None`). |
| Factory (PR#2) | `tests/unit/adapters/test_ocr_factory.py` (NEW) | `build_ocr_extractor(cfg engine=rapidocr)` → `RapidOCRAdapter`; `engine=paddle` → `PrintedTableAdapter`; unknown engine → `ValueError`; factory module imports with NO rapidocr/paddle installed. |
| Container wiring (PR#2) | extend `tests/unit/infrastructure/test_container*.py` | `engine=rapidocr` injects `RapidOCRAdapter` into `_ocr_adapter` + `deskew=None`; `enabled=False` still → `NullOcrExtractor`; `engine=paddle` unchanged. |
| Config (PR#2) | extend `tests/unit/application/test_config*.py` | `OcrConfig.engine` default `paddle`; env `RECONCILIATION__OCR__ENGINE=rapidocr` parses. |
| Real-data gate (PR#3) | `tests/integration/test_rapidocr_gate.py` (NEW, `@pytest.mark.slow`, keyed on `CTR_PDF_PATH`) | render GT pages 0148/0156/0160 from `reg227_section.pdf`, run the REAL RapidOCRAdapter, assert recovered quantity multiset == #40 GT (`148: 3 qty`, `156: 4 qty`, `160: 4 qty`) EXACT. This is the proof-of-correctness over mock theatre (CLAUDE.md Fix Discipline #2). Skipped when the PDF env var is unset. |

Strict-TDD order per unit: write the failing test FIRST (it fails because the module/branch
doesn't exist), then implement to green. The pure parser tests gate PR#1 entirely without any SDK.

---

## 10. Slicing (for the tasks phase)

Confirmed 3-PR boundary; total estimate ~490–690 LOC **EXCEEDS the 400-line single-PR budget** →
chained/stacked PRs (resolve at the Review Workload Guard against the cached `delivery_strategy`).

- **PR#1 — pure box-row parser + strict-TDD suite.** `box_row_parser.py` + `test_box_row_parser.py`.
  No SDK, no Docker, no config coupling. Self-contained, ~150–200 LOC. Independently reviewable
  and mergeable; nothing else depends on it being wired yet.
- **PR#2 — adapter + factory + config + container wiring + adapter/factory/container tests.**
  `rapid_table.py`, `factory.py`, `OcrConfig.engine`, the `container.py:378-392` three-branch +
  deskew=None, and `test_rapid_table.py` / `test_ocr_factory.py` / container+config test extensions.
  Depends on PR#1's parser. ~200–300 LOC. Engine still defaults to `paddle`, so no behaviour
  change ships until PR#3 flips the deploy default — safe to merge.
- **PR#3 — deps + Dockerfile + model bundling + uv.lock + CONT assertions + integration gate +
  deploy-default flip.** `pyproject.toml` `[ocr]` extra, `uv.lock`, Dockerfile `--extra ocr` +
  warm-up + offline CONT assertion (builder/runtime/test stages), `docker-compose` env
  (`OCR__ENGINE=rapidocr`), `test_rapidocr_gate.py`. ~140–190 LOC + lockfile churn. This is the PR
  that actually turns rapidocr on in deploy; it must land last, after the gate proves accuracy.

**Boundary justification:** PR#1 is pure/isolated (lowest risk, no deps); PR#2 wires the engine
behind a default-off switch (no behaviour change); PR#3 is the infra + activation slice (highest
risk: air-gap bundling, deploy flip) gated by the real-data accuracy proof. Each PR is
independently green and reviewable; only PR#3 changes runtime behaviour.

---

## 11. Architecture-invariant compliance (auto-reject checklist)

- Domain purity — `box_row_parser.py` imports only stdlib + `domain.models`/`domain.normalizer`;
  no SDK/IO under `domain/`. PASS.
- Pipeline imports zero concrete adapters — unchanged; selection is in `container.py` + factory. PASS.
- Lazy heavy deps — rapidocr/onnxruntime/numpy/PIL imported INSIDE `rapid_table.py` methods;
  factory imports concrete adapters INSIDE branch bodies. PASS.
- Provider-agnostic, config-selected behind `ExtractionPort`, never vendor-bound — `OcrConfig.engine`
  literal + `build_ocr_extractor`; domain/pipeline never name RapidOCR. PASS.
- `fecha` never a grouping axis; units never converted (TNE→TN is LABEL normalization, not a
  qty conversion); three identifiers never confused — parser only emits desc/qty/unit; no key,
  date, or unit-conversion logic touched. PASS.
- Input PDF read-only; isolated output dir/run; local-first — no change; air-gap preserved
  (runtime stage has no egress; weights baked at build time). PASS.
- Reconciliation is the validation gate (flag `requires_review`, never auto-correct) — low-confidence
  and unit-ambiguous lines carry `requires_review=True`; reconciler unchanged. PASS.

## 12. Open risks / assumptions requiring validation (for tasks/apply)

- **Full-PDF orientation** unvalidated beyond reg227's 165 pages — mitigated by self-scoring retry;
  the integration gate covers only 3 pages. Apply-phase real-data run over a wider page set
  recommended (SA-5-style runtime check) before the deploy flip.
- **Unit-cell association** — the parser's unit-cell-on-row logic (§2.1) is a new geometric heuristic
  not present in the PoC (PoC only recovered desc+qty). Needs explicit parser tests with the unit
  column in different geometric positions; if fragile, fall back to a row-level `_UNIT_RE` scan over
  all same-band cells. This is a design-detail to lock in PR#1 tests, not an architectural fork.
- **rapidocr 3.8.1 `params=` key names** (`Det.ocr_version` etc.) confirmed by the probe but pinned
  to that version — `uv.lock` must pin rapidocr to a 3.8.x line so the param keys stay valid.
- **#50 dropped-guía** is OUT of scope (SDD#2); SDD#1 yields only the implicit fewer-silent-drops
  improvement at pipeline.py:976-982 (more pages get `len(lines)>0`). No API/schema change here.
