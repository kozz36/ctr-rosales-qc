# Tasks — r9c-reception-date-ceiling

**Change**: `r9c-reception-date-ceiling` · **Phase**: tasks (archived) · **Store**: hybrid · **Date**: 2026-06-03
**Branch**: `feat/rev2-identity-domain` (continuing)
**Strict TDD**: active
**Gate**: Judgment-Day APPROVED after 3 rounds + 2 fix iterations. 972 backend unit tests.
**Status**: Implemented & merged to main via PR #8.

All tasks are marked `[x]` (implemented).

---

## Slice RC-A — Pure domain ceiling function

### [x] RC.1 — `domain/date_ceiling.py`: `apply_reception_ceiling` pure function

**Spec refs**: FDR-014, FDR-015, ADR-C1.
**Depends on**: existing `domain/date_floor.py` (sibling, R9b).

**Deliverables**:
- New file `backend/src/reconciliation/domain/date_ceiling.py`
- `CeilingResult` namedtuple or dataclass: `(date: date | None, ceiling_applied: bool, crossed_bounds: bool)`
- `apply_reception_ceiling(reception: date | None, protocolo_ceiling: date | None, fecha_entrega: date | None) → CeilingResult`
- Four branches (Rules 1–4 per FDR-014 table); stdlib `datetime.date` only; no I/O, no Pydantic, no SDK.

**Tests** (new `backend/tests/unit/domain/test_date_ceiling.py`):
- Rule 1: `protocolo_ceiling=None` → passthrough, `ceiling_applied=False`, `crossed_bounds=False`
- Rule 2 (crossed-bounds): `fecha_entrega=date(2026,5,28) > protocolo_ceiling=date(2026,5,20)` → unchanged, `crossed_bounds=True`
- Rule 3: `reception=date(2027,5,28)` above ceiling → clamped to `protocolo_ceiling`, `ceiling_applied=True`
- Rule 4: `reception=date(2026,5,15)` below ceiling → unchanged, `ceiling_applied=False`
- Rule 4 boundary: `reception == protocolo_ceiling` → unchanged, `ceiling_applied=False`
- Divergence-ordering invariant: crossed-bounds does NOT set `ceiling_applied=True`

**Commit message**: `feat(domain): add apply_reception_ceiling pure physical upper-bound function (FDR-014, FDR-015)`

---

## Slice RC-B — Domain model extensions

### [x] RC.2 — `GuiaDeRemision` + `GuiaContribution` + `ReconciliationRow`: ceiling side-channels

**Spec refs**: FDR-014, FDR-015, ADR-C4.
**Depends on**: RC.1 (`date_ceiling.py` stable).

**Deliverables** (`backend/src/reconciliation/domain/models.py`):
- `GuiaDeRemision.reception_ceiling_applied: bool = False` (additive)
- `GuiaDeRemision.delivery_after_protocolo: bool = False` (additive)
- `GuiaDeRemision.fecha_entrega: date | None = None` (promoted to persisted field — was transient)
- `GuiaContribution.reception_ceiling_applied: bool = False` (additive)
- `GuiaContribution.delivery_after_protocolo: bool = False` (additive)
- `ReconciliationRow.has_reception_ceiling: bool` (computed: `any(g.reception_ceiling_applied for g in self.guias)`)

**Tests** (update `backend/tests/unit/domain/test_models.py`):
- Old serialised dict without new fields → `model_validate` succeeds; all default `False` / `None`
- `GuiaContribution.reception_ceiling_applied=True` stored and retrieved correctly
- `GuiaContribution.delivery_after_protocolo=True` stored and retrieved correctly
- `ReconciliationRow.has_reception_ceiling`: all False → False; one True → True; mixed → True
- `GuiaDeRemision.fecha_entrega` round-trips through serialization

**Commit message**: `feat(domain): add reception ceiling side-channel fields and promote fecha_entrega to persisted field (ADR-C4)`

---

## Slice RC-C — ReconciliationService wiring

### [x] RC.3 — `ReconciliationService.reconcile`: ceiling wiring + ordering invariant

**Spec refs**: FDR-014, FDR-015, ADR-C3.
**Depends on**: RC.1 (ceiling function), RC.2 (model fields).

**Deliverables** (`backend/src/reconciliation/domain/reconciliation_service.py`):
- Import `apply_reception_ceiling` from `domain.date_ceiling` (domain-to-domain import, safe).
- After `check_fecha_divergence` call: apply `apply_reception_ceiling(guia.fecha, registro.fecha_authoritative, guia.fecha_entrega)`.
- On `ceiling_applied=True`: set `guia.reception_ceiling_applied=True`, OR-set `guia.requires_review=True`.
- On `crossed_bounds=True`: set `guia.delivery_after_protocolo=True`, OR-set `guia.requires_review=True`; emit `delivery_after_protocolo` WARNING.
- MUST NOT move the divergence check after the ceiling application.

