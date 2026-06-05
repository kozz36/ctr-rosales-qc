# Verify Report: guia-classification-keystone (revision 2)

**Status:** PASS-WITH-WARNINGS (no CRITICAL; 1 SUGGESTION)
**Date:** 2026-06-04
**Branch:** feat/guia-classification-keystone (not pushed)

## Executive Summary
Both slices implemented exactly per spec rev-2. Positional gate predicate matches the spec
semantics verbatim; errored_guias is a strictly additive side-channel. Architecture invariants
hold (domain purity, ports-only pipeline, grouping key unchanged). Tests are genuine behavior
tests, not mock theatre. 15/15 targeted + 309/309 application suite GREEN. 0 CRITICAL, 0 WARNING,
1 SUGGESTION (pre-noted cosmetic dead-code). Scope clean: no classifier verdict change, no
frontend change, no #3 probe leak. Real-data validation (Task C) + Judgment-Day (Task D) remain
as orchestrator gates before push.

## REQ1 — Positional gate (extraction, EXT-019 rev-2)
PASS. pipeline.py:967-991 (`_stage_assemble_blocks` else-branch).
- Classifier verdict UNCHANGED — no IGNORED enum; Condition B stays GUIA/FORMA_HEADER_HEURISTIC
  (EXT-S19d GREEN, classifier.py not in diff).
- Predicate is positional and equivalent to spec:
  `absorb = not is_heuristic_only or (current_block.identity_source=="qr" and raw.registro==current_block.registro)`
  where `is_heuristic_only = page_cls.title_matched=="FORMA_HEADER_HEURISTIC" and identity is None`.
  Semantically identical to the spec form `absorb = current_block is not None and identity is None
  and raw.registro==current_block.registro and current_block.identity_source=="qr"` for the
  Condition-B case (the only case where is_heuristic_only is True). For non-heuristic continuation
  pages absorb=True (genuine continuation preserved).
- EXT-S19c regression GREEN: p151 QR + p152 heuristic same registro → 1 block, source_pages=[151,152].
- EXT-S19a/e GREEN: non-adjacent / ocr_fallback-anchored / registro-mismatch Condition-B pages NOT
  absorbed, source_pages not inflated.

## REQ2 — errored_guias side-channel (reconciliation, REC-EG-001..003)
PASS. models.py:390-401 (ErroredGuia), pipeline.py:229 (field), :376-384 (populate), :453 (return).
- Additive-only invariant CONFIRMED: errored_guias is a list comprehension over `blocks` reading
  `block.lines==0`; it constructs new ErroredGuia objects and never mutates blocks/guias/rows.
  rows come from `_stage_reconcile(declared, guias)` independently. 0-line blocks still flow to
  reconcile (not dropped) — only ALSO surfaced. Test 4 pins good-row summed_qty==100 unaffected.
- Populated after `_stage_sunat_fetch` (line 372 → 376), per spec.
- registro: str|None, guia_id: str, source_pages: list[int] — matches REC-EG-001.

## Architecture invariants
PASS (no CRITICAL).
- Domain purity: ErroredGuia uses stdlib @dataclass only; domain/models.py imports no SDK/IO
  (pydantic is the pre-existing domain serialization lib, not introduced here).
- pipeline.py imports ZERO concrete adapters (rg confirmed NONE); ErroredGuia imported from
  domain.models.
- Grouping key (registro, material_canonical, unidad) UNCHANGED; fecha not a grouping axis; units
  not converted (reconcile path untouched by this change).

## Tests are real behavior (not mock theatre)
PASS. test_positional_gate.py asserts on actual block assembly, block count, and source_pages
contents; calls the real `_stage_assemble_blocks`. test_errored_guias.py runs the real `pipeline.run()`
end to end with fakes and asserts errored_guias contents, registro/guia_id/source_pages, AND the
good row's summed_qty invariance. Both files would FAIL without the implementation (RED state
confirmed in apply commits 86f32e0 / abbc653).

## Test results (run by verifier)
- tests/unit/application/test_positional_gate.py + test_errored_guias.py: 15 passed (0.10s)
- tests/unit/application/ (broader affected suite): 309 passed (5.47s)

## Findings
### CRITICAL
- None.
### WARNING
- None.
### SUGGESTION
- S1 (cosmetic dead-code): test_positional_gate.py:163 — the first `blocks` assignment in
  `test_no_open_block_condition_b_produces_no_guia` is computed but never asserted (only `blocks2`
  at :179 is used). Harmless; pre-noted in apply. Could be removed for clarity.

## Scope discipline
PASS. Diff f57d20f..HEAD touches only pipeline.py (+44/-6), models.py (+14), the two new test
files, and openspec docs. No classifier change, no frontend/ change, no transient/systematic/
reintentar/reprocesar (#3) leak.

## Remaining (orchestrator gates, NOT verify scope)
- Task C — real-data validation against ground-truth 67e4e7a1 ranges (requires real PDF path).
- Task D — Judgment-Day adversarial gate (mandatory before push per CLAUDE.md §4).

## Next recommended
sdd-archive blocked until Task C (real-data) + Task D (judgment-day) clear. Unit-level
verification is clean.
