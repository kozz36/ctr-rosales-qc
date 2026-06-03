# Spec — Reception Date Ceiling (delta over fecha-divergence)
**Change**: r9c-reception-date-ceiling
**Domain**: fecha-divergence (delta over r9b-reception-date-delivery-floor + r9-fecha-divergence-review + r8-material-matching)
**Phase**: spec (archived)
**Date**: 2026-06-03

---

## Purpose

Enforce the physical upper bound on a guía's resolved reception date: goods cannot be
received after the Registro's authoritative declared reception moment (the handwritten
`Fecha:` on the Protocolo de Recepción). Together with the delivery floor established by
R9b, this completes the deterministic `[fecha_entrega, fecha_authoritative]` bracket on
every guía's resolved reception date.

This is a **delta spec** over:
- `openspec/specs/fecha-divergence/spec.md` (FDR-001 through FDR-013)
- `openspec/changes/r8-material-matching/specs/material-matching/spec.md` (MAT-001 through MAT-013)

All prior requirements remain in force. Requirements below ADD new behaviour.
Each entry is marked `[ADDED]`.

---

## Requirements

### FDR-014 — [ADDED] Reception date ceiling: Protocolo authoritative date as physical upper bound

When a Registro's authoritative declared reception date (`fecha_authoritative`, the
handwritten Protocolo date, vision-read per FDR-001) is available and meets the confidence
threshold (FDR-007), each guía's resolved reception date MUST satisfy the physical invariant:
**reception date <= Protocolo authoritative date**.

The domain service (`ReconciliationService.reconcile`) MUST apply the ceiling function
(`domain/date_ceiling.apply_reception_ceiling`) to each guía's resolved date AFTER the
R9 fecha-divergence check (`check_fecha_divergence`) and AFTER the R9b delivery floor
(`apply_delivery_floor`). This ordering is **CRITICAL** — the divergence check MUST
operate on the ORIGINAL resolved date so the R9 misfiled-guía signal is never masked by
the ceiling clamp.

The ceiling function MUST implement the following three-branch logic:

| Branch | Condition | Result | Side-channels |
|--------|-----------|--------|---------------|
| Rule 1 | `protocolo_ceiling` is None | Passthrough — `reception` unchanged | `ceiling_applied=False`, `crossed_bounds=False` |
| Rule 2 (crossed-bounds) | `fecha_entrega` is not None AND `fecha_entrega > protocolo_ceiling` | Passthrough — do NOT clamp; emit `delivery_after_protocolo` WARNING | `ceiling_applied=False`, `crossed_bounds=True` |
| Rule 3 | `reception > protocolo_ceiling` | Clamp DOWN to `protocolo_ceiling` | `ceiling_applied=True`, `crossed_bounds=False` |
| Rule 4 | `reception <= protocolo_ceiling` (and not crossed-bounds) | Unchanged | `ceiling_applied=False`, `crossed_bounds=False` |

When `ceiling_applied = True`, the domain service MUST:
- Set `GuiaDeRemision.reception_ceiling_applied = True`
- OR-set `GuiaDeRemision.requires_review = True` with reason `"reception_ceiling_applied"`
- Propagate `reception_ceiling_applied` through `GuiaContribution`
- Surface `ReconciliationRow.has_reception_ceiling` (computed: `any(g.reception_ceiling_applied for g in self.guias)`)

The material MATCH/MISMATCH status for the group MUST NOT change because of the ceiling.
The ceiling MUST NOT auto-correct quantities, keys, or declared values.

When the Protocolo ceiling is unavailable (null or low-confidence per FDR-005/FDR-007),
the ceiling is a provable no-op (Rule 1 passthrough) and the run is byte-identical to the
R9b baseline.

#### Acceptance Scenarios

**Scenario FDR-S24 — Protocolo ceiling absent: ceiling is a no-op**

Given the Registro N° has no readable Protocolo date (null or low-confidence)
And a guía with resolved reception date `2026-05-28`
When `apply_reception_ceiling(reception=date(2026,5,28), protocolo_ceiling=None, fecha_entrega=...)` is called
Then Rule 1 passthrough applies
And `reception_ceiling_applied = False`
And `crossed_bounds = False`
And the guía's `fecha` remains `2026-05-28`
And `requires_review` is NOT set by the ceiling
And the run output is byte-identical to the R9b baseline

**Scenario FDR-S25 — Reception date exceeds Protocolo ceiling: clamp applied**

