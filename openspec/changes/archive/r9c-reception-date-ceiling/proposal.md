# Proposal — r9c-reception-date-ceiling

**Change**: `r9c-reception-date-ceiling`
**Phase**: archived (implemented & merged)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Parent**: Symmetric upper-bound complement to `r9b-reception-date-delivery-floor`. Builds on
the date-bracket pipeline established by R9b (`apply_delivery_floor`, `fecha_entrega` on
`GuiaDeRemision`). Depends on the authoritative Protocolo declared date being available per
Registro N° (established by R9 `_stage_extract_declared_date` / `fecha_authoritative`).

**Gate**: Judgment-Day APPROVED after 3 rounds + 2 fix iterations. 972 backend unit tests passing.
**Status**: Implemented & merged to main via PR #8.

---

## 1. Intent

### Problem

After R9b, each guía's resolved reception date has a deterministic **lower floor** (SUNAT
`fecha_entrega`): goods cannot be received before they are delivered. However, the symmetric
**upper ceiling** was left unenforced: the Registro's handwritten Protocolo date is the
authoritative declared reception moment, so a guía whose resolved date EXCEEDS that date
would have been accepted silently, despite being physically impossible (a guía cannot be
received after the reception event itself was recorded).

Three failure modes existed after R9b:

1. `infer_reception_year` could produce a resolved date that exceeds the Protocolo ceiling
   (e.g., year-inference picks the wrong future year), making the date physically impossible.
2. When `fecha_entrega` (SUNAT delivery floor) exceeds the Protocolo ceiling — a
   physically impossible configuration caused by a human Protocolo-assembly error — the
   pipeline had no distinct signal for this crossed-bounds anomaly.
3. `fecha_entrega` was populated on `GuiaDeRemision` only during the initial pipeline run;
   `ReviewService` re-reconcile calls (reassign / line-edit / field-edit) did not pass
   `delivery_dates` into `ReconciliationService.reconcile`, so the floor/ceiling bracket and
   the crossed-bounds guard were silently lost on any review action.

### Why now

Judgment-Day rounds on the full R9 change family surfaced the missing upper ceiling as the
direct symmetric gap. The `[floor, ceiling]` bracket is only complete once both bounds are
enforced. Additionally, the persistence gap (failure mode 3) meant that review actions could
silently produce incorrect bracket behavior — making this a correctness regression in the
review workflow.

### Success looks like

- A guía whose resolved reception date EXCEEDS the Protocolo ceiling is clamped DOWN to
  that ceiling; `reception_ceiling_applied` side-channel is set; `requires_review` OR-set;
  `has_reception_ceiling` computed on the row.
- When `fecha_entrega` (SUNAT floor) is LATER than the Protocolo ceiling (crossed-bounds,
  physically impossible), the system does NOT clamp and instead raises a distinct
  `delivery_after_protocolo` WARNING, flagging `requires_review`.
- `fecha_entrega` is persisted on `GuiaDeRemision` and the `delivery_dates` map is rebuilt
  from guías on ALL code paths (pipeline AND every ReviewService re-reconcile call).
- The R9 fecha-divergence WARNING is NEVER masked: divergence is computed on the ORIGINAL
  date; the ceiling clamp is applied AFTER.
- When the Protocolo ceiling is unavailable or low-confidence, the ceiling is a no-op
  (graceful degrade identical to R9b floor absent behavior).

---

## 2. Scope

### In scope

**Pure domain function `domain/date_ceiling.py`**:
- `apply_reception_ceiling(reception: date | None, protocolo_ceiling: date | None,
  fecha_entrega: date | None) → CeilingResult`
- `CeilingResult`: namedtuple or dataclass carrying `(date | None, ceiling_applied: bool,
  crossed_bounds: bool)`.
- Three outer branches: (1) `protocolo_ceiling` is None → passthrough, no-op; (2)
  `fecha_entrega` is not None AND `fecha_entrega > protocolo_ceiling` → crossed-bounds
  (do NOT clamp; return original `reception`, `crossed_bounds=True`); (3) otherwise →
  clamp if `reception > protocolo_ceiling` → `(protocolo_ceiling, True, False)`, else
  passthrough `(reception, False, False)`.

**Critical ordering in `ReconciliationService.reconcile`**: ceiling applied AFTER
`check_fecha_divergence`. This ordering MUST NOT be inverted — divergence is computed on
the ORIGINAL date so the R9 WARNING is never masked.

