# Spec — Extraction Domain (Delta)
**Change**: deterministic-ocr-backend (SDD#1)
**Domain**: extraction (delta against promoted spec at `openspec/specs/extraction/spec.md`)
**Phase**: spec
**Date**: 2026-06-06

---

## Purpose

This document is an additive delta to the promoted extraction spec. It specifies the
behavioural requirements for making deterministic OCR the PRIMARY quantity extractor for
guía printed tables, replacing the paddle-specific wiring with a provider-agnostic engine
factory and adding RapidOCR PP-OCRv5-server as the deployed engine.

All existing extraction requirements (EXT-001 through EXT-026) remain in force unless
explicitly modified below.

**What MUST be true after this change is applied:**

1. `ExtractionPort.extract_printed_table` on guía pages is satisfied by a `RapidOCRAdapter`
   (when `ocr.engine=rapidocr`) or the existing `PrintedTableAdapter` (when `ocr.engine=paddle`),
   selected exclusively by configuration.
2. `application/pipeline.py` continues to import ZERO concrete adapters.
3. A pure, reusable box-row parser is the bridge between raw RapidOCR cell output and
   `list[MaterialLine]` — it has no RapidOCR dependency and is unit-testable in isolation.
4. Sideways-scanned guía pages are self-corrected per page without a new model dependency.
5. The RapidOCR runtime is available offline in the deployed image.
6. The existing canonical matching (Tier-1 dual-spec + Tier-2 grade-tolerant) and reconciliation
   gate are unchanged; OCR is strictly upstream of them.

---

## Delta Requirements

> Each entry is marked [ADDED] or [MODIFIED: modifies <id>].

### EXT-027 — [MODIFIED: replaces EXT-004 engine coupling] Engine-agnostic OCR config and factory

**[MODIFIED: EXT-004 binds `extract_printed_table` to PaddleOCR via `PrintedTableAdapter`
specifically. The change makes the engine selection provider-agnostic via config and a factory,
so the deploy target (RapidOCR, no paddle) and the dev alternative (paddle) coexist without
code change.]**

`OcrConfig` MUST expose an `engine` field of type `Literal["paddle", "rapidocr"]` with a
default of `"paddle"` (backward-compatible). The field MUST be settable via the environment
variable `RECONCILIATION__OCR__ENGINE` (Pydantic-settings env_prefix `RECONCILIATION__`,
nested delimiter `__`).

A factory function `build_ocr_extractor(cfg: OcrConfig) -> ExtractionPort` MUST exist in the
adapter layer (at `adapters/ocr/factory.py` or equivalent). This factory is the **sole** module
that imports any concrete OCR adapter class. It MUST lazy-import concrete adapters inside its
body (not at module top level) so the test suite runs without any OCR SDK installed.

`build_ocr_extractor` MUST apply the following selection logic:

| `ocr.enabled` | `ocr.engine` | Resolved adapter |
|---|---|---|
| `False` | any | `NullOcrExtractor` (zero OCR calls) |
| `True` | `"rapidocr"` | `RapidOCRAdapter` |
| `True` | `"paddle"` | `PrintedTableAdapter` (existing) |

`application/pipeline.py` MUST NOT import `RapidOCRAdapter`, `PrintedTableAdapter`, or any
concrete OCR adapter class — it depends only on `ExtractionPort` and calls
`build_ocr_extractor` (or the composition root calls it and injects the result).

The deploy defaults MUST be `RECONCILIATION__OCR__ENABLED=true` and
`RECONCILIATION__OCR__ENGINE=rapidocr`.

#### Scenario EXT-S027a — engine=rapidocr resolves to RapidOCRAdapter

Given `ocr.enabled=true` and `ocr.engine="rapidocr"` in config
When `build_ocr_extractor(cfg)` is called
Then an instance satisfying `ExtractionPort` is returned
And the instance is a `RapidOCRAdapter`
And no `paddle` or `paddleocr` symbol is imported during the call

#### Scenario EXT-S027b — engine=paddle resolves to PrintedTableAdapter

Given `ocr.enabled=true` and `ocr.engine="paddle"` in config
When `build_ocr_extractor(cfg)` is called
Then an instance satisfying `ExtractionPort` is returned
And the instance is a `PrintedTableAdapter`
And no `rapidocr` symbol is imported during the call

#### Scenario EXT-S027c — enabled=false resolves to NullOcrExtractor regardless of engine

Given `ocr.enabled=false` in config (any engine value)
When `build_ocr_extractor(cfg)` is called
Then a `NullOcrExtractor` is returned
And neither `rapidocr` nor `paddle` nor `paddleocr` is imported

#### Scenario EXT-S027d — pipeline.py imports zero concrete OCR adapters

Given the `deterministic-ocr-backend` change fully applied
When `import backend.src.reconciliation.application.pipeline` is executed
Then the module-level namespace contains no reference to `RapidOCRAdapter`,
  `PrintedTableAdapter`, or any concrete OCR adapter class
And the import succeeds without installing `rapidocr` or `paddleocr`

#### Scenario EXT-S027e — engine selector configurable via env var

Given `RECONCILIATION__OCR__ENGINE=rapidocr` set in the environment
And `RECONCILIATION__OCR__ENABLED=true`
When `OcrConfig` is instantiated and `build_ocr_extractor` is called
Then the active extractor is a `RapidOCRAdapter`
And no code modification is required

---

### EXT-028 — [ADDED] RapidOCRAdapter — printed table extraction contract

`RapidOCRAdapter` MUST implement `ExtractionPort` with the following contract:

- `extract_printed_table(image: bytes) -> list[MaterialLine]` — the primary operation.
  Given a guía page image (PNG bytes), it MUST return one `MaterialLine` per valid table row.
- `extract_declared(text: str) -> list[DeclaredMaterial]` — MUST return `[]` (the declared
  side is always sourced from digital text, never from this adapter).

`RapidOCRAdapter` MUST lazy-import `rapidocr`, `onnxruntime`, `numpy`, and `PIL` (Pillow)
INSIDE its methods — never at module top level. The test suite MUST run without these
packages installed when the adapter is not exercised.

`RapidOCRAdapter` MUST own the orientation retry loop (see EXT-030) — this is an adapter
concern, not a domain concern.

The adapter MUST NOT be imported by `domain/` or `application/pipeline.py`.

#### Scenario EXT-S028a — extract_declared always returns empty list

Given a `RapidOCRAdapter` instance
When `extract_declared(text="any text")` is called
Then `[]` is returned with no exception raised
And no OCR engine call is made

#### Scenario EXT-S028b — heavy imports absent at module load

Given the `adapters/ocr/rapid_ocr_adapter.py` module (or equivalent) is imported at module level
When the module is first imported
Then `sys.modules` does NOT contain `rapidocr`, `onnxruntime`, or `numpy`
  (these are imported inside methods only)

---

### EXT-029 — [ADDED] Box-row parser — pure function, engine-independent

A standalone pure function (module: `adapters/ocr/box_row_parser.py` or equivalent) MUST
convert a list of `(box: Sequence[Sequence[float]], text: str, score: float)` OCR cells into
a `list[MaterialLine]` (or an equivalent row-dict before the domain model is applied).

"Pure" MUST mean: the function has no import of `rapidocr`, `onnxruntime`, `paddleocr`, or
any IO library. It is a transformation from raw cell data to structured rows; it MUST be
importable and unit-testable with NO OCR SDK installed.

**DESC↔QTY pairing algorithm (MUST):**

1. Compute the centroid Y coordinate for each cell.
2. Group cells into row bands using a DPI-scaled tolerance:
   `row_band_px = round(40 * (dpi / 200))` where `dpi` is the render DPI of the image.
   Two cells are in the same row band when `|centroid_y_A − centroid_y_B| <= row_band_px`.
3. For each row band, classify cells as:
   - **QTY**: text is EITHER (a) a decimal number of shape `^\d+[.,]\d+$` — one-or-more
     integer digits and one-or-more fractional digits, with NO artificial digit caps (admits
     `2.5`, `0.008`, `5800.00`, `1234.56`; aligned with the declared-side extractor
     `(\d+(?:[.,]\d+)?)`), OR (b) a bare integer `^\d+$` that has an adjacent UNIT cell in its
     row band (the unit-suffix disambiguator; admits `25 RD`, `5800 KG`). A bare integer with
     NO adjacent unit is NOT a QTY — it is an incidental number (line-item / lote `119`, guía
     code `408916`) or a diameter lead (`1`, `3`, `8`). Empirically (177 real qty tokens, full
     PDF) there are NO thousands separators; `.` is always the decimal separator, so a `,` is
     treated as a DECIMAL separator (`,`→`.`).
   - **DESC**: text matches a material descriptor pattern that is broader than the original
     corpus `_DESC_RE` (MUST recognize at least: `BARRA`, `ACERO`, `A615`, `A706`, `FIERRO`,
     `VARILLA`, `ALAMBRE`, codes like `40xxxx`, and any token containing a diameter notation
     `\d+/\d+"` or `\d+[Mm][Mm]`).
   - **IGNORED**: cells that match neither (header cells, row-number cells, supplier text).
4. For each QTY cell, the DESC cell in the SAME row band that is NEAREST to its LEFT MUST
   be the description for that quantity. A QTY cell with no DESC cell to its left in the
   same row band MUST be ignored (not emitted as a row).
5. Unit: taken from a UNIT cell (`TN`, `KG`, `RD`, `Rollo`, `TNE` — see unit normalization
   below) in the same row band as the QTY cell. A unit found in the PREFERRED column position
   (same band, RIGHT of the qty column) yields a CONFIDENT line. A unit claimed via a relaxed
   out-of-column fallback violates positional evidence and MUST set `requires_review=True`
   (NEVER confident — consistent with the no-unit-found path). A unit cell MUST be claimed only
   by the DESC row that OWNS it (the band-nearest DESC) and exactly once, so a unit is never
   STOLEN by a greedy nearest-across-bands pick when rows are packed tighter than the band.

**Unit normalization (label-only — NOT a conversion):**

`TNE` MUST be normalized to `TN` in the output `MaterialLine.unidad`. This is a display-
label normalization only; the numeric quantity is NEVER multiplied, divided, or adjusted.
No other unit conversion is permitted. KG, TN, RD, Rollo MUST remain as-is.

**Incidental-number guard (MUST):**

Standalone integers that match the following patterns MUST NOT be classified as QTY:
- Bare integers with NO adjacent unit cell: line-item / lote numbers (`1`, `119`) and guía
  codes (`408916`).
- Diameter leads: a number immediately followed by `"` (inch) or `mm` / `MM` in the same
  token (e.g. `1"`, `1 3/8"`).
A valid QTY MUST EITHER contain a decimal separator (`.` or `,`) OR be a bare integer
accompanied by an adjacent UNIT cell in its row band (the unit-suffix disambiguator) that
removes the ambiguity.

The parser function MUST accept a `dpi: int` parameter (default `200`) so the caller can
pass the actual render DPI without hardcoding.

#### Scenario EXT-S029a — correct DESC↔QTY pairing on a multi-row table

Given a list of OCR cells representing a 4-row GRE table (columns: item, código, diameter, cantidad, unidad)
  with rows at Y centroids [120, 160, 200, 240] pixels (DPI=200, band=40px)
  and QTY cells containing {0.008, 0.136, 0.191, 0.041} aligned to the right
When `parse_box_rows(cells, dpi=200)` is called
Then 4 `MaterialLine`-shaped rows are returned
And each row pairs the QTY with the DESC cell in its own row band (never cross-band)
And the QTY values are {0.008, 0.136, 0.191, 0.041} (not swapped or merged)

#### Scenario EXT-S029b — TNE normalized to TN; KG/RD/Rollo unchanged

Given OCR cells containing a row with unidad text `TNE` and cantidad `0.136`
When `parse_box_rows(cells, dpi=200)` is called
Then the emitted row has `unidad="TN"` and `cantidad=0.136`
And the cantidad value is unchanged (no multiplication by any conversion factor)

Given OCR cells containing rows with unidad values `KG`, `RD`, `Rollo`
When `parse_box_rows` is called
Then the emitted rows have `unidad` values `KG`, `RD`, `Rollo` respectively (no change)

#### Scenario EXT-S029c — old _LINE_RE-produced-zero-lines case now yields rows

Given a set of OCR cells from a real columnar GRE table where the text spans multiple
  non-contiguous bounding boxes (cells are NOT on the same horizontal line of text)
  such that a simple line-of-text regex (`_LINE_RE`) would produce 0 matched lines
When `parse_box_rows(cells, dpi=200)` is called
Then at least 1 valid `MaterialLine`-shaped row is returned

#### Scenario EXT-S029d — incidental numbers not misread as QTY

Given OCR cells containing:
  - a cell with text `1` at X=50 (leftmost — line-item number position)
  - a cell with text `1"` (diameter notation)
  - a cell with text `408916` (product code — 6-digit integer)
  - a cell with text `0.037` (valid decimal quantity)
When `parse_box_rows(cells, dpi=200)` is called
Then only the cell with text `0.037` is classified as QTY
And `1`, `1"`, and `408916` are NOT classified as QTY

#### Scenario EXT-S029e — non-rebar descriptor still recognized

Given OCR cells containing a DESC cell with text `FIERRO CORRUGADO 1/2"` (not BARRA/ACERO)
When `parse_box_rows(cells, dpi=200)` is called
Then the cell is classified as DESC (not IGNORED)
And its associated QTY cell is included in the output

#### Scenario EXT-S029f — pure function: importable without any OCR SDK

Given the box-row parser module is imported in an environment where `rapidocr`, `onnxruntime`,
  and `paddleocr` are NOT installed
When the module is imported and `parse_box_rows` is called with synthetic cell data
Then no `ImportError` is raised
And the function returns the expected rows

#### Scenario EXT-S029g — DPI-scaled band: 150 DPI yields 30px band; 300 DPI yields 60px band

Given `dpi=150`
When the row band tolerance is computed
Then `row_band_px = round(40 * (150 / 200)) = 30`

Given `dpi=300`
When the row band tolerance is computed
Then `row_band_px = round(40 * (300 / 200)) = 60`

---

### EXT-030 — [ADDED] Self-scoring orientation auto-fix (adapter strategy)

`RapidOCRAdapter.extract_printed_table` MUST apply an orientation auto-fix before returning
rows, using the box-row parser as the oracle:

**Default rotation**: rotate the input image by **−90°** before the first OCR call.
This default handles the known reg227 scan convention (all 165 pages scanned sideways).

**Retry fallback**: if the box-row parser returns **0 valid rows** after the default −90°
rotation, the adapter MUST retry with rotations `{0°, 90°, 180°, 270°}` (four additional
OCR calls, one per candidate), apply the box-row parser to each result, and select the
rotation that yields the **most valid rows**. Ties are broken by the order `[0, 90, 180, 270]`
(first rotation with the maximum count wins).

**Scope guard (MUST):** the orientation retry MUST apply ONLY inside
`RapidOCRAdapter.extract_printed_table`. It MUST NOT be applied to:
- Declared-side pages (no OCR on declared pages — EXT-003).
- Protocolo pages (no force-rotation on non-guía pages).
- Any path where `extract_declared` is called.

No new model dependency or external service is introduced by this feature. The only
additional cost is one or more extra `rapidocr` OCR calls on mis-oriented pages, which
MUST be logged at DEBUG level with the selected rotation and valid-row count.

#### Scenario EXT-S030a — default -90° rotation applied on a sideways page

Given a guía page image that was scanned sideways (-90° rotation relative to upright)
  (i.e., the default reg227 convention)
When `RapidOCRAdapter.extract_printed_table(image)` is called
Then the image is rotated -90° before the first OCR pass
And the parser returns N > 0 valid rows
And no retry is triggered
And the returned `list[MaterialLine]` has N items with correct quantities

#### Scenario EXT-S030b — retry triggered when default yields 0 rows; correct rotation selected

Given a guía page image that is upright (0° — not the default -90° convention)
When `RapidOCRAdapter.extract_printed_table(image)` is called
Then the first pass (−90° rotation) yields 0 valid rows
And the adapter retries {0°, 90°, 180°, 270°}
And the 0° candidate yields the most valid rows
And the adapter returns the rows from the 0° candidate

#### Scenario EXT-S030c — non-guía path is never force-rotated

Given the pipeline invokes `extract_declared` (the no-op path)
When `RapidOCRAdapter.extract_declared(text)` is called
Then no image rotation is applied (the method receives no image)
And no RapidOCR engine call is made
And `[]` is returned

---

### EXT-031 — [ADDED] Ground-truth real-data accuracy gate

`RapidOCRAdapter.extract_printed_table` MUST pass a real-data accuracy gate against the
confirmed ground-truth pages from the OCR probe (keyed on `CTR_PDF_PATH` env var,
`@pytest.mark.slow`).

The gate MUST be expressed as exact quantity comparisons against the values in
`docs/eval/ground_truth.md`. The passing threshold is the observed probe result: 4/4 quantities
exact for page 0156; all quantities exact for pages 0148 and 0160.

**Ground-truth targets (from `docs/eval/ground_truth.md`):**

| Page | guia_id | Expected rows (cantidad, unidad after normalization) |
|---|---|---|
| 0148 | T112-0065418 | (0.037, TN), (0.014, TN), (0.102, TN) |
| 0156 | T112-0065426 | (0.008, TN), (0.136, TN), (0.191, TN), (0.041, TN) |
| 0160 | T009-0739440 | (1.616, TN), (0.238, TN), (1.643, TN), (0.121, TN) |

Unit normalization: `TNE → TN` as per EXT-029.

The gate MUST verify:
1. Row count matches the expected count for each page.
2. Each extracted `cantidad` matches the ground-truth value exactly (float equality after
   rounding to 3 decimal places, matching the GT precision).
3. Each `unidad` is `"TN"` (normalized from `TNE`).

#### Scenario EXT-S031a — page 0156 (4 rows, 4/4 exact)

Given the real guía page 0156 rendered as PNG (available at `docs/eval/pages/0156.png`)
  or rendered directly from the real PDF at `CTR_PDF_PATH`
When `RapidOCRAdapter.extract_printed_table(image)` is called
  (with default -90° rotation)
Then exactly 4 `MaterialLine` rows are returned
And the `cantidad` values are [0.008, 0.136, 0.191, 0.041] (order-independent, by position)
And all `unidad` values are `"TN"`

#### Scenario EXT-S031b — page 0148 (3 rows, all exact)

Given the real guía page 0148
When `RapidOCRAdapter.extract_printed_table(image)` is called
Then exactly 3 rows are returned with (0.037, TN), (0.014, TN), (0.102, TN)

#### Scenario EXT-S031c — page 0160 (4 rows, ACERO DIMENSIONADO series, all exact)

Given the real guía page 0160
When `RapidOCRAdapter.extract_printed_table(image)` is called
Then exactly 4 rows are returned with (1.616, TN), (0.238, TN), (1.643, TN), (0.121, TN)

---

### EXT-032 — [ADDED] Domain invariants preserved through OCR path

The following domain invariants MUST hold end-to-end when `ocr.engine=rapidocr`:

1. **Units never converted**: `RapidOCRAdapter` MUST NOT multiply, divide, or adjust any
   numeric quantity. The only permitted unit transformation is the label normalization
   `TNE → TN` (EXT-029). KG, TN, RD, Rollo values MUST sum independently through the
   reconciliation step.
2. **Reconciliation as validation gate**: an OCR misread that produces a quantity differing
   from the trusted declared value MUST result in `status=MISMATCH` and
   `requires_review=True` for the affected group. The system MUST NOT auto-correct the OCR
   value.
3. **Grouping key unchanged**: the reconciliation grouping key remains
   `(registro, material_canonical, unidad)`. The `fecha` field is NEVER part of the key.
   RapidOCRAdapter's output MUST NOT carry `fecha` (the handwritten date is read by the vision
   path — EXT-005/EXT-017).
