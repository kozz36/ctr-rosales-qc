# Tasks — r9-fecha-divergence-review

**Change**: `r9-fecha-divergence-review` · **Phase**: tasks · **Store**: hybrid · **Date**: 2026-06-02
**Branch**: `feat/rev2-identity-domain` (continuing; no new branch)
**Strict TDD**: active — `cd backend && uv run pytest` / `cd frontend && npm run test`
**Dependency**: Slice 1 (`r8-material-matching`, all R8.x) MUST be landed before this apply.

Delta over `r8-material-matching` (Slice 1). Adds:
(A) declared-side handwritten-date vision read via existing `VisionLLMPort`,
(B) pure-domain day-month divergence check as a post-grouping side-channel,
(C) divergence flag threaded line → contribution → row → DTO → Vue.
Group key, MATCH/MISMATCH logic, and quantity math stay untouched (FDR-011, ADR-3).

All tasks are ordered **test-first** per strict TDD mode. Each task produces a
green-test commit. Tests and code ship in the SAME commit (work-unit-commits skill).

---

## Review Workload Forecast

| Metric | Estimate |
|--------|----------|
| New files | 5 (domain: 1, config: 0, tests: 3 backend + 1 frontend component) |
| Modified files | 11 (models.py×2 paths, reconciliation.py, pipeline.py, config.py, digital_text_extractor.py, schemas.py, routes.py, types.ts, GuiaDrillDown.vue, ReconciliationRow.vue) |
| Estimated changed lines | ~480–560 |
| 400-line budget risk | **Medium-High** — exceeds budget but commits land on an unpushed feature branch; PRs are user-gated. Forecast is informational. |
| Chained PRs recommended | Not blocking — user will gate PR submission. Each task slice is independently committable per work-unit-commits skill. |
| Decision needed before apply | No — PRs deferred; proceed with work-unit commits on `feat/rev2-identity-domain`. |

---

## Task Dependency Graph

```
R9.1 (Registro.protocolo_page — page-number seam fix)   ← PREREQUISITE BLOCKER
  └─▶ R9.2 (domain models: Registro handwritten-date fields + GuiaContribution.fecha/divergence + ReconciliationRow.has_fecha_divergence)
        └─▶ R9.3 (DateDivergenceChecker pure domain service)
              └─▶ R9.4 (ReconciliationService divergence wiring — reconciliation.py)
                    ├─▶ R9.5 (pipeline: protocolo_crop config + _stage_extract_declared_date)
                    │         └─▶ R9.6 (API schema + routes: additive DTO fields)
                    │                   └─▶ R9.7 (frontend: types + FechaDivergenceBadge + GuiaDrillDown + ReconciliationRow)
                    │                               └─▶ R9.8 (real-data e2e gate)
                    └─▶ (R9.5 can begin in parallel once R9.4 is stable — see note below)

Parallel opportunities:
  R9.5 and R9.4 share no code paths (pipeline vs reconciler); R9.5 begins
  immediately after R9.2 models are stable (it only writes Registro fields
  that R9.4 reads). In single-executor apply: run sequentially R9.1→…→R9.8.
  R9.6 and R9.7 are sequential (frontend depends on stable DTO types).
```

---

## Slice R9-A — Prerequisite: page-number seam

> Sequential. Must land before anything else reads `protocolo_page`.

### [x] R9.1 — `Registro.protocolo_page` propagation fix (digital_text_extractor + models)

**Spec refs**: FDR-001, FDR-008, ADR-1, ADR-2.
**Depends on**: existing `Registro` model (models.py:115) and
`extract_registro_from_proto_page` (digital_text_extractor.py:338).
**Parallel with**: nothing — foundation for the declared-side read.

**The bug being fixed.** `extract_registro_from_proto_page` receives `source_page: int` (line 338)
but the constructed `Registro(numero=..., fecha_declarada=..., declared_lines=...)` (lines 359–363)
discards it. `Registro` has no page field. The pipeline's declared-date sub-stage (R9.5) needs
the Protocolo source page to know which page to render and crop; without it the sub-stage cannot
target the right page.

**Deliverables**:

`backend/src/reconciliation/domain/models.py` — add one backward-compatible field to `Registro`:
```python
class Registro(BaseModel):
    numero: str
    fecha_declarada: date | None
    declared_lines: list[MaterialLine]
    # R9.1: source page of the Protocolo de Recepción (0-based index in PDF).
    # None when Registro originates from a detail page, not a Protocolo.
    protocolo_page: int | None = None
```

