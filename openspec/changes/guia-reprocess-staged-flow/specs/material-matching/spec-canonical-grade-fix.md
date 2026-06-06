# Delta Spec — Material Canonical Matching: Dual-Spec + Grade-Tolerant Fix

**Change**: guia-reprocess-staged-flow
**Slice**: canonical-grade-fix (commits 934f525, 5f3c4fe, 383cec2, 137a7be)
**Capability modified**: `material-matching` (delta over `openspec/specs/material-matching/spec.md`)
**Branch**: feat/guia-reprocess-reprocesar-ia
**Date**: 2026-06-05
**Status**: IMPLEMENTED — JD-APPROVED (3 rounds)

---

## Purpose

Fix the silent UNRESOLVED gap discovered during SA-5 runtime validation of PR #3: vision-read
guía descriptions use the **no-slash dual-spec form** (`a615a706`) that the prior regex
(`a615/a706` slash-only) did not match, causing real corpus lines to land UNRESOLVED instead of
MATCH. Eleven real UNRESOLVED lines in registry 227: 3 resolved deterministically after the
Tier-1 fix; 8 resolved via Tier-2 grade-tolerant recovery (OCR misreads `580`/`680`/`660`
for G60).

This is a **delta spec**: all requirements in `openspec/specs/material-matching/spec.md`
(MAT-001 through MAT-013 and their acceptance scenarios) remain in force.
Requirements below ADD or MODIFY behaviour. Each entry is marked `[ADDED]` or
`[MODIFIED: replaces <id>]`.

---

## Requirements

### MAT-003-B — [MODIFIED: extends MAT-003] Dual-spec family: no-slash forms MUST match

**[MODIFIED: MAT-003 listed only slash-separated inputs. Physical guía (vision) reads write the
dual-cert suffix WITHOUT a slash. All of the following MUST produce the same canonical grade
`A615 G{n}` as their slash-form equivalents.]**

`_SPEC_FAMILY_RE` MUST match the A615/A706 family in all real-corpus spellings:

| Form | Example | Notes |
|---|---|---|
| Bare | `a615` | No dual-cert suffix |
| Slash | `a615/a706` | Original clean form |
| No-slash concatenated | `a615a706` | Physical guía (vision) canonical form |
| OCR digit noise | `a6151a706` | Stray `1` injected between `615` and `a706` by OCR |
| Hyphen | `a615-a706` | Alternate separator |
| Space | `a615 a706` | Alternate separator |
| AG prefix | `ag615/a706` | Declared Forma form with leading `ag` |

The grade `G{n}` is extracted SEPARATELY (see MAT-003-C below) — the spec family and the grade
are independent tokens. The family match does NOT consume the grade token.

### MAT-003-C — [ADDED] Valid grades are DISTINCT: G60 / G42 / G75

The normalizer MUST keep G60, G42, and G75 as **distinct** canonical grade values. They are
physically different products (yield strength). **NEVER collapse G42 or G75 into G60.**

`_VALID_GRADE_LEVELS` MUST be `{60, 42, 75}` and nothing else.

### MAT-003-D — [ADDED] Grade detection is CONTEXT-ANCHORED, not a whole-string scan

Grade detection MUST be anchored to a **grade-context token**, not a whole-string `\d{3}` scan.
Two grade-context detectors:

**Detector 1 — `_G_PREFIXED_GRADE_RE`**: a `g` / `gr` / `grado` prefix followed by a
digit-anchored payload (`\d[a-z0-9]*`). The digit anchor is CRITICAL (JD FIX R2): pure-alpha
`g`-initial words (`gerdau`, `galvanizado`, `grapa`) MUST NOT qualify as grade contexts. Genuine
OCR noise payloads (`g7s` → `7s`, `g6o` → `6o`, `g660` → `660`) start with a digit and MUST
still be caught and cause a bail to None.

