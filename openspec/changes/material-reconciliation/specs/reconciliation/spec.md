# Spec — Reconciliation Domain
**Change**: material-reconciliation
**Domain**: reconciliation
**Phase**: spec
**Date**: 2026-05-31

---

## Purpose

The reconciliation domain is the product's core invariant engine. It receives normalized extracted rows from guía pages and declared material lists, groups them by the canonical grouping key, independently sums quantities per unit, compares the summed totals against declared totals, and emits a MATCH or MISMATCH flag per group. It also models the guía reassignment operation that corrects misfiled delivery notes.

The reconciliation result is the final output of the automated pipeline before human review; it doubles as the OCR validation gate because mismatches surface extraction errors.

---

## Requirements

### REC-001 — Grouping key

The `ReconciliationService` MUST group all extracted guía rows by the four-field key:
`(registro, fecha, material_canonical, unidad)`

No field in the grouping key MAY be omitted or substituted.
`material_canonical` is the output of `MaterialNormalizer`; `unidad` is the raw unit string as extracted (never normalized or converted).

### REC-002 — Per-unit independent summation (domain invariant)

The system MUST sum quantities independently per unit.
Quantities expressed in different units (KG, TN, RD, Rollo) MUST NEVER be added together.
Unit conversion between KG, TN, RD, and Rollo is PROHIBITED.
`MaterialNormalizer` MUST NOT modify the unit field; it MUST only canonicalize the material description.
Any implementation that converts, normalizes, or combines values across units MUST be treated as a defect.

### REC-003 — Declared-side reference

The declared quantity for each `(registro, fecha, material_canonical, unidad)` group MUST be sourced from the digital text of the detail page Notes or Protocolo de Recepción.
The declared value is the trusted reference. It is never derived from OCR output.
If no declared value exists for a group that appears in the guía extractions, the group MUST be flagged `declared_missing: true` and surfaced for review.
If a declared group has no corresponding guía rows, it MUST be flagged `guia_missing: true` and surfaced for review.

### REC-004 — MATCH / MISMATCH detection

For each reconciled group:
- **MATCH**: summed guía quantity equals declared quantity **exactly** (tolerance = 0; no rounding epsilon).
- **MISMATCH**: summed guía quantity differs from declared quantity by any nonzero amount, however small.

(Previously: tolerance was described as "default 0; exact match required unless explicitly configured otherwise" — now locked unconditionally to EXACT (0). Any nonzero delta is a MISMATCH.)

A MISMATCH MUST be flagged and surfaced in the review UI.
A MISMATCH MUST NOT be auto-corrected or auto-resolved by the system.
The engineer is the sole authority for resolving mismatches.

### REC-005 — Reconciliation as OCR validation gate

The reconciliation step is the primary mechanism for surfacing OCR extraction errors.
A MISMATCH between guía sum and declared quantity MUST be treated as a signal that the extracted quantities or dates may contain errors.
The review UI MUST show, per reconciled group, both the declared value and the per-source-page extracted values with their confidence scores and source page thumbnails.

### REC-006 — Guía reassignment

The domain MUST support a `reassign_guia` operation that moves a guía (identified by guía number + source page index) from its currently assigned `(registro, fecha)` to a corrected `(registro, fecha)`.
After reassignment:
- The MATCH/MISMATCH status of the source group MUST be recomputed.
- The MATCH/MISMATCH status of the target group MUST be recomputed.
- The reassignment MUST be recorded in the audit trail (original registro, original fecha, new registro, new fecha, operator timestamp).
- The reassignment MUST be reversible within the same run.

### REC-007 — No silent exclusions

Every page classified as `guia` MUST be accounted for in the reconciliation output — either contributing to a group's sum, or flagged with a reason it could not be included (e.g., `ocr_empty_after_deskew`, `date_requires_review`, `unclassified`).
Silently dropping any guía page from the reconciliation audit trail is PROHIBITED.

### REC-008 — Immutable domain core

`ReconciliationService` MUST be implemented as a pure domain service with no I/O, no framework dependencies, and no imports of adapter-layer or infrastructure modules.
All inputs MUST be passed as domain value objects; all outputs MUST be domain value objects.
Side effects (persistence, HTTP, file I/O) are PROHIBITED inside `ReconciliationService`.

### REC-009 — Confidence propagation

Each reconciled group MUST carry the minimum confidence score among all OCR quantity extractions that contributed to its sum.
This aggregate confidence MUST be surfaced in the review UI alongside the MATCH/MISMATCH flag.

### REC-010 — Numeric tolerance (locked: EXACT)

