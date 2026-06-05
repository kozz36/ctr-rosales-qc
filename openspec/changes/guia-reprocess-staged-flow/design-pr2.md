# Design: guia-reprocess-staged-flow — PR #2 (REINTENTAR deterministic recovery)

## Technical Approach
Approach B (locked): a NEW `application/reprocess_service.py::ReprocessService` orchestrates adapter calls (render → decode hashqr → SUNAT fetch → normalize) and hands the recovered guía to `ReviewService.add_recovered_guia` — the SOLE mutation hook. ReviewService keeps SRP (value-edit coordinator); ReprocessService is the adapter orchestrator. Deterministic, NO vision. Recovered lines `requires_review=True`; date = SUNAT `fecha_entrega` via the existing R9b floor. The recovered guía is normalized IDENTICALLY to a first-pass guía by reusing the pipeline's `MaterialKeyResolver` + `apply_delivery_floor`. No pipeline re-run.

## Architecture Decisions (confirmed vs real code)

| # | Decision | Choice | Rationale / rejected |
|---|----------|--------|----------------------|
| 1 | ReprocessService deps | ctor: `doc_source: DocumentSourcePort`, `identity: IdentityExtractionPort`, `sunat: SunatGreFetchPort`, `key_resolver: MaterialKeyResolver`, `review_service: ReviewService`, `config: AppConfig`. `apply_retry(guia_id) -> RetryResult`. | Ports-only — no concrete adapter import in `application/`. Rejected injecting a separate render port (see #2). `review_service` ref makes `add_recovered_guia` the single entry point. |
| 2 | Render port | REUSE existing `DocumentSourcePort.render_page(idx, dpi=200)` (ports.py L43; impl `PdfStructureAdapter`). NO new PageRenderPort. DPI **300** for re-decode. | A render abstraction ALREADY exists behind a Protocol — defining `PageRenderPort` would duplicate it. `_QR_DPI=200` was the original miss; 300 improves small-QR decode without the 400 memory/time cost on the 493-page PDF. Refines locked-decision #2 (no new port needed). |
| 3 | Normalization parity (correctness crux) | Build SUNAT→`MaterialLine` exactly as pipeline `_apply_sunat_result` (pipeline.py L1216-1226): `description_canonical=descripcion` (placeholder), `unidad=_normalize_sunat_unit(item.unidad)` filtered to domain set, `confidence=1.0`, `source_page=first errored page`, `requires_review=True`. Then run `key_resolver.resolve(desc_raw, unidad)` → set `description_canonical=key.group_token`, `match_method`, `requires_review = True OR key.requires_review` (mirror `_norm_line` L1553). Date: `apply_delivery_floor(None, fecha_entrega)` and persist `fecha_entrega` on the guía. | A recovered guía MUST reconcile identically to a pipelined one. Reusing the SAME `_normalize_sunat_unit`, `MaterialKeyResolver.resolve`, and `apply_delivery_floor` guarantees the `(registro, group_token, unidad)` key matches. `_normalize_sunat_unit` is module-level (importable); extract the MaterialLine build into a shared helper to avoid drift. |
| 4 | `add_recovered_guia` | `add_recovered_guia(guia: GuiaDeRemision) -> list[ReconciliationRow]` on ReviewService: append to `_guias`, drop matching `guia_id` from `_errored_guias`, re-reconcile via existing `_reconciler.reconcile(_declared, _guias, delivery_dates=_delivery_dates())`, emit `recovered_guia` sidecar event, `_persist()`. Idempotent: if `guia_id` already in `_guias` (not in `_errored_guias`) → return current rows, no event. | Mirrors `apply_reassignment` mutate→reconcile→persist (L398-413). `_delivery_dates()` already reads `fecha_entrega` off guías → floor/ceiling bracket survives. Idempotency guards double-click + restart replay. |
| 5 | `recovered_guia` sidecar replay | New `EditEvent.kind="recovered_guia"`, `new_value=guia.model_dump(mode="json")`. In `restore_from_sidecar` replay loop add a branch: `GuiaDeRemision.model_validate(new_value)` → `service.add_recovered_guia(...)` (tolerate errors like the others). | Reuse `review_sidecar.json` (locked). On restart, build_review_service hydrates `_errored_guias` from cache (still errored) THEN replay moves recovered ones back. The guía model is already fully normalized at persist time, so replay needs NO re-fetch/re-decode — deterministic + air-gap-safe on restart. |
| 6 | `decode_hashqr_url` Protocol promotion | Add `decode_hashqr_url(self, image: bytes, page_idx: int \| None = None) -> str \| None` to `IdentityExtractionPort`. Concrete adapter already satisfies it (qr_barcode.py L259). REMOVE the `hasattr(self._identity, "decode_hashqr_url")` duck-type guard at pipeline.py:510 (now contractual). | ReprocessService depends on the Protocol, not the concrete adapter. Adapter already conforms → no impl change. Removing the guard is a small cleanup; keep the `if not decoded` logic. |
| 7 | API | `POST /runs/{run_id}/errored-guias/{guia_id}/retry` (SYNC, single) → `RetryGuiaResponse{run_id, recovered: bool, rows: [...], errored_guias: [...]}`. Per-Registro: `POST /runs/{run_id}/registros/{registro}/retry` (background via `BackgroundTasks`) → `RetryBatchResponse{run_id, task: "accepted", count}`; client re-polls `GET /table`. | Single retry render+decode+SUNAT for 1 guía is fast → sync, returns updated rows + remaining errored. reg227's 24× batch risks endpoint timeout (explore risk-5) → background, mirroring `_run_pipeline_background`. ReprocessService built per-call in the route from the run-registry `ctx`/`config` (rebuild doc_source/identity/sunat/key_resolver like `build_pipeline`). |
| 8 | Frontend | REINTENTAR `<button>` per errored-guía item in `ErroredGuiasPanel.vue` + a per-Registro REINTENTAR (grouped). `client.ts::retryGuia(runId, guiaId)` + `retryRegistro`. Loading/disabled per-item state (`retryingId` ref); on success invalidate the `GET /table` TanStack query (refetch rows + errored_guias). Reflect `retry_attempted` (gates PR#3 button): if `retry_attempted && still errored` → disable REINTENTAR, show "SUNAT no disponible". | PR#1 left the panel read-only. Reuse the reassign client/response pattern (`{run_id, rows}` + errored_guias). Query invalidation keeps grid + panel consistent without manual state merge. |

## Data Flow
```
POST /errored-guias/{id}/retry
  └─ route: rebuild ReprocessService(ctx, config) ─ ports only
       └─ apply_retry(guia_id)
            render_page(page,300) → decode_hashqr_url → SunatGreFetchPort.fetch(url)
              ├─ url None / fetch None → mark retry_attempted=True, stay errored ─┐
              └─ OfficialGre.lines → _normalize_sunat_unit → MaterialLine(requires_review=True)
                   → key_resolver.resolve → group_token / match_method
                   → apply_delivery_floor(None, fecha_entrega); persist fecha_entrega
                   → ReviewService.add_recovered_guia(guia)
                        append _guias; drop _errored_guias; reconcile(_delivery_dates());
                        emit recovered_guia sidecar event; _persist()
       ◄─ RetryGuiaResponse{recovered, rows, errored_guias}
restart ─ build_review_service ─ hydrate _errored_guias(cache) ─ restore_from_sidecar
            replay recovered_guia → add_recovered_guia (model already normalized; no re-fetch)
```

## File Changes
| File | Action | Description |
|------|--------|-------------|
| `application/reprocess_service.py` | Create | `ReprocessService` + `RetryResult`; ports-only orchestration; shared `_build_recovered_guia` helper |
| `application/review_service.py` | Modify | `add_recovered_guia`; `recovered_guia` EditEvent kind; replay branch in `restore_from_sidecar` |
| `domain/ports.py` | Modify | Add `decode_hashqr_url` to `IdentityExtractionPort` |
| `application/pipeline.py` L510 | Modify | Remove `hasattr` duck-type guard (contractual now) |
| `infrastructure/container.py` | Modify | `build_reprocess_service(ctx, config, review_service)` rebuilding doc_source/identity/sunat/key_resolver |
| `infrastructure/api/routes.py` | Modify | `+retry` (sync) + per-Registro `+retry` (background) endpoints |
| `infrastructure/api/schemas.py` | Modify | `RetryGuiaResponse`, `RetryBatchResponse` |
| `frontend/src/api/client.ts` + `types.ts` | Modify | `retryGuia` / `retryRegistro` + response types |
| `frontend/src/features/review/ErroredGuiasPanel.vue` | Modify | REINTENTAR buttons + loading/disabled + retry_attempted reflection |

## Interfaces
```python
class IdentityExtractionPort(Protocol):
    def decode_identity(self, image: bytes, page_idx: int | None = None) -> GuiaIdentity | None: ...
    def decode_hashqr_url(self, image: bytes, page_idx: int | None = None) -> str | None: ...

class ReprocessService:
    def apply_retry(self, guia_id: str) -> RetryResult: ...  # RetryResult{recovered, rows, errored_guias}

class ReviewService:
    def add_recovered_guia(self, guia: GuiaDeRemision) -> list[ReconciliationRow]: ...
```

## Testing Strategy (Strict-TDD, failing-first)
| Layer | Test | Approach |
|-------|------|----------|
| Unit | ReprocessService transient success | FAKE doc_source(render→bytes), FAKE identity(decode_hashqr_url→url), FAKE sunat(fetch→OfficialGre); assert guía recovered, lines `requires_review=True`, key == pipeline key |
| Unit | ReprocessService no-url / sunat-None | FAKE returns None → stay errored, `retry_attempted=True`, no mutation |
| Unit | `add_recovered_guia` correctness | recovered guía re-reconciles to MATCH/expected; additive (other rows unchanged); removed from errored |
| Unit | `add_recovered_guia` idempotency | re-add same guia_id → no dup, no extra event |
| Integration | restart replay | sidecar with `recovered_guia` event → restore_from_sidecar moves it out of errored, no re-fetch |
| API | sync retry endpoint | returns recovered rows + remaining errored_guias; 404 unknown run/guia |
| Frontend (vitest) | REINTENTAR button | click → calls retryGuia, loading/disabled, query invalidated; disabled+message when retry_attempted & errored |
| E2E (SA-5, REQUIRED) | Playwright | upload → review → click REINTENTAR on a transient errored guía → guía leaves panel, row reconciles, 0 console errors |

## Migration / Rollout
No migration. Pure additive: new endpoints/service unused until button clicked; `recovered_guia` events backward-compatible (old caches lack them). Rollback = revert PR; recovered guías are `requires_review` and correctable via reassign/edit.

## Invariants honored
Domain pure; ReprocessService + ReviewService depend ONLY on ports + config (zero concrete adapter import in `application/`); heavy deps (fitz/pyzbar/zxing/requests) lazy in adapters; grouping key `(registro, material_canonical, unidad)` — fecha never grouping; units never converted (`_normalize_sunat_unit` maps, never converts); reconciliation is the validation gate (recovered lines `requires_review`, never auto-accepted); input PDF read-only (render_page is a read-only fitz open).

## Open Questions
None blocking. NOTE (SA-2): locked-decision #2 said "define a minimal PageRenderPort if render is direct" — real code already has `DocumentSourcePort.render_page` behind a Protocol, so NO new port is defined; ReprocessService reuses it. This satisfies the ports-only intent without duplication.
