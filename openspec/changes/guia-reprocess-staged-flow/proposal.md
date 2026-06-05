# Proposal: Surface errored_guias in the review/table path (PR #1 foundation)

## Intent

The pipeline already persists `errored_guias` (0-line guía blocks) to the extraction
cache (keystone #2), but the READ side was never wired. `build_review_service`
(`container.py:589-599`) loads `declared`/`guias`/`rows` and ignores
`cache["errored_guias"]`, so `ReviewService` has no concept of errored guías and
`GET /table` cannot surface them. The operator therefore **cannot see** which guías
errored. Confirmed by the `ErroredGuia` docstring itself: "the consuming read side is
wired in change #3 (staged REINTENTAR / review-table flow)" — this slice.

This foundation slice closes the read gap, read-only. It also lays the `retry_attempted`
contract that PR #2/#3 gate their action buttons on.

## Scope

### In Scope (additive only)
- `build_review_service`: read `cache.get("errored_guias", [])`, hydrate `list[ErroredGuia]`, pass to `ReviewService` (both `__init__` and `restore_from_sidecar`).
- `ReviewService`: hold `_errored_guias` + read-only `errored_guias` accessor. NO mutation/reprocess. Restart path (`restore_from_sidecar`) preserves it.
- `ReconciliationTableResponse`: add `errored_guias: list[ErroredGuiaResponse] = []` (additive, default empty).
- `routes.get_table`: populate `errored_guias` from `review_service.errored_guias`.
- `ErroredGuia` domain model: add `retry_attempted: bool = False` (additive; no-op this slice, gates PR #2/#3 buttons).
- Frontend: read-only `ErroredGuiasPanel.vue` (peer to `UnresolvedGuiasPanel.vue`) above the grid, listing per-Registro "Error en páginas X". NO buttons.

### Out of Scope (deferred to PR #2/#3)
- `ReprocessService` (Application orchestrator — Approach B).
- REINTENTAR re-decode (QR re-render + SUNAT re-fetch) — PR #2.
- Reprocesar-con-IA / `VisionLLMPort.read_material_table` — PR #3.
- Any `add_recovered_guia` mutation, re-reconcile, transient/systematic classification.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `review`: `GET /table` additively surfaces persisted `errored_guias` (read-only); `ReviewService` carries them and survives restart.

## Approach

Pure read-side wiring, no new behavior. ReviewService stays a value-edit coordinator —
`_errored_guias` is inert state with a read accessor only (Approach B keeps all reprocess
orchestration out of ReviewService; that arrives as `ReprocessService` in PR #2/#3).
`ErroredGuiaResponse` already exists in `schemas.py:137`; the DTO change is purely adding
the field to `ReconciliationTableResponse`. The frontend mirrors the proven
`UnresolvedGuiasPanel.vue` structure, render-only.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `infrastructure/container.py:589-599` | Modified | Hydrate `errored_guias` from cache, pass to ReviewService |
| `application/review_service.py` (`__init__`, `restore_from_sidecar`, accessors) | Modified | Add `_errored_guias` + `errored_guias` property; thread param through restart path |
| `infrastructure/api/schemas.py:290` | Modified | `ReconciliationTableResponse.errored_guias` field |
| `infrastructure/api/routes.py:366` (`get_table`) | Modified | Populate `errored_guias` |
| `domain/models.py:389` (`ErroredGuia`) | Modified | Add `retry_attempted: bool = False` |
| `frontend/src/api/types.ts` | Modified | `ErroredGuiaResponse` type + field on table response |
| `frontend/src/features/review/ErroredGuiasPanel.vue` | New | Read-only per-Registro error list |
| `frontend/src/features/review/ReviewPage.vue` | Modified | Mount panel, feed from table query |

## Constraints (hard invariants)

- **Domain purity**: `ErroredGuia` stays pure Pydantic; no IO/SDK under `domain/`.
- **Ports at the boundary**: `application` depends only on ports + config; no new adapter import.
- **Lazy heavy deps**: adapters unchanged; no top-level heavy imports introduced.
- **`errored_guias` additive-only**: NEVER touches group key `(registro, material_canonical, unidad)`, status, delta, qty. `fecha` is never a grouping axis; units never converted.
- **Validation gate**: read-only — this slice DISPLAYS errored guías, NEVER mutates reconciliation or auto-corrects.
- **Input PDF read-only**; vision provider-agnostic (untouched here).

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| `restore_from_sidecar` drops `errored_guias` on restart | Med | Thread param through both `__init__` and `restore_from_sidecar`; add a restart-path test asserting preservation |
| Additive DTO breaks existing `/table` consumers | Low | Field defaults to `[]`; existing clients ignore unknown field; frontend type is optional |
| Frontend regression (SA-5) | Med | This touches `frontend/src/**` → **Playwright runtime validation required before done** (upload → review → ErroredGuiasPanel renders errored guías) |

## Rollback Plan

Pure additive diff. Revert the PR commit(s): the new field defaults to `[]`, the new
panel is removed, `retry_attempted` defaults `False`. No data migration, no cache schema
break (the cache key already exists). Reverting restores prior behavior exactly.

## Dependencies

- Keystone #2 (already shipped): `errored_guias` persisted to extraction cache.

## Future slices (deferred — not specced/designed here)

- **PR #2 — REINTENTAR**: `ReprocessService` + `apply_retry` (QR re-render + SUNAT re-fetch) + `POST /errored-guias/{guia_id}/retry` + button.
- **PR #3 — Reprocesar con IA**: `VisionLLMPort.read_material_table` (adapters + null stub) + `apply_reprocess` + endpoint + button.

## Success Criteria

- [ ] After restart, `build_review_service` rehydrates `errored_guias` from cache (test proves preservation through `restore_from_sidecar`).
- [ ] `GET /table` returns `errored_guias` populated from `ReviewService.errored_guias`.
- [ ] `ErroredGuia.retry_attempted` defaults `False`; existing serialization unaffected.
- [ ] `ErroredGuiasPanel.vue` renders per-Registro "Error en páginas X" read-only (no buttons).
- [ ] Playwright validation: errored guías visible in the running app (SA-5 gate).
- [ ] No change to group key, status, delta, or qty for any non-errored guía.
