# Exploration: guia-reprocess-bulk-viewer

> Materialized copy of Engram topic `sdd/guia-reprocess-bulk-viewer/explore` (#3008).

## Change Name
`guia-reprocess-bulk-viewer`

## Context
UX extension of PR#3 (Reprocesar con IA). Features:
1. Bulk "Procesar todos con IA" per-Registro (vision batch, bounded concurrency)
2. Guía row → open PageSheetViewer lightbox (drill-down click-through)
3. Separate "Pendientes por procesar" tab in ReviewPage
4. Drill-down `[Acciones]` menu (Reasignar · Reprocesar · Corregir manual)

## Current State

### Backend
- Per-guía reprocess: `POST /runs/{run_id}/errored-guias/{guia_id}/reprocess` → `async reprocess_guia()` → `ReprocessService.apply_reprocess()`. Bounded by `asyncio.Semaphore(max_concurrency=3)` + `asyncio.Lock` for commits. Defined in `backend/src/reconciliation/application/reprocess_service.py`.
- Per-Registro RETRY batch (SUNAT, not vision): `POST /runs/{run_id}/registros/{registro}/retry` → `retry_registro()` uses `background_tasks.add_task(_retry_batch)` — sync loop over errored guías, per-failure isolation via try/except. Defined in `backend/src/reconciliation/infrastructure/api/routes.py` around line 961.
- **Issue #42 confirmed**: `_retry_batch` in `retry_registro` does NOT call `review_service.mark_retry_attempted(guia_id)` on failure, unlike the per-guía `retry_guia` endpoint (line 935).
- Schemas: `RetryBatchResponse` (202 + count, for SUNAT retry). No `ReprocessBatchResponse` exists yet.
- No per-Registro vision batch endpoint exists today.

### Frontend
- `ErroredGuiasPanel.vue`: collapsible panel, per-guía `REINTENTAR` + `Reprocesar con IA` buttons. No bulk button. Standalone panel above ReviewGrid in `ReviewPage.vue`.
- `ReviewPage.vue`: hosts the panel inline. PageSheetViewer mounted with `v-model="showPageViewer"` + `onPageClick(page)` handler. The handler derives `viewerRowPages` from the main reconciliation rows array (not from errored guías).
- `GuiaDrillDown.vue`: renders guía rows in the main grid. "Páginas" column shows `guia.source_pages.join(', ')` as plain text — NOT clickable. Has inline cantidad edit (`useGuiaLineEdit`) and Reassign action. Emits `reassign: [guiaId]` and `rowUpdated: []` — no `pageClick` today. `COLSPAN = 11` hardcoded (fragile, must match ReviewGrid COLUMNS + expand).
- `SourcePages.vue`: chip component with `pageClick: [page: number]` emit; used in `ReconciliationRow.vue` / `ReviewGrid.vue` to bubble up to `ReviewPage.onPageClick`.
- `PageSheetViewer.vue`: props `modelValue: bool`, `runId: string`, `page: number`, `rowPages?: number[]`. Takes `rowPages` for prev/next navigation.

### Existing page-click event chain (reconciliation grid)
`SourcePages chip click` → `ReconciliationRow.vue` → `ReviewGrid.vue` → `ReviewPage.vue::onPageClick(page)` → sets `viewerPage` + `viewerRowPages` + `showPageViewer=true`.

## Approaches (recommendations)
- **F1 bulk endpoint** — Option B: route-level `async def _reprocess_batch()` background task using `asyncio.gather(*[apply_reprocess(...)], return_exceptions=True)`. Reuses the existing `Semaphore(max_concurrency=3)` — 24 coroutines start but only 3 run concurrent vision calls. Per-failure isolation via `return_exceptions=True`. 202 + client polls GET /table.
- **F2 viewer link** — Option C: replace plain Páginas span with `<SourcePages>` component in `GuiaDrillDown.vue`; emit `pageClick` and bubble through `ReconciliationRow.vue`. Inherits chip interactivity + divergent-page highlighting.
- **F3 tabs** — Option A: tab bar in `ReviewPage.vue` ("Reconciliación" | "Pendientes por procesar"); pending tab hosts `ErroredGuiasPanel` + bulk button. Local `ref` activeTab (no Vue Router change).

## Risks
1. Async background task / nested event loop — must use `async def` BG task (NOT `asyncio.run` inside running loop). Strict-TDD: test first.
2. Semaphore is per-`ReprocessService` instance, shared across batch + per-guía calls — INTENDED (bounds total concurrent vision calls).
3. kimi-k2.5:cloud quantity accuracy unverified — `requires_review=True` safety net on every recovered line; bulk amplifies exposure.
4. Partial-failure visibility — 202+poll limits operational feedback; failed guías stay in errored list.
5. SA-5 — all features visible-UX → Playwright runtime validation mandatory before done.
6. `GuiaDrillDown` `COLSPAN = 11` fragile — must remain unchanged.
7. Tab is local UI state, not a route.

## Reuse vs New Inventory
| Item | Reuse | New |
|------|-------|-----|
| Vision concurrency | `Semaphore` + `Lock` in `ReprocessService` | none |
| Batch endpoint pattern | `retry_registro` route template | `POST /registros/{r}/reprocess` + `async def` BG task |
| Response DTO | `RetryBatchResponse` template | `ReprocessBatchResponse` |
| API client | `retryRegistro()` template | `reprocessRegistroBatch()` |
| Viewer lightbox | `PageSheetViewer.vue` | none |
| Page chip | `SourcePages.vue` | none |
| Inline cantidad edit | `useGuiaLineEdit` | surface via Acciones menu |
| Tab structure | none | tab bar + `activeTab` ref |

## Ready for Proposal
Yes — open questions resolved in the confirmed scope passed by the orchestrator.