`backend/src/reconciliation/adapters/pdf/digital_text_extractor.py` — in
`extract_registro_from_proto_page` (line 359), pass `protocolo_page=source_page`:
```python
return Registro(
    numero=numero,
    fecha_declarada=fecha,
    declared_lines=lines,
    protocolo_page=source_page,   # ← was silently discarded; now propagated
)
```
`extract_registro_from_detail_page` (line 332) does NOT set `protocolo_page` — it stays `None`
(detail pages have no Protocolo "Fecha:" field to read).

**Tests** (`backend/tests/unit/adapters/pdf/test_digital_text_extractor.py` — update existing):
- `extract_registro_from_proto_page` with `source_page=7` → `registro.protocolo_page == 7`.
- `extract_registro_from_proto_page` with `source_page=0` → `registro.protocolo_page == 0`
  (0 is valid; must not be treated as falsy).
- `extract_registro_from_detail_page` → `registro.protocolo_page is None` (detail page never
  sets this field).
- Old serialised `Registro` dict without `protocolo_page` key →
  `model_validate({...})` succeeds with `protocolo_page=None` (backward-compat roundtrip).

**Commit message**: `fix(adapter): propagate protocolo_page from Protocolo parser to Registro (ADR-2)`
**Completable in**: one session (tiny — ~8 lines production, ~15 lines tests).

---

## Slice R9-B — Pure Domain: models + divergence checker

> R9.2 and R9.3 are sequential. R9.3 depends on the new model fields.

### [x] R9.2 — Domain model extensions: handwritten declared date + per-guía divergence fields

**Spec refs**: FDR-001, FDR-002, FDR-004, FDR-005, FDR-007, FDR-008, ADR-2, ADR-4.
**Depends on**: R9.1 (`Registro.protocolo_page` stable).
**Parallel with**: nothing at this step.

**Deliverables** (`backend/src/reconciliation/domain/models.py`):

1. Extend `Registro` (already has `protocolo_page` from R9.1) with the handwritten-date fields:
```python
class Registro(BaseModel):
    ...
    protocolo_page: int | None = None                   # R9.1
    # R9.2: handwritten declared date from Protocolo vision read (authoritative per #2709)
    fecha_declarada_handwritten: date | None = None
    fecha_declarada_confidence: float | None = None
    fecha_declarada_year_inferred: bool = False

    @computed_field
    @property
    def fecha_authoritative(self) -> date | None:
        """Handwritten Protocolo date when available; falls back to electronic fecha_declarada."""
        return self.fecha_declarada_handwritten or self.fecha_declarada
```
All new fields have backward-compatible defaults. Keep `fecha_declarada` (electronic) for
provenance/audit and as the rollback fallback in `fecha_authoritative`.

2. Extend `GuiaContribution` (models.py:56) with per-guía divergence fields:
```python
class GuiaContribution(BaseModel):
    ...
    # R9.2: divergence side-channel (ADR-4 — mirrors year_inferred pattern, rev-3 D5)
    fecha: date | None = None               # guía's handwritten reception date for compare/display
    fecha_divergence: bool = False          # True when guia fecha diverges from declared (day-month)
    divergence_reason: Literal["fecha_divergence"] | None = None
```

3. Add `has_fecha_divergence` computed field to `ReconciliationRow` (models.py:139), mirroring
   the `any_year_inferred` precedent from rev-3:
```python
class ReconciliationRow(BaseModel):
    ...
    @computed_field
    @property
    def has_fecha_divergence(self) -> bool:
        """True when at least one contributing guía has a fecha divergence (group indicator)."""
        return any(g.fecha_divergence for g in self.guias)
```

**Domain purity constraint**: all new fields are stdlib `date | None`, `float | None`, `bool`,
`Literal[...]`. No I/O, no SDK, no adapter import introduced in `domain/models.py`.

**Tests** (`backend/tests/unit/domain/test_models.py` — update):
- `Registro` without new fields → `model_validate` with old dict succeeds; `fecha_authoritative`
  returns `fecha_declarada` (electronic fallback path).
- `Registro` with `fecha_declarada_handwritten` set and `fecha_declarada` also set →
  `fecha_authoritative` returns `fecha_declarada_handwritten` (handwritten wins).
- `Registro` with `fecha_declarada_handwritten=None` and `fecha_declarada` set →
  `fecha_authoritative` returns `fecha_declarada` (fallback).
- `Registro` with both None → `fecha_authoritative` is None.
- `GuiaContribution` without new fields → defaults to `fecha=None`, `fecha_divergence=False`,
  `divergence_reason=None`.
- `GuiaContribution` with `fecha_divergence=True`, `divergence_reason="fecha_divergence"` →
  fields stored correctly.
- `ReconciliationRow.has_fecha_divergence`: row with no guías → False.
- Row with all guías `fecha_divergence=False` → False.
- Row with one guía `fecha_divergence=True` → True.
- Row with mixed guías (some True, some False) → True.

