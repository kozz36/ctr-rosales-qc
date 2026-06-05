# Design: Surface errored_guias in the review/table path (PR #1 foundation)

## Technical Approach

Pure read-side wiring (no new behavior). The pipeline already persists `errored_guias` to the
extraction cache (keystone #2); this slice connects that cache key to the review/table read path
so the operator can SEE 0-line guías after a restart. `errored_guias` is inert constructor state
on `ReviewService` (Approach B: reprocess orchestration deferred to PR #2/#3), surfaced additively
on `ReconciliationTableResponse`, and rendered read-only by a new `ErroredGuiasPanel.vue` mirroring
the proven `UnresolvedGuiasPanel.vue`. Additive-only: never touches the group key, status, delta,
or qty of any reconciliation row.

## Architecture Decisions

| Decision | Choice | Alternatives rejected | Rationale |
|----------|--------|-----------------------|-----------|
| ReviewService threading | Add `errored_guias: list[ErroredGuia] \| None = None` (default `None` → `[]`) as a **trailing** param on BOTH `__init__` and `restore_from_sidecar`; store `self._errored_guias`; expose read-only `@property errored_guias`. | New required positional arg (breaks every caller/test); a separate setter after construction (state can be lost on restart). | Trailing optional param preserves the 4-arg signature — every existing call site and test keeps working. `restore_from_sidecar` is the SOLE path `build_review_service` uses (L594), and it calls `cls(...)` internally (L435), so the param must thread through both. |
| Why NOT replay errored_guias as events | Pass as constructor state; do NOT add to the sidecar replay loop. | Persist as sidecar edit events and replay. | errored_guias originate from the **extraction cache**, not from operator edit events. The replay loop (L439-487) only handles `field_edit`/`guia_line_edit`/`reassignment`. errored_guias are immutable run output this slice → constructor state, never replayed. |
| Cache hydration point | In `build_review_service` (container.py:589-599), after the `rows` line: `errored_guias = [ErroredGuia.model_validate(e) for e in cache.get("errored_guias", [])]`, pass to `restore_from_sidecar`. | Hydrate inside ReviewService. | Container owns cache→domain hydration (mirrors declared/guias/rows). `.get(..., [])` is graceful for pre-keystone-#2 caches. Add `ErroredGuia` to the existing lazy import block (L583). |
| DTO shape | `ReconciliationTableResponse.errored_guias: list[ErroredGuiaResponse] = Field(default_factory=list)`. Reuse existing `ErroredGuiaResponse` (schemas.py:137 — same 3 fields). | New table-specific DTO. | `ErroredGuiaResponse` already mirrors the domain model 1:1; reuse avoids duplication. `default_factory=list` is backward-compatible (existing clients ignore the new field). |
| `retry_attempted` placement | `ErroredGuia.retry_attempted: bool = False` (additive defaulted field). NOT exposed on `ErroredGuiaResponse` this slice (no consumer yet). | Add to DTO now. | Cache round-trip is backward-compatible: a new defaulted field deserializes cleanly from caches written before it existed (`model_validate` fills the default). Lays the PR #2/#3 button-gating contract without surfacing it prematurely. |
| Frontend panel | New read-only `ErroredGuiasPanel.vue` (peer to `UnresolvedGuiasPanel.vue`), mounted in `ReviewPage.vue` above the grid, fed from `tableQuery.data.value?.errored_guias`. NO action buttons. | Inject error rows into `ReviewGrid.vue` group headers. | Separate panel avoids mixing `ErroredGuia` shape with `ReconciliationRowResponse`; matches the established unresolved-panel pattern; clean injection point for PR #2/#3 buttons. |

## Data Flow

    Pipeline (keystone #2) ──→ extraction cache["errored_guias"]
                                        │
                                        ▼
        build_review_service: model_validate → list[ErroredGuia]
                                        │ (constructor state, trailing param)
                                        ▼
        restore_from_sidecar → ReviewService._errored_guias  ──(survives restart)──┐
                                        │ .errored_guias (read-only property)        │
                                        ▼                                            │
        GET /table → ReconciliationTableResponse.errored_guias ─→ types.ts ─→ ErroredGuiasPanel.vue
                                                                                (read-only render)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/.../domain/models.py` (~389) | Modify | `ErroredGuia.retry_attempted: bool = False` (additive). |
| `backend/.../application/review_service.py` (`__init__` L110, `restore_from_sidecar` L410) | Modify | Trailing `errored_guias` param on both; `self._errored_guias`; read-only `errored_guias` property. |
| `backend/.../infrastructure/container.py` (589-599) | Modify | Hydrate `list[ErroredGuia]` from `cache.get("errored_guias", [])`; pass to `restore_from_sidecar`; add `ErroredGuia` to lazy import. |
| `backend/.../infrastructure/api/schemas.py` (`ReconciliationTableResponse` L290) | Modify | Add `errored_guias: list[ErroredGuiaResponse] = Field(default_factory=list)`. |
| `backend/.../infrastructure/api/routes.py` (`get_table` L366) | Modify | Map `review_service.errored_guias` → `ErroredGuiaResponse` list on the response. |
| `frontend/src/api/types.ts` (~163) | Modify | Add `ErroredGuiaResponse` interface; add `errored_guias: ErroredGuiaResponse[]` to `ReconciliationTableResponse`. |
| `frontend/src/features/review/ErroredGuiasPanel.vue` | Create | Read-only collapsible panel mirroring UnresolvedGuiasPanel; per-Registro "Error en páginas X"; NO buttons. |
| `frontend/src/features/review/ReviewPage.vue` (~40) | Modify | Compute `erroredGuias` from table query; mount panel above the grid. |

## Interfaces / Contracts

```python
# review_service.py — signature preserved, trailing optional param
def __init__(self, declared, guias, rows, ctx,
             errored_guias: list[ErroredGuia] | None = None) -> None:
    self._errored_guias: list[ErroredGuia] = list(errored_guias or [])

@property
def errored_guias(self) -> list[ErroredGuia]:
    return list(self._errored_guias)

@classmethod
def restore_from_sidecar(cls, declared, guias, rows, ctx,
                         errored_guias: list[ErroredGuia] | None = None) -> "ReviewService":
    service = cls(declared, guias, rows, ctx, errored_guias=errored_guias)
    # ... existing replay loop unchanged (errored_guias NOT replayed)
```

```typescript
// types.ts
export interface ErroredGuiaResponse {
  registro: string | null
  guia_id: string
  source_pages: number[]
}
// ReconciliationTableResponse gains: errored_guias: ErroredGuiaResponse[]
```

## Testing Strategy (Strict-TDD — failing test FIRST)

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit (backend) | `ErroredGuia.retry_attempted` defaults `False`; cache round-trip (`model_dump`→`model_validate`) survives the new field; old cache JSON without the field still loads. | `cd backend && uv run pytest` |
| Integration (backend) | cache→`build_review_service`→`ReviewService.errored_guias` populated; **restart-preservation**: rebuild via `restore_from_sidecar` (with sidecar edits present) keeps `errored_guias` intact; `GET /table` returns `errored_guias`; additive — no change to rows/status/delta/qty for non-errored guías. | `cd backend && uv run pytest` |
| Unit (frontend) | `ErroredGuiasPanel.vue` renders per-Registro error entries; empty state hides the panel (mirror UnresolvedGuiasPanel `v-if length > 0`). | `cd frontend && npm test` |
| E2E (SA-5) | Upload → review → ErroredGuiasPanel visible with errored guías, 0 console errors. | Playwright MCP (REQUIRED before "done"). |

## Migration / Rollout

No migration. Pure additive: DTO field defaults `[]`, `retry_attempted` defaults `False`, cache key
already present since keystone #2. Rollback = revert the commit(s); no cache-schema break.

## Open Questions

None blocking. PR #2/#3 concerns (ReprocessService, VisionLLMPort.read_material_table, endpoints,
buttons, classification) are explicitly out of scope and deferred.