Given the Registro N° has `fecha_authoritative = date(2026, 5, 28)` (Protocolo ceiling)
And a guía whose resolved reception date is `date(2027, 5, 28)` (year-inference artifact)
And `fecha_entrega = date(2026, 5, 20)` (floor, does not exceed ceiling)
When `apply_reception_ceiling(reception=date(2027,5,28), protocolo_ceiling=date(2026,5,28), fecha_entrega=date(2026,5,20))` is called
Then Rule 3 applies: returned date is `date(2026, 5, 28)` (clamped to ceiling)
And `ceiling_applied = True`
And `crossed_bounds = False`
And `GuiaDeRemision.reception_ceiling_applied = True`
And `requires_review = True` with reason `"reception_ceiling_applied"`
And the group's MATCH/MISMATCH status is unaffected

**Scenario FDR-S26 — Reception date at or below Protocolo ceiling: no clamp**

Given `fecha_authoritative = date(2026, 5, 28)` (Protocolo ceiling)
And a guía with resolved reception date `date(2026, 5, 15)` (below ceiling)
When `apply_reception_ceiling(reception=date(2026,5,15), protocolo_ceiling=date(2026,5,28), fecha_entrega=None)` is called
Then Rule 4 applies: returned date is `date(2026, 5, 15)` (unchanged)
And `ceiling_applied = False`
And `requires_review` is NOT set by the ceiling

**Scenario FDR-S27 — Ceiling does NOT mask R9 divergence WARNING**

Given `fecha_authoritative = date(2026, 5, 28)` (Protocolo ceiling)
And a guía whose ORIGINAL resolved date is `date(2027, 4, 15)` (day=15, month=04 — diverges from declared day=28, month=05)
And the guía ALSO exceeds the ceiling
When `ReconciliationService.reconcile` runs for this guía
Then the R9 fecha-divergence WARNING IS emitted (ORIGINAL date diverges: month 04 ≠ 05)
And AFTER the divergence check, the ceiling clamp is applied: date → `date(2026, 5, 28)`
And `reception_ceiling_applied = True`
And both the fecha-divergence WARNING and the ceiling side-channel are present on the contribution record
And the divergence WARNING was NOT suppressed by the ceiling

---

### FDR-015 — [ADDED] Crossed-bounds anomaly: delivery after Protocolo — warn, do NOT clamp

When the SUNAT delivery floor (`fecha_entrega`) is LATER than the Protocolo authoritative
ceiling (`fecha_authoritative`), the physical invariant is violated at the input level: the
supplier delivered goods after they were supposedly received. This is always a
**Protocolo-assembly human error** (e.g., wrong Registro N° stamped on the Protocolo page).

The system MUST NOT clamp in either direction when crossed-bounds is detected:
- Clamping to the ceiling would push the date below the SUNAT floor (violating FDR-012).
- Clamping to the floor would push the date above the ceiling (violating FDR-014).

Instead the system MUST:
- Leave the guía's resolved reception date unchanged (original value).
- Set `GuiaDeRemision.delivery_after_protocolo = True` (crossed-bounds side-channel).
- OR-set `GuiaDeRemision.requires_review = True` with reason `"delivery_after_protocolo"`.
- Emit a distinct `delivery_after_protocolo` WARNING in the reconciliation result.
- Propagate `delivery_after_protocolo` through `GuiaContribution` → API DTO.

The `delivery_after_protocolo` WARNING is **non-blocking** — the material MATCH/MISMATCH
status for the group MUST NOT change because of the crossed-bounds anomaly. No auto-correction
is applied. The existing `GuiaReassignDialog` flow is the human resolution path.

#### Acceptance Scenarios

**Scenario FDR-S28 — Crossed-bounds: delivery after Protocolo — warn, no clamp**

Given `fecha_authoritative = date(2026, 5, 20)` (Protocolo ceiling)
And a guía with `fecha_entrega = date(2026, 5, 28)` (delivery floor AFTER ceiling)
And the guía's resolved reception date is `date(2026, 5, 25)` (between floor and ceiling — impossible bracket)
When `apply_reception_ceiling(reception=date(2026,5,25), protocolo_ceiling=date(2026,5,20), fecha_entrega=date(2026,5,28))` is called
Then Rule 2 (crossed-bounds) applies
And the returned date is `date(2026, 5, 25)` (UNCHANGED — no clamp)
And `ceiling_applied = False`
And `crossed_bounds = True`
And `GuiaDeRemision.delivery_after_protocolo = True`
And `requires_review = True` with reason `"delivery_after_protocolo"`
And the group's MATCH/MISMATCH status is unaffected

**Scenario FDR-S29 — Crossed-bounds does NOT prevent R9b floor from having been applied**

