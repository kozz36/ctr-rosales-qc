# Delta Spec: guia-reprocess-staged-flow — PR #2 (REINTENTAR Recovery)

**Change**: guia-reprocess-staged-flow
**Slice**: PR #2 — REINTENTAR: errored-guía recovery via higher-DPI re-decode + SUNAT
**Capability modified**: `review` (extends REV-E01..REV-E05 from the PR #1 foundation spec)
**Depends on**: PR #1 MERGED (`errored_guias` read-path live; `ErroredGuia.retry_attempted` exists; `ErroredGuiasPanel.vue` read-only)
**Date**: 2026-06-05

---

## Scope of This Delta

This spec describes WHAT MUST BE TRUE after PR #2 is applied.
It does NOT cover design or implementation choices (those belong in the design artifact).

PR #2 adds exactly one new capability surface to the review domain: **a single-guía REINTENTAR
primitive** that re-renders source pages at higher DPI, re-decodes the hashqr_url, fetches
SUNAT, normalises the result, and recovers the guía into the reconciliation state. A
per-Registro batch is a loop over that primitive running as a background task.

PR #3 (vision / Reprocesar-con-IA) and any rollback/undo are explicitly OUT OF SCOPE here.

---

## Added Requirements

### REV-R01 — IdentityExtractionPort exposes decode_hashqr_url

`IdentityExtractionPort` in `domain/ports.py` MUST declare a `decode_hashqr_url` method
that accepts rendered page bytes and a page index and returns the URL-variant QR payload
(`str | None`).

The concrete `QrBarcodeExtractionAdapter` already implements this method; PR #2 promotes the
signature to the Protocol so that `ReprocessService` depends only on the port, not on a
duck-type `hasattr` check.

Domain stays pure: the Protocol is an abstract declaration only (no imports, no logic).

#### Scenario: IdentityExtractionPort.decode_hashqr_url is a callable Protocol method

- GIVEN the `IdentityExtractionPort` Protocol
- WHEN a conforming adapter is passed where the port is expected
- THEN static type-checking (`mypy --strict`) accepts it without error
- AND `QrBarcodeExtractionAdapter` satisfies the Protocol without structural change

---

### REV-R02 — ReprocessService depends ONLY on ports and config

A new application service `application/reprocess_service.py` MUST exist that:

- Accepts `IdentityExtractionPort`, a PDF-render port (read-only access to `ctx.pdf_path`),
  `SunatGreFetchPort`, a material-normalizer, a `MaterialKeyResolver`, and config as
  constructor arguments (all injected — zero concrete adapter references at module level).
- Holds a reference to `ReviewService` exclusively to call `add_recovered_guia`.
- Exposes `retry_guia(run_id, guia_id) -> RetryResult` as the single-guía primitive.
- Exposes `retry_registro(run_id, registro) -> list[RetryResult]` as the per-Registro batch.

`ReprocessService` MUST NOT import any concrete adapter, framework SDK (`fitz`, `anthropic`,
`openai`, `pyzbar`, `zxing-cpp`, `openpyxl`), or IO library at module level.
Heavy dependencies MUST remain lazy-imported inside the adapter methods that use them.

#### Scenario: ReprocessService can be instantiated without heavy deps installed

- GIVEN the Python environment has `fitz`, `anthropic`, `openai`, `pyzbar`, and `zxing-cpp`
  NOT installed
- WHEN `from application.reprocess_service import ReprocessService` is executed
- THEN no `ImportError` is raised
- AND the module loads successfully

---

### REV-R03 — REINTENTAR single-guía — transient success path

When `ReprocessService.retry_guia` is invoked for an errored guía that has a decodable
hashqr_url at higher DPI and a non-empty SUNAT response:

1. The service MUST re-render each page in `errored_guia.source_pages` from `ctx.pdf_path`
   at a DPI higher than the original pipeline pass (the exact DPI is a design choice; the
   spec only requires it be configurable and above the original 200 DPI).
2. It MUST call `IdentityExtractionPort.decode_identity` and
   `IdentityExtractionPort.decode_hashqr_url` on the re-rendered images.
3. It MUST call `SunatGreFetchPort.fetch(hashqr_url)` to obtain `OfficialGre` with line items.
4. If SUNAT returns at least one line item, it MUST:
   a. Build a `GuiaDeRemision` with `fecha` set to the SUNAT `fecha_entrega` (R9b floor;
      deterministic; NO vision call is made in this path).
   b. Set `requires_review=True` on every `MaterialLine` of the recovered guía.
   c. Run the material normalizer and `MaterialKeyResolver` inline on the recovered guía
      BEFORE passing it to `ReviewService`.
   d. Call `ReviewService.add_recovered_guia(guia)` — the sole mutation entry point.
   e. Set `ErroredGuia.retry_attempted = True` on the entry.
5. After `add_recovered_guia`:
   a. The guía MUST be removed from `_errored_guias`.
   b. `ReviewService` MUST re-reconcile via `_reconciler.reconcile(declared, guias,
      delivery_dates=_delivery_dates())` using the existing path.
   c. The updated reconciliation rows MUST reflect the recovered material lines.
6. The source PDF MUST NOT be opened for write or modified at any point.

#### Scenario: REINTENTAR recovers a TRANSIENT guía — rows updated, error shrinks

- GIVEN a run with an errored guía `guia_id="T001-0001"` (registro=232, source_pages=[4,5])
- AND the guía has `retry_attempted=False`
- AND at 350 DPI the QR on page 4 decodes to a valid hashqr_url
- AND SUNAT returns 2 material lines for that hashqr_url
- WHEN `POST /api/v1/runs/{run_id}/errored-guias/T001-0001/retry` is called
- THEN the response is `200 OK`
- AND `errored_guias` in the response no longer contains `T001-0001`
- AND the `rows` list includes reconciliation rows reflecting the recovered material
- AND every recovered `MaterialLine` has `requires_review=True`
- AND the guía `fecha` equals its SUNAT `fecha_entrega` (no vision date call)
- AND no other registro's rows are altered
- AND the input PDF is unchanged

---

### REV-R04 — REINTENTAR single-guía — failure path

When re-decode yields no hashqr_url, OR SUNAT returns zero lines:

1. The service MUST NOT add any partial or garbage `GuiaDeRemision` to `ReviewService`.
2. The errored guía MUST remain in `_errored_guias`.
3. `ErroredGuia.retry_attempted` MUST be set to `True` (gates the PR #3 Reprocesar-con-IA
   button in the frontend).
4. The reconciliation rows MUST be unchanged.
5. The response MUST communicate the failure reason (no-hashqr-url vs. sunat-empty) so the
   frontend can display a meaningful status.

#### Scenario: REINTENTAR — no hashqr_url found at higher DPI — guía stays errored

- GIVEN a run with an errored guía `guia_id="T002-0002"` (registro=227, source_pages=[7])
- AND re-rendering at higher DPI still yields no URL-variant QR payload
- WHEN `POST /api/v1/runs/{run_id}/errored-guias/T002-0002/retry` is called
- THEN the response indicates failure (e.g. `{"recovered": false, "reason": "no_hashqr_url"}`)
- AND `errored_guias` still contains `T002-0002`
- AND `T002-0002.retry_attempted` is `True`
- AND no new guía is present in the reconciliation rows
- AND no existing reconciliation row is altered

#### Scenario: REINTENTAR — hashqr_url decoded but SUNAT returns empty lines — guía stays errored

- GIVEN a run with an errored guía `guia_id="T003-0003"` (registro=229, source_pages=[9])
- AND re-rendering decodes a valid hashqr_url
- AND SUNAT returns zero material lines for that url
- WHEN `POST /api/v1/runs/{run_id}/errored-guias/T003-0003/retry` is called
- THEN the response indicates failure (e.g. `{"recovered": false, "reason": "sunat_empty"}`)
- AND `errored_guias` still contains `T003-0003`
- AND `T003-0003.retry_attempted` is `True`
- AND no new guía appears in reconciliation rows
- AND no existing reconciliation row is altered

---

### REV-R05 — add_recovered_guia is the sole ReviewService mutation hook

`ReviewService.add_recovered_guia(guia: GuiaDeRemision) -> list[ReconciliationRow]` MUST:

- Append the normalized, validated `GuiaDeRemision` to `self._guias`.
- Remove the corresponding `ErroredGuia` entry from `self._errored_guias` (match by `guia_id`).
- Call `_reconciler.reconcile(self._declared, self._guias, delivery_dates=self._delivery_dates())`
  using the existing re-reconcile path (including existing floor/ceiling logic via persisted
  `fecha_entrega` on `GuiaDeRemision`).
- Call `self._persist()` to write the updated state to the sidecar.
- Return the new full row list.

`add_recovered_guia` MUST NOT:
- Alter any row that does not belong to the recovered guía's `(registro, canonical_key, unidad)` group.
- Modify, re-normalize, or re-key any existing (non-recovered) `GuiaDeRemision` in `_guias`.
- Bypass or short-circuit the existing `_delivery_dates()` path (the floor/ceiling bracket
  on `fecha_entrega` MUST remain active for the recovered guía because `fecha_entrega` is
  persisted on `GuiaDeRemision`).
- Accept a guía whose `MaterialLine` items have `requires_review=False` from the recovery
  path; this invariant is enforced by the service before calling `add_recovered_guia`.

#### Scenario: add_recovered_guia is additive — unrelated rows untouched

- GIVEN a run with 3 reconciliation rows (registros 230, 231, 232) all with status MATCH
- AND an errored guía for registro 232 is being recovered
- WHEN `add_recovered_guia` is called with the recovered guía
- THEN rows for registros 230 and 231 retain their exact prior status, delta, and qty values
- AND the registro 232 row reflects the recovered material
- AND no row has `requires_review` cleared from `True` to `False`

#### Scenario: add_recovered_guia — existing floor/ceiling bracket active for recovered guía

- GIVEN a recovered guía for registro 232 with `fecha_entrega = 2026-05-28`
- AND the registro 232 Protocolo declared date = 2026-05-28
- WHEN `add_recovered_guia` is called
- THEN the reconcile pass runs with `delivery_dates` that includes the recovered guía's
  `fecha_entrega`
- AND date-floor (R9b) and date-ceiling (R9c) logic applies to the recovered guía as to any
  other guía
- AND no floor/ceiling regression affects pre-existing guías

---

### REV-R06 — Persistence: recovered_guia event in review_sidecar.json

When a REINTENTAR succeeds, `ReviewService._persist()` MUST write a `recovered_guia` event
to `review_sidecar.json` structured the same way as existing sidecar events (e.g.
reassignment). The event MUST carry sufficient information to reconstruct the recovered
`GuiaDeRemision` — at minimum: `guia_id`, `registro`, `source_pages`, all material lines
with `requires_review=True`, and `fecha` (SUNAT `fecha_entrega`).

`ReviewService.restore_from_sidecar` MUST replay `recovered_guia` events by:
- Re-adding the recovered `GuiaDeRemision` to `_guias`.
- Removing the matching `ErroredGuia` from `_errored_guias`.

After a full replay the live state MUST be identical to the state before restart.

#### Scenario: restart after successful REINTENTAR — recovery survives

- GIVEN a run where `T001-0001` (registro=232) was recovered via REINTENTAR
- AND the `recovered_guia` event was written to `review_sidecar.json`
- WHEN the application is restarted and `build_review_service` + `restore_from_sidecar`
  are executed for that run
- THEN `review_service.errored_guias` does NOT contain `T001-0001`
- AND `review_service._guias` contains the recovered `GuiaDeRemision` for `T001-0001`
- AND `GET /runs/{run_id}/table` returns reconciliation rows that include the recovered material
- AND `retry_attempted` on the surviving errored guías (if any) retains its persisted value

#### Scenario: restart with no sidecar events — state unchanged

- GIVEN a run with two errored guías and no sidecar events
- WHEN the application is restarted and `restore_from_sidecar` is called
- THEN `review_service.errored_guias` returns the same two entries as before restart
- AND no `add_recovered_guia` or re-reconcile is triggered
- AND `GET /runs/{run_id}/table` returns the same rows as before restart

---

### REV-R07 — Per-Registro batch retry runs as a background task

`ReprocessService.retry_registro(run_id, registro)` MUST:

- Iterate over every `ErroredGuia` in `_errored_guias` whose `registro` matches.
- Call `retry_guia` for each, in sequence, stopping individual failures without aborting
  the remaining guías in the batch.
- Execute as a `BackgroundTask` (FastAPI or equivalent) so the endpoint returns immediately
  with a `202 Accepted` and a status handle; the caller polls for completion or receives a
  callback.
- Report per-guía outcomes (recovered / failed / reason) in the task result.

The batch endpoint MUST NOT block the HTTP response for the full render+SUNAT latency of
all guías. A per-Registro batch of 24 guías (reg227 worst-case) MUST NOT time out the
HTTP layer.

#### Scenario: per-Registro batch retry — partial success

- GIVEN a run with 3 errored guías for registro 227 (T001, T002, T003)
- AND T001 and T003 decode at higher DPI; T002 does not
- WHEN `POST /api/v1/runs/{run_id}/registros/227/retry` is called
- THEN the response is `202 Accepted` immediately (no blocking)
- AND when the background task completes, T001 and T003 are removed from `errored_guias`
- AND T002 remains in `errored_guias` with `retry_attempted=True`
- AND reconciliation rows for registro 227 include the material from T001 and T003
- AND rows for all other registros are unaffected

---

### REV-R08 — REINTENTAR API endpoint

`POST /api/v1/runs/{run_id}/errored-guias/{guia_id}/retry` MUST:

- Return `200 OK` with `RetryGuiaResponse` on both success and failure.
  `RetryGuiaResponse` MUST include: `recovered: bool`, `reason: str | None`,
  `rows: list[ReconciliationRowResponse]`, `errored_guias: list[ErroredGuiaResponse]`.
- Return `404 Not Found` if `run_id` is unknown or `guia_id` is not in `errored_guias`.
- Return `503 Service Unavailable` if `sunat.enabled=False` (SUNAT not configured; REINTENTAR
  cannot proceed without SUNAT).
- NOT modify the input PDF.
- Be idempotent from the state perspective: calling retry on an already-recovered guía
  (no longer in `errored_guias`) returns `404`.

#### Scenario: retry endpoint — 404 for unknown guia_id

- GIVEN a valid `run_id` with no errored guía having `guia_id="UNKNOWN-0001"`
- WHEN `POST /api/v1/runs/{run_id}/errored-guias/UNKNOWN-0001/retry` is called
- THEN the response is `404 Not Found`

#### Scenario: retry endpoint — 503 when sunat.enabled=False

- GIVEN `sunat.enabled=False` in the active config
- WHEN `POST /api/v1/runs/{run_id}/errored-guias/T001-0001/retry` is called
- THEN the response is `503 Service Unavailable`

---

### REV-R09 — Frontend: REINTENTAR button wired in ErroredGuiasPanel

The existing read-only `ErroredGuiasPanel.vue` (PR #1) MUST gain a REINTENTAR button per
errored-guía entry that:

- Is enabled only when `!entry.retry_attempted` (already attempted → button disabled or
  replaced with a status indicator).
- On click, calls `POST /api/v1/runs/{run_id}/errored-guias/{guia_id}/retry` and:
  - Shows a loading state during the request.
  - On success (`recovered: true`): invalidates the `GET /table` TanStack Query cache so the
    grid re-renders with updated rows and the panel refreshes (entry removed or marked recovered).
  - On failure (`recovered: false`): shows the `reason` as a user-readable status string
    ("No se encontró código QR" / "SUNAT no devolvió materiales") and disables the button.
- The per-Registro batch endpoint (if wired in this PR) MUST also follow the
  `202 Accepted` → poll-or-event model rather than a blocking UI action.

`retry_attempted: boolean` MUST be typed on `ErroredGuiaResponse` in `types.ts` (it exists
from PR #1's domain model; this PR makes the frontend READ it to control button state).

#### Scenario: REINTENTAR button — disabled after retry_attempted=true

- GIVEN the table response includes an errored guía with `retry_attempted: true`
- WHEN `ErroredGuiasPanel` renders
- THEN the REINTENTAR button for that entry is disabled (or replaced with a status indicator)
- AND no retry API call is triggered on interaction

#### Scenario: REINTENTAR button — success flow refreshes table

- GIVEN a guía `T001-0001` in the panel with `retry_attempted: false`
- WHEN the user clicks REINTENTAR
- AND the API returns `{"recovered": true, ...}`
- THEN the panel no longer shows `T001-0001`
- AND the reconciliation grid updates to include the recovered material rows

#### Scenario: REINTENTAR button — failure flow shows reason

- GIVEN a guía `T002-0002` with `retry_attempted: false`
- WHEN the user clicks REINTENTAR
- AND the API returns `{"recovered": false, "reason": "no_hashqr_url"}`
- THEN the button is disabled or replaced with a status text
- AND a human-readable error message is shown in the panel entry (not a raw error string)

---

## MUST-NOT Invariants (hard; reject any implementation that violates these)

| Invariant | Binding rule |
|-----------|-------------|
| Domain purity | No SDK, framework, or IO import under `domain/`. `IdentityExtractionPort` is a pure Protocol. |
| Ports at the boundary | `ReprocessService` imports ZERO concrete adapters at module level; all adapter access via injected Protocol instances. |
| Lazy heavy deps | `fitz`, `pyzbar`, `zxing-cpp`, `anthropic`, `openai` remain lazy-imported INSIDE adapter methods only. |
| Vision provider-agnostic | No vision call of any kind in the REINTENTAR path. `VisionLLMPort` is not touched in PR #2. |
| fecha never a grouping axis | Reconciliation key is `(registro, material_canonical, unidad)`. The recovered guía's `fecha_entrega` is date metadata only. |
| Units never converted | KG, TN, RD, Rollo are summed independently per unit type. No cross-unit conversion on recovery. |
| requires_review always True on recovery | Every `MaterialLine` from a recovered guía MUST have `requires_review=True`. No auto-accept, ever. |
| add_recovered_guia is the sole mutation hook | No other code path may append a guía to `_guias` or mutate `_errored_guias`. |
| Input PDF read-only | `ctx.pdf_path` is opened read-only. No write, truncate, or rename of the PDF. |
| Existing rows/keys immutable | `add_recovered_guia` MUST NOT alter the `status`, `delta`, `qty`, or `canonical_key` of any row that does not involve the recovered guía. |
| SUNAT gate | REINTENTAR MUST NOT proceed (returns 503) when `sunat.enabled=False`. No fallback to guessing quantities. |

---

## Out of Scope (explicit — absence is a conformance requirement)

- Vision / `VisionLLMPort.read_material_table` / Reprocesar-con-IA (PR #3).
- Any vision date call on the recovered guía (date = SUNAT `fecha_entrega` only).
- Rollback / undo of a recovery (recovered guía is `requires_review`; reassign/edit corrects).
- Transient vs. systematic pre-classification (classification is implicit in REINTENTAR outcome).
- `read_material_table` method on any port or adapter.
- `NullVisionAdapter` changes (PR #3 concern).

---

## Acceptance Summary

| Req | Core scenario(s) | Pass condition |
|-----|-----------------|----------------|
| REV-R01 | Protocol promotion | `IdentityExtractionPort.decode_hashqr_url` declared; `QrBarcodeExtractionAdapter` satisfies it |
| REV-R02 | No heavy-dep import | Module loads without fitz/pyzbar/zxing-cpp installed |
| REV-R03 | Transient success | Rows updated; errored shrinks; all lines `requires_review=True`; no vision call |
| REV-R04 | Failure paths | errored unchanged except `retry_attempted=True`; no garbage guía added |
| REV-R05 | Additive isolation | Unrelated rows untouched; floor/ceiling bracket active |
| REV-R06 | Restart persistence | Recovery survives restart; sidecar replay restores guía + shrinks errored_guias |
| REV-R07 | Batch background | 202 Accepted immediately; partial success reported per guía |
| REV-R08 | API contract | 200/404/503 semantics; `RetryGuiaResponse` shape |
| REV-R09 | Frontend wiring | Button gated on `retry_attempted`; success invalidates cache; failure shows reason |
