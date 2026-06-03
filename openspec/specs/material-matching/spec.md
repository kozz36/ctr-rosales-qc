# Spec — Material Canonical Matching
**Change**: r8-material-matching
**Domain**: material-matching (delta over reconciliation domain)
**Phase**: spec
**Date**: 2026-06-02

---

## Purpose

Resolve the declared (Autodesk Forma) ↔ guía (SUNAT GRE) MATCH gap caused by both
sides naming the same physical rebar with different text. The current exact-string
grouping yields zero MATCH on real data. This spec mandates a canonical key that
normalises both sides deterministically before grouping, with a local-LLM fallback
for the ambiguous tail, so the reconciliation engine can produce actual MATCH results.

This is a **delta spec**: all requirements in
`openspec/changes/material-reconciliation/specs/reconciliation/spec.md`
(REC-001 through REC-C07 and their acceptance scenarios) remain in force.
Requirements below ADD or MODIFY behaviour within that base. Each entry is marked
`[ADDED]` or `[MODIFIED: replaces <id>]`.

---

## Requirements

### MAT-001 — [MODIFIED: replaces REC-001] Grouping key includes canonical material key

**[MODIFIED: the `material_canonical` component of the grouping key is now produced by
`MaterialKeyNormalizer` + the `CanonicalKey` value object, not by the legacy
`MaterialNormalizer`. The `fecha` component is removed from the material key itself
(see MAT-007). The new grouping key is `(registro, canonical_key, unidad)`.]**

`ReconciliationService` MUST group all extracted guía rows and declared rows by the
three-field key `(registro, canonical_key, unidad)`.

`canonical_key` MUST be the output of `MaterialKeyNormalizer.normalise(raw_description)`
returning a `CanonicalKey` value object. The raw description MUST NOT be used directly as
a grouping component.

`fecha` MUST NOT be a component of the material grouping key. A `fecha` divergence between
a guía's handwritten reception date and its registro's `fecha_declarada` is the misfiled-guía
signal and is handled separately by the reassignment path (REC-C01, REC-C02).

`unidad` MUST be carried verbatim from the extracted row — it is never normalised,
converted, or inferred. It is a distinct key component and MUST NOT be embedded inside
`canonical_key`.

### MAT-002 — [ADDED] CanonicalKey value object

The domain MUST define a `CanonicalKey` value object with five components:

| Field | Type | Allowed values / format |
|---|---|---|
| `familia` | str | `"BARRA"` (this change; extendable) |
| `grado` | str | See MAT-003 grade collapse table |
| `diametro` | str | See MAT-004 diameter table |
| `presentacion` | str | `"9M"` or `"DOB"` |
| `unidad` | str | carried verbatim — NOT part of this VO; sits one level up |

`CanonicalKey` MUST be immutable (frozen dataclass or equivalent). Two `CanonicalKey`
instances with identical field values MUST compare equal (`__eq__`/`__hash__` defined).

`CanonicalKey` MUST live in the pure domain layer
(`backend/src/reconciliation/domain/`). It MUST NOT import any SDK, I/O library,
or adapter-layer module.

### MAT-003 — [ADDED] Grade collapse: deterministic normalisation

`MaterialKeyNormalizer` MUST collapse all known dual-grade Aceros Arequipa rebar
variant texts to the canonical grade string `"A615 G60"`.

The following input patterns MUST produce `grado = "A615 G60"` (case-insensitive
match after stripping punctuation noise):

| Input pattern (examples) | Normalised grado |
|---|---|
| `a615` | `A615 G60` |
| `a615/a706 g60` | `A615 G60` |
| `a615/a706g60` | `A615 G60` |
| `ag615/a706 g60` | `A615 G60` |
| `ag615/a706g60` | `A615 G60` |
| `a a615-g60` | `A615 G60` |
| `a615 g60` | `A615 G60` |

Any grade string that does NOT match the enumerated dual-grade patterns MUST remain
distinct (i.e., collapse is bounded to the known set). An unrecognised grade MUST NOT
be silently mapped to `A615 G60`; instead the description falls to the LLM fallback
path (MAT-006).