4. **Domain purity**: no file under `backend/src/reconciliation/domain/` MUST import or
   reference `rapidocr`, `onnxruntime`, `RapidOCRAdapter`, or `box_row_parser`.
5. **Input PDF read-only**: OCR processing MUST NOT modify the source PDF or any existing
   file. Each pipeline run writes to its own isolated output directory.

#### Scenario EXT-S032a — OCR misread flagged; never auto-corrected

Given a guía page where `RapidOCRAdapter` reads `cantidad=0.190` for a row
And the declared quantity for the same material group is `0.191`
When reconciliation runs
Then the group has `status=MISMATCH`
And `requires_review=True` on the affected row
And the declared value remains `0.191` (unchanged)
And the OCR value `0.190` is recorded in the reconciliation audit trail

#### Scenario EXT-S032b — mixed-unit table: KG and TN sum independently

Given a guía page containing two rows: (descripción_A, 500, KG) and (descripción_A, 0.5, TN)
When `parse_box_rows` returns both rows and reconciliation groups them
Then the KG quantity and TN quantity are summed in separate groups keyed by `unidad`
And neither quantity is converted to the other unit

#### Scenario EXT-S032c — domain/ files unchanged after SDD#1 applied

Given the `deterministic-ocr-backend` change fully applied
When `git diff main -- backend/src/reconciliation/domain/` is inspected
Then zero files in the domain layer are modified, added, or removed by this change

