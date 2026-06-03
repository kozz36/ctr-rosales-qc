# Tasks — r9b-reception-date-delivery-floor

**Change**: `r9b-reception-date-delivery-floor` · **Phase**: tasks (archived) · **Store**: hybrid · **Date**: 2026-06-03
**Branch**: `feat/rev2-identity-domain` (continuing)
**Strict TDD**: active
**Gate**: Judgment-Day APPROVED after 2 rounds. 892 backend unit tests.
**Status**: Implemented & merged to main via PR #5.

All tasks are marked `[x]` (implemented).

---

## Slice RF-A — Pure domain floor function

### [x] RF.1 — `domain/date_floor.py`: `apply_delivery_floor` pure function

**Spec refs**: FDR-012, ADR-F1, ADR-F2.
**Depends on**: existing `domain/date_inference.py` (sibling).

**Deliverables**:
- New file `backend/src/reconciliation/domain/date_floor.py`
- `apply_delivery_floor(reception: date | None, fecha_entrega: date | None) -> tuple[date | None, bool]`
- Four branches (Rules 1–4); stdlib `datetime.date` only; no I/O, no Pydantic, no SDK.

**Tests** (new `backend/tests/unit/domain/test_date_floor.py`):
- Rule 1: `fecha_entrega=None` → passthrough regardless of `reception`
- Rule 2: `reception=None`, `fecha_entrega=date(2026,5,20)` → `(date(2026,5,20), True)`
- Rule 3: `reception=date(2025,5,28)`, `fecha_entrega=date(2026,5,20)` → `(date(2026,5,20), True)`
- Rule 4: `reception=date(2026,5,28)`, `fecha_entrega=date(2026,5,20)` → `(date(2026,5,28), False)`
- Rule 4 boundary: `reception == fecha_entrega` → unchanged, `floor_applied=False`
- `DivergenceResult`-equivalent: return type is a plain tuple (simpler than a dataclass for this use case)
- No mocks required (pure function, FDR-S20 precondition)

**Commit message**: `feat(domain): add apply_delivery_floor pure physical lower-bound function (FDR-012)`

---

## Slice RF-B — Domain model extensions

### [x] RF.2 — `GuiaDeRemision` + `GuiaContribution` + `ReconciliationRow`: `delivery_floor_applied`

**Spec refs**: FDR-012, FDR-013, ADR-F4.
**Depends on**: RF.1 (`date_floor.py` stable).

**Deliverables** (`backend/src/reconciliation/domain/models.py`):
- `GuiaDeRemision.delivery_floor_applied: bool = False` (additive)
- `GuiaContribution.delivery_floor_applied: bool = False` (additive)
- `ReconciliationRow.has_delivery_floor: bool` (computed: `any(g.delivery_floor_applied for g in self.guias)`)

**Tests** (update `backend/tests/unit/domain/test_models.py`):
- Old serialised dict without `delivery_floor_applied` → `model_validate` succeeds; default `False`
- `GuiaContribution.delivery_floor_applied=True` stored and retrieved correctly
- `ReconciliationRow.has_delivery_floor`: all False → False; one True → True; mixed → True

**Commit message**: `feat(domain): add delivery_floor_applied side-channel field to GuiaDeRemision, GuiaContribution, ReconciliationRow (ADR-F4)`

---

## Slice RF-C — Pipeline wiring

### [x] RF.3 — `_stage_normalize_dates`: floor wiring + null-day/month gap

**Spec refs**: FDR-012, FDR-013, ADR-F3.
**Depends on**: RF.1 (floor function), RF.2 (model fields).

**Deliverables** (`backend/src/reconciliation/application/pipeline.py`):
- Import `apply_delivery_floor` from `domain.date_floor` inside the method (lazy or module-level — domain import is safe).
- After `infer_reception_year`: call `apply_delivery_floor(reception, sunat_fetch_map.get(guia_id).fecha_entrega if guia_id in sunat_fetch_map else None)`.
- Pre-inference null-day/month gap (ADR-F3): when `day is None AND month is None AND fecha_entrega is not None`, set `fecha = fecha_entrega`, `delivery_floor_applied = True`, `requires_review = True` (skip `infer_reception_year` call for this guía).
- When `floor_applied=True`: set `guia.delivery_floor_applied = True`, OR-set `guia.requires_review = True`.

**Tests** (update `backend/tests/unit/application/test_pipeline.py` or new targeted file):
- SUNAT disabled (no `sunat_fetch_map` entry): `delivery_floor_applied=False`; `fecha` unchanged (FDR-S20)
- Reception after entrega: no floor applied (FDR-S22)
- Reception before entrega: floor applied, `requires_review=True` (FDR-S21)
- Null vision date + `fecha_entrega` present: floored to `fecha_entrega`, `delivery_floor_applied=True` (FDR-S23)
- Null vision date + SUNAT absent: `fecha` remains None (FDR-006 / null-fecha fallback)

**Commit message**: `feat(pipeline): wire delivery floor in _stage_normalize_dates; handle null-day/month + fecha_entrega gap (ADR-F3)`

---

## Slice RF-D — API surface

### [x] RF.4 — API schema + routes: `delivery_floor_applied` additive DTO fields

**Spec refs**: FDR-012, ADR-F4.
**Depends on**: RF.2 (domain fields), RF.3 (pipeline wiring).

**Deliverables**:
- `backend/src/reconciliation/infrastructure/api/schemas.py`:
  - `GuiaContributionResponse.delivery_floor_applied: bool = Field(default=False, description="True when the guía's reception date was floored to the SUNAT delivery date (physical lower-bound enforcement).")`
  - `ReconciliationRowResponse.has_delivery_floor: bool = Field(default=False, description="True when at least one contributing guía had its reception date floored to the delivery date.")`
- `backend/src/reconciliation/infrastructure/api/routes.py` — update `_row_to_response` to map `g.delivery_floor_applied` and `row.has_delivery_floor`.

**Tests** (update `backend/tests/unit/infrastructure/test_api_routes.py`):
- Contribution with `delivery_floor_applied=True` → DTO `delivery_floor_applied=True`
- Row with `has_delivery_floor=True` → DTO `has_delivery_floor=True`
- All-default case: `delivery_floor_applied=False`, `has_delivery_floor=False`
- Old response without new keys → `model_validate` succeeds with defaults (backward compat)

**Commit message**: `feat(api): surface delivery_floor_applied fields on GuiaContributionResponse and ReconciliationRowResponse (ADR-F4)`

---

## Invariants each task MUST NOT break

| Invariant | Enforced by |
|-----------|-------------|
| Domain purity — no SDK/IO in `domain/` | RF.1: stdlib datetime only |
| `fecha` NOT in `_GroupKey` | RF.3: floor adjusts guía fecha only; `_GroupKey` untouched |
| MATCH EXACT(0) — no qty change | RF.3: reconciliation qty comparison block is untouched |
| Null baseline / null guía fecha — FDR-006 preserved | RF.3: null-date handling for SUNAT-absent case unchanged |
| `requires_review` only OR-set | RF.3: existing block extended additively |
| Graceful degrade (SUNAT off) | RF.3: Rule 1 passthrough when `fecha_entrega=None` |
| Backward compatibility | RF.2: all new fields have `= False` defaults |
