# Proposal — canonical-compound-fraction

**Change**: `canonical-compound-fraction`
**Phase**: archived (implemented & merged)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Gate**: Judgment-Day dual-blind (both judges APPROVE WITH FINDINGS — 0 Critical, 0 Warning)
  + SA-5 e2e validation on real data (Registro 232, deterministic vision-off + SUNAT mode).
**Status**: Implemented & merged to main via PR #29 (branch `fix/canonical-compound-fraction`).
**Issue**: #28

---

## 1. Intent

### Problem

SUNAT GRE documents from Corporación Aceros Arequipa write the compound diameter
`1 3/8"` (one-and-three-eighths inch) with a **dot** as the whole/fraction separator:
`1.3/8"`. The `MaterialKeyNormalizer` compound-fraction regex did not accept the dot
variant, so `1.3/8"` fell through to the bare `3/8"` match, producing an incorrect
canonical key and causing a MISMATCH (or mis-group) instead of correctly matching against
the declared `1 3/8"` diameter.

Detected live during SA-5 Playwright validation on real data.

### Why now

Domain-correctness blocker discovered in production data. The R8 canonical-key normalizer
is the reconciliation engine's core value; an incorrect canonical key for a real supplier
format silently produces wrong reconciliation output.

### Success looks like

- `MaterialKeyNormalizer` accepts `1.3/8"` (dot-separated) and canonicalizes it to `1 3/8"`.
- Separators `.` and `-` are also accepted in addition to whitespace.
- `3/8"` (bare, no whole part) still canonicalizes correctly to `3/8"`.
- Real-data e2e confirmed: Registro 232 `1 3/8" DOB` MATCH 0.628; `3/8" DOB` correctly
  de-contaminated (not absorbed into the `1 3/8"` group).

---

## 2. Scope

**Domain-only:**
- `backend/src/reconciliation/domain/material_key_normalizer.py`: extend the compound-fraction
  regex to accept `\b1\s*[.\-]?\s*3/8` (dot and hyphen separators in addition to whitespace).
- RED test for `1.3/8"` normalization before the fix.
- Judgment-Day adversarial review required (domain change).

**Out of scope:** no pipeline, API, or frontend change.

---

## 3. Rollback / Abort plan

Revert the normalizer regex change. Impact: `1.3/8"` reverts to mis-canonicalizing as
`3/8"`. No data persistence impact — output is derived on each run.