### MAT-004 — [ADDED] Diameter normalisation: canonical set

`MaterialKeyNormalizer` MUST extract `diametro` using an ordered-match regex that
first attempts compound fractions before simple fractions to prevent mis-tokenisation
of `1 3/8"` as `1"`.

The canonical diameter set is exactly:

| Canonical value | Matched inputs (examples) |
|---|---|
| `8mm` | `8mm`, `8 mm` |
| `3/8"` | `3/8"`, `3/8 pulg`, `3/8` |
| `1/2"` | `1/2"`, `1/2 pulg`, `1/2` |
| `5/8"` | `5/8"`, `5/8 pulg`, `5/8` |
| `3/4"` | `3/4"`, `3/4 pulg`, `3/4` |
| `1"` | `1"`, `1 pulg` (not compound: must not match `1 3/8"`) |
| `1 3/8"` | `1 3/8"`, `1 3/8 pulg` |

A description whose diameter cannot be matched against this set MUST fall to the LLM
fallback path (MAT-006). The normaliser MUST NOT invent a diameter value.

### MAT-005 — [ADDED] Presentación normalisation: 9M vs DOB — NEVER merged

`MaterialKeyNormalizer` MUST extract `presentacion` from the description and MUST
produce exactly one of two values:

- `"9M"` — straight 9 m rebar; signals from tokens `x 9m`, `x9m`, `9m`
- `"DOB"` — cut or bent rebar; signals from tokens `dob`, `dimensionado`, `apl`
  (including compound forms such as `acero dimensionado`)

A description that contains signals for `9M` MUST yield `presentacion = "9M"`.
A description that contains signals for `DOB` MUST yield `presentacion = "DOB"`.
A description that contains BOTH signals simultaneously MUST NOT be normalised
deterministically; it MUST fall to the LLM fallback path (MAT-006).
A description that contains NEITHER signal MUST fall to the LLM fallback path (MAT-006).

`CanonicalKey` instances with `presentacion = "9M"` and `presentacion = "DOB"` MUST
NEVER compare equal, even when all other fields match.
Grouping MUST NOT merge a straight-bar row into a cut/bent-bar group under any
circumstance.

### MAT-006 — [ADDED] LLM fallback: MaterialInferencePort

The domain MUST define a `MaterialInferencePort` Protocol (pure domain, no concrete
imports) with a single method:

```
infer(raw_description: str) -> InferenceResult
```

`InferenceResult` MUST carry:
- `familia: str`
- `grado: str`
- `diametro: str`
- `presentacion: str`
- `confidence: float` (0.0–1.0; model-reported or heuristic)

When `MaterialKeyNormalizer` cannot deterministically resolve all five components of
a `CanonicalKey` (missing grade, diameter, or presentación), it MUST invoke
`MaterialInferencePort.infer()` with the raw description.

The resulting `CanonicalKey` MUST be constructed from the `InferenceResult` and MUST
have `match_method = "llm_inferred"` and `requires_review = True` — regardless of the
`confidence` value.

An LLM-inferred match MUST NEVER be auto-confirmed. It is always surfaced for human
review (OCR-validation-gate invariant).

When `MaterialInferencePort` raises an exception or returns a result that cannot be
parsed into a valid `CanonicalKey`, the normaliser MUST yield an `UNRESOLVED` sentinel
key (not raise) with `requires_review = True`. The run MUST continue; the unresolved
row surfaces in the review UI.

### MAT-007 — [ADDED] Ollama LLM adapter (infrastructure)

The infrastructure layer MUST provide an `OllamaMaterialInferenceAdapter` that
implements `MaterialInferencePort`.

The adapter MUST:
- Use the OpenAI-compatible API path already provisioned for `VisionLLMPort`
  (reuses the existing provider-agnostic Dependency-Inversion + Strategy pattern).
- Target model: `qwen3.5:9b` (configurable; default to this model).
- Set `temperature = 0` (deterministic output).
- Strip `<think>...</think>` blocks from the response before JSON parsing.
- Parse the response as a JSON object with keys `familia`, `grado`, `diametro`,
  `presentacion`, `confidence`.
