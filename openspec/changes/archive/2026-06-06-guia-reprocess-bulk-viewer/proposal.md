# Proposal: Guía Reprocess Bulk + Page Viewer (UX extension of PR#3)

## Intent
PR#3 shipped per-guía "Reprocesar con IA" but the QC engineer still processes a Registro's errored guías one button at a time, cannot jump from a guía row to its scanned page, and has no dedicated workspace for pending guías. This change closes the operational gap: bulk per-Registro AI reprocessing, drill-down → page viewer, a Pendientes tab, and an Acciones menu — so resolving a Registro's pending guías is a single guided flow, not N manual clicks.

## Business Value
Cuts the time-to-clear a Registro of errored guías; gives the engineer page-level visual verification at the point of decision; consolidates the pending-work surface. All recovered lines keep `requires_review=True`, so speed never bypasses the human validation gate.

## Scope

### In Scope (4 features + 1 fix)
- **F1 — Bulk "Procesar todos con IA" per-Registro**: `POST /runs/{run_id}/registros/{registro}/reprocess` as an `async def _reprocess_batch()` FastAPI background task running `asyncio.gather(..., return_exceptions=True)` over existing `ReprocessService.apply_reprocess`, bounded by its `Semaphore(reprocess_max_concurrency=3)` — never 24 concurrent (KI-2). Per-guía failure isolated. UI: confirm dialog showing call count ("¿Procesar N guías con IA? = N llamadas"); LIVE "Procesando N…" with incremental table polling; "N recuperadas / M fallaron" summary; M failures stay pending.
- **F2 — Drill-down guía → page viewer**: replace plain "Páginas" span in `GuiaDrillDown.vue` with `<SourcePages>`; bubble `pageClick` through `ReconciliationRow.vue` → `ReviewPage.onPageClick` → `PageSheetViewer.vue`. `viewerRowPages` = that guía's own `source_pages`.
- **F3 — Tabs**: `ReviewPage.vue` → "Reconciliación" | "Pendientes por procesar". Pending tab hosts `ErroredGuiasPanel` + F1 button. Default = "Reconciliación"; "Pendientes" carries an errored-count badge.
- **F4 — Drill-down `[Acciones]` menu**: single `[Reasignar]` becomes `[Acciones]` → Reasignar (existing) · Reprocesar (single-guía reprocess, for the M failures) · Corregir manual (surface existing `useGuiaLineEdit` cantidad edit; editable-field set decided in spec).
- **Fix #42**: `_retry_batch` missing `mark_retry_attempted`. NOTE: the NEW bulk REPROCESS must NOT set `retry_attempted` on vision failure (that flag gates the SUNAT REINTENTAR button, not the AI button) — reprocess is stateless-retryable.