Given `fecha_authoritative = date(2026, 5, 20)` (Protocolo ceiling)
And `fecha_entrega = date(2026, 5, 28)` (floor AFTER ceiling — crossed-bounds)
And the guía had `reception=None` so R9b floor was already applied in `_stage_normalize_dates`
  giving `fecha = date(2026, 5, 28)` with `delivery_floor_applied = True`
When `ReconciliationService.reconcile` applies the ceiling check
Then crossed-bounds is detected (`fecha_entrega=2026-05-28 > ceiling=2026-05-20`)
And the ceiling does NOT clamp the already-floored date
And `delivery_after_protocolo = True` and `delivery_floor_applied = True` BOTH appear on the contribution
And `requires_review = True` with BOTH reasons `["delivery_floor_applied", "delivery_after_protocolo"]`

---

### FDR-016 — [ADDED] Persistence of fecha_entrega and delivery_dates across review re-reconcile

`GuiaDeRemision.fecha_entrega: date | None` MUST be a **persisted field** on the domain
model — not a transient pipeline variable. It MUST be serialized with the extraction cache
/ sidecar so that any code path that loads a `GuiaDeRemision` from storage has access to
`fecha_entrega` without an additional SUNAT fetch.

The `delivery_dates` map (guia_id → `fecha_entrega`) MUST be rebuilt from the loaded guías
before EVERY call to `ReconciliationService.reconcile`. This rebuild MUST occur on ALL the
following code paths:

1. `application/pipeline.py` `_stage_reconcile` (pipeline run)
2. `services/review_service.py` — guía reassign path
3. `services/review_service.py` — guía line-edit path
4. `services/review_service.py` — guía field-edit path

The rebuild pattern MUST be:

```python
delivery_dates = {
    g.id: g.fecha_entrega
    for g in guias
    if g.fecha_entrega is not None
}
```

This pattern MUST NOT rely on a separately maintained in-memory map (e.g., a pipeline-scoped
`sunat_fetch_map`) that is not available on the ReviewService path.

**Rationale**: without this invariant, any review action (reassign, edit) that triggers
`ReconciliationService.reconcile` internally would do so with `delivery_dates={}`,
silently dropping the R9b floor, the R9c ceiling, and the crossed-bounds guard. The result
would be incorrect bracket behavior on every row touched by a review action — a correctness
regression invisible to the material MATCH/MISMATCH status but actively wrong for the
date-bracket domain invariant.

#### Acceptance Scenarios

**Scenario FDR-S30 — fecha_entrega survives review reassign: bracket applied post-reassign**

Given a guía with `fecha_entrega = date(2026, 5, 20)` persisted on `GuiaDeRemision`
And a review action reassigns that guía to a different Registro N°
When `ReviewService.reassign_guia` calls `ReconciliationService.reconcile` internally
Then `delivery_dates` is rebuilt from guías (including the reassigned guía's `fecha_entrega`)
And the R9b floor and R9c ceiling bracket is applied on the post-reassign reconcile result
And `delivery_floor_applied` and `reception_ceiling_applied` reflect the current bracket state

**Scenario FDR-S31 — SUNAT disabled: delivery_dates is empty; bracket is no-op**

Given `sunat.enabled = false` (default)
And all guías have `fecha_entrega = None` (no SUNAT data)
When `delivery_dates` is rebuilt from guías
Then `delivery_dates = {}` (empty — no floor/ceiling inputs available)
And `ReconciliationService.reconcile(delivery_dates={})` produces output byte-identical to the R9 baseline
And neither `delivery_floor_applied` nor `reception_ceiling_applied` is set on any guía

**Scenario FDR-S32 — fecha_entrega backward-compat: old cache without field deserializes cleanly**

Given a serialized extraction cache produced before R9c (no `fecha_entrega` field on guías)
When the cache is deserialized into `GuiaDeRemision` objects
Then `GuiaDeRemision.fecha_entrega` defaults to `None`
And no deserialization error occurs
And the run degrades gracefully (bracket is no-op for all guías, per FDR-S31)

---

## Out of scope for this change

- Changing the material grouping key (FDR-011 / MAT-001 — permanent invariant).
- Modifying the R9 divergence check or Protocolo date extraction pipeline.
- Auto-reassignment or any correction beyond the ceiling clamp.
- Unit conversion or any quantity axis change.
- Advisory vision hint (Part 2, slice 3/4) — DEFERRED pending real-vision A/B data.
- Frontend visual treatment for `reception_ceiling_applied` or `delivery_after_protocolo`
  (backend-only for this change; frontend surfaces existing `requires_review` flag).