---

### EXT-033 — [ADDED] RapidOCR dependencies and Docker air-gap

The following deployment requirements MUST hold:

1. **Optional dependency group**: the project MUST expose a `[project.optional-dependencies]`
   group named `ocr` (or equivalent) containing at minimum `rapidocr`, `onnxruntime`,
   `Pillow>=10.0`, `numpy>=1.26`. The Dockerfile builder layer MUST install this group
   (`uv sync --extra ocr` or equivalent).

2. **Paddle absence retained**: the existing CONT-S02 assertion — that `import paddle` and
   `import paddleocr` are NOT present in the runtime image — MUST remain satisfied. Installing
   `rapidocr` + `onnxruntime` MUST NOT pull in `paddlepaddle` or `paddleocr` as transitive
   dependencies.

3. **RapidOCR runtime assertion**: a startup assertion (CONT smoke test) MUST verify
   `import rapidocr` succeeds in the deployed image. This mirrors the existing paddle-absence
   assertion in the opposite direction.

4. **Model bundling (air-gap)**: the PP-OCRv5-server ONNX model weights (~165 MB:
   detection ~84 MB + recognition ~81 MB) MUST be present in the deployed image at the path
   that `RapidOCR(params=...)` expects, so that the first OCR call succeeds with NO network
   access. The MUST-satisfy condition: the first `extract_printed_table` call in a network-
   isolated container MUST NOT raise a download-related exception.
   Permitted strategies (either satisfies the requirement):
   - Build-time warm-up: a `RUN python -c "from rapidocr import RapidOCR; RapidOCR()"` step
     in the Dockerfile that triggers the auto-download into the venv.
   - Pre-copy: the `.onnx` weight files are copied to the exact `rapidocr/models/` venv-
     relative path during the Docker build.