- Lazy-import the SDK (`openai` package) inside the method body — MUST NOT import at
  module level.
- If `temperature`, model name, or base URL are not separately configured, MUST fall
  back gracefully (degrade to `UNRESOLVED`; MUST NOT raise).

The adapter MUST live in the infrastructure layer
(`backend/src/reconciliation/infrastructure/` or equivalent). It MUST NOT be imported
directly by any domain module.

### MAT-008 — [ADDED] match_method and requires_review on ReconciliationRow

Every `ReconciliationRow` MUST carry:

- `match_method: Literal["deterministic", "llm_inferred", "unresolved"]`
  Reflects the normalisation method used for the canonical key of that group.
  When a group mixes deterministic and llm_inferred contributions, the row's
  `match_method` MUST be `"llm_inferred"` (conservative escalation).
- `requires_review: bool`
  MUST be `True` when `match_method` is `"llm_inferred"` or `"unresolved"`.
  MUST be `False` when `match_method` is `"deterministic"` and no other review
  flag (MISMATCH, `any_year_inferred`, etc.) is set.

These fields MUST be included in the reconciliation output and MUST be surfaced in the
review grid (read-only display). They MUST be included in the xlsx/csv export.

### MAT-009 — [ADDED] Deterministic normaliser is pure domain

`MaterialKeyNormalizer` MUST be implemented as a pure domain class with no I/O,
no HTTP calls, no file reads, and no imports of adapter-layer or infrastructure
modules.

All regex patterns and the canonical diameter/grade/presentación tables MUST be
defined as module-level constants (not fetched at runtime).

The normaliser MUST be independently unit-testable without any adapter present.

### MAT-010 — [ADDED] Unit independence invariant (reinforced)

`unidad` (KG / TN / RD / Rollo) MUST NOT be a field inside `CanonicalKey`.
It MUST be carried as a separate key component in the grouping tuple
`(registro, canonical_key, unidad)`.
The normaliser MUST NOT infer, convert, or substitute a unit value.
Two rows that are identical in canonical key but differ in `unidad` MUST produce
SEPARATE reconciliation groups. No cross-unit summation or comparison is permitted.

### MAT-011 — [ADDED] Reconciliation outcomes with canonical key

`ReconciliationService` MUST produce the following outcomes per group
`(registro, canonical_key, unidad)`:

| Status | Condition |
|---|---|
| `MATCH` | Summed guía qty == declared qty (EXACT, tolerance = 0) |
| `MISMATCH` | Summed guía qty ≠ declared qty (any nonzero delta) |
| `DECLARED_MISSING` | No declared entry exists for a canonical key that appears in guías |
| `GUIA_MISSING` | A declared canonical key has no contributing guía rows |

All four statuses MUST be produced correctly when using the canonical key for grouping.
No status may be suppressed or defaulted to MATCH without explicit comparison.

### MAT-012 — [ADDED] LLM adapter unavailability: graceful degradation

When `OllamaMaterialInferenceAdapter` cannot reach the local Ollama endpoint (connection
refused, timeout, model not loaded), the adapter MUST:
- Return an `InferenceResult` with `confidence = 0.0` and all tuple fields set to
  empty string, OR raise a typed exception caught by the normaliser.
- The normaliser MUST catch the failure and yield an `UNRESOLVED` sentinel key with
  `requires_review = True`.
- Deterministic matches for other rows in the same run MUST be unaffected.
- The run MUST complete without raising an unhandled exception.

### MAT-013 — [ADDED] Real-data acceptance: section #4252

The system MUST correctly reconcile the following real-data case from section #4252:

- Declared: `BARRA AG615/A706 G60 1/2" x 9M` = `4.124 TN`
- Guía contributions: pages 5, 6, and 8 of the input PDF (three separate guías), each
  describing the same item with variant text (e.g., `BARRA A A615-G60 1/2" X 9M`)
- Expected canonical key: `CanonicalKey(familia="BARRA", grado="A615 G60", diametro='1/2"', presentacion="9M")`
- Expected outcome: summed guía qty = `4.124 TN` == declared qty `4.124 TN` → **MATCH**
- Expected `match_method`: `"deterministic"` (both sides normalise via regex without LLM)
- Expected `requires_review`: `False`

