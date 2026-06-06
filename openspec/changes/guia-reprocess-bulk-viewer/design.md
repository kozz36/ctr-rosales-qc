# Design: Guía Reprocess Bulk + Page Viewer (UX extension of PR#3)

## Technical Approach
Reuse-first, additive. Backend: one new route + one DTO; the vision-concurrency
core (`ReprocessService.apply_reprocess` + its `Semaphore(3)`/`Lock`) is UNCHANGED.
Frontend: drop-in existing `SourcePages`/`PageSheetViewer`/`useGuiaLineEdit`; new tab
shell + Acciones menu are local component refactors. F4 "Corregir manual" needs ONE
backend extension (canonical reassignment of a guía line) — flagged below.

## Architecture Decisions

| # | Decision | Choice | Rejected | Rationale |
|---|----------|--------|----------|-----------|
| D1 | F1 bulk concurrency | `async def _reprocess_batch()` BG task driving `asyncio.gather(*[apply_reprocess(g) ...], return_exceptions=True)` | sync `_retry_batch` loop calling async service; new unbounded vision path | Service is `async`; sync loop cannot await it. Existing per-guía `reprocess_guia` is `async def` — same loop. `gather` over the 24 coroutines starts all but the service's own `Semaphore(3)` caps concurrent vision calls. NO 2nd limiter (KI-2). NO `asyncio.run` (nested-loop RuntimeError). |
| D2 | BG task callable type | Pass the async coroutine-function to `background_tasks.add_task` directly | wrap in `asyncio.run`; spawn `asyncio.create_task` manually | Starlette `BackgroundTask.__call__` inspects `iscoroutinefunction` and `await`s it on the running loop. Mirror `reprocess_guia`'s async signature; the route handler itself is `async def` (matches `reprocess_guia`, NOT sync `retry_registro`). |
| D3 | F1 live progress | Frontend polls `GET /table` (reuse `tableQuery.refetch()` + count delta) | reuse run-status channel; SSE/streaming | `GET /table` already returns `errored_guias[]`; polling it shrinks the list as guías recover — zero new backend surface. Run-status is `review`-terminal and not per-guía. SSE is explicit out-of-scope. Lowest risk = reuse the existing poll the panel already triggers. |
| D4 | F1 response | New `ReprocessBatchResponse(run_id, registro, count, task)` (202) mirroring `RetryBatchResponse` | reuse `RetryBatchResponse` | Distinct semantics (vision vs SUNAT); template-copy keeps DTO parity, additive + revertible. |
| D5 | #42 retry-batch fix | In `_retry_batch`, call `review_service.mark_retry_attempted(eg.guia_id)` when `apply_retry(...).recovered is False` (mirror per-guía `retry_errored_guia` L934). NEW `_reprocess_batch` MUST NOT call it (flag gates SUNAT REINTENTAR, not the AI button). | set flag in both | `retry_attempted` is the SUNAT-button gate; reprocess is stateless-retryable. |
| D6 | F2 viewer link | Replace plain `<span>{{ source_pages.join }}</span>` in `GuiaDrillDown` Páginas cell with `<SourcePages :pages="guia.source_pages" :run-id="runId" @page-click>`; bubble `pageClick` → `ReconciliationRow` → `ReviewPage.onPageClick`. `viewerRowPages` = the guía's OWN `source_pages`. | new viewer component; per-row page set | `onPageClick` already derives `viewerRowPages` from the owning row; for a guía chip, scope nav to that guía's pages. COLSPAN=11 UNCHANGED (cell swap, not column add). |
| D7 | F3 tabs | Local `activeTab` ref in `ReviewPage` (`'reconciliacion' \| 'pendientes'`), extract `PendientesPorProcesarTab.vue` hosting `ErroredGuiasPanel` + F1 bulk button | inline both tabs in ReviewPage; Vue Router tab routes | Extraction keeps `ReviewPage` thin (already 375 lines) and isolates the F1 bulk surface for Playwright. No URL routing (out-of-scope). Default tab `reconciliacion`; Pendientes badge = `erroredGuias.length`. |
| D8 | F4 Acciones menu | Replace single `[Reasignar]` button with `[Acciones]` disclosure → Reasignar (existing `emit('reassign')`) · Reprocesar (single-guía, calls existing `reprocessGuia` for the M failures) · Corregir manual (D9) | separate buttons inline | One menu avoids action-column sprawl; reuses existing emits/clients. |
| D9 | F4 "Corregir manual" data flow | Operator picks a DECLARED material from the SAME registro (dropdown sourced from `rows.filter(r => r.registro === guia.registro)` already in the table response) + types cantidad → backend ASSIGNS the guía line to that canonical material. Requires NEW optional field `assign_material_canonical` on `GuiaLineEditRequest` + service path that sets `description_canonical`/`match_method="operator"` + `requires_review=True`. | reuse cantidad-only `apply_guia_line_edit`; new reassign endpoint | The existing PATCH `/guias/{id}/lines` only edits `cantidad` on a line located by an EXISTING `description_canonical` — it CANNOT change the canonical assignment (verified `review_service.apply_guia_line_edit` L245-350). Operator-assigned matching is a canonical reassignment; minimal additive extension to the existing endpoint (immutable `model_copy` + re-reconcile + audit + `requires_review=True`) bypasses canonical-matching ambiguity cleanly without a new route. |

