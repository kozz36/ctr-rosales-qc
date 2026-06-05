# Delta for Review Domain — guia-reprocess-staged-flow (PR #1 FOUNDATION)

**Change**: guia-reprocess-staged-flow  
**Slice**: PR #1 — Foundation: read-only surface of errored guías  
**Modifies**: openspec/specs/review/spec.md  
**Date**: 2026-06-05

---

## ADDED Requirements

### REV-E01 — ErroredGuia domain model carries retry_attempted flag

`ErroredGuia` MUST carry `retry_attempted: bool = False` as an additive field.
This field is inert in this slice (always `False`). It MUST default to `False` and MUST be
included in `model_dump(mode="json")` serialization so that the extraction cache round-trip
preserves it without format change.
This field gates PR #2/#3 REINTENTAR and Reprocesar-con-IA buttons; no logic reads it in
this slice.

`ErroredGuia` MUST remain a pure Pydantic `BaseModel` under `domain/models.py` with no SDK,
framework, or IO imports.

#### Scenario: ErroredGuia serializes retry_attempted as False by default

- GIVEN an `ErroredGuia` is instantiated with `registro`, `guia_id`, and `source_pages`
- WHEN `model_dump(mode="json")` is called
- THEN the output dict includes `"retry_attempted": false`
- AND no existing key (`registro`, `guia_id`, `source_pages`) is altered

---

### REV-E02 — build_review_service hydrates errored_guias from extraction cache