**Side-channel fields** (additive, default `False` / backward-compatible):
- `GuiaDeRemision.reception_ceiling_applied: bool = False`
- `GuiaContribution.reception_ceiling_applied: bool = False`
- `ReconciliationRow.has_reception_ceiling: bool` (computed)
- `GuiaDeRemision.has_reception_ceiling` equivalent surfaced via DTO

**Persistence fix — `fecha_entrega` as single source of truth**:
- `GuiaDeRemision.fecha_entrega: date | None` — persisted field (not a transient pipeline
  variable); serialized with the extraction cache/sidecar.
- `delivery_dates` map (guia_id → fecha_entrega) MUST be built from guías on BOTH the
  pipeline path (`_stage_reconcile`) AND all three ReviewService re-reconcile calls
  (reassign, line-edit, field-edit) — so the bracket and crossed-bounds guard survive review.

**Crossed-bounds `delivery_after_protocolo` WARNING**:
- Distinct from `delivery_floor_applied` and `fecha_divergence`.
- Non-blocking; OR-sets `requires_review`; does NOT clamp the date.
- Surfaced in API response per guía contribution (`delivery_after_protocolo: bool`).

**Advisory vision hint (Part 2, MAY)**:
- The guía date-read MAY receive an advisory lower-bound context hint built from
  `fecha_entrega` on the non-batch vision path.
- MUST NOT change the `{date, confidence}` vision contract.
- Year is still reconstructed by inference — the hint is advisory only.
- Deferred if real-vision A/B data is unavailable (slice 3/4 pending).

### Out of scope

- Changing the material grouping key (FDR-011 / MAT-001 — permanent invariant).
- Auto-reassignment or date correction beyond the ceiling clamp.
- Any change to the R9 divergence predicate or Protocolo date extraction.
- Unit conversion (forbidden domain invariant).
- Slice 3/4 (stage reorder for the upper-bound prompt hint + `{DD-MM}-{ok/warning}` contract)
  — DEFERRED pending real-vision A/B.

---

## 3. Approach

Pure domain function with side-channel booleans — structurally identical to
`apply_delivery_floor` (R9b). The ceiling function is stateless, has zero I/O, and is
independently unit-testable (no mocks required; stdlib `date` objects only).

The critical ordering constraint (divergence-before-clamp) is enforced in
`ReconciliationService.reconcile`, not in the pipeline, because the divergence check and
ceiling application both operate over the fully-resolved guía dates at reconciliation time.
This is the cleanest place to enforce the ordering invariant.

The persistence fix follows the "single source of truth" principle: `fecha_entrega` travels
with the `GuiaDeRemision` object (persisted) so any code path that holds a `GuiaDeRemision`
automatically has the floor needed to rebuild `delivery_dates` without an additional SUNAT
fetch. This is the key architectural lesson: side-channel logic inside `reconcile()` is
lost on review re-reconcile unless its inputs ride the persisted guía.

---

## 4. Risks & Mitigations

| Risk | Trigger | Impact | Mitigation |
|------|---------|--------|------------|
| Divergence WARNING masked by ceiling | Ceiling applied BEFORE divergence check | R9 signal silently lost | Enforce divergence-before-clamp ordering in `ReconciliationService.reconcile`; covered by unit test |
| Crossed-bounds clamping to floor | `fecha_entrega > protocolo_ceiling`: system clamps to ceiling, pushing date below SUNAT floor | Physically invalid date | Crossed-bounds branch returns original date + WARNING; no clamping; unit-tested |
| `delivery_dates` absent on ReviewService re-reconcile | Review action (reassign/edit) calls `reconcile` without `delivery_dates` | Bracket silently lost; ceiling/floor not applied on review | Rebuild `delivery_dates` from `guia.fecha_entrega` inside every ReviewService re-reconcile call |
| Graceful degrade absent | Protocolo ceiling unavailable (null or low-confidence) | Ceiling applied against None → runtime error | Rule 1 passthrough (`protocolo_ceiling is None`); unit-tested |

---

## 5. Rollback / Abort plan

Additive only. All new side-channel fields default to `False`; the ceiling function is a
no-op when `protocolo_ceiling` is None (the graceful-degrade case that applies whenever
the Protocolo date is unavailable or low-confidence). The persistence fix is additive —
`GuiaDeRemision.fecha_entrega` was previously only a transient pipeline variable; making it
a persisted field does not break existing serialized caches (backward-compatible default
`None`). Revert is a one-commit rollback with zero data-migration impact.