**Commit message**: `feat(domain): add handwritten declared date + per-guía divergence fields to models (ADR-2/4)`
**Completable in**: one session (small — ~35 lines production, ~40 lines tests).

---

### [x] R9.3 — `DateDivergenceChecker` — pure domain service

**Spec refs**: FDR-003, FDR-004, FDR-005, FDR-006, FDR-010, ADR-3.
**Depends on**: R9.2 (model fields stable; `DivergenceResult` depends on no model field but
the checker function signature consumes `date | None` — no model import needed).
**Parallel with**: nothing at this step.

**Deliverables** (new file `backend/src/reconciliation/domain/date_divergence.py`):
```python
"""Pure domain fecha-divergence predicate (r9 / ADR-3).

Sibling to date_inference.py. No I/O, no SDK, no adapter imports.
Predicate: compare day + month only (tolerance 0). Year comparison is
explicitly excluded — year-inference asymmetry between declared and guía
sides (#2753) causes spurious year divergence; day-month is the trusted signal.
"""
from __future__ import annotations
from datetime import date
from typing import Literal
from dataclasses import dataclass

DivergenceReason = Literal["fecha_divergence"]

@dataclass(frozen=True)
class DivergenceResult:
    diverges: bool
    reason: DivergenceReason | None          # "fecha_divergence" when diverges else None
    declared_fecha: date | None
    guia_fecha: date | None

def check_fecha_divergence(
    declared_fecha: date | None,
    guia_fecha: date | None,
) -> DivergenceResult:
    """Return divergence result for a single (declared, guia) date pair.

    Null safety (FDR-005, FDR-006): if EITHER side is None → cannot validate →
    NOT divergent. A null baseline must never paint all guías red.
    """
    if declared_fecha is None or guia_fecha is None:
        return DivergenceResult(False, None, declared_fecha, guia_fecha)
    diverges = (declared_fecha.month, declared_fecha.day) != (guia_fecha.month, guia_fecha.day)
    return DivergenceResult(
        diverges=diverges,
        reason="fecha_divergence" if diverges else None,
        declared_fecha=declared_fecha,
        guia_fecha=guia_fecha,
    )
```
Module imports: ONLY stdlib `datetime`, `typing`, `dataclasses`. No Pydantic, no port,
no I/O. This is a pure function — no class instantiation required at call site.

**Tests** (new file `backend/tests/unit/domain/test_date_divergence.py`):
- Same day+month, same year → `diverges=False`, `reason=None` (FDR-S06).
- Same day+month, **different year** → `diverges=False`, `reason=None` (FDR-S04 — year-only
  divergence is NOT a divergence; this is the critical test for ADR-3 rationale).
- Different day, same month → `diverges=True`, `reason="fecha_divergence"` (FDR-S05).
- Same day, different month → `diverges=True`, `reason="fecha_divergence"`.
- Different day AND different month → `diverges=True`.
- `declared_fecha=None`, `guia_fecha` set → `diverges=False` (null-safe, FDR-S10).
- `declared_fecha` set, `guia_fecha=None` → `diverges=False` (null-safe, FDR-S11).
- Both None → `diverges=False`.
- `DivergenceResult` is frozen: attempt to mutate any field raises `FrozenInstanceError`.
- No HTTP call, no file read, no mock required for any of the above (FDR-S18 compliance
  — test can confirm by asserting the test runs with zero patches/mocks).

**Commit message**: `feat(domain): add DateDivergenceChecker pure day-month divergence predicate (ADR-3)`
**Completable in**: one session (small — ~35 lines production, ~35 lines tests).

---

## Slice R9-C — Reconciler wiring

> Sequential. `reconciliation.py` is the core change that wires the domain pieces together.

### [x] R9.4 — `ReconciliationService` divergence wiring

**Spec refs**: FDR-003, FDR-004, FDR-005, FDR-006, FDR-009, FDR-011, ADR-4.
**Depends on**: R9.2 (model fields), R9.3 (`check_fecha_divergence`).

**Deliverables** (`backend/src/reconciliation/domain/reconciliation.py`):

1. Import `check_fecha_divergence` from `domain/date_divergence.py` (pure domain import, safe).

2. In the `reconcile()` contribution-build block (currently lines 132–145), copy `g.fecha`
   onto each `GuiaContribution`:
```python
contributions: list[GuiaContribution] = [
    GuiaContribution(
        guia_id=g.guia_id,
        source_pages=g.source_pages,
        cantidad=total_qty,
        unidad=key.unidad,
        confidence=g.identity_confidence,
        identity_source=g.identity_source,
        year_inferred=g.year_inferred,
        fecha=g.fecha,              # R9.4: copy guía fecha for display and divergence
    )
    for g, total_qty in contrib_map.values()
]
```