## Data Flow

F1 bulk:

    [Procesar todos con IA] → POST /runs/{id}/registros/{r}/reprocess (202, count=N)
        └─ BG: async _reprocess_batch → gather(apply_reprocess×N, return_exceptions)
                   └─ Semaphore(3) bounds vision; per-guía isolation; requires_review=True
    Frontend: poll GET /table → errored_guias[] shrinks → "N recuperadas / M fallaron"

F2 page click:

    SourcePages chip (in GuiaDrillDown) ──pageClick──▶ ReconciliationRow ──pageClick──▶ ReviewPage.onPageClick ──▶ PageSheetViewer(page, viewerRowPages = guía.source_pages)

F4 Corregir manual:

    [Acciones]→Corregir manual → dialog(declared dropdown from registro rows + cantidad)
        └─ PATCH /guias/{guia_id}/lines { assign_material_canonical, cantidad }
               └─ apply_guia_line_edit sets description_canonical + match_method="operator" + requires_review=True → re-reconcile → invalidate table

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `backend/.../api/routes.py` | Modify | New `async def reprocess_registro` (202) + inner `async def _reprocess_batch`; #42 fix in `_retry_batch`; extend `edit_guia_line` to pass `assign_material_canonical` |
| `backend/.../api/schemas.py` | Modify | New `ReprocessBatchResponse`; add optional `assign_material_canonical: str \| None` to `GuiaLineEditRequest` |
| `backend/.../application/reprocess_service.py` | None | Core UNCHANGED (confirmed) — reuse `apply_reprocess` + Semaphore |
| `backend/.../application/review_service.py` | Modify | `apply_guia_line_edit` accepts `assign_material_canonical` → sets `description_canonical`/`match_method="operator"`/`requires_review=True` on the line copy |
| `frontend/src/api/{client,types}.ts` | Modify | `reprocessRegistroBatch()` + `ReprocessBatchResponse`; `assign_material_canonical?` on `GuiaLineEditRequest` |
| `frontend/.../ReviewPage.vue` | Modify | Tab bar + `activeTab` ref + Pendientes count badge |
| `frontend/.../PendientesPorProcesarTab.vue` | Create | Hosts `ErroredGuiasPanel` + per-Registro bulk button + confirm + live summary |
| `frontend/.../ErroredGuiasPanel.vue` | Modify | Per-Registro bulk button, confirm dialog (call count), in-flight state, "N/M" summary |
| `frontend/.../GuiaDrillDown.vue` | Modify | `<SourcePages>` in Páginas cell + `pageClick` emit; `[Acciones]` menu; Corregir-manual dialog |
| `frontend/.../ReconciliationRow.vue` | Modify | Forward `pageClick` from drill-down (already forwards from grid) |
| tests (pytest + vitest) | Create | strict-TDD: F1 no-nested-loop, #42 flag, F4 canonical assign, frontend tab/menu/viewer |

## Interfaces / Contracts

```python
class ReprocessBatchResponse(BaseModel):
    run_id: str
    registro: str
    count: int  # guías queued for vision reprocess
    task: str = "started"

# GuiaLineEditRequest gains:
assign_material_canonical: str | None = None  # operator-assigned canonical; None = cantidad-only edit (back-compat)
```

```ts
export async function reprocessRegistroBatch(runId: string, registro: string): Promise<ReprocessBatchResponse>
```

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit (py) | `_reprocess_batch` awaits without nested-loop error; per-guía failure isolated; #42 sets `retry_attempted` only on retry-fail, NOT on reprocess-fail; F4 assign sets canonical+`match_method="operator"`+`requires_review=True` | fake VisionLLMPort + fake ReviewService; assert no `RuntimeError`, flag state, recovered/failed counts |
| Unit (vitest) | tab switch + badge count; Acciones menu items; Corregir-manual dialog dropdown sourced from registro rows; `pageClick` bubbles guía pages | mount with stubbed composables |
| Integration | POST batch → 202 + count; poll table shrinks errored | TestClient |
| E2E (SA-5) | upload → Pendientes tab → bulk reprocess (live + N/M) → drill-down chip opens viewer → Acciones→Corregir manual assigns canonical | Playwright on running app, MANDATORY before done |

## Migration / Rollout
No migration. Fully additive; each feature + #42 fix reverts independently. No domain-key,
unit, or grade change. `requires_review=True` preserved on every recovered/corrected line.

## Delivery Notes
4 features + 1 fix likely >400 lines → forecast chained/stacked PRs at tasks phase under
`ask-on-risk`. Frontend-visual work follows the `frontend-design` skill and is assigned the
**opus** model at apply (per user). strict_tdd TRUE: failing test FIRST on all `*.py` /
`frontend/src/**`.

## Open Questions
- [ ] SPEC-OWNED (F4/D9): confirm `match_method="operator"` label + whether Corregir-manual
  is exposed for ALL guía rows or only errored/unresolved ones. Editable-field set for the
  dialog (cantidad always; canonical always) — proposal deferred this to spec. Design covers
  the data flow; the exact UX/field policy is a spec decision (not blocking the architecture).