This case MUST be covered by an integration or e2e test that exercises the full
normalisation + grouping + comparison path.

---

## Acceptance Scenarios

### Scenario MAT-S01 — Grade collapse: all known dual-grade variants → A615 G60

**Given** the following raw description inputs:
  - `"BARRA AG615/A706 G60 1/2\" x 9M"`
  - `"BARRA A615/A706 G60 1/2\" x 9M"`
  - `"BARRA A A615-G60 1/2\" X 9M"`
  - `"barra a615 g60 1/2\" x 9m"`
**When** `MaterialKeyNormalizer.normalise()` is called for each
**Then** all four produce `grado = "A615 G60"` in the resulting `CanonicalKey`
**And** `match_method = "deterministic"` for all four
**And** no LLM call is made

### Scenario MAT-S02 — Diameter: compound fraction matched before simple fraction

**Given** the raw description `"BARRA A615 G60 1 3/8\" x 9M"`
**When** `MaterialKeyNormalizer.normalise()` is called
**Then** `diametro = '1 3/8"'`
**And** `diametro` is NOT `'1"'` and NOT `'3/8"'`
**And** `match_method = "deterministic"`

### Scenario MAT-S03 — Presentación: 9M and DOB produce separate canonical keys

**Given** two raw descriptions:
  - `"BARRA A615 G60 1/2\" x 9M"`  (straight bar)
  - `"BARRA A615 G60 1/2\" (DOB)"`  (cut/bent bar)
**When** `MaterialKeyNormalizer.normalise()` is called for each
**Then** the first produces `presentacion = "9M"`
**And** the second produces `presentacion = "DOB"`
**And** the two `CanonicalKey` instances are NOT equal (`key_9m != key_dob`)
**And** `ReconciliationService` places their rows in separate groups

### Scenario MAT-S04 — Presentación: acero dimensionado → DOB

**Given** the raw guía description `"ACERO DIMENSIONADO - BARRA A615 G60 1\" DOB APL"`
**When** `MaterialKeyNormalizer.normalise()` is called
**Then** `presentacion = "DOB"`
**And** `familia = "BARRA"`
**And** `match_method = "deterministic"`

### Scenario MAT-S05 — Unresolvable description falls to LLM, flagged requires_review

**Given** a raw description `"FIERRO CORRUGADO TIPO X 5/8\""` (unknown grade, no
  known presentación signal)
**And** `OllamaMaterialInferenceAdapter` returns
  `{familia: "BARRA", grado: "A615 G60", diametro: "5/8\"", presentacion: "9M", confidence: 0.72}`
**When** `MaterialKeyNormalizer.normalise()` is called
**Then** the resulting `CanonicalKey` is built from the LLM output
**And** `match_method = "llm_inferred"`
**And** `requires_review = True`
**And** the row is surfaced in the review UI with a `requires_review` indicator

### Scenario MAT-S06 — LLM fallback: Ollama unavailable → UNRESOLVED, run continues

**Given** a raw description that is ambiguous (would trigger LLM fallback)
**And** `OllamaMaterialInferenceAdapter` raises a connection error (Ollama not running)
**When** `MaterialKeyNormalizer.normalise()` is called
**Then** the normaliser returns an `UNRESOLVED` sentinel key
**And** `requires_review = True`
**And** `match_method = "unresolved"`
**And** the run does NOT raise an unhandled exception
**And** all other rows in the same run that normalise deterministically are unaffected

### Scenario MAT-S07 — Units form separate groups, never merged

**Given** two guía rows for the same canonical key `BARRA A615 G60 1/2" 9M` in the
  same registro:
  - Row A: `cantidad = 4.124`, `unidad = "TN"`
  - Row B: `cantidad = 4124.0`, `unidad = "KG"`