3. In the `requires_review` block (currently lines 154–166), AFTER the contributions list is
   built, run the divergence check per contribution and mutate the contributions list with
   divergence flags:
```python
# R9.4 (FDR-003/004): per-guía fecha divergence check (side-channel; never touches status/delta).
row_declared_authoritative = None
for reg in declared:
    if reg.numero == key.registro:
        row_declared_authoritative = reg.fecha_authoritative
        break

fecha_divergence_flags: dict[str, tuple[bool, str | None]] = {}
for contrib in contributions:
    result = check_fecha_divergence(row_declared_authoritative, contrib.fecha)
    fecha_divergence_flags[contrib.guia_id] = (result.diverges, result.reason)
    if result.diverges:
        row_requires_review = True

contributions = [
    c.model_copy(update={
        "fecha_divergence": fecha_divergence_flags[c.guia_id][0],
        "divergence_reason": fecha_divergence_flags[c.guia_id][1],
    })
    for c in contributions
]
```
`row_declared_authoritative` uses `reg.fecha_authoritative` (ADR-2 computed field) —
the single read-point that honours the handwritten-first, electronic-fallback priority.

4. Change the display-fecha line (currently `reconciliation.py:92`,
   `declared_fecha.setdefault(key, registro.fecha_declarada)`) to use
   `registro.fecha_authoritative`:
```python
declared_fecha.setdefault(key, registro.fecha_authoritative)
```
This is the one-line display-fecha change described in ADR-2. Grouping key is untouched.

**Critical invariants** (assert these hold after the change):
- `_GroupKey` is NOT modified (neither fecha nor divergence enters the key).
- `status`, `delta`, `summed_qty` are NOT changed by the divergence block.
- The divergence block runs AFTER contributions are built (it reads `g.fecha`).
- `row_requires_review |= True` only; it is never set to False by the divergence block.

**Tests** (`backend/tests/unit/domain/test_reconciliation.py` — update):
- MATCH group where declared and guía date are same day-month → contributions have
  `fecha_divergence=False`; `row.has_fecha_divergence=False`; status MATCH (FDR-S08/S09).
- MATCH group where one guía has diverging day-month → `fecha_divergence=True` on that guía's
  contribution; `row.has_fecha_divergence=True`; status is still MATCH (FDR-S09 — material
  status UNCHANGED by divergence).
- Group where declared date resolves to `fecha_authoritative=None` (null baseline) →
  `fecha_divergence=False` for ALL contributions; no false red (FDR-S10).
- Group where guía `fecha=None` → `fecha_divergence=False` for that contribution (FDR-S11).
- Year-only divergence (same day-month, different inferred year) → `fecha_divergence=False`
  (FDR-S04 — critical invariant test).
- `row.has_fecha_divergence` computed from contribution list is True when any contribution
  has `fecha_divergence=True`, False when none do.
- MISMATCH group with diverging guía → status is still MISMATCH; divergence is additive
  side-channel only (FDR-S09 generalised).
- `requires_review=True` when any contribution has `fecha_divergence=True`.
- `requires_review` is NOT set to False by the divergence check (only OR-additive).
- Display fecha now sourced from `fecha_authoritative` (not `fecha_declarada`) for
  declared-bearing groups (ADR-2, one-line change at reconciliation.py:92).

**Commit message**: `feat(domain): wire DateDivergenceChecker into ReconciliationService; display fecha = fecha_authoritative (ADR-4)`
**Completable in**: one session (medium — ~40 lines production, ~55 lines tests).

---

## Slice R9-D — Pipeline: declared vision read

> Sequential after R9.2 (needs Registro model fields). Can start once R9.2 is done —
> does NOT need R9.3 or R9.4. In single-executor apply, run after R9.4 for simplicity.

### [x] R9.5 — `protocolo_crop` config + `_stage_extract_declared_date` pipeline sub-stage

**Spec refs**: FDR-001, FDR-002, FDR-007, ADR-1, ADR-6, ADR-7.
**Depends on**: R9.1 (`Registro.protocolo_page`), R9.2 (Registro handwritten-date fields).
**Parallel with**: R9.4 (no shared code path — pipeline.py vs reconciliation.py).

**Deliverables**:

`backend/src/reconciliation/application/config.py` — add `protocolo_crop` to `VisionConfig`:
```python
class VisionConfig(BaseSettings):
    ...
    stamp_crop: StampCropConfig = Field(default_factory=StampCropConfig)       # guía stamp (existing)
    protocolo_crop: StampCropConfig = Field(default_factory=StampCropConfig)   # Protocolo "Fecha:" field (NEW, ADR-6)
    fallback_dpi: int = Field(default=300, gt=0)
```
`StampCropConfig` defaults to all-zero box → `is_enabled` returns False → full-page
≥300dpi fallback path in `_prepare_vision_image` is triggered automatically.
This is the safe conservative default: declared read works before the crop box is tuned
for the Protocolo layout.

