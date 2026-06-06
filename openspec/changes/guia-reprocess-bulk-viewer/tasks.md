# Tasks: guia-reprocess-bulk-viewer

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 620–780 (additions + deletions) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR-A: backend (F1 endpoint + #42 + F4 canonical extension) · PR-B: frontend F3 tabs + F1 frontend · PR-C: frontend F2 viewer + F4 Acciones/Corregir + SA-5 Playwright |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — orchestrator must ask before apply |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| A | Backend: ReprocessBatchResponse DTO, POST /reprocess endpoint, #42 fix, F4 canonical extension, match_method "operator" widening | PR-A | base = feat/guia-reprocess-bulk-viewer; pure Python; all tests pass before frontend work starts |
| B | Frontend: types/client additions, F3 tab shell + badge, F1 frontend (confirm + live polling + N/M summary) | PR-B | base = PR-A branch; frontend-visual (opus); vitest green |
| C | Frontend: F2 SourcePages drop-in, F4 Acciones menu + Corregir-manual dialog, SA-5 Playwright validation | PR-C | base = PR-B branch; frontend-visual (opus); SA-5 MANDATORY before done |

---

## Phase 1 — Foundation: DTOs, contracts, and the #42 bugfix
*REV-R20, REV-R26 | D4, D5 | PR-A*

- [x] 1.1 **RED** — `backend/tests/unit/infrastructure/test_api_routes.py`: add test asserting `POST /runs/{id}/registros/{r}/reprocess` returns 202 with `ReprocessBatchResponse` fields (`run_id`, `registro`, `count`, `task="started"`) — test MUST fail (route absent).
- [x] 1.2 **RED** — `backend/tests/unit/infrastructure/test_api_routes.py`: add test asserting the same endpoint returns 503 when `vision.enabled=False` (NullVisionAdapter injected) — test MUST fail.
- [x] 1.3 **RED** — `backend/tests/unit/application/test_retry_batch_fix42.py` (new file): add test asserting `_retry_batch` calls `mark_retry_attempted` for each guía it processes — test MUST fail (flag never set in batch path).
- [x] 1.4 **GREEN** — `backend/src/reconciliation/infrastructure/api/schemas.py`: add `ReprocessBatchResponse(run_id: str, registro: str, count: int, task: str = "started")` Pydantic model.
- [x] 1.5 **GREEN** — `backend/src/reconciliation/infrastructure/api/routes.py`: implement `async def reprocess_registro(...)` with inner `async def _reprocess_batch(...)` driven by `asyncio.gather(..., return_exceptions=True)`; 503 guard for vision-off; DOES NOT call `mark_retry_attempted`. Wire 202 response with `ReprocessBatchResponse`. Tests 1.1 and 1.2 turn green.
- [x] 1.6 **GREEN** — `backend/src/reconciliation/infrastructure/api/routes.py`: in `_retry_batch`, add `review_service.mark_retry_attempted(eg.guia_id)` per guía (mirror per-guía path L934). Test 1.3 turns green.
- [x] 1.7 **COMMIT** — `feat(api): POST /registros/{registro}/reprocess 202 + fix #42 mark_retry_attempted in _retry_batch` — conventional commit, no AI attribution. Commit: 7891cd3.

## Phase 2 — Backend: F4 canonical reassignment extension
*REV-R25 | D9 | PR-A (continued)*

- [x] 2.1 **RED** — `backend/tests/unit/application/test_review_service_operator_assign.py` (new file): test that `apply_guia_line_edit` with `assign_material_canonical` sets `description_canonical`, `match_method="operator"`, `requires_review=True` on the stored line — test MUST fail (field absent).
- [x] 2.2 **RED** — same file: regression test asserting `ReconciliationRowResponse` with `match_method="operator"` serializes without raising (mirrors the grade_tolerant 500 regression).
- [x] 2.3 **GREEN** — `backend/src/reconciliation/infrastructure/api/schemas.py`: add optional `assign_material_canonical: str | None = None` to `GuiaLineEditRequest`; widen `match_method` Literal to include `"operator"`. Also widen `MatchMethod` in `domain/material_key.py`.
- [x] 2.4 **GREEN** — `backend/src/reconciliation/application/review_service.py`: extend `apply_guia_line_edit` to handle `assign_material_canonical` — when present, `model_copy` line with `description_canonical`, `match_method="operator"`, `requires_review=True`; re-reconcile; emit `manual_correction` audit event. Tests 2.1 and 2.2 turn green.
- [x] 2.5 **GREEN** — `backend/src/reconciliation/infrastructure/api/routes.py`: extend `edit_guia_line` route to pass `assign_material_canonical` from request DTO to `review_service.apply_guia_line_edit`.
- [x] 2.6 **COMMIT** — `feat(review): operator-assigned canonical correction (match_method="operator", requires_review=True)` — conventional commit. Commit: 3ddd80f.

## Phase 3 — Backend concurrency isolation tests
*REV-R20-S02, S03, S05, S06 | D1, D2 | PR-A (continued)*

- [x] 3.1 **RED** — `backend/tests/unit/application/test_reprocess_batch_concurrency.py` (new file): test that `_reprocess_batch` runs without `RuntimeError` (no nested event-loop). Test MUST fail if implementation is missing.
- [x] 3.2 **RED** — same file: test that a per-guía exception does NOT abort the batch (other guías run). Test MUST fail.
- [x] 3.3 **RED** — same file: test that `_reprocess_batch` never calls `mark_retry_attempted`. Test MUST fail.
- [x] 3.4 **GREEN** — tests 3.1–3.3 turn green (implementation already in place from Phase 1 commit 7891cd3).
- [x] 3.5 **COMMIT** — `test(api): concurrency + isolation + retry_attempted invariants for _reprocess_batch` — conventional commit. Commit: edf83f7.

## Phase 4 — Frontend: types, client, and F3 tab shell
*REV-R21, REV-R23 | D3, D7 | PR-B | frontend-visual (opus)*

- [x] 4.1 **RED** — `frontend/src/__tests__/features/ReviewPage.tabs.test.ts` (created): two tabs ("Reconciliación", "Pendientes por procesar"); default active "Reconciliación"; Pendientes badge = `erroredGuias.length`; ARIA tablist/tab/tabpanel/aria-selected. RED confirmed.
- [x] 4.2 **GREEN** — `frontend/src/api/types.ts`: added `ReprocessBatchResponse` interface (`run_id, registro, count, task` — matches MERGED backend DTO, NOT runId/total/recovered/failed). Added optional `assign_material_canonical?: string | null` to `GuiaLineEditRequest` (snake_case to mirror the backend body posted verbatim — see SA-2 note).
- [x] 4.3 **GREEN** — `frontend/src/api/client.ts`: implemented `reprocessRegistroBatch(runId, registro): Promise<ReprocessBatchResponse>` → `POST /runs/{runId}/registros/{encoded registro}/reprocess`.
- [x] 4.4 **GREEN** — `frontend/src/features/review/ReviewPage.vue`: added `activeTab` ref (`'reconciliacion' | 'pendientes'`); ARIA tab bar; Pendientes badge = `erroredCount`; Reconciliación tab (grid + unresolved panel, v-show keeps state) / Pendientes tab mounts `<PendientesPorProcesarTab>` (v-if). Test 4.1 green.
- [x] 4.5 **COMMIT** — `feat(review): tab bar Reconciliación / Pendientes with errored count badge`.

## Phase 5 — Frontend: F1 bulk reprocess (confirm + live polling + summary)
*REV-R21-S01..S04 | D3, D4 | PR-B | frontend-visual (opus)*

- [x] 5.1 **RED** — `frontend/src/__tests__/features/PendientesPorProcesarTab.test.ts` (created): per-Registro bulk button; confirm dialog shows call count (N guías = N llamadas); button disabled + "Procesando…" in-flight; `refetch` emitted on poll interval after 202; "N recuperadas / M fallaron" derived from list delta on terminal poll. RED confirmed.
- [x] 5.2 **GREEN** — `frontend/src/features/review/PendientesPorProcesarTab.vue` (created): hosts `<ErroredGuiasPanel>` for per-guía actions; per-Registro "Procesar todos con IA" button grouped by registro; ARIA confirm dialog (role=dialog, focus-to-confirm, Esc/backdrop cancel) with call count; on confirm calls `reprocessRegistroBatch`; in-flight state per registro; emits `refetch` on `setInterval` until the registro's remaining count stops shrinking; derives + shows "N recuperadas / M fallaron"; 503 → friendly vision-disabled message.
- [x] 5.3 **GREEN** — `frontend/src/features/review/ErroredGuiasPanel.vue`: UNCHANGED (per-guía retry/reprocess wiring reused as-is; bulk lives in the parent tab's group headers per D7 — see SA-2 deviation note). Tests 5.1 green.
- [x] 5.4 **COMMIT** — `feat(review): bulk per-registro AI reprocess confirm + live progress + N/M summary`.

## Phase 6 — Frontend: F2 SourcePages drop-in viewer
*REV-R22-S01, S02 | D6 | PR-C | frontend-visual (opus)*

- [ ] 6.1 **RED** — `frontend/src/features/review/__tests__/GuiaDrillDown.spec.ts`: test that Páginas cell renders `<SourcePages>` chips; `pageClick` emits with `{page, pages: guia.source_pages}`; expansion state unchanged. Test MUST fail.
- [ ] 6.2 **GREEN** — `frontend/src/features/review/GuiaDrillDown.vue`: replace plain `<span>{{ source_pages.join(',') }}</span>` Páginas cell with `<SourcePages :pages="guia.source_pages" :run-id="runId" @page-click="onPageClick" />`; emit `pageClick` upward. COLSPAN=11 UNCHANGED.
- [ ] 6.3 **GREEN** — `frontend/src/features/review/ReconciliationRow.vue`: forward `pageClick` from `<GuiaDrillDown>` to parent (mirror existing pattern). Test 6.1 turns green.
- [ ] 6.4 **COMMIT** — `feat(review): SourcePages chips in GuiaDrillDown bubble pageClick to PageSheetViewer` — conventional commit.

## Phase 7 — Frontend: F4 Acciones menu + Corregir manual dialog
*REV-R24, REV-R25 | D8, D9 | PR-C | frontend-visual (opus)*

- [ ] 7.1 **RED** — `frontend/src/features/review/__tests__/GuiaDrillDown.spec.ts`: add tests that `[Acciones]` menu renders three items (Reasignar, Reprocesar, Corregir manual); Reasignar emits `reassign`; Reprocesar calls existing `reprocessGuia`; Corregir manual opens dialog. Test MUST fail.
- [ ] 7.2 **RED** — same spec file: test that Corregir-manual dialog dropdown sources materials from `rows.filter(r => r.registro === guia.registro)` only (NOT other registros); cantidad is editable; submit calls PATCH with `assignMaterialCanonical` + cantidad; `requires_review` true in payload. Test MUST fail.
- [ ] 7.3 **GREEN** — `frontend/src/features/review/GuiaDrillDown.vue`: replace `[Reasignar]` with `[Acciones]` disclosure menu — Reasignar (existing `emit('reassign')`), Reprocesar (call `reprocessGuia`), Corregir manual (opens local dialog). Dialog: material dropdown from `props.tableRows.filter(r => r.registro === guia.registro)`; cantidad input; on submit call `editGuiaLine({ assignMaterialCanonical, cantidad })`. Tests 7.1 and 7.2 turn green.
- [ ] 7.4 **COMMIT** — `feat(review): [Acciones] menu + Corregir manual dialog (operator-assigned canonical)` — conventional commit.

## Phase 8 — SA-5 Playwright runtime validation
*REV-R21, REV-R22, REV-R23, REV-R24, REV-R25 | SA-5 MANDATORY | PR-C*

- [ ] 8.1 Start the app locally (`docker compose up` or `make dev`); upload a real PDF section containing registro 227 (or the nearest available errored-guía test section).
- [ ] 8.2 Navigate to ReviewPage → verify "Reconciliación" tab active on load; "Pendientes" badge shows correct errored count.
- [ ] 8.3 Click "Pendientes" tab → confirm `ErroredGuiasPanel` visible per registro; click "Procesar todos con IA" for one registro → confirm dialog shows call count → confirm → observe in-flight button disabled → wait for polling to shrink the list → verify "N recuperadas / M fallaron" summary.
- [ ] 8.4 Expand a reconciliation row that has guías with `source_pages` → click a page chip in the drill-down → confirm `PageSheetViewer` opens at that page with `viewerRowPages` = guía's own pages; expansion state unchanged.
- [ ] 8.5 Open `[Acciones]` menu in a drill-down → Reasignar opens reassign dialog (unchanged) → Reprocesar fires per-guía endpoint → Corregir manual: select declared material from dropdown (only that registro's materials present); enter cantidad; submit → table updates; `requires_review` badge visible on corrected line.
- [ ] 8.6 **COMMIT** — `test(e2e): SA-5 Playwright runtime validation — tabs, bulk reprocess, viewer, acciones/corregir` — conventional commit.

---

## Dependency Graph

```
1 → 2 → 3 → 4 → 5 → 6 → 7 → 8
        ↑
    (concurrency tests reuse Phase 1 implementation)
```

Phases 1–3 are sequential (each builds on the previous). Phases 4–5 are sequential but independent from 6–7. Phases 6 and 7 are sequential. Phase 8 depends on all of 4–7.

Parallel opportunity: Phases 4–5 (frontend F1+F3) and phases 6–7 (frontend F2+F4) CAN run in parallel writers IF PR-A is merged first and isolated worktrees are approved — orchestrator decides.

## REV / Decision Mapping

| Work-unit | REQ IDs | Design Decisions |
|-----------|---------|-----------------|
| Phase 1 | REV-R20, REV-R26 | D1, D2, D4, D5 |
| Phase 2 | REV-R25 | D9 |
| Phase 3 | REV-R20-S02/S03/S05/S06 | D1, D2 |
| Phase 4 | REV-R21, REV-R23 | D3, D7 |
| Phase 5 | REV-R21-S01..S04 | D3, D4 |
| Phase 6 | REV-R22 | D6 |
| Phase 7 | REV-R24, REV-R25 | D8, D9 |
| Phase 8 | REV-R21..R25 (SA-5) | all |

## Frontend-Visual Work Units (route to opus at apply)
Phases 4, 5, 6, 7, 8 are frontend-visual — apply with `frontend-design` skill + opus model.
Phases 1, 2, 3 are backend-only — apply with sonnet model.