5. **uv.lock updated**: `uv.lock` MUST be updated to pin the `rapidocr` and `onnxruntime`
   versions after `--extra ocr` is added. The committed lockfile MUST be reproducible.

#### Scenario EXT-S033a — RapidOCR import assertion passes in deployed image

Given a container built from the SDD#1 Dockerfile
When a process inside the container runs `python -c "import rapidocr"`
Then the import succeeds with no exception
And `import paddle` raises `ImportError` (paddle absence retained)

#### Scenario EXT-S033b — OCR call succeeds in a network-isolated container

Given a network-isolated container (no external DNS, no outbound HTTP)
And the container was built with model weights bundled at build time
When `RapidOCRAdapter.extract_printed_table(image)` is called
Then the call succeeds (no ConnectionError, no download timeout)
And at least 1 row is returned for a valid guía image

#### Scenario EXT-S033c — rapidocr does not pull paddle as a transitive dep

Given the `ocr` optional-dependency group installed via `uv sync --extra ocr`
When `pip show rapidocr onnxruntime` lists their transitive deps
Then neither `paddlepaddle`, `paddlepaddle-gpu`, nor `paddleocr` appear in the dependency tree

---

## Non-goal Boundary (SDD#1 scope guard)

### EXT-NG-001 — #50 dropped-page sentinel is NOT part of this change