The equality threshold for MATCH determination is locked to **0** (exact match). No configuration override is permitted.
Declared quantity MUST equal the guía sum exactly; any nonzero delta MUST be treated as a MISMATCH.
Per-unit tolerance overrides and rounding epsilon are PROHIBITED for this change.
(Previously: tolerance was described as configurable with a default of 0 — now locked unconditionally.)

---

## Acceptance Scenarios

### Scenario REC-S01 — MATCH detected for a well-reconciled group

**Given** declared quantity for `(registro=4252, fecha=2025-03-15, material_canonical="BARRA CORRUGADA 1/2", unidad="KG")` is 1250.0 KG
**And** two guía pages contribute extracted rows: 750.0 KG (confidence 0.95) and 500.0 KG (confidence 0.88)
**When** `ReconciliationService` processes these rows
**Then** the summed quantity for the group is 1250.0 KG
**And** the group status is `MATCH`
**And** the group's aggregate confidence is 0.88 (minimum)

### Scenario REC-S02 — MISMATCH detected and flagged for review

**Given** declared quantity for a group is 1250.0 KG
**And** guía extractions sum to 1260.0 KG (OCR misread a digit)
**When** `ReconciliationService` processes these rows
**Then** the group status is `MISMATCH`
**And** the group surfaces in the review UI with declared=1250.0, summed=1260.0, delta=+10.0
**And** each contributing guía row's source page and confidence are shown
**And** the system does NOT auto-correct the sum

### Scenario REC-S03 — Units are NEVER converted or merged

**Given** two guía rows for the same material canonical and registro/fecha:
  - Row A: 1.25 TN
  - Row B: 1250.0 KG
**When** `ReconciliationService` groups these rows
**Then** they are placed in SEPARATE groups:
  - Group 1: `(registro, fecha, "BARRA CORRUGADA 1/2", "TN")` → sum = 1.25 TN
  - Group 2: `(registro, fecha, "BARRA CORRUGADA 1/2", "KG")` → sum = 1250.0 KG
**And** no conversion between TN and KG is performed
**And** no cross-unit addition occurs

### Scenario REC-S04 — Guía with missing declared counterpart flagged

**Given** a guía page contributes rows for a group `(registro=4252, fecha=2025-03-15, "ALAMBRE N°16", "KG")`
**And** no declared entry exists for this material in registro 4252
**When** `ReconciliationService` reconciles
**Then** the group is created with status `declared_missing: true`
**And** the group surfaces in the review UI
**And** it is NOT silently excluded from the reconciliation output

### Scenario REC-S05 — Declared material with no guía rows flagged

**Given** declared quantity for `(registro=4251, fecha=2025-02-10, "BARRA CORRUGADA 3/8", "KG")` is 800.0 KG
**And** no guía page contributes rows matching this group
**When** `ReconciliationService` reconciles
**Then** the group is created with status `guia_missing: true`
**And** it surfaces in the review UI
**And** summed quantity is 0.0

### Scenario REC-S06 — Guía reassignment recomputes both groups

**Given** guía N°12345 (source page 47) is currently assigned to `(registro=4252, fecha=2025-03-15)`
**And** this causes a MISMATCH in that registro
**When** the engineer reassigns guía N°12345 to `(registro=4251, fecha=2025-02-10)`
**Then** the sum for `(registro=4252, fecha=2025-03-15)` is recomputed without guía N°12345
**And** the sum for `(registro=4251, fecha=2025-02-10)` is recomputed including guía N°12345
**And** MATCH/MISMATCH status is refreshed for BOTH groups
**And** the reassignment is recorded in the audit trail with original and new registro/fecha values

### Scenario REC-S07 — All guía pages accounted for in output

**Given** the ingestion produced 469 scanned pages total
**And** the PageClassifier assigned 320 pages as `guia`
**When** reconciliation completes
**Then** the reconciliation audit trail references exactly 320 guía pages
**And** each page either contributes to a group's sum or carries an explicit exclusion reason
**And** zero guía pages are absent from the audit trail

### Scenario REC-S08 — ReconciliationService has no I/O side effects

**Given** `ReconciliationService.reconcile(declared_rows, extracted_rows)` is called
**Then** no file is read or written
**And** no HTTP call is made
**And** no database query is executed
**And** the result is a pure data structure of reconciled groups

---

## Out of scope for this domain

- PDF reading, page rendering, deskew (ingestion domain).
- OCR or vision LLM calls (extraction domain).
- Material description canonicalization (MaterialNormalizer, called before this domain receives data).
- UI rendering, editing, and export (review and export domains).