**Detector 2 — `_POST_FAMILY_NUMERIC_GRADE_RE`**: a `{2,3}`-digit token immediately after the
spec family (e.g. `a615a706 580 ...`). The `{2,3}` quantifier is CRITICAL (JD FIX R1):
a single-digit diameter lead (`1` of `1"` or `1 3/8"`) MUST NOT be captured as a grade. Prior
`(\d+)` was data-corrupting: it ate the `1` of `1"` / `1 3/8"` diameter lines that carry no
grade token, turning all such grade-less large-diameter lines into UNRESOLVED. The fix is the
`{2,3}` quantifier alone — no `(?!\s*\d)` lookahead (that would have wrongly rejected the
space-separated legacy form `a615a706 680 3/4"` where `680` is legitimately followed by a digit).

Resolution rules (applied after spec-family gate):
1. Any grade-context token whose payload is NOT in `{60, 42, 75}` → return **None** (illegible/invalid).
2. More than one distinct valid grade → return **None** (contradictory; e.g. `g60` + `g75`).
3. Exactly one valid grade → `A615 G{n}`.
4. No grade-context token at all → default **G60** (clean bare A615, incidental numbers not grade contexts).

### MAT-014 — [ADDED] `parse_partial` method for Tier-2 recovery

`MaterialKeyNormalizer` MUST expose a `parse_partial(raw: str) -> tuple[str, str, str] | None`
method that extracts the non-grade triple `(familia, diámetro, presentación)` without attempting
grade resolution.

- Returns the triple when all three are extractable.
- Returns `None` when any of the three is ambiguous/missing (a missing diameter or presentación
  means the line cannot be grade-tolerantly matched — never guess).
- Pure domain: no grade is inferred or defaulted here.

### MAT-015 — [ADDED] Grade-tolerant reconciliation: Tier-2 pre-pass

`ReconciliationService` MUST implement `_apply_grade_tolerant_recovery`, a pure pre-pass invoked
BEFORE grouping, that rewrites UNRESOLVED guía lines which uniquely match a same-registro
declared item on non-grade attributes.

**Algorithm MUST follow exactly**:

1. Build a declared-attr index: for each declared line with a resolved (non-UNRESOLVED) canonical
   key, call `parse_partial(description_raw)` → `(familia, diámetro, presentación)` and add to
   `dict[(registro, familia, diámetro, presentación, unidad)] → set[canonical_key_string]`.
2. For each UNRESOLVED guía line (canonical starts with `"UNRESOLVED::"`):
   a. Call `parse_partial(description_raw)`.
   b. If None → leave UNRESOLVED (cannot identify the item).
   c. Look up the index at `(guia.registro, familia, diámetro, presentación, unidad)`.
   d. If exactly one declared canonical → adopt it; set `match_method="grade_tolerant"`;
      `requires_review=True`. The adopted grade comes from the DECLARED item — never hardcode G60.
   e. If zero or >1 → leave UNRESOLVED (ambiguous).
3. Same-registro only. Units must match exactly. Quantities are never touched. No cross-registro leakage.
4. Returns a NEW guías list (no input mutation).

### MAT-016 — [ADDED] `grade_tolerant` MatchMethod and priority

`MatchMethod` literal MUST include `"grade_tolerant"` as a valid value (alongside `deterministic`,
`llm_inferred`, `codigo_sunat`, `unresolved`).

`grade_tolerant` MUST be in `_REQUIRES_REVIEW_METHODS` — always flags for human review.

`grade_tolerant` MUST have worst-wins priority 1 (`_MATCH_METHOD_PRIORITY`):
above `deterministic=0`, below `llm_inferred=2` and `unresolved=3`.

### MAT-017 — [ADDED] API DTO includes `grade_tolerant`

`infrastructure/api/schemas.py` `ReconciliationRowResponse.match_method` MUST be:
```python
Literal["deterministic", "grade_tolerant", "llm_inferred", "codigo_sunat", "unresolved"]
```
A missing `"grade_tolerant"` value causes a Pydantic validation 500 on the table endpoint when
any recovered guía line is returned. This MUST be kept in sync with `material_key.py::MatchMethod`.

---

## Acceptance Scenarios

### Scenario MAT-S13 — No-slash dual-spec: `a615a706` normalizes same as `a615/a706`