`backend/src/reconciliation/application/pipeline.py` — add new private sub-stage
`_stage_extract_declared_date` called from the main pipeline flow AFTER
`_stage_extract_declared` and BEFORE `_stage_reconcile`:

```python
def _stage_extract_declared_date(
    self,
    registros: list[Registro],
    decode_map: dict[int, bytes],   # render-cache: page_index → rendered bytes
) -> list[Registro]:
    """Read the handwritten 'Fecha:' from each Registro's Protocolo page via VisionLLMPort.

    ADR-1: reuses VisionLLMPort.read_handwritten_date — no new port.
    ADR-6: uses vision.protocolo_crop (full-page fallback when crop disabled).
    ADR-7: confidence gate — low confidence (< threshold) flags the registro,
           never asserts a baseline; divergence check auto-skips on None declared.
    Cost cap: declared reads counted against the SAME vision.max_vision_calls cap.
    """
    updated: list[Registro] = []
    for reg in registros:
        if reg.protocolo_page is None:
            # Detail-page-only registro: no Protocolo to read; fecha_authoritative
            # falls back to electronic fecha_declarada (rollback path, ADR-2).
            updated.append(reg)
            continue

        page_bytes = decode_map.get(reg.protocolo_page)
        if page_bytes is None:
            updated.append(reg)
            continue

        vision_image = _prepare_vision_image_proto(page_bytes, self._config)
        result = self._vision.read_handwritten_date(vision_image)  # VisionLLMPort

        threshold = self._config.confidence.threshold  # 0.85
        if result is None or result.confidence < threshold:
            # ADR-7: fail-closed. Low confidence → flag registro, skip divergence.
            updated.append(reg.model_copy(update={
                "fecha_declarada_handwritten": None,
                "fecha_declarada_confidence": getattr(result, "confidence", None),
            }))
            continue

        # Bounded year inference — declared side has lower=None (no SUNAT bound, ADR-1).
        reconstructed, year_inferred = infer_reception_year(
            result.day, result.month, result.year,
            upper=date.today(), lower=None,
        )
        updated.append(reg.model_copy(update={
            "fecha_declarada_handwritten": reconstructed,
            "fecha_declarada_confidence": result.confidence,
            "fecha_declarada_year_inferred": year_inferred,
        }))

    return updated
```

Add a companion helper `_prepare_vision_image_proto(image: bytes, config: AppConfig) -> bytes`
(sibling to `_prepare_vision_image`) that uses `config.vision.protocolo_crop` instead of
`config.vision.stamp_crop`. The function body is structurally identical to `_prepare_vision_image`
but selects the Protocolo-specific crop box. Factor out shared crop logic if both helpers
grow beyond ~10 lines to avoid duplication; for now a thin wrapper is acceptable.

Wire the sub-stage into the pipeline execution flow, between `_stage_extract_declared` and
the reconciliation call. Pass the existing `decode_map` (render-cache, already populated by
the guía vision stage). Account for the declared reads in the `vision.max_vision_calls` cap
(increment or check the counter that guards the guía vision stage — confirm the existing cap
check is in one place so it can include declared reads).

**Tests** (`backend/tests/unit/application/test_pipeline.py` — update +
new file `backend/tests/unit/application/test_stage_extract_declared_date.py`):
- `_stage_extract_declared_date` with `protocolo_page=None` → registro returned unchanged,
  no vision call made.
- `protocolo_page` set but page bytes absent from `decode_map` → registro returned unchanged.
- Vision returns `confidence=0.72` (below 0.85) → `fecha_declarada_handwritten=None`,
  `fecha_declarada_confidence=0.72`; vision called exactly once (ADR-7, FDR-S12).
- Vision returns `confidence=0.92`, day=28, month=5, year=26 → `fecha_declarada_handwritten`
  is `date(2026, 5, 28)`; `fecha_declarada_year_inferred` reflects `infer_reception_year`
  output (FDR-S01, FDR-S03).
- Two registros, one with `protocolo_page` set and one without → vision called exactly once
  (only for the one with a page); both returned.
- `config.vision.protocolo_crop` all-zero (default) → full-page fallback DPI path used
  (ADR-6); vision image is not an empty bytes object.
- `_prepare_vision_image_proto` vs `_prepare_vision_image`: same crop-disabled path produces
  a non-empty image (structural parity test).
- Pipeline integration: after `_stage_extract_declared_date`, `registered[0].fecha_authoritative`
  returns the handwritten date when confidence ≥ threshold, and falls back to `fecha_declarada`
  when handwritten is None.

