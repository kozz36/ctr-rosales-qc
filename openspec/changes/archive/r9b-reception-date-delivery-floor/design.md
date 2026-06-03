# Design — r9b-reception-date-delivery-floor

**Change**: `r9b-reception-date-delivery-floor`
**Phase**: design (archived)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Reads**: proposal.md (this change), R9 design.md, `domain/date_floor.py` (implemented),
`application/pipeline.py` `_stage_normalize_dates`, `domain/models.py` (GuiaDeRemision +
GuiaContribution), `infrastructure/api/schemas.py` + `routes.py`.
**Patterns**: Ports & Adapters (Hexagonal), pure domain function, additive side-channel
(mirrors `year_inferred` / `fecha_divergence` provenance pattern from rev-3 D5 / R9).

---

## 0. Architectural through-line

The floor rule is a **physical invariant enforcement function** placed in the pure domain
(`domain/date_floor.py`), wired by the application layer (pipeline), and surfaced via the
same additive side-channel mechanism already proven by `year_inferred` (rev-3 D5) and
`fecha_divergence` (R9 ADR-4). No new port is introduced. The SUNAT `fecha_entrega` is
already available in `sunat_fetch_map` (populated by `_stage_sunat_fetch` when SUNAT is
enabled); the floor function reads it read-only.

```
_stage_normalize_dates (pipeline.py)
  └─ infer_reception_year(day, month, year, upper, lower=fecha_entrega) → (date|None, year_inferred)
       └─ apply_delivery_floor(reception, fecha_entrega) → (date|None, floor_applied)
              ┌─ Rule 1: fecha_entrega is None → passthrough
              ├─ Rule 2: reception is None    → floor to fecha_entrega, floor_applied=True
              ├─ Rule 3: reception<entrega    → floor to fecha_entrega, floor_applied=True  [defense-in-depth]
              └─ Rule 4: reception>=entrega   → unchanged, floor_applied=False
       → GuiaDeRemision.fecha (possibly floored)
       → GuiaDeRemision.delivery_floor_applied (side-channel bool)
       → if floor_applied: GuiaDeRemision.requires_review |= True
```

---

## ADR-F1 — Pure domain function, no new port

`apply_delivery_floor` is a deterministic pure function over `date | None` values.
It does not read configuration, call I/O, or depend on any adapter. It lives in
`domain/date_floor.py` alongside `date_inference.py` and `date_divergence.py` —
the same domain-date cluster.

The `fecha_entrega` value is a plain `date | None` passed by the pipeline; the domain
function never imports `OfficialGre` or `SunatGreFetchPort`. This preserves domain purity:
the application layer (pipeline) extracts `fecha_entrega` from `sunat_fetch_map[guia_id]`
and passes it as a plain `date`. No adapter-layer type crosses the domain boundary.

---

## ADR-F2 — Rule 3 is defense-in-depth, not the primary path

`infer_reception_year(lower=fecha_entrega)` constrains candidate years to
`>= lower`. If `fecha_entrega = 2026-05-20` and `DD=15, MM=04`, the year inference
will only produce `date(Y, 04, 15)` for Y such that `date(Y,04,15) >= 2026-05-20`.
Year 2026 produces `2026-04-15 < 2026-05-20` → rejected; no candidate → returns None
(Rule 2 path). Year 2027 would produce `2027-04-15` which is a valid candidate — so
inference returns `2027-04-15` (above the lower bound, not below it).

Therefore Rule 3 (`reception < entrega`) is unreachable through the normal path.
It is included in the domain function so the function is self-consistent and independently
testable, and to guard against a future refactor that bypasses the lower-bound constraint
in `infer_reception_year`. The unit test for Rule 3 documents the contract explicitly.

---

## ADR-F3 — JD-found gap: null day/month + fecha_entrega present

Judgment-Day round 1 identified that when both day AND month are None from vision
(OCR/vision returned nothing for the stamp), `infer_reception_year` is never called
(no day/month to place). The pipeline would leave `fecha=None` even when `fecha_entrega`
is a known lower bound.

Fix: in `_stage_normalize_dates`, before calling `infer_reception_year`, check:
if `day is None AND month is None AND fecha_entrega is not None`, set the guía fecha
directly to `fecha_entrega` and set `delivery_floor_applied=True`, `requires_review=True`.
This is the explicit pre-inference floor for the null-date case; it is not handled by
`apply_delivery_floor` (which operates on already-resolved `date | None`) but by the
pipeline stage logic.

---

## ADR-F4 — Side-channel propagation: mirrors year_inferred / fecha_divergence

`GuiaDeRemision.delivery_floor_applied: bool = False` — additive, default False.
`GuiaContribution.delivery_floor_applied: bool = False` — propagated from the guía.
`ReconciliationRow.has_delivery_floor: bool` (computed) — `any(g.delivery_floor_applied for g in self.guias)`.
`GuiaContributionResponse.delivery_floor_applied: bool = False` — DTO field.
`ReconciliationRowResponse.has_delivery_floor: bool = False` — DTO field.

All new fields have backward-compatible defaults (`False`). The API DTO mapping follows
the exact same pattern as `year_inferred` / `fecha_divergence` in `_row_to_response`
(routes.py). No new endpoint introduced.

---

## Component summary

| New / changed | Layer | Responsibility |
|---------------|-------|----------------|
| `domain/date_floor.py` — `apply_delivery_floor` | domain (pure) | Four-branch physical floor; no I/O; stdlib date only |
| `GuiaDeRemision.delivery_floor_applied` | domain (`models.py`) | Side-channel bool (additive) |
| `GuiaContribution.delivery_floor_applied` | domain (`models.py`) | Propagated from guía |
| `ReconciliationRow.has_delivery_floor` (computed) | domain (`models.py`) | Group-level indicator (derived) |
| `_stage_normalize_dates` floor wiring | application (`pipeline.py`) | Post-inference floor + null-day/month gap (ADR-F3) |
| `GuiaContributionResponse.delivery_floor_applied` + `ReconciliationRowResponse.has_delivery_floor` | infrastructure (api `schemas.py`) | Additive DTO surface |
| `_row_to_response` | infrastructure (api `routes.py`) | Map domain fields → DTO |

---

## Invariants preserved

- **Domain purity**: `date_floor.py` imports only stdlib `datetime`. No SDK, no adapter.
- **`fecha` NOT in `_GroupKey`**: floor adjusts the fecha value on the guía; it does not
  change the grouping key (MAT-001 / FDR-011 permanent invariant).
- **MATCH EXACT(0)**: reconciliation qty comparison untouched; floor is additive side-channel.
- **Graceful degrade when SUNAT off**: `fecha_entrega is None` → Rule 1 passthrough; run is
  byte-identical to R9 baseline.
- **`requires_review` only OR-set**: existing block extended additively; never cleared.
- **Local-first / air-gap**: no new egress; SUNAT path uses existing `sunat_fetch_map`.
- **Reversibility**: `delivery_floor_applied=False` is the default; SUNAT disabled → no-op.