**Given** the guía description `"barra a615a706 g60 3/4\" dob apl"`
**And** the declared description `"BARRA A615 G60 3/4\" DOB"`
**When** `MaterialKeyNormalizer.parse()` is called for each
**Then** both produce `CanonicalKey(familia="BARRA", grado="A615 G60", diametro='3/4"', presentacion="DOB")`
**And** `match_method = "deterministic"` for both
**And** `ReconciliationService` groups them into the same reconciliation group

### Scenario MAT-S14 — OCR digit-noise dual-spec: `a6151a706` normalizes correctly

**Given** the guía description `"barra a6151a706 g60 1/2\" x 9m"`
**When** `MaterialKeyNormalizer.parse()` is called
**Then** `grado = "A615 G60"` and `diametro = '1/2"'` and `presentacion = "9M"`
**And** `match_method = "deterministic"`

### Scenario MAT-S15 — Illegible OCR grade `580` → None → Tier-2 recovery

**Given** a guía line `raw = "barra a615a706 580 3/4\" dob"`, `unidad = "TN"`
**And** the same registro has declared `"BARRA A615 G60 3/4\" DOB"` as the ONLY declared item
  with `(BARRA, 3/4", DOB, TN)`
**When** `MaterialKeyNormalizer.parse()` is called (Tier 1) → returns None (580 ∉ {60,42,75})
**And** `_apply_grade_tolerant_recovery` runs (Tier 2)
**Then** the line adopts the declared canonical `"BARRA A615 G60 3/4\" DOB"`
**And** `match_method = "grade_tolerant"`
**And** `requires_review = True`

### Scenario MAT-S16 — Ambiguous Tier-2: G60 + G75 both declared → stays UNRESOLVED

**Given** a guía line with illegible grade `580` for `(BARRA, 3/4", DOB, TN)` in registro R
**And** registro R has TWO declared items: `"BARRA A615 G60 3/4\" DOB"` AND `"BARRA A615 G75 3/4\" DOB"`
**When** `_apply_grade_tolerant_recovery` runs
**Then** the line stays `UNRESOLVED` (2 candidates → ambiguous)
**And** `match_method = "unresolved"`

### Scenario MAT-S17 — Diameter lead `1"` NOT misread as grade

**Given** a guía description `"barra a615 1\" x 9m"` (no grade token, largest-diameter form)
**When** `MaterialKeyNormalizer.parse()` is called
**Then** `grado = "A615 G60"` (bare A615, no grade context → default G60)
**And** `diametro = '1"'`
**And** `match_method = "deterministic"`
(The `1` of `1"` MUST NOT trigger grade-context detection)

### Scenario MAT-S18 — Diameter lead `1 3/8"` NOT misread as grade

**Given** a guía description `"barra a615 1 3/8\" x 9m"` (no grade token)
**When** `MaterialKeyNormalizer.parse()` is called
**Then** `grado = "A615 G60"` (bare A615, no grade context → default G60)
**And** `diametro = '1 3/8"'`
**And** `match_method = "deterministic"`

### Scenario MAT-S19 — G42 stays G42, not collapsed to G60

**Given** a description `"BARRA A615 G42 3/8\" x 9m"`
**When** `MaterialKeyNormalizer.parse()` is called
**Then** `grado = "A615 G42"` (NOT `"A615 G60"`)
**And** `match_method = "deterministic"`

### Scenario MAT-S20 — `grade_tolerant` rendered in API response without 500

**Given** a reconciliation run that contains a grade-tolerant recovered guía line
**When** the review table endpoint (`GET /runs/{id}/reconciliation`) is called
**Then** the response includes rows with `match_method = "grade_tolerant"`
**And** no Pydantic validation error is raised (500)
**And** `requires_review = true` for those rows

---

## Out of scope for this delta

- G42 / G75 Tier-2 multi-grade corpus testing beyond the in-domain scenarios above.
- Any change to the LLM inference path (MAT-006 / MAT-007 unchanged).
- Export column changes (xlsx/csv `match_method` column already included in MAT-008/MAT-010; no additional export work).
- Vision quantity-accuracy evaluation (kimi-k2.5:cloud quantity misread `0.091` vs `191` — separate eval tracked in backlog; `requires_review` is the operational safety net).
