# Delta for Reconciliation Domain
**Change**: guia-classification-keystone
**Phase**: spec
**Date**: 2026-06-04

---

## ADDED Requirements

### REC-EG-001 — Errored-guías side-channel on PipelineResult

`PipelineResult` MUST carry an additive field `errored_guias: list[ErroredGuia]` (default
empty list) that surfaces, per Registro, every guía that resolved to 0 material lines after
the SUNAT fetch stage.

`ErroredGuia` MUST carry exactly:
- `registro: str` — the Registro N° the guía was assigned to
- `guia_id: str` — the deterministic `{serie}-{numero}` identifier (or OCR-fallback value)
- `source_pages: list[int]` — all page indices in the guía block

No additional fields (transient/systematic classification, re-decode probe result) MUST be
included in this change; those are deferred to change #3.

The field MUST be populated **after** the SUNAT fetch stage, examining each assembled guía
block for `len(lines) == 0`.

### REC-EG-002 — Errored-guías side-channel is strictly additive

The `errored_guias` side-channel MUST NOT alter:
- the grouping key `(registro, material_canonical, unidad)` of any reconciled group
- the `status` (MATCH / MISMATCH / declared_missing / guia_missing) of any group
- the `delta` or `summed_qty` of any group
- the `cantidad` of any `GuiaContribution` from a correctly-processed guía

A guía that appears in `errored_guias` contributes 0 lines to the reconciled sum. This is
NOT a new exclusion — the guía already contributed 0 lines. The side-channel makes the
omission visible rather than silent.

### REC-EG-003 — 0-line guías MUST NOT be silently included

A `GuiaDeRemision` with `len(lines) == 0` MUST NOT contribute to any `ReconciliationRow`
sum as if it were a valid guía with unread quantities. It MUST appear in `errored_guias`
so the engineer can identify which pages failed and take action (manual re-decode, SUNAT
retry, or reassignment).

This requirement extends REC-007 (no silent exclusions): instead of being silently dropped
or silently contributing 0, the guía is **visibly accounted for** in the `errored_guias`
side-channel.

---

## Hard invariant constraints (MUST NOT)

- MUST NOT add any field to the reconciliation grouping key.
- MUST NOT import any vendor SDK, HTTP library, or IO library inside the domain layer.
- MUST NOT perform any SUNAT network call from within `ReconciliationService`.
- MUST NOT auto-correct or reassign a guía because it appears in `errored_guias`.
- `fecha` MUST NOT be used as a grouping axis (R8/MAT-001; absolute).

---

## Acceptance Scenarios

### Scenario REC-EG-S01 — 0-line guía surfaces in errored_guias; correct guías unaffected

- GIVEN registro 232 has two guía blocks after SUNAT fetch:
  - `T112-0065421` with 3 material lines (OCR/SUNAT succeeded)
  - `T112-0065422` with 0 material lines (URL-variant QR, SUNAT fetch failed)
- WHEN `PipelineResult` is produced
- THEN `errored_guias` contains exactly one entry:
  `{registro: "232", guia_id: "T112-0065422", source_pages: [...]}`
- AND `T112-0065421` is NOT listed in `errored_guias`
- AND the `ReconciliationRow` for registro 232 uses ONLY `T112-0065421`'s quantities
- AND `summed_qty`, `status`, and `delta` for all groups in registro 232 are identical
  to what they would be without the `errored_guias` feature present

### Scenario REC-EG-S02 — Additive-only invariant: correctly-processed guía is unaffected

- GIVEN a run produces `errored_guias = [{registro: "227", guia_id: "T009-0741770", source_pages: [86]}]`
- WHEN the caller reads the reconciliation rows for registro 228
- THEN no row in registro 228 has its `summed_qty`, `delta`, `status`, or `guias` list altered
  relative to a baseline run where no `errored_guias` exists
- AND `ErroredGuia` entries for registro 227 do NOT appear in registro 228's rows or contributions

### Scenario REC-EG-S03 — errored_guias empty when all guías have lines

- GIVEN all assembled guía blocks have at least 1 material line after SUNAT fetch
- WHEN `PipelineResult` is produced
- THEN `errored_guias` is an empty list (not null, not absent)
- AND no group key, status, delta, or quantity is affected

### Scenario REC-EG-S04 — Multiple errored guías across registros collected correctly

- GIVEN registro 227 has 1 guía with 0 lines and registro 232 has 2 guías with 0 lines
- WHEN `PipelineResult` is produced
- THEN `errored_guias` contains exactly 3 entries
- AND each entry carries the correct `registro`, `guia_id`, and `source_pages`
- AND guías with lines in registro 227 and 232 are NOT in `errored_guias`

---

## Out of scope for this delta

- Frontend REINTENTAR / "Reprocesar con IA" UI — deferred to change #3.
- Transient-vs-systematic subclassification of errored guías — deferred to change #3.
- QR re-decode probe via `IdentityExtractionPort` — deferred to change #3.
- openspec documentation pass — deferred to change #7.
- Any change to MATCH/MISMATCH logic, grouping key, or quantity summation.