### Out of Scope (explicit)
- Cross-model consensus (#44) · deadline-guard request cancellation (#41) · unit-map (#43) · streaming/SSE batch progress (polling is MVP) · URL-deep-linked tab routing.

## Capabilities

### New Capabilities
- None.

### Modified Capabilities
- `review`: adds bulk per-Registro AI reprocess (202 batch, bounded, per-guía isolation, `requires_review` preserved, `retry_attempted` NOT set on reprocess failure); page-viewer access from guía drill-down; Pendientes tab; Acciones menu (reassign · single reprocess · manual correction). Includes #42 `mark_retry_attempted` fix on SUNAT retry batch.

## Approach
Backend reuses `apply_reprocess` + its Semaphore via an async background task (explore Option B) — no new unbounded vision path, vision stays provider-agnostic behind `VisionLLMPort`. Frontend reuses `SourcePages`, `PageSheetViewer`, `useGuiaLineEdit`, and the `RetryBatchResponse` DTO template. Tab + Acciones are local component refactors. Frontend-visual work follows the `frontend-design` skill; per user, the apply phase for frontend-visual work is assigned the **opus** model.

## Affected Areas
| Area | Impact | Description |
|------|--------|-------------|
| `backend/.../api/routes.py` | New + Modified | bulk reprocess endpoint + `async def _reprocess_batch`; #42 fix in `_retry_batch` |
| `backend/.../api/schemas.py` | New | `ReprocessBatchResponse` DTO |
| `backend/.../application/reprocess_service.py` | Possibly Modified | thin batch coordination if extracted (else route-level) |
| `frontend/src/api/{client,types}.ts` | New | `reprocessRegistroBatch` + type |
| `ReviewPage.vue` | Modified | tab bar + `activeTab` + count badge |
| `ErroredGuiasPanel.vue` | Modified | per-Registro bulk button, confirm, live state, summary |
| `GuiaDrillDown.vue` | Modified | `<SourcePages>` + `pageClick` emit; `[Acciones]` menu |
| `ReconciliationRow.vue` | Modified | forward `pageClick` |
| tests (backend pytest + frontend vitest) | New | strict-TDD, test-first |

## Architecture Invariants (auto-reject anti-patterns)
- Domain pure (no SDK/IO under `domain/`); `application/` imports only Protocols/config; adapters lazy-import heavy deps.
- Vision provider-agnostic behind `VisionLLMPort`; bulk REUSES bounded `apply_reprocess` — no new unbounded vision path.
- Reconciliation is the validation gate — never auto-correct; recovered lines `requires_review=True`.
- `fecha` never a grouping axis; units never converted (KG/TN/RD/Rollo); grades G60/G42/G75 distinct; input PDF read-only; local-first.
- SA-5: all 4 features are visible-UX → Playwright runtime validation MANDATORY before "done" (planned into apply, not optional).

## Risks
| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Async BG-task nested event loop (`asyncio.run` in running loop) | Med | Use `async def` BG task; strict-TDD failing test FIRST asserting no nested-loop error |
| Bulk amplifies vision quantity misreads (#40: kimi ~83% line acc, 20–40% empty pages) | High | `requires_review=True` on every recovered line — MVP-acceptable BECAUSE of the gate; surfaced explicitly |
| Partial-failure UX confusion | Med | Confirm dialog + "N recuperadas / M fallaron" summary; M stay pending |
| Bulk billed-call cost (N cloud calls) | Med | Confirm dialog shows exact call count before firing |
| Semaphore saturation if batch + per-guía overlap | Low | Shared `Semaphore(3)` bounds total — intended; UI gates the bulk button while in-flight |
| SA-5 runtime validation skipped | Med | Mandatory Playwright gate planned into apply |
| `GuiaDrillDown` `COLSPAN=11` fragility | Low | Replacing span with `<SourcePages>` does not change column count; keep constant unchanged |
| **Delivery size** | — | 4 features + fix likely **>400 lines** → flag chained-PR decision at tasks phase under `ask-on-risk` |

## Rollback Plan
Backend additive (new endpoint + DTO) — revert by removing the route/schema; #42 fix is a one-line guarded addition, independently revertible. Frontend changes are componentized: tab/menu/viewer-link revert independently. No DB/migration, no domain-key change — pure additive side-channel.

## Dependencies
- Vision enabled (`provider=ollama, OLLAMA__MODEL=kimi-k2.5:cloud, DEADLINE_S=60`) for live reprocess; degrades gracefully when `vision.enabled=false`.
- Frontend skills: `frontend-design` + `vue-architect`; backend skill: `material-canonical-matching` (recovered-line invariants).

## Success Criteria
- [ ] One click reprocesses all errored guías in a Registro, bounded at 3 concurrent, with confirm + live progress + recovered/failed summary.
- [ ] Guía drill-down page chips open `PageSheetViewer` at the correct page.
- [ ] Pendientes tab with count badge; default tab = Reconciliación.
- [ ] Acciones menu exposes Reasignar · Reprocesar · Corregir manual.
- [ ] #42 fixed; bulk reprocess does NOT set `retry_attempted` on failure.
- [ ] Every recovered/grade_tolerant line stays `requires_review=True`.
- [ ] Strict-TDD test-first on all `*.py` / `frontend/src/**`; SA-5 Playwright validation passed.