**Commit message**: `feat(pipeline): add _stage_extract_declared_date + protocolo_crop config (ADR-1/6/7)`
**Completable in**: one session (medium — ~70 lines production, ~55 lines tests).

---

## Slice R9-E — API surface

> Sequential after R9.4 (domain fields must exist in DTO source).

### [x] R9.6 — API schema + routes: additive DTO fields

**Spec refs**: FDR-008, ADR-5.
**Depends on**: R9.4 (domain + reconciler fields stable).
**Parallel with**: nothing — R9.7 depends on R9.6.

**Deliverables**:

`backend/src/reconciliation/infrastructure/api/schemas.py`:

1. Add to `GuiaContributionResponse` (currently ends at `year_inferred`, line 45):
```python
# R9.6 (FDR-008): fecha divergence fields — additive, backward-compatible defaults.
fecha: date | None = Field(default=None, description="Guía handwritten reception date.")
fecha_divergence: bool = Field(
    default=False,
    description="True when the guía's handwritten date diverges from the registro's declared date (day-month mismatch).",
)
divergence_reason: Literal["fecha_divergence"] | None = Field(
    default=None,
    description="Divergence classification code.",
)
```

2. Add to `ReconciliationRowResponse` (currently ends at `any_year_inferred` or similar):
```python
# R9.6 (FDR-008): group divergence indicator — derived from guías (mirrors has_fecha_divergence on domain model).
has_fecha_divergence: bool = Field(
    default=False,
    description="True when at least one contributing guía has a fecha divergence (group-level indicator).",
)
```

`backend/src/reconciliation/infrastructure/api/routes.py` — update `_row_to_response`
(around line 83) to map new domain fields to DTO:
- Per contribution: map `g.fecha`, `g.fecha_divergence`, `g.divergence_reason`.
- Per row: map `row.has_fecha_divergence`.

**Tests** (`backend/tests/unit/infrastructure/test_api_routes.py` — update):
- `GET /runs/{id}/rows` response: each contribution DTO has `fecha`, `fecha_divergence`,
  `divergence_reason` fields (defaults when not diverging).
- Contribution with `fecha_divergence=True` → DTO `fecha_divergence=True`,
  `divergence_reason="fecha_divergence"` (FDR-S14).
- Row with `has_fecha_divergence=True` → DTO `has_fecha_divergence=True`.
- Contribution with all-default values → DTO round-trip produces
  `fecha=None`, `fecha_divergence=False`, `divergence_reason=None`.
- Old response without new keys → `model_validate` succeeds with defaults
  (backward-compat — no existing client breaks).

**Commit message**: `feat(api): surface fecha_divergence fields on GuiaContributionResponse and ReconciliationRowResponse (ADR-5)`
**Completable in**: one session (small — ~20 lines production, ~25 lines tests).

---

## Slice R9-F — Frontend

> Sequential after R9.6 (types must match stable DTO).

### [x] R9.7 — Frontend: types + `FechaDivergenceBadge` + `GuiaDrillDown` + `ReconciliationRow`

**Spec refs**: FDR-009, ADR-8.
**Depends on**: R9.6 (DTO shape finalised).
**Parallel with**: nothing — one commit.

**Deliverables**:

`frontend/src/api/types.ts`:
- Add to `GuiaContributionResponse` interface (after `year_inferred`, line 67):
```typescript
/** Guía handwritten reception date (ISO-8601 string or null). */
fecha: string | null
/** True when this guía's handwritten date diverges from the registro's declared date. */
fecha_divergence: boolean
/** Divergence reason code, or null when not divergent. */
divergence_reason: 'fecha_divergence' | null
```
- Add to `ReconciliationRowResponse` interface (after `any_year_inferred`, line 107):
```typescript
/** True when at least one contributing guía has a fecha divergence (group-level roll-up). */
has_fecha_divergence: boolean
```

`frontend/src/features/review/FechaDivergenceBadge.vue` (NEW — modeled on `YearInferredBadge.vue`):
- Template: `<span>` with `role="img"`, `⚠` icon (`aria-hidden="true"`), label "Fecha no coincide".
- Prop: `compact?: boolean` (icon-only in compact mode; mirrors `YearInferredBadge`).
- Style: RED tokens — use `--status-mismatch-*` CSS custom properties (already defined for
  `recon-row--mismatch`); border and background tinted red. NOT yellow (divergence ≠ inference).
- A11y: icon + label pattern (WCAG 1.4.1 — state not conveyed by color alone).
- Tooltip: "Fecha no coincide: la fecha de recepción de esta guía difiere de la fecha declarada
  en el Protocolo. Verifique si esta guía está archivada en el registro correcto."

