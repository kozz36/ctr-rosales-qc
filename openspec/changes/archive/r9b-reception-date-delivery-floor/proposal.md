# Proposal — r9b-reception-date-delivery-floor

**Change**: `r9b-reception-date-delivery-floor`
**Phase**: archived (implemented & merged)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Parent**: Physical-invariant complement to `r9-fecha-divergence-review`. Builds on the
reception-date pipeline established by R9 (`_stage_extract_declared_date`, `infer_reception_year`,
`fecha_authoritative`). Depends on SUNAT GRE `fecha_entrega` being available via
`OfficialGre.fecha_entrega` (EXT-023 / R10 containerized-verification slice).

**Gate**: Judgment-Day APPROVED after 2 rounds. 892 backend unit tests passing.
**Status**: Implemented & merged to main via PR #5.

---

## 1. Intent

### Problem

After R9, the pipeline resolves a guía's reception date from the handwritten Protocolo date
(authoritative upper-authority) and `infer_reception_year`. However, a physical invariant was
left unenforced: goods cannot be received at the construction site **before** the supplier delivers
them. The SUNAT GRE `fecha_entrega` (delivery date from the official GRE document) is the
deterministic lower bound on the possible reception date.

Two failure modes existed:
1. When `infer_reception_year` resolved a date that fell before `fecha_entrega` (e.g., year
   inference picked the wrong year, yielding a date in the past relative to delivery), the pipeline
   accepted the physically impossible date without flagging it.
2. When both the handwritten day/month were absent AND `fecha_entrega` was present, the pipeline
   had no fallback mechanism — the reception date was left null even though the delivery date was
   a provably valid lower bound.

### Why now

The R9 review gate (Judgment-Day, round 1) identified a gap: `infer_reception_year` pre-filters
candidates to `>= lower` when the SUNAT lower bound is present, but a post-hoc physical-floor
check was never added for the residual case where inference returns a date or the null path when
day/month are absent. Without this floor, a year-inference artifact could silently produce a date
that precedes delivery, eroding the accuracy guarantee.

### Success looks like

- A guía whose resolved reception date falls BEFORE its `fecha_entrega` is corrected to
  `fecha_entrega` and flagged `requires_review` with a non-blocking `delivery_floor_applied`
  WARNING.
- A guía with no handwritten day/month but with a known `fecha_entrega` receives `fecha_entrega`
  as the reception date (floor applied) and is flagged `requires_review`.
- When SUNAT is disabled (default) or `fecha_entrega` is absent: the floor is a no-op; the run is
  byte-identical to the pre-change run.
- The floor is defense-in-depth; it does NOT change the domain's primary path (year inference
  pre-filters `>= lower` already). The floor catches residual pipeline cases only.

---

## 2. Scope

### In scope

**Pure domain function `domain/date_floor.py`**:
- `apply_delivery_floor(reception: date | None, fecha_entrega: date | None) → (date | None, bool)`
- Four branches: (1) `fecha_entrega` is None → passthrough, `floor_applied=False`;
  (2) `reception` is None → floor to `fecha_entrega`, `floor_applied=True`;
  (3) `reception < fecha_entrega` → floor to `fecha_entrega`, `floor_applied=True`;
  (4) `reception >= fecha_entrega` → unchanged, `floor_applied=False`.

**Side-channel field `delivery_floor_applied: bool`** on both `GuiaDeRemision` and
`GuiaContribution` (additive, default `False`, backward-compatible).

**Pipeline wiring**: in `application/pipeline.py`, `_stage_normalize_dates` (after
`infer_reception_year`): call `apply_delivery_floor` with the resolved reception date and the
guía's `fecha_entrega` from `sunat_fetch_map`. Also handle the JD-identified gap: when both
day/month are None AND `fecha_entrega` is present, floor to `fecha_entrega` immediately
(before inference would produce None).

**`requires_review` propagation**: when `floor_applied=True`, OR-set `requires_review` on the
`GuiaDeRemision`. Propagate `delivery_floor_applied` through `GuiaContribution` →
`ReconciliationRow.has_delivery_floor` (computed) → API DTO `_row_to_response`.

**Graceful degrade**: SUNAT disabled or `fecha_entrega` absent → `apply_delivery_floor`
returns passthrough; no behavioral change from R9 baseline. Output byte-identical.

### Out of scope

- Changing the material grouping key (FDR-011 / MAT-001 — permanent invariant).
- Modifying the divergence check or Protocolo date extraction.
- Auto-reassignment or date correction beyond the physical floor.
- Any change when SUNAT is disabled or `fecha_entrega` is None.

---

## 3. Approach

Pure domain function with a side-channel boolean — structurally identical to `year_inferred`
(rev-3 D5) and `fecha_divergence` (R9). The floor function is stateless, has zero I/O, and
is independently unit-testable (four-branch test with stdlib `date` objects, no mocks).

The pipeline wires it as a post-inference step, consuming `sunat_fetch_map[guia_id].fecha_entrega`
from the already-populated SUNAT map (populated by `_stage_sunat_fetch` upstream). When the map
is absent or the entry has no `fecha_entrega`, the call is a no-op.

Note on Rule 3 defense-in-depth: `reception < entrega` is unreachable through the normal pipeline
path because `infer_reception_year(lower=fecha_entrega)` already constrains candidates to
`>= lower`. Rule 3 activates only if a future refactor removes or bypasses that lower-bound
constraint. It is included explicitly as a spec requirement so the domain function is
self-consistent and independently testable.

---

## 4. Risks & Mitigations

| Risk | Trigger | Impact | Mitigation |
|------|---------|--------|------------|
| Floor incorrectly applied when SUNAT is off | `fecha_entrega` is None | Silent no-op (Rule 1 passthrough) | `apply_delivery_floor` returns passthrough on `None`; covered by unit test |
| Year-inference SUNAT lower-bound already prevents floor from firing | Rule 3 unreachable path | No impact — floor is defense-in-depth | Documented as ADR note; unit test explicitly covers the < case to keep the contract honest |
| `requires_review` cascades to MATCH rows | Floor applied on a matching group | Row flagged for review, status stays MATCH | `requires_review` is additive; MATCH/MISMATCH logic is untouched |

---

## 5. Rollback / Abort plan

Additive only. `delivery_floor_applied=False` is the default; disabling SUNAT (default) makes
the floor a provable no-op. Revert is a one-commit rollback with zero data-migration impact.
