# Spec — Export Domain
**Change**: material-reconciliation
**Domain**: export
**Phase**: spec
**Date**: 2026-05-31

---

## Purpose

The export domain serializes the final reconciliation table — including any review edits, guía reassignments, and the audit trail — to a structured file format (xlsx or csv) that the QC engineer can deliver as part of the project record.

---

## Requirements

### EXP-001 — Supported output formats

The system MUST support export to:
- `.xlsx` (Excel workbook)
- `.csv` (comma-separated values, UTF-8)

The engineer MUST be able to select the desired format before triggering export.
The `ExcelReportAdapter` MUST implement the `ReportPort` interface.
`ReportPort` MUST be defined in the domain/application layer with no dependency on a specific serialization library.

### EXP-002 — Reconciliation table sheet / section

The primary export content MUST be a flat table ordered by `Registro N°` ascending, then `Fecha` ascending, then `Material` ascending.

Each row MUST include exactly the following columns:

| Column header | Internal field | Description |
|---|---|---|
| `Registro` | `registro` | Registro N° |
| `Fecha` | `fecha_entrega` | Fecha de entrega (DD/MM/YYYY) |
| `Material` | `material_canonical` | Canonicalized material description |
| `Unidad` | `unidad` | Unit (KG / TN / RD / Rollo — as extracted) |
| `Declarado` | `qty_declared` | Declared quantity (from digital text) |
| `Sumado (guías)` | `qty_summed` | Sum of guía quantities after review |
| `Delta` | `delta` | qty_summed − qty_declared |
| `Estado` | `status` | MATCH / MISMATCH / DECLARED_MISSING / GUIA_MISSING |
| `Confianza mín` | `confidence_min` | Minimum OCR confidence among contributing guía rows |
| `Páginas origen` | `source_pages` | Comma-separated list of source page indices |

(Previously: column set included `guias_reassigned` as an additional MUST column — removed from the main table to align with the locked column set. Reassignment information is preserved in the audit trail sheet.)

The xlsx workbook MUST also include a **Summary sheet** as a separate tab.
The summary sheet MUST include at minimum: total groups, count of MATCH groups, count of MISMATCH groups, count of DECLARED_MISSING groups, count of GUIA_MISSING groups, run identifier, and export timestamp.
(Previously: a summary sheet was not specified — now MUST.)

### EXP-003 — Audit trail sheet / section

The xlsx export MUST include a second sheet (tab) named "Audit Trail" containing the run's full audit log.
Each row MUST include:
- `timestamp`
- `action_type` (`value_edit` | `guia_reassign` | `flag_resolved`)
- `target` (group key or guía identifier)
- `old_value`
- `new_value`
- `operator`

For csv export, the audit trail MUST be exported as a separate `*_audit.csv` file in the same output directory.

### EXP-004 — Engineer-corrected values take precedence

When the engineer has corrected an extracted value in the review UI, the export MUST use the corrected value in `qty_summed` and MUST record both the original extracted value and the corrected value in the audit trail row for the edit.

### EXP-005 — Reassigned guías recorded in audit trail

Guías that were reassigned during review MUST be recorded in the audit trail sheet (EXP-003) with their original and new `(registro, fecha)` values.
The specific guías that were reassigned SHOULD also be identified in a supplementary column or note in the audit trail row for discoverability.
The main reconciliation table (EXP-002) does NOT include a `guias_reassigned` column; reassignment visibility is provided via the audit trail.
(Previously: `guias_reassigned` was a MUST column in the main table — moved to audit trail to match the locked column set.)

### EXP-006 — Output file placement

The export file MUST be written to the run's isolated output directory.
The filename MUST include the run identifier or a timestamp to prevent collisions across runs.
The source PDF and any previously generated exports from other runs MUST NOT be overwritten.

### EXP-007 — ReportPort abstraction

The export operation MUST be invoked via `ReportPort.generate(reconciliation_result, audit_trail, format)`.
The `ExcelReportAdapter` and any future `CsvReportAdapter` MUST implement `ReportPort`.
The application layer MUST NOT reference a concrete adapter class; it MUST depend on the `ReportPort` interface.

### EXP-008 — Export is idempotent

Calling export multiple times for the same run and same format MUST produce an equivalent output.
If an export file already exists in the output directory for the same run and format, the system SHOULD overwrite it (not append) and MUST NOT corrupt the file.

---

## Acceptance Scenarios

### Scenario EXP-S01 — xlsx export produces correct table and summary sheet

**Given** a completed reconciliation with 5 groups (3 MATCH, 1 MISMATCH, 1 GUIA_MISSING)
**And** the engineer has corrected one quantity value in review
**When** the engineer triggers export as xlsx
**Then** the xlsx file is created in the run output directory
**And** the first sheet (reconciliation table) contains exactly 5 rows ordered by Registro, Fecha, Material
**And** each row contains exactly the 10 columns: Registro, Fecha, Material, Unidad, Declarado, Sumado (guías), Delta, Estado, Confianza mín, Páginas origen
**And** the MISMATCH row has Estado = "MISMATCH" and a non-zero Delta
**And** the GUIA_MISSING row has Sumado (guías) = 0 and Estado = "GUIA_MISSING"
**And** the row with the engineer-corrected value uses the corrected Sumado (guías)
**And** the workbook contains a Summary sheet listing total=5, MATCH=3, MISMATCH=1, GUIA_MISSING=1, DECLARED_MISSING=0

### Scenario EXP-S02 — Audit trail sheet present in xlsx

**Given** the engineer made 2 value edits and 1 guía reassignment during review
**When** the xlsx export is generated
**Then** the workbook contains a second sheet named "Audit Trail"
**And** the sheet has exactly 3 rows (2 value_edit + 1 guia_reassign)
**And** each row includes timestamp, action_type, target, old_value, new_value

### Scenario EXP-S03 — csv export produces two files

**Given** the engineer triggers export as csv
**Then** a primary `*_reconciliation.csv` file is created with the reconciliation table rows
**And** a secondary `*_audit.csv` file is created with the audit trail rows
**And** both files are UTF-8 encoded
**And** both files are in the run's isolated output directory

### Scenario EXP-S04 — Reassigned guía recorded in audit trail

**Given** guía N°12345 was reassigned from registro 4252 to registro 4251 during review
**When** the export is generated
**Then** the Audit Trail sheet contains a row with action_type = "guia_reassign", target = "guía N°12345", old_value = "registro=4252", new_value = "registro=4251"
**And** the main reconciliation table rows for registro 4251 and 4252 do NOT include a `guias_reassigned` column
**And** the audit trail row includes timestamp and operator fields

### Scenario EXP-S05 — Export does not modify source PDF

**Given** an export is triggered
**When** the export completes
**Then** the source PDF file is unchanged (byte-identical to before the export)
**And** no other run's output directory is modified

### Scenario EXP-S06 — ReportPort abstraction verified at call site

**Given** the application layer triggers export
**Then** the call is made through the `ReportPort` interface method
**And** no import of `ExcelReportAdapter` or openpyxl appears in the application or domain layer

---

## Out of scope for this domain

- Reconciliation computation (handled by the reconciliation domain).
- Review editing and reassignment (handled by the review domain).
- Cloud upload, email delivery, or sharing of export files.
- PDF generation (not a requirement for this change).
