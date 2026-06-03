# Design — r9c-reception-date-ceiling

**Change**: `r9c-reception-date-ceiling`
**Phase**: design (archived)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-03
**Reads**: proposal.md (this change), R9 design.md, R9b design.md, `domain/date_ceiling.py`
(implemented), `domain/reconciliation_service.py`, `domain/models.py` (GuiaDeRemision +
GuiaContribution), `application/pipeline.py` `_stage_reconcile`, `services/review_service.py`,
`infrastructure/api/schemas.py` + `routes.py`.
**Patterns**: Ports & Adapters (Hexagonal), pure domain function, additive side-channel
(mirrors `year_inferred` / `fecha_divergence` / `delivery_floor_applied` provenance pattern
from rev-3 D5 / R9 / R9b), single-source-of-truth persistence for bracket inputs.

---

## 0. Architectural through-line — the symmetric `[floor, ceiling]` bracket

R9b established the LOWER bound: goods cannot be received before they are delivered
(`fecha_entrega` → floor). R9c establishes the UPPER bound: goods cannot be received after
the reception event itself was recorded (`fecha_authoritative` on the Protocolo → ceiling).
Together they form a deterministic `[fecha_entrega, fecha_authoritative]` bracket on every
guía's resolved reception date.

```
                    [  floor (R9b)  ,  ceiling (R9c)  ]
                    fecha_entrega      fecha_authoritative
                         |                    |
guía resolved date:  must be ≥ floor     must be ≤ ceiling
                                             ↑
                        crossed-bounds if floor > ceiling
                        (Protocolo-assembly human error — warn, do NOT clamp)
```

The critical ordering inside `ReconciliationService.reconcile`:

```
check_fecha_divergence(guia, declared_date)        ← R9 divergence (ORIGINAL date)
   ↓
apply_reception_ceiling(reception, ceiling, floor)  ← R9c ceiling (AFTER divergence)
   ↓
if ceiling_applied: set side-channel, OR-set requires_review
if crossed_bounds:  emit delivery_after_protocolo WARNING, OR-set requires_review
```

This ordering is **CRITICAL**: divergence MUST run on the ORIGINAL date. Inverting it would
mask the R9 misfiled-guía signal whenever a guía date is clamped by the ceiling.

---

## ADR-C1 — Pure domain function, no new port

`apply_reception_ceiling` is a deterministic pure function over `date | None` values.
It does not read configuration, call I/O, or depend on any adapter. It lives in
`domain/date_ceiling.py` alongside `date_floor.py` and `date_divergence.py` — the same
domain-date cluster.

The `protocolo_ceiling` and `fecha_entrega` values are plain `date | None` passed by
`ReconciliationService`; the domain function never imports `OfficialGre`, `SunatGreFetchPort`,
or any adapter-layer type. Domain purity is preserved: the application layer extracts both
dates from their respective domain objects and passes them as plain Python `date` values.

---

## ADR-C2 — Crossed-bounds policy: warn, do NOT clamp

When `fecha_entrega > protocolo_ceiling`, the physical invariant implies the SUNAT delivery
date is LATER than the declared reception date — goods were supposedly delivered AFTER being
received. This is physically impossible and is always a **Protocolo-assembly human error**
(e.g., the engineer stamped the wrong Registro N° on the Protocolo page, or two Registros
were swapped during document assembly).

**Decision**: do NOT clamp in either direction. Clamping to the ceiling would push the
resolved date below the SUNAT floor, violating the physical lower bound established by R9b.
Clamping to the floor would push the date above the ceiling, violating R9c. Neither clamp
is valid. The correct response is a distinct `delivery_after_protocolo` WARNING that flags
the guía `requires_review` and surfaces the anomaly for human inspection. The date is
left unchanged (original resolved value).

This policy is symmetric with the floor policy: neither bound auto-corrects beyond its
valid domain of operation.

---

## ADR-C3 — Ordering invariant: divergence BEFORE ceiling

The R9 fecha-divergence check (`check_fecha_divergence`) compares the ORIGINAL guía
date against the declared Protocolo date. If the ceiling clamp ran first, a guía date
originally above the declared date would be clamped down — and the divergence predicate
would then see two identical dates and emit no WARNING, silently hiding the misfiled-guía
signal.

The ordering is enforced in `ReconciliationService.reconcile` (domain layer), not in the
pipeline, because both operations share the same `GuiaDeRemision` object at reconciliation
time. The pipeline stage (`_stage_reconcile`) does not need to know the ordering — it
delegates to the service which owns both operations.

Unit test `test_ceiling_does_not_mask_divergence` explicitly verifies: given a guía whose
original date diverges from declared AND exceeds the ceiling, BOTH the fecha-divergence
WARNING and the ceiling side-channel are emitted after `reconcile` runs.

---

## ADR-C4 — Persistence: fecha_entrega as single source of truth on GuiaDeRemision

