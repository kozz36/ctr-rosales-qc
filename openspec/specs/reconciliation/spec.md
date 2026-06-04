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

> **[SUPERSEDED — rev-3 delta (2026-06-02), R8/MAT-001]** The `fecha` field was REMOVED
> from the grouping key. The effective key is the three-field tuple
> `(registro, material_canonical, unidad)`. Rationale: `fecha` is vision-read and noisy
> (the year is unreliable); folding it into the key split declared↔guía groups whenever the
> vision-read date differed, killing MATCH. A Registro N° is one reception event = one date,
> so `registro` already disambiguates — material reconciliation is date-independent.
> Reception-date handling (digital Protocolo declared-date authority + day-month divergence as a
> reviewable misfiled-guía signal) is a SEPARATE, additive concern, NOT a grouping axis.
> This delta note also governs the four-field key references in REC-003, REC-C01, and the
> Scenario prose below (kept verbatim for history; the live key is three-field).

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

## Delta — rev 2 (2026-06-01): guía-granularity review model + fecha authority + UNRESOLVED fallback

> The requirements below ADD or MODIFY behaviour relative to REC-001 through REC-010 above.
> Each entry is marked [ADDED] or [MODIFIED: replaces <id>].

### REC-C01 — [ADDED] Authoritative fecha: handwritten reception date only

**[MODIFIED: makes the grouping-key `fecha` invariant explicit at the reconciliation tier;
reinforces EXT-017]**

The `fecha` component of the grouping key `(registro, fecha, material_canonical, unidad)`
MUST be sourced exclusively from the handwritten reception date extracted by
`VisionLLMPort` (vision LLM) from the guía page stamp area.
The electronic GRE date obtained via `SunatGreFetchPort` MUST NOT be used as `fecha`,
even when a SUNAT fetch is enabled.

A `GuiaDeRemision` whose `fecha` differs from the `Registro.fecha_declarada` of its
assigned registro MUST be treated as a potentially misfiled guía and MUST be surfaced in
the review UI for the engineer to reassign or confirm.

### REC-C02 — [ADDED] GuiaContribution — inline in ReconciliationRow

Each `ReconciliationRow` MUST expose an inline `guias` field of type
`list[GuiaContribution]`.

`GuiaContribution` MUST carry:
- `guia_id: str` — the deterministic `{serie}-{numero}` identifier from the QR tier, or
  the OCR-fallback identity string; MUST be unique per block