Issue #50 (silent drop of identity-less GUIA pages at `pipeline.py:976-982`) MUST NOT be
addressed in SDD#1 beyond the implicit improvement that enabling OCR reduces the number of
pages with `len(lines)==0`.

SDD#1 MUST NOT add any new API field, new domain model, new HTTP endpoint, or new UI element
to surface dropped pages. No `sentinel_emit`, `IDENTITY_MISSING` status, or equivalent
construct is in scope.

The explicit surfacing of dropped/identity-less guía pages, including any API/schema changes
and UI treatment, is deferred to **SDD#2**.

This is recorded as an explicit non-requirement (SA-2 boundary) so that implementation
sub-agents do not improvise a #50 fix during SDD#1 apply.

---

## Acceptance Scenarios Summary

The scenarios above are grouped by requirement for strict-TDD targeting:

| Requirement | Scenario IDs | TDD tier |
|---|---|---|
| EXT-027 (factory/config) | S027a–S027e | (b) adapter unit + config tests |
| EXT-028 (RapidOCRAdapter contract) | S028a–S028b | (b) adapter unit tests |
| EXT-029 (box-row parser) | S029a–S029g | (a) pure unit tests |
| EXT-030 (orientation auto-fix) | S030a–S030c | (b) adapter unit tests (injected mock engine) |
| EXT-031 (GT real-data gate) | S031a–S031c | (c) @pytest.mark.slow, CTR_PDF_PATH |
| EXT-032 (domain invariants) | S032a–S032c | (a)+(b) unit tests; S032c = git diff assertion |
| EXT-033 (deps/Docker/air-gap) | S033a–S033c | (c) containerized-verify (Makefile/Compose) |

---

## Out of scope for this delta

- Changes to page classification (EXT-001/EXT-019) — classifier is unchanged.
- Changes to vision date extraction (EXT-005/EXT-017/EXT-020/EXT-021) — vision demoted to
  rare fallback but its contract is unchanged.
- Changes to QR identity extraction (EXT-011/EXT-012) — unchanged.
- Changes to block grouping (EXT-015/EXT-022) — unchanged.
- Changes to SUNAT fetch port (EXT-016/EXT-023/EXT-026) — unchanged.
- API/schema changes (#50 sentinel) — deferred to SDD#2.
- Cross-model vision consensus (#44) — out of scope.
- Reconciliation domain changes (grouping, grade-matching, tolerance) — unchanged.
- Frontend changes — SDD#1 is backend-only.