**Problem identified during Judgment-Day**: `fecha_entrega` was previously populated only
in `sunat_fetch_map` during the pipeline run (`_stage_sunat_fetch`). The map was a transient
pipeline variable not passed to `ReviewService`. Every `ReviewService` re-reconcile call
(reassign, line-edit, field-edit) invoked `ReconciliationService.reconcile(delivery_dates={})`,
silently dropping the floor and ceiling bracket.

**Fix**: `GuiaDeRemision.fecha_entrega: date | None = None` is promoted to a **persisted
field** (serialized with the extraction cache/sidecar). During `_stage_sunat_fetch`, the
pipeline writes `guia.fecha_entrega = sunat_fetch_map[guia_id].fecha_entrega`. After
serialization, every downstream consumer — including ReviewService — reads `fecha_entrega`
from the guía object itself.

`delivery_dates` (guia_id → fecha_entrega) MUST be rebuilt from guías before every
`reconcile` call:

```python
delivery_dates = {
    g.id: g.fecha_entrega
    for g in guias
    if g.fecha_entrega is not None
}
```

This rebuild MUST appear in:
1. `application/pipeline.py` `_stage_reconcile`
2. `services/review_service.py` reassign path
3. `services/review_service.py` line-edit path
4. `services/review_service.py` field-edit path

The architectural lesson: **any side-channel input to `reconcile()` that is not persisted
on the domain object will be silently lost whenever reconcile is called outside the initial
pipeline context**. The fix is to make `reconcile()` inputs ride the persisted domain object,
not an ephemeral pipeline-scoped map.

---

## ADR-C5 — Advisory vision hint (MAY, deferred)

Part 2 of R9c specifies that the non-batch vision path MAY receive an advisory lower-bound
context hint built from `fecha_entrega` when reading a guía's handwritten date. The intent
is to reduce year-inference ambiguity by anchoring the vision LLM's date interpretation
closer to the known delivery window.

This hint MUST NOT change the `{date, confidence}` contract returned by `VisionLLMPort`.
The year is still reconstructed by bounded inference after vision returns. The hint is
purely advisory context in the prompt.

**Decision**: This slice (3/4) is DEFERRED pending real-vision A/B data that quantifies
whether the hint reduces year-inference errors without introducing new false positives. It
is documented here so the deferred scope is explicit and does not re-enter as an undocumented
gap in a future change.

---

## Component summary

| New / changed | Layer | Responsibility |
|---------------|-------|----------------|
| `domain/date_ceiling.py` — `apply_reception_ceiling` | domain (pure) | Three-branch ceiling; crossed-bounds detection; no I/O; stdlib date only |
| `GuiaDeRemision.reception_ceiling_applied` | domain (`models.py`) | Side-channel bool (additive) |
| `GuiaDeRemision.fecha_entrega` (promoted to persisted) | domain (`models.py`) | Single source of truth for floor; survived review re-reconcile |
| `GuiaContribution.reception_ceiling_applied` | domain (`models.py`) | Propagated from guía |
| `ReconciliationRow.has_reception_ceiling` (computed) | domain (`models.py`) | Group-level indicator (derived) |
| `GuiaDeRemision.delivery_after_protocolo` | domain (`models.py`) | Crossed-bounds side-channel bool (additive) |
| `ReconciliationService.reconcile` — ceiling wiring | domain (service) | Enforce divergence-before-clamp ordering; crossed-bounds WARNING |
| `delivery_dates` rebuild in all reconcile callers | application (`pipeline.py`) + `review_service.py` | Rebuild from guías; pass to reconcile on every call path |
| `GuiaContributionResponse.reception_ceiling_applied` + `delivery_after_protocolo` | infrastructure (api `schemas.py`) | Additive DTO fields |
| `ReconciliationRowResponse.has_reception_ceiling` | infrastructure (api `schemas.py`) | Group-level DTO field |
| `_row_to_response` | infrastructure (api `routes.py`) | Map new domain fields → DTO |

---

## Invariants preserved

- **Domain purity**: `date_ceiling.py` imports only stdlib `datetime`. No SDK, no adapter.
- **`fecha` NOT in `_GroupKey`**: ceiling adjusts the guía fecha value; it does not change
  the grouping key (MAT-001 / FDR-011 permanent invariant).
- **MATCH EXACT(0)**: reconciliation qty comparison untouched; ceiling is additive side-channel.
- **R9 divergence NEVER masked**: ceiling runs AFTER `check_fecha_divergence` (ADR-C3).
- **Crossed-bounds: no clamp in either direction** (ADR-C2 policy decision).
- **Graceful degrade when ceiling absent**: `protocolo_ceiling is None` → passthrough; run
  is byte-identical to R9b baseline.
- **`requires_review` only OR-set**: existing block extended additively; never cleared.
- **Local-first / air-gap**: no new egress; ceiling uses dates already in domain objects.
- **Reversibility**: all new fields default to `False` / `None`; ceiling absent → no-op.
- **Persistence backward-compat**: `GuiaDeRemision.fecha_entrega = None` default; old
  serialized caches deserialize without error.