- `source_pages: list[int]` — all page indices in the guía block
- `cantidad: float` — this guía's contribution to the group's sum for this unit
- `unidad: str` — the unit for this contribution (matches the group's unit)
- `confidence: float` — minimum OCR confidence for quantity rows in this block
- `identity_source: Literal["qr", "ocr_fallback"]`

The `guias` list MUST be populated INLINE in the DTO; a separate API endpoint that fetches
guía detail on demand MUST NOT be used (chosen explicitly to avoid N+1 query patterns
when the review UI renders the full grid).

`summed_qty` on `ReconciliationRow` MUST be a derived, read-only field computed as
`sum(g.cantidad for g in guias)`. It MUST NOT be directly editable via any API endpoint.

### REC-C03 — [ADDED] Reassign by guia_id (serie-numero)

**[MODIFIED: replaces the guía identification scheme in REC-006 which used "guía number +
source page index"]**

The `reassign_guia` operation MUST identify the guía to move by `guia_id`
(`{serie}-{numero}`) rather than by guía number + source page index alone.
When multiple guías in the same group share a source page (not expected in normal
operation but MUST be handled defensively), `guia_id` MUST be the disambiguating key.
The API endpoint for reassignment MUST accept `guia_id` as the target identifier.

### REC-C04 — [ADDED] Guía-line cantidad edit; summed_qty is read-only

A new editing path MUST be supported: the engineer edits the `cantidad` value of a
specific line within a specific `GuiaContribution` (identified by `guia_id` and line
index or material description).
After a line `cantidad` edit:
1. The `GuiaContribution.cantidad` for that block is recomputed as the sum of its
   corrected line quantities.
2. The `ReconciliationRow.summed_qty` is recomputed as `sum(g.cantidad for g in guias)`.
3. The MATCH/MISMATCH status for the group is recomputed.
4. The audit trail records the edit with `action_type = "guia_line_edit"`, the `guia_id`,
   the line identifier, `old_value`, and `new_value`.

The following edit path is PROHIBITED and MUST be removed:
- Editing `summed_qty` directly on a `ReconciliationRow` as a field named `fecha` or as
  any computed aggregate field. This was identified as the root cause of the
  `date.fromisoformat("845")` corruption bug (frontend CRITICAL-2 from §frontend-review).

### REC-C05 — [ADDED] UNRESOLVED guías surface in reconciliation output

A `GuiaDeRemision` whose `registro` is `None` or matches the pattern `"UNRESOLVED:*"`
MUST NOT be silently grouped or discarded.
Such guías MUST be collected in a dedicated `unresolved_guias: list[GuiaDeRemision]`
field on the reconciliation output (alongside the normal `rows` list).
Each unresolved guía MUST appear in the review UI under an "unresolved guías" bucket so
the engineer can assign a registro manually.
An unresolved guía MUST be counted in the reconciliation audit trail as a distinct item
(contributing zero to any declared-group sum until assigned).

### REC-C06 — [ADDED] API surface additions

The following API surface changes MUST be implemented to support the guía-granularity
review model:

**Modified response shape**:
`ReconciliationRowResponse` MUST include a `guias: list[GuiaContributionResponse]` field
(inline; not a separate lazy-loaded endpoint). The `GuiaContributionResponse` fields mirror
`GuiaContribution` (guia_id, source_pages, cantidad, unidad, confidence, identity_source).

**New endpoint**:
`PATCH /runs/{run_id}/guias/{guia_id}/lines`
- Purpose: edit the `cantidad` of a specific line within a guía block.
- Request body: `{ "line_index": int, "cantidad": float }` or `{ "material_canonical": str, "cantidad": float }`.
- Response: updated `ReconciliationRowResponse` for every affected group (source and, after
  reassignment, target).
- Idempotent: calling with the same value MUST produce the same result.
- Error: returns 404 when `guia_id` is not found in the run; returns 422 when `cantidad < 0`.

**Modified reassign endpoint**:
`POST /runs/{run_id}/reassign` MUST accept `guia_id` (in addition to or replacing the
previous `source_page` identification scheme). If both are provided, `guia_id` takes
precedence.

---

## Acceptance Scenarios — Delta rev 2

### Scenario REC-S01 — [MODIFIED] MATCH with guia list populated

**Given** declared quantity for `(registro=232, fecha=2025-03-15, material_canonical="BARRA CORRUGADA 1/2", unidad="KG")` is 1250.0 KG
**And** guía block `T009-0741770` (pages 47–48) contributes 750.0 KG (min confidence 0.95)
**And** guía block `T009-0741771` (page 50) contributes 500.0 KG (min confidence 0.88)
**When** `ReconciliationService` processes these rows
**Then** the group status is `MATCH`
**And** `ReconciliationRow.summed_qty = 1250.0` (derived, read-only)
**And** `ReconciliationRow.guias` contains exactly 2 `GuiaContribution` entries:
  - `{guia_id: "T009-0741770", source_pages: [47, 48], cantidad: 750.0, confidence: 0.95}`
  - `{guia_id: "T009-0741771", source_pages: [50], cantidad: 500.0, confidence: 0.88}`
**And** the group's aggregate confidence is 0.88

> **Note**: scenario REC-S01 above replaces the greenfield REC-S01 (which used `registro=4252`
> — a section ID, not a Registro N°). The correct business key is the Registro N° (e.g., `232`).

### Scenario REC-C01 — [ADDED] Handwritten fecha drives grouping; electronic fecha ignored

**Given** guía block `T009-0741770` has:
  - `VisionLLMPort` returned handwritten fecha `2025-03-15` (confidence 0.91)
  - SUNAT GRE fetch (enabled in this scenario) returned electronic fecha `2025-03-18`
**When** `ReconciliationService` groups the guía's rows
**Then** the grouping key uses `fecha = 2025-03-15` (handwritten)
**And** `fecha = 2025-03-18` does NOT appear in any grouping key
**And** the electronic date is NOT stored as a group-key component anywhere in the output

### Scenario REC-C02 — [ADDED] Misfiled guía detected by fecha divergence

**Given** registro 232 has `fecha_declarada = 2025-03-15`
**And** guía block `T009-0741770` has handwritten fecha `2025-02-10` (a different date)
**When** reconciliation processes the guía
**Then** the guía is surfaced in the review UI with a "fecha mismatch" indicator
**And** the reconciliation row for `(232, 2025-02-10, ...)` is created (using the guía's actual fecha)
**And** a misfiled-guía flag is set so the engineer can reassign

### Scenario REC-C03 — [ADDED] Reassign targets guia_id

**Given** guía `T009-0741770` is currently contributing to group `(registro=232, fecha=2025-03-15)`
**And** this guía belongs to `(registro=231, fecha=2025-02-10)` (misfiled)
**When** the engineer reassigns `guia_id="T009-0741770"` to `(registro=231, fecha=2025-02-10)`
**Then** all `GuiaContribution` entries for `T009-0741770` are removed from group `(232, 2025-03-15)`
**And** a `GuiaContribution` for `T009-0741770` is added to group `(231, 2025-02-10)`
**And** `summed_qty` is recomputed for BOTH groups
**And** MATCH/MISMATCH status is refreshed for BOTH groups
**And** the audit trail records `action_type="guia_reassign"`, `guia_id="T009-0741770"`,
  `old_value="(232, 2025-03-15)"`, `new_value="(231, 2025-02-10)"`

### Scenario REC-C04 — [ADDED] Guía-line cantidad edit recomputes summed_qty

**Given** guía `T009-0741770` contributes a line with `material_canonical="BARRA CORRUGADA 1/2"`,
  `cantidad=1260.0`, `unidad="KG"` (OCR misread)
**And** the group's `summed_qty` is currently 1260.0
**When** the engineer edits the line cantidad to `1250.0` via `PATCH /runs/{id}/guias/T009-0741770/lines`
**Then** `GuiaContribution.cantidad` for `T009-0741770` is updated to 1250.0
**And** `ReconciliationRow.summed_qty` recomputes to 1250.0
**And** the group status updates from MISMATCH to MATCH
**And** the audit trail records `action_type="guia_line_edit"`, `guia_id="T009-0741770"`,
  `old_value=1260.0`, `new_value=1250.0`

### Scenario REC-C05 — [ADDED] summed_qty direct edit is rejected

**Given** a `ReconciliationRow` with `summed_qty = 1260.0`
**When** the API receives a PATCH request targeting `summed_qty` as an editable field
**Then** the API returns 422 (Unprocessable Entity)
**And** no reconciliation state is modified
**And** no audit trail entry is created

### Scenario REC-C06 — [ADDED] Unresolved guía surfaces in reconciliation output

**Given** a guía block whose `_derive_numero` returns `None` (no matching section entry)
**When** reconciliation processes the output
**Then** the `GuiaDeRemision` appears in `reconciliation_result.unresolved_guias`
**And** the guía does NOT contribute to any declared-group sum
**And** the reconciliation audit trail includes the unresolved guía by `guia_id` or page index
**And** the review UI surfaces it in the "unresolved guías" bucket

### Scenario REC-C07 — [ADDED] Section ID never used as registro key

**Given** a page whose section/Contents map entry is `4252`
**And** the Registro N° derivation fails (no matching registro)
**When** reconciliation receives the guía
**Then** no `ReconciliationRow` is keyed with `registro = "4252"`
**And** the guía appears in `unresolved_guias` with an explanatory sentinel

---

## Delta — rev 3 (2026-06-02): year inference provenance in reconciliation output

> The requirements below ADD or MODIFY behaviour relative to REC-001 through REC-C06 above.
> Each entry is marked [ADDED] or [MODIFIED: replaces <id>].

### REC-C07 — [ADDED] year_inferred provenance propagated through ReconciliationRow

When a `GuiaDeRemision.fecha` was set via bounded year inference (EXT-021,
`year_inferred = true`), this provenance MUST be preserved in the reconciliation output.

`GuiaContribution` MUST carry a `year_inferred: bool` field (default `false`).
When `year_inferred = true` on a `GuiaDeRemision`, all `GuiaContribution` entries derived
from that guía MUST have `year_inferred = true`.

`ReconciliationRow` MUST expose `any_year_inferred: bool` — a derived flag that is `true`
when at least one contributing `GuiaContribution` has `year_inferred = true`.

`any_year_inferred = true` on a `ReconciliationRow` MUST be surfaced as an advisory
indicator in the review UI (distinct from the red `requires_review` / MISMATCH flag).
It does NOT block reconciliation or change MATCH/MISMATCH logic; it is a transparency
signal for the engineer.

The `any_year_inferred` flag MUST be included in the export audit trail.

---

## Acceptance Scenarios — Delta rev 3

### Scenario REC-C08 — [ADDED] year_inferred propagates from guía to ReconciliationRow

**Given** guía block `T009-0741770` has `fecha = 2026-05-28` with `year_inferred = true`
**And** this guía contributes to group
  `(registro=232, fecha=2026-05-28, material_canonical="BARRA CORRUGADA 1/2", unidad="KG")`
**When** `ReconciliationService` processes the group
**Then** `GuiaContribution` for `T009-0741770` has `year_inferred = true`
**And** `ReconciliationRow.any_year_inferred = true`
**And** the MATCH/MISMATCH determination is not affected by `year_inferred`

### Scenario REC-C09 — [ADDED] any_year_inferred false when all dates directly read

**Given** all guía blocks contributing to a group have `year_inferred = false`
  (vision directly read the year component with high confidence)
**When** `ReconciliationService` computes the group
**Then** `ReconciliationRow.any_year_inferred = false`
**And** no advisory indicator is set for this row

---

## Out of scope for this domain

- PDF reading, page rendering, deskew (ingestion domain).
- OCR or vision LLM calls (extraction domain).
- Material description canonicalization (MaterialNormalizer, called before this domain receives data).
- UI rendering, editing, and export (review and export domains).