**When** `ReconciliationService` groups and reconciles these rows
**Then** they produce TWO distinct reconciliation groups:
  - Group 1: key `(registro, BARRA A615 G60 1/2" 9M, "TN")`, summed_qty = 4.124
  - Group 2: key `(registro, BARRA A615 G60 1/2" 9M, "KG")`, summed_qty = 4124.0
**And** no conversion between TN and KG is performed
**And** no cross-unit summation occurs

### Scenario MAT-S08 — Real-data #4252: declared ↔ guías MATCH

**Given** section #4252 with:
  - Declared row: `raw = "BARRA AG615/A706 G60 1/2\" x 9M"`, `cantidad = 4.124`, `unidad = "TN"`
  - Guía page 5 row: `raw = "BARRA A615/A706 G60 1/2\" X 9M"` (variant text)
  - Guía page 6 row: `raw = "BARRA A A615-G60 1/2\" X 9M"` (different variant text)
  - Guía page 8 row: `raw = "barra a615 g60 1/2\" x 9m"` (lowercase variant)
  - All guía rows: `unidad = "TN"`, individual quantities that sum to `4.124`
**When** `MaterialKeyNormalizer.normalise()` is applied to all four descriptions
**And** `ReconciliationService` groups by `(registro, canonical_key, unidad)`
**Then** all four descriptions normalise to the same `CanonicalKey`
  `CanonicalKey(familia="BARRA", grado="A615 G60", diametro='1/2"', presentacion="9M")`
**And** the group's `summed_qty = 4.124 TN`
**And** `status = MATCH`
**And** `match_method = "deterministic"`
**And** `requires_review = False`

### Scenario MAT-S09 — MISMATCH is not auto-corrected

**Given** a canonical group where declared qty = `4.124 TN`
**And** summed guía qty = `4.120 TN` (OCR misread)
**When** `ReconciliationService` reconciles the group
**Then** `status = MISMATCH`
**And** the group surfaces in the review UI with declared = 4.124, summed = 4.120,
  delta = -0.004
**And** the system does NOT auto-correct the sum or the declared value
**And** the declared quantity remains `4.124 TN` unchanged

### Scenario MAT-S10 — match_method exported in xlsx/csv output

**Given** a completed reconciliation run with groups of `match_method` values
  `"deterministic"`, `"llm_inferred"`, and `"unresolved"`
**When** the export adapter produces the xlsx or csv output
**Then** each row in the export includes a `match_method` column
**And** each row includes a `requires_review` column
**And** the values match those on the corresponding `ReconciliationRow`

### Scenario MAT-S11 — LLM <think> block stripped, JSON parsed cleanly

**Given** `OllamaMaterialInferenceAdapter` receives a model response that contains:
  `<think>Let me analyse this…</think>\n{"familia": "BARRA", "grado": "A615 G60", "diametro": "1/2\"", "presentacion": "9M", "confidence": 0.88}`
**When** the adapter processes the response
**Then** the `<think>...</think>` block is stripped
**And** the remaining JSON is parsed without error
**And** the `InferenceResult` carries `grado = "A615 G60"`, `diametro = '1/2"'`,
  `presentacion = "9M"`, `confidence = 0.88`

### Scenario MAT-S12 — fecha divergence is NOT a matching failure

**Given** a guía row with `handwritten_fecha = 2025-03-10`
**And** the assigned registro has `fecha_declarada = 2025-03-15`
**When** `ReconciliationService` groups the guía row
**Then** the grouping key is `(registro, canonical_key, unidad)` — `fecha` is NOT in the key
**And** the canonical key matches the declared side
**And** the misfiled-guía flag is raised on the guía for the fecha divergence (per REC-C01)
**And** the `status` is determined solely by qty comparison, not by fecha match

---

## Out of scope for this change

- SUNAT `código producto` join as an authoritative key (Forma side has no código map yet).
- PaddleOCR extraction quality or runtime accuracy (orthogonal to matching).
- Frontend changes beyond read-only display of `match_method` and `requires_review` in
  the existing review grid.
- Unit conversion between KG / TN / RD / Rollo (domain invariant: PROHIBITED).
- New vision providers, batching changes, or persistence / DB schema changes.
- Extending the canonical key beyond `BARRA` familia (future; one-material MVP).
