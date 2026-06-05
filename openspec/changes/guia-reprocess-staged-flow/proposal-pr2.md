# Proposal: guia-reprocess-staged-flow — PR #2 (REINTENTAR)

## Intent

Operators see errored guías (0 material lines) read-only after PR #1. The **TRANSIENT** class
(keystone analysis: reg232) is recoverable: the `hashqr_url` the original 200-DPI pass missed
re-decodes at higher DPI, then SUNAT supplies authoritative lines. PR #2 makes REINTENTAR
recover these **deterministically (NO vision)**, shrinking the error set without a pipeline re-run.

## Scope

### In Scope
- `application/reprocess_service.py` (NEW `ReprocessService`): adapter orchestration (render → decode → SUNAT → normalize → recover). Depends ONLY on ports + config (Approach B).
- `ReviewService.add_recovered_guia(guia) -> list[ReconciliationRow]`: single pure mutation hook (append to `_guias`, drop from `_errored_guias`, re-reconcile via existing `_delivery_dates()` path, persist).
- Endpoint `POST /api/v1/runs/{run_id}/errored-guias/{guia_id}/retry`.
- Frontend: wire REINTENTAR button on each `ErroredGuiasPanel.vue` entry (PR #1 left it read-only).
- The 5-step sequence: re-render `source_pages` from `ctx.pdf_path` @ 300–400 DPI → `decode_identity` + `decode_hashqr_url` → `SunatGreFetchPort.fetch` → normalize lines (MaterialNormalizer + MaterialKeyResolver, date = SUNAT `fecha_entrega` via R9b floor, lines `requires_review=True`) → `add_recovered_guia`. On no-URL/SUNAT-fail: stay errored, set `retry_attempted=True`.

### Out of Scope (explicit)
- Vision / `VisionLLMPort.read_material_table` / "Reprocesar con IA" (PR #3).
- Any vision date call on the recovered guía (deterministic SUNAT-date mode only).
- Transient/systematic PRE-classification (implicit in REINTENTAR outcome; explore Area 5).
- Rollback/undo of a recovery (see OQ-D).

## Capabilities

### New Capabilities
None — REINTENTAR extends existing review behavior; no new capability spec.

### Modified Capabilities
- `review`: add the REINTENTAR recovery requirement (errored guía → re-decode + SUNAT → recovered guía flagged `requires_review`; failure sets `retry_attempted`).

## Approach

Approach B (locked). `ReprocessService` is the adapter orchestrator (hexagonal application
service); `ReviewService` keeps SRP — gains only the pure `add_recovered_guia` mutation. The
recovery replicates the pipeline SUNAT→GuiaDeRemision path (`pipeline.py` ~1216, `MaterialLine`
with `confidence=1.0`, then normalizer) WITHOUT re-running stages. `decode_hashqr_url` currently
lives on the concrete adapter only (accessed via `hasattr` in `pipeline.py:510`) — PR #2 should
promote it to `IdentityExtractionPort` so `ReprocessService` depends on the Protocol, not a duck-type.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `application/reprocess_service.py` | New | `ReprocessService` — render+decode+SUNAT+normalize orchestration |
| `application/review_service.py` | Modified | `add_recovered_guia` pure mutation hook |
| `domain/ports.py` (`IdentityExtractionPort`) | Modified | Promote `decode_hashqr_url` to the Protocol |
| `adapters/identity/qr_barcode.py` | Reference | `decode_hashqr_url` already exists (L259) |
| `adapters/sunat/` (`SunatDescargaqrAdapter`) | Reference | `fetch(hashqr_url)` reused |
| `adapters/.../PdfStructureAdapter` | Reference | `render_page(idx, dpi)` re-render @ higher DPI (read-only) |
| `infrastructure/container.py` | Modified | `build_reprocess_service` wiring (identity + sunat + doc_source + key_resolver + ctx) |
| `infrastructure/api/routes.py` | Modified | new `retry` endpoint |
| `infrastructure/api/schemas.py` | Modified | `RetryGuiaResponse` (updated rows + errored_guias) |
| `frontend/.../ErroredGuiasPanel.vue` + api client/types | Modified | REINTENTAR action wired |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| In-memory state divergence (two services mutate `_guias`) | Med | `add_recovered_guia` is the SOLE entry point into ReviewService's guías list |
| Recovered guía not normalized before add (re-reconcile assumes normalized) | Med | ReprocessService runs MaterialKeyResolver + date floor INLINE before `add_recovered_guia` |
| Sync endpoint latency: high-DPI render + decode + SUNAT (esp. per-Registro batch, reg227 24×) | Med | OQ-B — decide sync vs background-task |
| Recovery state lost on restart (recovered guía + shrunk error set) | Med | OQ-A — sidecar event replay |
| Re-reconcile correctness regression (delivery floor/ceiling) | Low | Reuse existing `_delivery_dates()` map; `fecha_entrega` persists on `GuiaDeRemision` |

## Rollback Plan

Revert the PR. Pure additive: `add_recovered_guia` unused, `ReprocessService` unwired, endpoint
removed, button reverts to read-only. No cache/schema migration. Recovered guías are flagged
`requires_review` — existing reassign/edit can correct any wrongly-recovered guía.

## Open Questions (structured — flag for orchestrator/user; do NOT invent per SA-2)

- **OQ-A (sidecar persistence)**: must REINTENTAR survive restart? *Recommend*: reuse
  `review_sidecar.json` with a new `recovered_guia` event type replayed like reassignment
  (consistent with existing MERGE `_persist` + `restore_from_sidecar` model). Flag.
- **OQ-B (sync vs background)**: high-DPI render + decode + SUNAT in a sync endpoint risks
  timeout (explore Risk 5). *Recommend*: background-task pattern mirroring the initial run for
  per-Registro batch; sync acceptable for single-guía. Flag.
- **OQ-C (per-guía vs per-Registro)**: mockup had a per-Registro "Error en páginas X [REINTENTAR]"
  group row; data model is per-guía. *Recommend*: per-guía action as the primitive, per-Registro
  as a batch loop over it. Flag (couples with OQ-B).
- **OQ-D (rollback)**: undo of a recovery in scope? *Recommend*: OUT of scope — recovered guía is
  `requires_review`; reassign/edit corrects it. Flag.
- **OQ-E (date on recovery)**: confirm recovered guía date = SUNAT `fecha_entrega` (R9b floor,
  deterministic, NO vision). *Recommend*: YES — consistent with `vision.enabled=false` mode; no
  conflict with reception-date-authority (declared side stays the digital Protocolo parse; guía
  date as delivery floor is safe because it is flagged `requires_review`). Flag if reviewer disagrees.

## Dependencies

PR #1 (MERGED): `errored_guias` read-path live; `ErroredGuia.retry_attempted` field exists;
`ErroredGuiasPanel.vue` rendering errored guías read-only. SUNAT must be enabled (`sunat.enabled`).

## Success Criteria

- [ ] REINTENTAR on a TRANSIENT errored guía recovers it: re-decode finds `hashqr_url`, SUNAT
      returns lines, guía added (flagged `requires_review`), removed from errored set, rows re-reconciled.
- [ ] No-URL or SUNAT-fail leaves the guía errored with `retry_attempted=True` (gates PR #3 button).
- [ ] `ReprocessService` imports ZERO concrete adapters; domain stays pure; heavy deps lazy.
- [ ] Recovered guía never auto-accepted (always `requires_review`); grouping key + units untouched.
- [ ] Input PDF read-only (re-render reads `ctx.pdf_path`).
