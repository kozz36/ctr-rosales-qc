# Status — canonical-compound-fraction

**Change**: `canonical-compound-fraction`
**Branch**: `fix/canonical-compound-fraction`
**Date**: 2026-06-03

## Status

Implemented & merged to main via **PR #29**.

**Gate**: Judgment-Day dual-blind — both jd-judge-a and jd-judge-b independently returned
APPROVE WITH FINDINGS (0 Critical, 0 Warning). Mergeable as-is.

**TDD cycle**: RED commit `0bf123c` (compound-fraction dot-separator test) → GREEN commit
`b8bf80d`.

**SA-5 e2e (real data)**: Registro 232, deterministic vision-off + SUNAT mode (pages 1-25
subset, run 843998e6). Result: `1 3/8" DOB` MATCH 0.628; `3/8" DOB` de-contaminated.
Confirmed correct behavior on actual SUNAT GRE document from Corporación Aceros Arequipa.

## Key artifacts

- `backend/src/reconciliation/domain/material_key_normalizer.py` — compound-fraction regex
  extended: `\b1\s*[.\-]?\s*3/8` (dot + hyphen separator variants accepted).
- `backend/tests/unit/domain/test_material_key_normalizer.py` — RED test for `1.3/8"`.

## Requirement IDs

No new spec requirement ID allocated; the fix is a correctness extension of **MAT-002**
(compound diameter normalization) in `openspec/specs/material-matching/spec.md`.

**Gotcha**: The dot-separator format is specific to Corporación Aceros Arequipa's SUNAT GRE
export. Other suppliers may use space or hyphen. The regex `[.\-]?` accepts all three
separator variants and is anchored with `\b1` to prevent false matches on bare fractions
like `3/8"`.