`build_review_service` MUST read `cache.get("errored_guias", [])` from the extraction cache
and hydrate a `list[ErroredGuia]` from the persisted dicts.
It MUST pass this list to `ReviewService.__init__` and `ReviewService.restore_from_sidecar`.
If the cache key is absent (older pipeline run that pre-dates keystone #2), the list MUST
default to `[]` without raising an error.

#### Scenario: Cache contains errored_guias — hydrated into ReviewService

- GIVEN a persisted extraction cache with `"errored_guias": [{"registro": 227, "guia_id": "T001-0123456", "source_pages": [5, 6], "retry_attempted": false}]`
- WHEN `build_review_service` is called with that cache
- THEN the resulting `ReviewService` has `errored_guias` returning a list of length 1
- AND the item has `registro=227`, `guia_id="T001-0123456"`, `source_pages=[5, 6]`

#### Scenario: Cache has no errored_guias key — defaults to empty list

- GIVEN a persisted extraction cache with NO `"errored_guias"` key (pre-keystone-#2 run)
- WHEN `build_review_service` is called
- THEN the resulting `ReviewService` has `errored_guias` returning `[]`
- AND no exception is raised

---

### REV-E03 — ReviewService holds errored_guias as read-only accessible state

`ReviewService` MUST hold `_errored_guias: list[ErroredGuia]` as internal state.
It MUST expose a read-only `errored_guias` property that returns the list.
It MUST NOT expose any mutation method for `_errored_guias` in this slice (no
`add_recovered_guia`, `apply_retry`, or equivalent — those are PR #2/#3).
`restore_from_sidecar` MUST accept and restore `errored_guias` so that the state survives
an application restart.

#### Scenario: ReviewService exposes errored_guias read-only

- GIVEN a `ReviewService` initialized with a non-empty `errored_guias` list
- WHEN `review_service.errored_guias` is accessed
- THEN it returns the same list passed at initialization
- AND no public method mutates the list in this slice

#### Scenario: restart preserves errored_guias via restore_from_sidecar

- GIVEN a run with two errored guías in the extraction cache
- AND `build_review_service` hydrated them on the first start
- WHEN the application is restarted and `restore_from_sidecar` is called for that run
- THEN `review_service.errored_guias` returns the same two `ErroredGuia` items
- AND no re-extraction or re-reconciliation is triggered

---

### REV-E04 — GET /table returns errored_guias from ReviewService

`ReconciliationTableResponse` MUST include an `errored_guias: list[ErroredGuiaResponse]`
field with a default of `[]`.
The `GET /runs/{run_id}/table` route MUST populate this field from
`review_service.errored_guias`.
The field MUST be additive: existing consumers that ignore the field MUST continue to function
without modification.
`ErroredGuiaResponse` already exists in `schemas.py`; no new DTO type is introduced.

#### Scenario: errored_guias present — appear in /table response

- GIVEN a run with two errored guías (`registro=227, pages=[5,6]` and `registro=230, pages=[11]`)
- AND the ReviewService carries them
- WHEN `GET /runs/{run_id}/table` is called
- THEN the response body includes `"errored_guias"` with 2 entries
- AND each entry contains `registro`, `guia_id`, `source_pages`, `retry_attempted`
- AND the existing `rows` and `unresolved_guias` fields are unaffected

#### Scenario: no errored_guias — empty list returned, no crash

- GIVEN a run with zero errored guías
- WHEN `GET /runs/{run_id}/table` is called
- THEN the response body includes `"errored_guias": []`
- AND the response is `200 OK` with no error

#### Scenario: existing /table consumer ignoring errored_guias is unaffected

- GIVEN an API client that parses only `rows` from `ReconciliationTableResponse`
- WHEN `GET /runs/{run_id}/table` returns a response with `"errored_guias": [...]`
- THEN the client parses `rows` correctly and does not raise a deserialization error
- AND no existing reconciliation row, status, delta, or qty value is changed

---

### REV-E05 — Frontend read-only ErroredGuiasPanel per Registro

The frontend MUST render a read-only `ErroredGuiasPanel.vue` component that displays
errored guías grouped by `registro` with the text "Error en páginas X" (where X is a
comma-separated list of `source_pages`).
The panel MUST be mounted in `ReviewPage.vue` and fed from the `GET /table` query result.
The panel MUST NOT include any action buttons, retry triggers, or mutation controls in
this slice.
`ErroredGuiaResponse` MUST be typed in `types.ts` and included on the
`ReconciliationTableResponse` TypeScript type.

#### Scenario: errored_guias display read-only per Registro

- GIVEN the table API returns `errored_guias` with two entries for registro 227 (pages 5, 6)
  and one entry for registro 230 (page 11)
- WHEN the review page renders
- THEN `ErroredGuiasPanel` shows two sections: registro 227 with "Error en páginas 5, 6"
  and registro 230 with "Error en página 11"
- AND no REINTENTAR, Reprocesar-con-IA, or any other button is rendered in the panel

#### Scenario: empty errored_guias — panel absent or empty, no crash

- GIVEN the table API returns `"errored_guias": []`
- WHEN the review page renders
- THEN `ErroredGuiasPanel` renders as empty or is not shown
- AND no JavaScript error occurs

---

## MUST-NOT Invariants (additive-only enforcement)

The following behaviors are PROHIBITED in this slice:

- The group key `(registro, material_canonical, unidad)` MUST NOT be altered.
- `fecha` MUST NOT be introduced as a grouping axis.
- Units `KG`, `TN`, `RD`, `Rollo` MUST NOT be converted or merged across types.
- Reconciliation row `status`, `delta`, and `qty` fields MUST NOT be modified by any code
  in this slice.
- `ErroredGuia` and `ErroredGuiaResponse` MUST NOT carry or trigger any reprocess,
  retry, or mutation action.
- The input PDF MUST NOT be opened for write or modified.
- `ReprocessService`, `VisionLLMPort.read_material_table`, `apply_retry`,
  `apply_reprocess`, and `add_recovered_guia` are explicitly OUT OF SCOPE — their
  absence is a conformance requirement, not an omission.

---

## Out of Scope (explicit)

- `ReprocessService` (Approach B application orchestrator) — PR #2/#3.
- REINTENTAR re-decode (QR re-render + SUNAT re-fetch) — PR #2.
- Reprocesar-con-IA / `VisionLLMPort.read_material_table` — PR #3.
- `add_recovered_guia` mutation, re-reconcile after reprocess.
- Transient vs. systematic classification of errored guías.
- `POST /runs/{run_id}/errored-guias/{guia_id}/retry` or `/reprocess` endpoints.
- Any REINTENTAR / Reprocesar-con-IA buttons in the frontend.