`frontend/src/features/review/GuiaDrillDown.vue`:
- Import `FechaDivergenceBadge`.
- When `guia.fecha_divergence` is true on a contribution row:
  - Apply class `guia-drill-down__row--divergent` (add CSS: red left-border +
    `background-color: var(--status-mismatch-bg, rgba(220,38,38,0.06))`).
  - Render `<FechaDivergenceBadge />` in the Fecha cell (the `<td>` that currently shows
    `year_inferred` badge or date text), alongside the existing `SourcePages` chip
    (`guia.source_pages`) so the page reference stays visible (FDR-S15, FDR-S07).
  - The existing per-guía Reassign button remains the resolution path — no new control (ADR-8).

`frontend/src/features/review/ReconciliationRow.vue`:
- Import `FechaDivergenceBadge`.
- When `row.has_fecha_divergence` is true, render `<FechaDivergenceBadge compact />` in
  Col 9 (the review/flags cell, beside the existing `requires_review` ⚠ and
  `YearInferredBadge`) (FDR-S16, ADR-8).
- When `row.has_fecha_divergence` is false, render nothing for this badge (FDR-S17).

**Tests** (`frontend` — `cd frontend && npm run test`):
- `FechaDivergenceBadge.vue`:
  - Default (non-compact) renders icon + "Fecha no coincide" label.
  - Compact mode renders icon, label hidden.
  - Has `role="img"` on the root span (a11y).
  - Has a non-empty `title` (tooltip) attribute.
- `GuiaDrillDown.vue` (update existing test):
  - Contribution with `fecha_divergence=true` → `.guia-drill-down__row--divergent` class
    present on the row; `FechaDivergenceBadge` rendered (FDR-S15).
  - Contribution with `fecha_divergence=false` → no divergent class; no `FechaDivergenceBadge`.
  - Two contributions (one diverging, one not) → only the diverging row has the class.
- `ReconciliationRow.vue` (update existing test):
  - `row.has_fecha_divergence=true` → `FechaDivergenceBadge` rendered in Col 9 (FDR-S16).
  - `row.has_fecha_divergence=false` → no `FechaDivergenceBadge` in that slot (FDR-S17).

**Commit message**: `feat(frontend): add FechaDivergenceBadge + red row in GuiaDrillDown + group badge in ReconciliationRow (ADR-8)`
**Completable in**: one session (medium — ~100 lines frontend production, ~50 lines tests).

---

## Slice R9-G — Real-Data Validation Gate

> Sequential. Start only after R9.5–R9.7 all complete. This is the trusted gate.

### [x] R9.8 — Real-data e2e gate: Registro 232 divergence assertion + full regression guard

**Spec refs**: FDR-S01, FDR-S03, FDR-S09, FDR-S19, ADR-7.
**Depends on**: R9.5–R9.7 all complete (full pipeline + API + frontend wired).

**Deliverables** (new file `backend/tests/integration/test_pipeline_r9_gate.py`):

1. **Pure-domain divergence assertion** (fast, no PDF):
   - Call `check_fecha_divergence(date(2026, 5, 28), date(2026, 5, 28))` → `diverges=False`.
   - Call `check_fecha_divergence(date(2026, 5, 28), date(2025, 5, 28))` → `diverges=False`
     (year-only — critical year-inference invariant, FDR-S04).
   - Call `check_fecha_divergence(date(2026, 5, 28), date(2026, 4, 15))` → `diverges=True`.
   - Call `check_fecha_divergence(None, date(2026, 5, 28))` → `diverges=False`.

2. **ReconciliationService unit-level simulation with divergence** (no PDF):
   - Build `Registro(numero="232", fecha_declarada=date(2026,5,28),
     fecha_declarada_handwritten=date(2026,5,28), fecha_declarada_confidence=0.92, ...)`.
   - Build two `GuiaDeRemision` objects: one with `fecha=date(2026,5,28)` (matching),
     one with `fecha=date(2026,4,15)` (diverging). Both contribute to the same material group.
   - Call `ReconciliationService().reconcile(...)`.
   - Assert: matching guía contribution has `fecha_divergence=False`.
   - Assert: diverging guía contribution has `fecha_divergence=True`,
     `divergence_reason="fecha_divergence"`.
   - Assert: `row.has_fecha_divergence=True` (at least one diverging).
   - Assert: `row.status` is MATCH or MISMATCH based on quantities ONLY — not changed by
     divergence (FDR-S19, FDR-S09).
   - Assert: `row.requires_review=True` (divergence OR-sets this).

3. **Pipeline configuration guard** (no PDF):
   - Instantiate `ReconciliationPipeline` (default config).
   - Assert `pipeline._config.vision.protocolo_crop` is a `StampCropConfig` instance.
   - Assert `protocolo_crop.is_enabled` is False by default (conservative full-page fallback).
   - Assert `pipeline._config.vision.stamp_crop` is unaffected (no regression to R7 guía crop).