**Tests** (update `backend/tests/unit/domain/test_reconciliation_service.py`):
- Ceiling absent: `reception_ceiling_applied=False`; run output unchanged (FDR-S24)
- Reception above ceiling: clamped, `reception_ceiling_applied=True`, `requires_review=True` (FDR-S25)
- Reception below ceiling: unchanged, `reception_ceiling_applied=False` (FDR-S26)
- `test_ceiling_does_not_mask_divergence`: date diverges AND exceeds ceiling → both fecha-divergence WARNING and ceiling side-channel present (FDR-S27, ADR-C3 ordering invariant)
- Crossed-bounds: `delivery_after_protocolo=True`, date unchanged, no clamp (FDR-S28)
- Crossed-bounds + prior floor applied: both `delivery_floor_applied=True` and `delivery_after_protocolo=True` on same guía (FDR-S29)

**Commit message**: `feat(domain): wire reception ceiling in ReconciliationService.reconcile; enforce divergence-before-clamp ordering (ADR-C3)`

---

## Slice RC-D — Persistence fix: delivery_dates on all reconcile call paths

### [x] RC.4 — `_stage_reconcile` + `ReviewService`: rebuild delivery_dates from guías

**Spec refs**: FDR-016, ADR-C4.
**Depends on**: RC.2 (`fecha_entrega` persisted on model).

**Deliverables**:
- `backend/src/reconciliation/application/pipeline.py` `_stage_reconcile`: replace any use of
  `sunat_fetch_map` as `delivery_dates` source with rebuild from `guia.fecha_entrega`.
- `backend/src/reconciliation/services/review_service.py` — reassign, line-edit, field-edit
  paths: add `delivery_dates` rebuild from guías before each `reconcile` call.
- During `_stage_sunat_fetch`: write `guia.fecha_entrega = sunat_fetch_map[guia_id].fecha_entrega`
  so the field is populated before serialization.

**Tests** (update targeted pipeline and review_service tests):
- Pipeline path: `delivery_dates` is rebuilt from guías; guía with `fecha_entrega` set → floor/ceiling applied on reconcile (FDR-S30)
- Review reassign path: after reassign, reconcile uses `delivery_dates` from guías (FDR-S30)
- SUNAT disabled: `delivery_dates={}` from empty guías → bracket is no-op; output byte-identical (FDR-S31)
- Old cache backward-compat: `fecha_entrega=None` default → no-op on all guías (FDR-S32)

**Commit message**: `fix(pipeline,review): persist fecha_entrega on GuiaDeRemision; rebuild delivery_dates from guías on all reconcile call paths (FDR-016, ADR-C4)`

---

## Slice RC-E — API surface

### [x] RC.5 — API schema + routes: ceiling DTO fields

**Spec refs**: FDR-014, FDR-015, ADR-C4.
**Depends on**: RC.2 (domain fields), RC.3 (service wiring), RC.4 (persistence fix).

**Deliverables**:
- `backend/src/reconciliation/infrastructure/api/schemas.py`:
  - `GuiaContributionResponse.reception_ceiling_applied: bool = Field(default=False, description="True when the guía's reception date was clamped to the Protocolo authoritative ceiling date.")`
  - `GuiaContributionResponse.delivery_after_protocolo: bool = Field(default=False, description="True when fecha_entrega (SUNAT floor) exceeds the Protocolo ceiling — crossed-bounds anomaly.")`
  - `ReconciliationRowResponse.has_reception_ceiling: bool = Field(default=False, description="True when at least one contributing guía had its reception date clamped to the Protocolo ceiling.")`
- `backend/src/reconciliation/infrastructure/api/routes.py` — update `_row_to_response` to map
  `g.reception_ceiling_applied`, `g.delivery_after_protocolo`, and `row.has_reception_ceiling`.

**Tests** (update `backend/tests/unit/infrastructure/test_api_routes.py`):
- Contribution with `reception_ceiling_applied=True` → DTO `reception_ceiling_applied=True`
- Contribution with `delivery_after_protocolo=True` → DTO `delivery_after_protocolo=True`
- Row with `has_reception_ceiling=True` → DTO `has_reception_ceiling=True`
- All-default case: all new fields `False`
- Old response without new keys → `model_validate` succeeds with defaults (backward compat)

**Commit message**: `feat(api): surface reception_ceiling_applied, delivery_after_protocolo, has_reception_ceiling on API DTOs (FDR-014, FDR-015)`

---

## Invariants each task MUST NOT break

| Invariant | Enforced by |
|-----------|-------------|
| Domain purity — no SDK/IO in `domain/` | RC.1: stdlib datetime only |
| `fecha` NOT in `_GroupKey` | RC.3: ceiling adjusts guía fecha only; `_GroupKey` untouched |
| MATCH EXACT(0) — no qty change | RC.3: reconciliation qty comparison block is untouched |
| R9 divergence NEVER masked | RC.3: `check_fecha_divergence` called BEFORE `apply_reception_ceiling` |
| Crossed-bounds: no clamp in either direction | RC.1 + RC.3: Rule 2 returns original date + `crossed_bounds=True` |
| `requires_review` only OR-set | RC.3: existing block extended additively |
| Graceful degrade (ceiling absent) | RC.1: Rule 1 passthrough when `protocolo_ceiling=None` |
| Backward compatibility | RC.2: all new fields have `= False` / `= None` defaults |
| `fecha_entrega` as single source of truth | RC.4: rebuild `delivery_dates` from guías on ALL reconcile paths |
