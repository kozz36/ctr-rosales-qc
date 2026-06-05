# Tasks: Guía Classification Keystone (backend, change #2)

**Strict TDD active** — test command: `cd backend && uv run pytest`
**Artifact store:** hybrid (engram #2916 + this file)
**Date:** 2026-06-04

---

## Prerequisite Verification (RESOLVED — no blocker)

CONFIRMED: `_stage_assemble_blocks` already receives `classifications: list[PageClassification]`
(pipeline.py:358, method signature :847-851). Design assumption is sound.

`ErroredGuia` and `errored_guias` do NOT yet exist anywhere in the codebase — Slice B is
a net-new addition.

---

## Slice A — Bug 1: Positional gate in `_stage_assemble_blocks`

**Files:** `backend/src/reconciliation/application/pipeline.py`,
new/extended `backend/tests/unit/application/test_positional_gate.py`

### Task A-1 — Failing tests for positional gate (strict-TDD first)

**Prereq:** none
**Sequential before A-2**
**Spec coverage:** EXT-S19a, EXT-S19c (regression guard), EXT-S19d (EXT-S24 regression guard),
EXT-S19e

Write these FAILING tests BEFORE touching pipeline.py:

1. `test_condition_b_no_preceding_qr_block_not_absorbed` — EXT-S19a
   - Condition-B raw page, `identity=None`, `title_matched="FORMA_HEADER_HEURISTIC"`,
     no open block (or open block with `identity_source="ocr_fallback"`)
   - Assert: page NOT present in any block's `source_pages`

2. `test_condition_b_registro_mismatch_not_absorbed` — EXT-S19e
   - Condition-B page `registro="228"`, current block `registro="232"`,
     `identity_source="qr"`
   - Assert: `absorb=False`; registro 232 `source_pages` unchanged

3. `test_continuation_page_absorbed_when_qr_block_open` — EXT-S19c (REGRESSION GUARD)
   - Page 151: Condition A, QR `T112-0065421`, `identity_source="qr"`
   - Page 152: Condition B only, same registro, `identity=None`
   - Assert: ONE block, `source_pages==[151,152]`, `guia_id=="T112-0065421"`
   - **This MUST FAIL before the gate is wired** (current code absorbs indiscriminately)

4. `test_ext_s24_classifier_verdict_unchanged` — EXT-S19d (EXT-S24 pin)
   - Instantiate `PageClassifier`, call with `image_dominant=True, qr_is_guia=False`
   - Assert: `kind=="GUIA"`, `title_matched=="FORMA_HEADER_HEURISTIC"`
   - Must be GREEN even before implementation (classifier is UNTOUCHED)

Run: `cd backend && uv run pytest tests/unit/application/test_positional_gate.py -x`
Expected: tests 1+2+3 RED, test 4 GREEN.

**Hard invariants:**
- Classifier domain (`domain/classifier.py`) UNTOUCHED — no new enum, no verdict change.
- `classifications` is already passed to `_stage_assemble_blocks`; no signature change needed.
- Domain purity: no IO/SDK import in `domain/`.

---

### Task A-2 — Implementation: positional gate in `_stage_assemble_blocks`

**Prereq:** Task A-1 (tests RED)
**Sequential after A-1**
**Spec coverage:** EXT-019 (revision 2), EXT-S19a, EXT-S19c, EXT-S19d, EXT-S19e

**File:** `backend/src/reconciliation/application/pipeline.py` (~:952-959)

In the `else` (continuation) branch, replace the unconditional append block with the
positional gate predicate from the spec:

```python
# Positional gate (EXT-019 rev-2 / EXT-S19a..e):
# A heuristic-only Condition-B page (FORMA_HEADER_HEURISTIC, identity=None)
# is absorbed ONLY when the open block was started by a real QR identity
# AND the registro matches. Otherwise dropped — not absorbed, not a new block.
page_cls = next(
    (c for c in classifications if c.page == raw.source_page), None
)
is_heuristic_only = (
    page_cls is not None
    and page_cls.title_matched == "FORMA_HEADER_HEURISTIC"
    and identity is None
)
absorb = not is_heuristic_only or (
    current_block is not None
    and current_block.identity_source == "qr"
    and raw.registro == current_block.registro
)
if absorb:
    current_block.source_pages.append(raw.source_page)
    current_block.lines.extend(raw.lines)
    if current_block.gre_hashqr_url is None and page_hashqr_url is not None:
        current_block.gre_hashqr_url = page_hashqr_url
# else: non-guía image page dropped — NOT absorbed, NOT a new block
```

The `start_new_block` path (:921-933) is UNCHANGED.

Run after implementation:
```
cd backend && uv run pytest tests/unit/application/test_positional_gate.py -x
cd backend && uv run pytest tests/unit/application/test_block_grouping.py \
    tests/unit/application/test_hybrid_classifier.py -x
```
Expected: all GREEN (new + existing EXT-S15-S18, EXT-S23-S25-S29 unchanged).

**Hard invariants:**
- `fecha` never a grouping axis; units never converted; grouping key `(registro, material_canonical, unidad)` unchanged.
- Input PDF read-only; each run produces its own isolated output dir.
- Domain `classifier.py` not imported or modified.

---

## Slice B — Bug 2: `errored_guias` side-channel

**Files:** `backend/src/reconciliation/domain/models.py`,
`backend/src/reconciliation/application/pipeline.py`,
new `backend/tests/unit/application/test_errored_guias.py`

**Independent of Slice A — can be developed in parallel.**

---

### Task B-1 — Failing tests for `errored_guias` side-channel (strict-TDD first)

**Prereq:** none
**Sequential before B-2/B-3**
**Spec coverage:** REC-EG-001, REC-EG-002, REC-EG-003; scenarios REC-EG-S01..S04

Write these FAILING tests BEFORE touching models.py or pipeline.py:

1. `test_errored_guia_model_fields`
   - `ErroredGuia(registro="232", guia_id="T112-0065422", source_pages=[45])`
   - Assert all three fields accessible with correct values
   - FAILS: `ErroredGuia` does not exist yet

2. `test_pipeline_result_errored_guias_default_empty`
   - Construct a minimal `PipelineResult(...)` (all required fields)
   - Assert `result.errored_guias == []`
   - FAILS: field does not exist yet

3. `test_0_line_block_appears_in_errored_guias` — REC-EG-S01 / REC-EG-003
   - Pipeline with two fake blocks post-SUNAT: one `lines=[]`, one `lines=[<line>]`
   - Assert `len(result.errored_guias) == 1`; entry has correct registro/guia_id/source_pages
   - Assert non-empty block NOT in `errored_guias`

4. `test_errored_guias_additive_only_invariant` — REC-EG-S02 / REC-EG-002
   - Same run; assert every `ReconciliationRow` for non-errored registro has identical
     `summed_qty`, `status`, `delta` vs a baseline run (no mutation by side-channel)

5. `test_errored_guias_empty_when_all_blocks_have_lines` — REC-EG-S03
   - All blocks ≥1 line → `result.errored_guias == []` (empty list, not null)

6. `test_multiple_errored_guias_across_registros` — REC-EG-S04
   - registro 227: 1 errored; registro 232: 2 errored
   - Assert `len(result.errored_guias) == 3`; each entry has correct fields

Run: `cd backend && uv run pytest tests/unit/application/test_errored_guias.py -x`
Expected: all RED.

**Hard invariants:**
- `ErroredGuia` will live in `domain/models.py` — pure, no IO, no SDK import.
- No SUNAT network call from `ReconciliationService`.
- `errored_guias` MUST NOT alter key/status/delta/qty of any correctly-processed guía.

---

### Task B-2 — Implementation: `ErroredGuia` in `domain/models.py`

**Prereq:** Task B-1 (tests RED)
**Sequential after B-1, before B-3**
**Spec coverage:** REC-EG-001

**File:** `backend/src/reconciliation/domain/models.py`

Add pure dataclass (mirrors `GuiaIdentity` pattern — no IO, no vendor import):

```python
@dataclass
class ErroredGuia:
    """A guía block that resolved to 0 material lines after SUNAT fetch.

    Additive side-channel only — NEVER alters grouping key, status,
    delta, qty, or any correctly-processed guía. (REC-EG-001/002/003)
    """
    registro: str | None
    guia_id: str
    source_pages: list[int]
```

**Hard invariant:** domain stays PURE — no IO, framework, or SDK import.

---

### Task B-3 — Implementation: wire `errored_guias` on `PipelineResult` and populate in `run()`

**Prereq:** Task B-2 (`ErroredGuia` exists)
**Sequential after B-2**
**Spec coverage:** REC-EG-001, REC-EG-002, REC-EG-003; all four scenarios

**File:** `backend/src/reconciliation/application/pipeline.py`

**Step 1 — `PipelineResult`** (~:208-227):
```python
errored_guias: list[ErroredGuia] = field(default_factory=list)
```

**Step 2 — population** (~:370, AFTER `sunat_fetch_map = self._stage_sunat_fetch(...)`):
```python
# REC-EG-001/003: collect 0-line blocks as visible omission (not silent).
# Additive side-channel — NEVER touches reconciliation key/status/delta/qty.
errored_guias: list[ErroredGuia] = [
    ErroredGuia(
        registro=block.registro,
        guia_id=block.guia_id,
        source_pages=list(block.source_pages),
    )
    for block in blocks
    if len(block.lines) == 0
]
```

**Step 3 — return** (~:431-439): add `errored_guias=errored_guias` to `PipelineResult(...)`.

Run after implementation:
```
cd backend && uv run pytest tests/unit/application/test_errored_guias.py -x
cd backend && uv run pytest tests/unit/application/ -x
```
Expected: new tests GREEN; all existing tests GREEN (additive only).

**Hard invariants:**
- `ErroredGuia` imported from `domain.models` — no new adapter import in pipeline.
- 0-line guías still flow through reconcile UNCHANGED; side-channel populated before vision,
  but reconcile logic is not touched.
- `errored_guias` field is never `None`; always a list (default `[]`).

---

## Task C — Real-data validation (ground-truth 67e4e7a1 ranges)

**Prereq:** A-2 + B-3 complete
**Sequential after both slices; independent of each other**

Run pipeline against the real PDF subset (pages 1-25 or the 67e4e7a1 subset):

```bash
cd backend && uv run python -m reconciliation.cli run \
    --pdf <path/to/real-pdf> \
    --pages 1-25 \
    --output /tmp/keystone-validate
```

Assertions:
1. registro 228 `source_pages` NO longer includes phantom pp98-137 range (Bug 1 fix verified).
2. Any 0-line guías (URL-QR / SUNAT-failed) appear in `errored_guias` in output JSON.
3. All previously-green MATCH results (e.g. registro 232 BARRA A615 G60 1/2" 9M = 4.124 TN)
   still MATCH with identical `summed_qty` and `delta`.
4. `TestR9RealPDFGate` (5/5) still passes (reception-date flow untouched):
   `cd backend && uv run pytest tests/targeted/test_r9_real_pdf_gate.py -x`

---

## Task D — Judgment-day gate (mandatory before push)

**Prereq:** C complete
**Sequential — blocks push**

Per `docs/CLAUDE.md` §Fix/Feature Discipline and §Working agreements:
Adversarial review (judgment-day or `ctr-reviewer` single-pass for lighter PRs) MUST run
before push. Reviewer MUST verify:

- No domain purity violation (no SDK/IO under `domain/`).
- `errored_guias` additive-only invariant: no row's key/status/delta/qty mutated.
- Positional gate does not over-drop (EXT-S19c regression guard GREEN).
- No new enum value introduced at the classifier level (EXT-S19d).
- Conventional commit messages (no AI attribution / Co-Authored-By).

Do NOT push until judgment-day returns APPROVED.

---

## Dependency Graph

```
A-1 (failing tests) ──► A-2 (positional gate impl) ──┐
                                                       ├──► C (real-data) ──► D (JD gate) ──► push
B-1 (failing tests) ──► B-2 (ErroredGuia model)      │
                     └──► B-3 (wire pipeline)   ──────┘
```

- Slice A and Slice B are **INDEPENDENT** — can be separate commits or parallel branches.
- Within each slice: tasks are **strictly sequential** (TDD contract).
- C + D are **strictly sequential** after both slices complete.

---

## Commit Strategy (reviewable work-units)

| # | Message | Task |
|---|---------|------|
| 1 | `test(extraction): failing positional gate tests EXT-S19a/c/d/e` | A-1 (RED) |
| 2 | `fix(extraction): positional gate in _stage_assemble_blocks (EXT-019 rev-2)` | A-2 |
| 3 | `test(reconciliation): failing errored_guias side-channel tests REC-EG-001-003` | B-1 (RED) |
| 4 | `feat(domain): add ErroredGuia model (REC-EG-001)` | B-2 |
| 5 | `feat(pipeline): wire errored_guias side-channel on PipelineResult (REC-EG-001-003)` | B-3 |

---

## Review Workload Forecast

| Metric | Estimate |
|---|---|
| Files changed | 3 (`pipeline.py`, `models.py`, 1–2 test files) |
| LOC added — tests | ~140 (A-1: ~60, B-1: ~80) |
| LOC changed — impl | ~45 (A-2: ~18 lines; B-2: ~12 lines; B-3: ~15 lines) |
| **Total estimated LOC delta** | **~185** |

- `Chained PRs recommended: No`
- `400-line budget risk: Low`
- `Decision needed before apply: No`

Both slices are additive and surgical. A single PR with 5 reviewable commits is the correct
delivery. No cascading changes to reconcile, domain date logic, frontend, or OCR.