4. **Full real-PDF e2e** (marks `pytest.mark.slow` or `pytest.mark.e2e`; MUST pass before
   change is declared complete):
   - Run full pipeline on the real PDF (`CTR-PLC01-FR001 Recepción de Materiales en Obra`).
   - Assert Registro 232 has `protocolo_page` set to a non-None integer (page-number
     propagation working end-to-end, R9.1).
   - Assert Registro 232 has `fecha_declarada_handwritten` set and `fecha_declarada_confidence ≥ 0.85`
     OR `fecha_declarada_handwritten=None` with `fecha_declarada_confidence` recorded
     (either outcome is valid; confirms the vision stage ran, ADR-7).
   - Assert all R8 gate assertions from `test_pipeline_r8_gate.py` still pass (full regression
     guard — MATCH status, `match_method="deterministic"`, `summed_qty` unchanged).
   - Assert no new rows have changed from MATCH to MISMATCH due to this change (date divergence
     is additive side-channel only; FDR-S09).
   - Assert that divergence flags, when set, are accompanied by `requires_review=True` on the
     relevant rows.

> **Note**: items 1–3 are fast regression guards. Item 4 is the trusted gate.
> The change MUST NOT be declared complete until item 4 passes on actual data
> (per `docs/HANDOFF.md` §4: "unit tests passed while the real pipeline was broken").

**Commit message**: `test(e2e): r9 real-data gate — Registro 232 divergence assertion + pipeline regression guard (FDR-S01/S19)`
**Completable in**: one session (integration — ~90 lines tests, no production changes).

---

## Task Dependency Summary

```
Slice R9-A (Prerequisite — page-number seam):
  R9.1 (protocolo_page propagation — digital_text_extractor + models)

Slice R9-B (Pure Domain):
  R9.1 ──▶ R9.2 (Registro handwritten fields + GuiaContribution fecha/divergence + ReconciliationRow.has_fecha_divergence)
  R9.2 ──▶ R9.3 (DateDivergenceChecker — new domain/date_divergence.py)

Slice R9-C (Reconciler):
  R9.2 + R9.3 ──▶ R9.4 (reconciliation.py divergence wiring + display fecha_authoritative)

Slice R9-D (Pipeline):
  R9.1 + R9.2 ──▶ R9.5 (config.py protocolo_crop + pipeline _stage_extract_declared_date)
  [R9.5 parallel with R9.4 in theory; in single-executor apply run after R9.4]

Slice R9-E (API):
  R9.4 ──▶ R9.6 (schemas.py + routes.py additive DTO fields)

Slice R9-F (Frontend):
  R9.6 ──▶ R9.7 (types.ts + FechaDivergenceBadge.vue + GuiaDrillDown.vue + ReconciliationRow.vue)

Slice R9-G (Real-Data Gate):
  R9.5 + R9.6 + R9.7 ──▶ R9.8 (e2e gate — MUST pass before change declared done)
```

**Total tasks**: 8 · **Sequential bottlenecks**: R9.1→2→3→4→6→7→8, R9.1→2→5→8 · **Parallel opportunities**: R9.4 ∥ R9.5 (pipeline vs reconciler; same model inputs)

---

## Invariants each task MUST NOT break

| Invariant | Enforced by |
|-----------|-------------|
| Domain purity — no SDK/IO in `domain/` | R9.2, R9.3: only stdlib + Pydantic |
| `fecha` NOT in `_GroupKey` | R9.4: `_GroupKey` touched nowhere; divergence is post-group side-channel |
| MATCH EXACT(0) — no qty change | R9.4: reconciliation qty comparison block is untouched |
| OCR-validation gate — low confidence flags, never asserts | R9.5: ADR-7 gate (`< 0.85` → handwritten=None) |
| Null baseline → no guía WARNINGs | R9.3: `check_fecha_divergence` null-safety; R9.4: reads `fecha_authoritative` |
| Year-only divergence → NOT a WARNING | R9.3: predicate tests day+month only; critical test in R9.3 and R9.8 |
| `requires_review` only OR-set, never cleared | R9.4: existing block extended additively |
| Local-first / air-gap | R9.5: reuses existing `VisionLLMPort`; no new egress |
| Reversibility | `fecha_authoritative` falls back to electronic; disabling declared read leaves Slice 1 as-is |
| Backward compatibility | All new fields have `= None` / `= False` defaults; old serialised runs parse cleanly |
| R7 guía stamp crop regression | R9.5: `stamp_crop` config untouched; only `protocolo_crop` added |
| R8 MATCH regression | R9.8 item 4: asserts all R8 gate assertions still pass |
