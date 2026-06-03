# Spec — Delivery Floor (delta over fecha-divergence)
**Change**: r9b-reception-date-delivery-floor
**Domain**: fecha-divergence (delta over r9-fecha-divergence-review + r8-material-matching)
**Phase**: spec (archived)
**Date**: 2026-06-03

---

## Purpose

Enforce the physical lower bound on a guía's resolved reception date: goods cannot be received
before they are delivered. When SUNAT is enabled and `fecha_entrega` is available on the
`OfficialGre` record, the pipeline MUST floor the resolved reception date to `fecha_entrega`
and flag the guía for human review. When SUNAT is disabled or `fecha_entrega` is absent, the
floor is a no-op and the run is byte-identical to the R9 baseline.

This is a **delta spec** over:
- `openspec/specs/fecha-divergence/spec.md` (FDR-001 through FDR-011)
- `openspec/changes/r8-material-matching/specs/material-matching/spec.md` (MAT-001 through MAT-013)

All prior requirements remain in force. Requirements below ADD new behaviour.
Each entry is marked `[ADDED]`.

---

## Requirements

### FDR-012 — [ADDED] Reception date delivery floor: SUNAT fecha_entrega as physical lower bound

When a guía has an associated SUNAT `fecha_entrega` (delivery date from `OfficialGre`,
available via `sunat_fetch_map` when `sunat.enabled = true`), the resolved reception date
for that guía MUST satisfy the physical invariant: **reception date >= delivery date**.

The pipeline MUST apply the following four-branch floor function
(`domain/date_floor.apply_delivery_floor`) in `_stage_normalize_dates` AFTER
`infer_reception_year`:

| Branch | Condition | Result | `delivery_floor_applied` |
|--------|-----------|--------|--------------------------|
| Rule 1 | `fecha_entrega` is None | Passthrough — `reception` unchanged | False |
| Rule 2 | `reception` is None, `fecha_entrega` is not None | Floor to `fecha_entrega` | True |
| Rule 3 | `reception < fecha_entrega` | Floor to `fecha_entrega` | True |
| Rule 4 | `reception >= fecha_entrega` | Unchanged | False |

When `delivery_floor_applied = True`, the pipeline MUST OR-set `requires_review = True`
on the guía and emit a non-blocking WARNING with reason `"delivery_floor_applied"`.

The material MATCH/MISMATCH status for the group MUST NOT change because of the floor.
The floor MUST NOT auto-correct quantities, keys, or declared values.

**Rule 3 is defense-in-depth** (unreachable through the normal path because
`infer_reception_year(lower=fecha_entrega)` already constrains candidate years to
`>= lower`). It is included to keep the domain function self-consistent and to guard
against future refactors that bypass the lower-bound constraint.

#### Acceptance Scenarios

**Scenario FDR-S20 — SUNAT absent (disabled): floor is a no-op**

Given `sunat.enabled = false` (default)
And a guía with resolved reception date `2026-05-28` (via year inference)
When `_stage_normalize_dates` runs
Then `apply_delivery_floor` is called with `fecha_entrega = None`
And the guía's `fecha` remains `2026-05-28` (Rule 1 passthrough)
And `delivery_floor_applied = False`
And `requires_review` is NOT set by the floor
And the run output is byte-identical to the R9 baseline

**Scenario FDR-S21 — Reception date before delivery date: floor applied**

Given `sunat.enabled = true`
And a guía whose `OfficialGre.fecha_entrega = 2026-05-20`
And `infer_reception_year` resolves to `reception = 2025-05-28` (year-inference artifact)
When `apply_delivery_floor(reception=date(2025,5,28), fecha_entrega=date(2026,5,20))` is called
Then the returned date is `date(2026,5,20)` (Rule 3 floor)
And `delivery_floor_applied = True`
And the guía's `requires_review = True` with reason `"delivery_floor_applied"`
And the group's MATCH/MISMATCH status is unaffected

**Scenario FDR-S22 — Reception date on or after delivery date: no floor**

Given `sunat.enabled = true`
And a guía with `OfficialGre.fecha_entrega = 2026-05-20`
And `infer_reception_year` resolves to `reception = 2026-05-28`
When `apply_delivery_floor(reception=date(2026,5,28), fecha_entrega=date(2026,5,20))` is called
Then the returned date is `date(2026,5,28)` (Rule 4 — unchanged)
And `delivery_floor_applied = False`
And `requires_review` is NOT set by the floor

---

### FDR-013 — [ADDED] Null reception date with known delivery date: floor to fecha_entrega

When a guía's handwritten day AND month are BOTH absent from the vision read (vision returned
no parseable date), AND `fecha_entrega` is known (SUNAT enabled and `OfficialGre` present),
the pipeline MUST floor the reception date to `fecha_entrega` BEFORE calling
`infer_reception_year` (since inference has no day/month to operate on).

The pipeline MUST:
- Set `GuiaDeRemision.fecha = fecha_entrega`
- Set `GuiaDeRemision.delivery_floor_applied = True`
- OR-set `GuiaDeRemision.requires_review = True`

When SUNAT is disabled or `fecha_entrega` is absent and day/month are null, the
reception date MUST remain null (existing R9 null-fecha handling — no change).

#### Acceptance Scenario

**Scenario FDR-S23 — Null vision date with known delivery: floor to fecha_entrega**

Given `sunat.enabled = true`
And a guía where the vision LLM returns no parseable date (day = None, month = None)
And the guía's `OfficialGre.fecha_entrega = 2026-05-20`
When `_stage_normalize_dates` processes this guía
Then `GuiaDeRemision.fecha = date(2026, 5, 20)` (Rule 2 / FDR-012 floor)
And `delivery_floor_applied = True`
And `requires_review = True`
And no fecha-divergence WARNING is emitted for this guía by the R9 divergence check
  (null-fecha path — FDR-006 still applies; the guía date is now floored, not null,
  so divergence check WILL run if the floored date differs from the declared date)

---

## Out of scope for this change

- Changing the material grouping key (FDR-011 / MAT-001 — permanent invariant).
- Modifying the R9 divergence check or Protocolo date extraction pipeline.
- Auto-reassignment or any correction beyond the physical floor.
- Any behavior change when SUNAT is disabled (`sunat.enabled = false` is the default).
- Unit conversion or any quantity axis change.
