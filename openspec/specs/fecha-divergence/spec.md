# Spec — Fecha Divergence Review
**Change**: r9-fecha-divergence-review
**Domain**: fecha-divergence (delta over reconciliation domain)
**Phase**: spec
**Date**: 2026-06-02

---

## Purpose

Restore the misfiled-guía detection signal that Slice 1 (`r8-material-matching` / MAT-001)
left unhandled by removing `fecha` from the grouping key. This spec mandates:

1. The authoritative declared reception date for a Registro N° is the **DIGITAL `Fecha:`**
   on the Protocolo de Recepción sheet (deterministic parse, no vision call).
   **Domain-correctness correction (2026-06-03)**: the original spec stated "HANDWRITTEN,
   vision-read" — the domain authority confirmed with real PDF evidence that the Protocolo
   `Fecha:` is DIGITAL/printed. FDR-001 and FDR-002 are updated accordingly.
2. A per-guía fecha-divergence check (pure domain) comparing each guía's handwritten date
   against the registro's digital declared date (`fecha_declarada`).
3. Non-blocking WARNING emission on divergence, carrying the guía page number, flagging
   `requires_review`, driving a RED highlight in the frontend.

This is a **delta spec**: all requirements in
`openspec/changes/material-reconciliation/specs/reconciliation/spec.md`
(REC-001 through REC-C07 and their acceptance scenarios) and
`openspec/changes/r8-material-matching/specs/material-matching/spec.md`
(MAT-001 through MAT-013) remain in force.
Requirements below ADD or MODIFY behaviour within that base. Each entry is marked
`[ADDED]` or `[MODIFIED: replaces <id>]`.

---

## Requirements

### FDR-001 — [MODIFIED 2026-06-03] Declared reception date authority: DIGITAL Protocolo date

**Correction**: the original FDR-001 stated the authority was the HANDWRITTEN "Fecha:"
vision-read via `VisionLLMPort`. The domain authority confirmed with real PDF evidence
that the Protocolo `Fecha:` is a **DIGITAL/printed** field from Forma — not handwritten.

The authoritative declared reception date for a Registro N° MUST be the **DIGITAL `Fecha:`
field** on the **Protocolo de Recepción** sheet, parsed deterministically by
`digital_text_extractor.py` (`_PROTO_REG_RE` + `_parse_date_ddmmyy`). This parse yields the
real year. **No `VisionLLMPort` call is made for the declared date.**

`Registro.fecha_declarada` carries this parsed date.
`Registro.fecha_authoritative` returns `self.fecha_declarada` directly (no handwritten
override field exists — `fecha_declarada_handwritten`, `fecha_declarada_confidence`, and
`fecha_declarada_year_inferred` have been removed).

If `digital_text_extractor.py` yields `None` for `fecha_declarada` on a Registro that has
a `protocolo_page` set, the pipeline emits a WARNING in `PipelineResult.warnings` so the
operator can inspect the source PDF. No auto-correction, no vision fallback.

#### Acceptance Scenarios

**Scenario FDR-S01 — Registro 232: Protocolo declared date from digital parse**

Given the Protocolo de Recepción page for Registro 232 contains printed text "Fecha: 28-05-26"
When `digital_text_extractor.py` parses that page
Then `Registro.fecha_declarada` is set to `date(2026, 5, 28)` (deterministic parse, real year)
And `Registro.fecha_authoritative == Registro.fecha_declarada == date(2026, 5, 28)`
And NO `VisionLLMPort` call is made for the declared date

**Scenario FDR-S02 — Declared date source is digital Protocolo parse, not vision**

Given a Registro N° whose Protocolo page and guía pages are both present in the run
When the pipeline reads the declared reception date
Then the declared date comes from the digital text parse of the Protocolo page
And the guía vision reads are NOT used to set the declared date
And the vision budget is reserved entirely for guía stamp-date reads

---

### FDR-002 — [MODIFIED 2026-06-03] Declared date year: exact from digital parse; guías use bounded inference

**Correction**: the original FDR-002 required year inference for the declared side because
the vision-read year was unreliable. After the FDR-001 correction, the declared date comes
from the DIGITAL parse (`_parse_date_ddmmyy`) which extracts the real year directly
(e.g. `"28-05-26"` → `date(2026, 5, 28)`). No year inference is needed on the declared side.

Year inference (`infer_reception_year`) applies ONLY to the guía side (vision-read stamp
dates where the year is unreliable). The declared date year is treated as exact.

The divergence check compares guía day-month (after year inference) against declared day-month
(from the digital parse year). Since the declared year is exact, a guía with the same
day-month but a different vision-reconstructed year MUST NOT be flagged as divergent
(year-only difference is a `year_ambiguity` per FDR-003 — unchanged).

#### Acceptance Scenario

**Scenario FDR-S03 — Declared year is exact; guía year uses bounded inference; same day-month → no divergence**

Given a Protocolo DIGITAL date parsed as `date(2026, 5, 28)` (exact)
And a guía handwritten date `28-05-26` (vision-read: day 28, month 05, year 26)
And no SUNAT lower-bound information is available
When year inference runs on the guía side (infers `2026-05-28`)
Then the divergence check compares day=28, month=05 (guía) against day=28, month=05 (declared)
And divergence = False (day and month match)
And no false-positive WARNING is emitted for this guía

---

### FDR-003 — [ADDED] Divergence predicate: day-month primary, bounded-year secondary

The fecha-divergence check MUST compare guía date against declared date using
**day and month as the primary comparison axes**.

The precise predicate is:

> Two dates are NON-DIVERGENT if and only if `guia.day == declared.day` AND
> `guia.month == declared.month`.
> Year is compared ONLY when both sides produce a reconstructed year via bounded inference
> AND the inferred years differ by more than zero. Year-only divergence (same day-month,
> different inferred year) MUST NOT emit a WARNING — it MUST be logged as a `year_ambiguity`
> note on the registro, not treated as a fecha divergence.

Rationale: vision year is unreliable; a difference purely in the inferred year is an
inference artefact, not a physical misfiled-guía signal. Day-month divergence is the
authoritative misfiling indicator.

Tolerance: ZERO. An exact day-month match (after normalisation) is required for NON-DIVERGENT.
No ±1 tolerance is applied to day or month values.

#### Acceptance Scenarios

**Scenario FDR-S04 — Same day-month, different inferred years: no WARNING emitted**

Given declared date infers to `2026-05-28` (day=28, month=05)
And guía date infers to `2025-05-28` (day=28, month=05, different year from inference noise)
When the divergence predicate runs
Then divergence = False (day and month match)
And no fecha-divergence WARNING is emitted for this guía
And the year discrepancy MAY be logged as `year_ambiguity` but MUST NOT produce a RED highlight

**Scenario FDR-S05 — Different day-month: WARNING emitted**

Given declared date resolves to day=28, month=05
And guía date resolves to day=15, month=04
When the divergence predicate runs
Then divergence = True
And a fecha-divergence WARNING is emitted for that guía (per FDR-004)

**Scenario FDR-S06 — Same day and month: no WARNING**

Given declared date resolves to day=28, month=05
And guía date resolves to day=28, month=05
When the divergence predicate runs
Then divergence = False
And no WARNING is emitted
And the material MATCH/MISMATCH status for that group is unaffected

---

### FDR-004 — [ADDED] Divergence WARNING: structure, page number, non-blocking

When the divergence predicate (FDR-003) returns True for a guía, `ReconciliationService`
MUST emit a structured divergence WARNING. The WARNING MUST:

- Be a **non-blocking side-channel**: the material reconciliation status (MATCH / MISMATCH /
  DECLARED_MISSING / GUIA_MISSING) for the group MUST NOT change because of fecha divergence.
- Set `requires_review = True` on the **guía contribution record** (not the group status).
- Set `review_reason` to include `"fecha_divergence"` (may be additive with other review reasons).
- Carry the guía's **page number** (1-indexed, source PDF page) in the warning payload. The page
  number MUST be taken from the guía's existing `source_pages` field (already populated in rev-3).
  If `source_pages` contains multiple pages, ALL page numbers MUST be included.
- Be included in the API response structure so the frontend can render the RED highlight
  and page reference without additional backend calls.

The WARNING MUST NOT auto-correct, auto-reassign, or modify any quantity, key, or declared field.
The existing `GuiaReassignDialog` flow remains the only reassignment path.

#### Acceptance Scenarios

**Scenario FDR-S07 — Diverging guía: WARNING carries page number**

Given a guía on PDF page 7 with handwritten date day=15, month=04
And the Registro declared date is day=28, month=05
When `ReconciliationService` runs the divergence check
Then a WARNING is produced with `guia_id = <id>`, `source_pages = [7]`, `review_reason = "fecha_divergence"`
And `requires_review = True` is set on that guía's contribution record
And the group's MATCH/MISMATCH status is unchanged
And the group's summed quantity is unchanged

**Scenario FDR-S08 — Non-diverging guía: no WARNING, no requires_review from fecha**

Given a guía with handwritten date day=28, month=05
And the Registro declared date is day=28, month=05
When `ReconciliationService` runs the divergence check
Then no fecha-divergence WARNING is produced for this guía
And `requires_review` on this guía's contribution record is NOT set by fecha-divergence
(other review flags from material matching are unaffected)

**Scenario FDR-S09 — WARNING does not change material status**

Given a reconciliation group with declared qty = 4.124 TN and summed guía qty = 4.124 TN
And one of the contributing guías has a fecha divergence (day-month mismatch)
When `ReconciliationService` produces the result
Then the group status is MATCH (qty comparison is the sole determinant of MATCH)
And the diverging guía has `requires_review = True` and `review_reason` includes `"fecha_divergence"`
And the MATCH status is NOT changed to MISMATCH or any other status due to fecha divergence

---

### FDR-005 — [MODIFIED 2026-06-03] Null declared date: pipeline WARNING, no guía divergence warnings

**Correction**: the original FDR-005 stated the null case came from vision extraction returning
null. After the FDR-001 correction, the declared date is the digital parse (`fecha_declarada`).
A null declared date means the digital parser found no "Fecha:" field on the Protocolo page.

When `fecha_declarada` is `None` on a Registro that has a `protocolo_page` set:
- The pipeline (Stage 4b guard) MUST emit a WARNING in `PipelineResult.warnings` with the
  Registro numero and page index (operator locates and corrects the source PDF).
- `ReconciliationService` MUST NOT emit per-guía fecha-divergence WARNINGs against a null
  declared baseline. A null baseline means "cannot validate", NOT "all guías diverge".
- The material MATCH/MISMATCH statuses for all groups under that Registro MUST be unaffected.

`requires_review` propagation to individual guías is NOT required by this fix (existing
model/flow does not support it cleanly); the pipeline-level WARNING is sufficient for operator
visibility (SA-2: no build-for-the-sake-of-building).

#### Acceptance Scenario

**Scenario FDR-S10 — Null declared date: pipeline WARNING, no guía divergence WARNINGs**

Given a Registro N° where `digital_text_extractor.py` yields `fecha_declarada = None`
And that Registro has `protocolo_page` set (Protocolo page found in the PDF)
And that Registro has three contributing guías with handwritten dates
When the pipeline runs
Then a WARNING appears in `PipelineResult.warnings` identifying the Registro by numero and page
And `ReconciliationService.reconcile` does NOT emit per-guía fecha-divergence WARNINGs
And all material MATCH/MISMATCH outcomes for those guías are unaffected

---

### FDR-006 — [ADDED] Null guía date: unknown, not divergent

When a guía's handwritten date is null (already vision-read as null; existing behaviour for
~13/35 guías) the divergence check MUST treat the guía date as UNKNOWN, not as divergent.

A null guía date MUST NOT trigger a fecha-divergence WARNING.
The existing `requires_review` flag from the null-fecha path (rev-3 behaviour) remains;
no new flag is added by this change.

#### Acceptance Scenario

**Scenario FDR-S11 — Null guía date: no fecha-divergence WARNING**

Given a Registro with declared date day=28, month=05
And a guía for that Registro with `handwritten_fecha = null`
When `ReconciliationService` runs the divergence check for that guía
Then no fecha-divergence WARNING is emitted
And the guía's existing `requires_review` state (from null-fecha) is preserved unchanged
And no RED highlight from fecha-divergence is shown for this guía in the frontend

---

### FDR-007 — [SUPERSEDED 2026-06-03] Low-confidence Protocolo date read — no longer applicable

**Status**: superseded by FDR-001 correction (2026-06-03).

The original FDR-007 handled the case where `VisionLLMPort` returned a low-confidence date
for the Protocolo vision read. Since FDR-001 now mandates a DIGITAL parse (no vision call on
the declared side), the concept of "low-confidence declared date" no longer applies.

The digital parse (`digital_text_extractor.py`) is deterministic: it either extracts a date
or returns `None`. The `None` case is handled by FDR-005 (pipeline WARNING).

**Confidence gate on guía dates** (unchanged): the 0.85 confidence threshold still applies to
the guía-side `VisionLLMPort` calls (`_stage_extract_vision`). A low-confidence guía date read
results in `fecha = None` on the guía, which is then handled by FDR-006 (null guía date →
not divergent). This guía-side confidence gate is not modified by this correction.

#### Acceptance Scenarios

**Scenario FDR-S12 — Digital declared date: no confidence gate; parse is deterministic**

Given a Protocolo page with printed "Fecha: 28-05-26"
When `digital_text_extractor.py` parses the date
Then `fecha_declarada = date(2026, 5, 28)` with certainty (no confidence score)
And the divergence check uses `date(2026, 5, 28)` as the declared baseline

**Scenario FDR-S13 — Declared date present; guía date matches: no WARNING**

Given `fecha_declarada = date(2026, 5, 28)` on the Registro
And a guía with handwritten day=28, month=05
When `ReconciliationService` runs
Then the divergence check returns False (same day-month)
And no false WARNING is emitted for the matching guía

---

### FDR-008 — [ADDED] API response: page number and divergence flag mandatory fields

The API response MUST surface, per guía contribution record:

- `source_pages: list[int]` — the 1-indexed PDF page number(s) for that guía (already
  exists in rev-3; MUST remain populated and MUST be included in any response that carries
  divergence information).
- `fecha_divergence: bool` — True when a fecha-divergence WARNING was emitted for this guía.
- `review_reasons: list[str]` — additive list of review reason codes; `"fecha_divergence"`
  appended when applicable (alongside existing codes such as `"null_fecha"`, `"year_inferred"`).

These fields MUST be present on every guía contribution record in the response, defaulting
to `fecha_divergence = False` and `review_reasons = []` when no divergence and no other review
flags are active.

The Registro-level response MUST carry:
- `registro_review_reasons: list[str]` — includes `"declared_date_missing"` or
  `"declared_date_low_confidence"` when applicable (FDR-005, FDR-007).

#### Acceptance Scenario

**Scenario FDR-S14 — API response shape: diverging guía carries page and flag**

Given a completed reconciliation run where guía on page 7 has a fecha divergence
When the API response is serialised
Then the guía contribution entry includes `source_pages = [7]`
And `fecha_divergence = True`
And `review_reasons` contains `"fecha_divergence"`
And the group's material status field is MATCH or MISMATCH (unaffected by divergence)

---

### FDR-009 — [ADDED] Frontend: RED highlight for diverging guías (individual and grouped)

The frontend MUST visually flag cada diverging guía in RED, providing the engineer with an
unambiguous visual indicator of potential misfiling.

Specific surfaces:

- **`GuiaDrillDown.vue`**: when `fecha_divergence = True` on a guía contribution, the guía
  row MUST render with a RED visual treatment (border, background, or badge — consistent with
  the existing error/warning badge pattern used by `YearInferredBadge.vue` and
  `ConfidenceBadge.vue`).
- **`SourcePages.vue`**: the page reference (1-indexed PDF page number from `source_pages`)
  MUST be shown alongside the RED indicator so the engineer can locate the physical page.
- **`ReconciliationRow.vue`**: when ANY guía under a registro has `fecha_divergence = True`,
  the row MUST display a group divergence indicator (count of diverging guías or a summary
  badge) so the engineer can identify affected registros at a glance without expanding every row.
- **`UnresolvedGuiasPanel.vue`** (or equivalent grouped surface): diverging guías MUST also
  appear in the unresolved/requires-review panel grouped by Registro N°, consistent with the
  existing grouping pattern for other review items.

The RED highlight MUST be applied **per diverging guía individually** AND at the
**grupo/Registro level** when multiple guías diverge.

The existing `GuiaReassignDialog.vue` flow (manual reassign) is the resolution path — no
new UI workflow is introduced by this change.

#### Acceptance Scenarios

**Scenario FDR-S15 — Single diverging guía: RED highlight in GuiaDrillDown**

Given a Registro N° with three guías where one has `fecha_divergence = True` and `source_pages = [7]`
When the frontend renders the GuiaDrillDown for that Registro
Then the diverging guía row renders with RED visual treatment
And the page reference "page 7" is shown adjacent to the RED indicator
And the other two non-diverging guías render without RED treatment

**Scenario FDR-S16 — Multiple diverging guías: group indicator on ReconciliationRow**

Given a Registro N° with five guías where three have `fecha_divergence = True`
When the frontend renders the ReconciliationRow for that Registro
Then the row shows a group divergence indicator (e.g. "3 guías with fecha divergence")
And the indicator is visible without expanding the row

**Scenario FDR-S17 — No diverging guías: no RED highlight rendered**

Given a Registro N° where all guías have `fecha_divergence = False`
When the frontend renders the ReconciliationRow and GuiaDrillDown
Then no fecha-divergence RED highlight or badge is rendered for any guía in that Registro

---

### FDR-010 — [ADDED] Divergence check is pure domain; no new port

The fecha-divergence comparison logic MUST be implemented as a pure domain operation in
`backend/src/reconciliation/domain/`. It MUST NOT import any SDK, I/O library, adapter-layer,
or infrastructure module.

The divergence check reads from already-extracted date fields on `Registro` and
`GuiaDeRemision` domain objects — it does not perform any I/O or vision call itself.

No new `Port` (Protocol) is introduced for the divergence check. After FDR-001 correction
(2026-06-03), no `VisionLLMPort` call is made for the declared date — `VisionLLMPort` is
used ONLY for guía stamp dates (`_stage_extract_vision`).

#### Acceptance Scenario

**Scenario FDR-S18 — Divergence check: no I/O, independently unit-testable**

Given `Registro` with `fecha_declarada = date(2026, 5, 28)` (digital parse)
And three `GuiaDeRemision` objects with dates: matching, diverging, and null
When the divergence check function is called with these objects directly (no mocks of HTTP/IO)
Then the function returns: no warning for the matching guía, a WARNING for the diverging guía,
  and no warning (but unknown flag) for the null guía
And the test requires no adapter, LLM endpoint, or PDF file to run

---

### FDR-011 — [ADDED] Material MATCH unaffected: fecha is not a group key component

`fecha` MUST NOT be added back to the material grouping key `(registro, canonical_key, unidad)`
as a result of this change. The grouping key established by MAT-001 (Slice 1) is final for this
axis. The divergence check operates exclusively as a post-grouping validation side-channel.

Any code path introduced by this change that touches the grouping logic MUST NOT introduce
a conditional or branch that implicitly folds `fecha` back into group identity.

#### Acceptance Scenario

**Scenario FDR-S19 — fecha divergence does not split a MATCH group**

Given a canonical group `(registro_232, BARRA A615 G60 1/2" 9M, TN)` with declared qty 4.124
And two contributing guías: one with fecha matching declared, one with fecha diverging
And the summed guía qty = 4.124 TN
When `ReconciliationService` reconciles
Then the group status is MATCH (both guías contribute to the same group regardless of fecha)
And the diverging guía has `fecha_divergence = True` in its contribution record
And the MATCH status is not split into two separate groups by the fecha difference

---

## Out of scope for this change

- Changing the material grouping key (Slice 1 / MAT-001 owns this; MUST NOT be touched).
- Auto-reassignment of diverging guías (manual engineer action through existing `GuiaReassignDialog`).
- SUNAT integration or online fetch (air-gap preserved; no network dependency added).
- Material canonical matching, grade/diameter normalisation (Slice 1 / MAT-001 through MAT-013).
- New vision providers, batching changes, or persistence/DB schema changes beyond adding
  the Protocolo-date read and the divergence fields.
- Unit conversion between KG / TN / RD / Rollo (forbidden domain invariant; irrelevant to dates).
- Exporting divergence flags to xlsx/csv (review-grid-only for this change; deferred).
- Automatic date correction or inference-driven reassignment.
- ±1 day tolerance or any fuzzy matching on the date comparison.

---

## Delta — r9b (2026-06-03): physical delivery floor — SUNAT fecha_entrega as lower bound

> The requirements below ADD new behaviour relative to FDR-001 through FDR-011 above.
> Source change: `r9b-reception-date-delivery-floor` (merged via PR #5).
> Gate: Judgment-Day APPROVED after 2 rounds. 892 backend unit tests.
> Each entry is marked [ADDED].

### FDR-012 — [ADDED] Reception date delivery floor: SUNAT fecha_entrega as physical lower bound

When a guía has an associated SUNAT `fecha_entrega` (delivery date from `OfficialGre`,
available via `sunat_fetch_map` when `sunat.enabled = true`), the resolved reception date
for that guía MUST satisfy the physical invariant: **reception date >= delivery date**.

The pipeline MUST apply the following four-branch floor function
(`domain/date_floor.apply_delivery_floor`) in `_stage_normalize_dates` AFTER
`infer_reception_year`:

| Branch | Condition | Result | `delivery_floor_applied` |
|--------|-----------|--------|--------------------------|
| Rule 1 | `fecha_entrega` is None | Passthrough — `reception` unchanged | False |
| Rule 2 | `reception` is None, `fecha_entrega` is not None | Floor to `fecha_entrega` | True |
| Rule 3 | `reception < fecha_entrega` | Floor to `fecha_entrega` | True |
| Rule 4 | `reception >= fecha_entrega` | Unchanged | False |

When `delivery_floor_applied = True`, the pipeline MUST OR-set `requires_review = True`
on the guía and emit a non-blocking WARNING with reason `"delivery_floor_applied"`.

The material MATCH/MISMATCH status for the group MUST NOT change because of the floor.
The floor MUST NOT auto-correct quantities, keys, or declared values.

When SUNAT is disabled (`sunat.enabled = false`, the default) or `fecha_entrega` is absent,
the floor is a provable no-op (Rule 1 passthrough) and the run is byte-identical to the R9
baseline.

**Rule 3 is defense-in-depth** (unreachable through the normal path because
`infer_reception_year(lower=fecha_entrega)` already constrains candidate years to
`>= lower`). It is included to keep the domain function self-consistent and to guard
against future refactors that bypass the lower-bound constraint.

#### Acceptance Scenarios

**Scenario FDR-S20 — SUNAT absent (disabled): floor is a no-op**

Given `sunat.enabled = false` (default)
And a guía with resolved reception date `2026-05-28` (via year inference)
When `_stage_normalize_dates` runs
Then `apply_delivery_floor` is called with `fecha_entrega = None`
And the guía's `fecha` remains `2026-05-28` (Rule 1 passthrough)
And `delivery_floor_applied = False`
And `requires_review` is NOT set by the floor
And the run output is byte-identical to the R9 baseline

**Scenario FDR-S21 — Reception date before delivery date: floor applied**

Given `sunat.enabled = true`
And a guía whose `OfficialGre.fecha_entrega = 2026-05-20`
And `infer_reception_year` resolves to `reception = 2025-05-28` (year-inference artifact)
When `apply_delivery_floor(reception=date(2025,5,28), fecha_entrega=date(2026,5,20))` is called
Then the returned date is `date(2026,5,20)` (Rule 3 floor)
And `delivery_floor_applied = True`
And the guía's `requires_review = True` with reason `"delivery_floor_applied"`
And the group's MATCH/MISMATCH status is unaffected

**Scenario FDR-S22 — Reception date on or after delivery date: no floor**

Given `sunat.enabled = true`
And a guía with `OfficialGre.fecha_entrega = 2026-05-20`
And `infer_reception_year` resolves to `reception = 2026-05-28`
When `apply_delivery_floor(reception=date(2026,5,28), fecha_entrega=date(2026,5,20))` is called
Then the returned date is `date(2026,5,28)` (Rule 4 — unchanged)
And `delivery_floor_applied = False`
And `requires_review` is NOT set by the floor

---

### FDR-013 — [ADDED] Null reception date with known delivery date: floor to fecha_entrega

When a guía's handwritten day AND month are BOTH absent from the vision read (vision returned
no parseable date), AND `fecha_entrega` is known (SUNAT enabled and `OfficialGre` present),
the pipeline MUST floor the reception date to `fecha_entrega` BEFORE calling
`infer_reception_year` (since inference has no day/month to operate on).

The pipeline MUST:
- Set `GuiaDeRemision.fecha = fecha_entrega`
- Set `GuiaDeRemision.delivery_floor_applied = True`
- OR-set `GuiaDeRemision.requires_review = True`

When SUNAT is disabled or `fecha_entrega` is absent and day/month are null, the
reception date MUST remain null (existing R9 null-fecha handling — FDR-006 unchanged).

#### Acceptance Scenario

**Scenario FDR-S23 — Null vision date with known delivery: floor to fecha_entrega**

Given `sunat.enabled = true`
And a guía where the vision LLM returns no parseable date (day = None, month = None)
And the guía's `OfficialGre.fecha_entrega = 2026-05-20`
When `_stage_normalize_dates` processes this guía
Then `GuiaDeRemision.fecha = date(2026, 5, 20)` (Rule 2 / FDR-012 floor)
And `delivery_floor_applied = True`
And `requires_review = True`
And divergence check may run against the floored date per the standard FDR-003 path
  (floored date is no longer null; FDR-006 null-guía-date path does NOT apply)

---

## Delta — r9c (2026-06-03): reception date ceiling — Protocolo authoritative date as physical upper bound

> The requirements below ADD new behaviour relative to FDR-001 through FDR-013 above.
> Source change: `r9c-reception-date-ceiling` (merged via PR #8).
> Gate: Judgment-Day APPROVED after 3 rounds + 2 fix iterations. 972 backend unit tests.
> Each entry is marked [ADDED].

### FDR-014 — [ADDED] Reception date ceiling: Protocolo authoritative date as physical upper bound

When a Registro's authoritative declared reception date (`fecha_authoritative`, the DIGITAL
Protocolo date from `fecha_declarada` per FDR-001 as corrected 2026-06-03) is available
(i.e. `fecha_declarada is not None`), each guía's resolved reception date MUST satisfy the
physical invariant: **reception date <= Protocolo authoritative date**.

The domain service (`ReconciliationService.reconcile`) MUST apply the ceiling function
(`domain/date_ceiling.apply_reception_ceiling`) to each guía's resolved date AFTER the
R9 fecha-divergence check (`check_fecha_divergence`) and AFTER the R9b delivery floor
(`apply_delivery_floor`). This ordering is **CRITICAL** — the divergence check MUST
operate on the ORIGINAL resolved date so the R9 misfiled-guía signal is never masked by
the ceiling clamp.

The ceiling function MUST implement the following four-branch logic:

| Branch | Condition | Result | Side-channels |
|--------|-----------|--------|---------------|
| Rule 1 | `protocolo_ceiling` is None | Passthrough — `reception` unchanged | `ceiling_applied=False`, `crossed_bounds=False` |
| Rule 2 (crossed-bounds) | `fecha_entrega` is not None AND `fecha_entrega > protocolo_ceiling` | Passthrough — do NOT clamp; emit `delivery_after_protocolo` WARNING | `ceiling_applied=False`, `crossed_bounds=True` |
| Rule 3 | `reception > protocolo_ceiling` | Clamp DOWN to `protocolo_ceiling` | `ceiling_applied=True`, `crossed_bounds=False` |
| Rule 4 | `reception <= protocolo_ceiling` (and not crossed-bounds) | Unchanged | `ceiling_applied=False`, `crossed_bounds=False` |

When `ceiling_applied = True`, the domain service MUST:
- Set `GuiaDeRemision.reception_ceiling_applied = True`
- OR-set `GuiaDeRemision.requires_review = True` with reason `"reception_ceiling_applied"`
- Propagate `reception_ceiling_applied` through `GuiaContribution`
- Surface `ReconciliationRow.has_reception_ceiling` (computed: `any(g.reception_ceiling_applied for g in self.guias)`)

The material MATCH/MISMATCH status for the group MUST NOT change because of the ceiling.
The ceiling MUST NOT auto-correct quantities, keys, or declared values.

When the Protocolo ceiling is unavailable (`fecha_declarada = None` per FDR-005),
the ceiling is a provable no-op (Rule 1 passthrough) and the run is byte-identical to the
R9b baseline.

#### Acceptance Scenarios

**Scenario FDR-S24 — Protocolo ceiling absent: ceiling is a no-op**

Given the Registro N° has `fecha_declarada = None` (digital parse found no date)
And a guía with resolved reception date `2026-05-28`
When `apply_reception_ceiling(reception=date(2026,5,28), protocolo_ceiling=None, fecha_entrega=...)` is called
Then Rule 1 passthrough applies
And `reception_ceiling_applied = False`
And `crossed_bounds = False`
And the guía's `fecha` remains `2026-05-28`
And `requires_review` is NOT set by the ceiling
And the run output is byte-identical to the R9b baseline

**Scenario FDR-S25 — Reception date exceeds Protocolo ceiling: clamp applied**

Given the Registro N° has `fecha_authoritative = date(2026, 5, 28)` (Protocolo ceiling)
And a guía whose resolved reception date is `date(2027, 5, 28)` (year-inference artifact)
And `fecha_entrega = date(2026, 5, 20)` (floor, does not exceed ceiling)
When `apply_reception_ceiling(reception=date(2027,5,28), protocolo_ceiling=date(2026,5,28), fecha_entrega=date(2026,5,20))` is called
Then Rule 3 applies: returned date is `date(2026, 5, 28)` (clamped to ceiling)
And `ceiling_applied = True`
And `crossed_bounds = False`
And `GuiaDeRemision.reception_ceiling_applied = True`
And `requires_review = True` with reason `"reception_ceiling_applied"`
And the group's MATCH/MISMATCH status is unaffected

**Scenario FDR-S26 — Reception date at or below Protocolo ceiling: no clamp**

Given `fecha_authoritative = date(2026, 5, 28)` (Protocolo ceiling)
And a guía with resolved reception date `date(2026, 5, 15)` (below ceiling)
When `apply_reception_ceiling(reception=date(2026,5,15), protocolo_ceiling=date(2026,5,28), fecha_entrega=None)` is called
Then Rule 4 applies: returned date is `date(2026, 5, 15)` (unchanged)
And `ceiling_applied = False`
And `requires_review` is NOT set by the ceiling

**Scenario FDR-S27 — Ceiling does NOT mask R9 divergence WARNING**

Given `fecha_authoritative = date(2026, 5, 28)` (Protocolo ceiling)
And a guía whose ORIGINAL resolved date is `date(2027, 4, 15)` (day=15, month=04 — diverges from declared day=28, month=05)
And the guía ALSO exceeds the ceiling
When `ReconciliationService.reconcile` runs for this guía
Then the R9 fecha-divergence WARNING IS emitted (ORIGINAL date diverges: month 04 ≠ 05)
And AFTER the divergence check, the ceiling clamp is applied: date → `date(2026, 5, 28)`
And `reception_ceiling_applied = True`
And both the fecha-divergence WARNING and the ceiling side-channel are present on the contribution record
And the divergence WARNING was NOT suppressed by the ceiling

---

### FDR-015 — [ADDED] Crossed-bounds anomaly: delivery after Protocolo — warn, do NOT clamp

When the SUNAT delivery floor (`fecha_entrega`) is LATER than the Protocolo authoritative
ceiling (`fecha_authoritative`), the physical invariant is violated at the input level: the
supplier delivered goods after they were supposedly received. This is always a
**Protocolo-assembly human error** (e.g., wrong Registro N° stamped on the Protocolo page).

The system MUST NOT clamp in either direction when crossed-bounds is detected:
- Clamping to the ceiling would push the date below the SUNAT floor (violating FDR-012).
- Clamping to the floor would push the date above the ceiling (violating FDR-014).

Instead the system MUST:
- Leave the guía's resolved reception date unchanged (original value).
- Set `GuiaDeRemision.delivery_after_protocolo = True` (crossed-bounds side-channel).
- OR-set `GuiaDeRemision.requires_review = True` with reason `"delivery_after_protocolo"`.
- Emit a distinct `delivery_after_protocolo` WARNING in the reconciliation result.
- Propagate `delivery_after_protocolo` through `GuiaContribution` → API DTO.

The `delivery_after_protocolo` WARNING is **non-blocking** — the material MATCH/MISMATCH
status for the group MUST NOT change because of the crossed-bounds anomaly. No auto-correction
is applied. The existing `GuiaReassignDialog` flow is the human resolution path.

#### Acceptance Scenarios

**Scenario FDR-S28 — Crossed-bounds: delivery after Protocolo — warn, no clamp**

Given `fecha_authoritative = date(2026, 5, 20)` (Protocolo ceiling)
And a guía with `fecha_entrega = date(2026, 5, 28)` (delivery floor AFTER ceiling)
And the guía's resolved reception date is `date(2026, 5, 25)` (between floor and ceiling — impossible bracket)
When `apply_reception_ceiling(reception=date(2026,5,25), protocolo_ceiling=date(2026,5,20), fecha_entrega=date(2026,5,28))` is called
Then Rule 2 (crossed-bounds) applies
And the returned date is `date(2026, 5, 25)` (UNCHANGED — no clamp)
And `ceiling_applied = False`
And `crossed_bounds = True`
And `GuiaDeRemision.delivery_after_protocolo = True`
And `requires_review = True` with reason `"delivery_after_protocolo"`
And the group's MATCH/MISMATCH status is unaffected

**Scenario FDR-S29 — Crossed-bounds does NOT prevent R9b floor from having been applied**

Given `fecha_authoritative = date(2026, 5, 20)` (Protocolo ceiling)
And `fecha_entrega = date(2026, 5, 28)` (floor AFTER ceiling — crossed-bounds)
And the guía had `reception=None` so R9b floor was already applied in `_stage_normalize_dates`
  giving `fecha = date(2026, 5, 28)` with `delivery_floor_applied = True`
When `ReconciliationService.reconcile` applies the ceiling check
Then crossed-bounds is detected (`fecha_entrega=2026-05-28 > ceiling=2026-05-20`)
And the ceiling does NOT clamp the already-floored date
And `delivery_after_protocolo = True` and `delivery_floor_applied = True` BOTH appear on the contribution
And `requires_review = True` with BOTH reasons `["delivery_floor_applied", "delivery_after_protocolo"]`

---

### FDR-016 — [ADDED] Persistence of fecha_entrega and delivery_dates across review re-reconcile

`GuiaDeRemision.fecha_entrega: date | None` MUST be a **persisted field** on the domain
model — not a transient pipeline variable. It MUST be serialized with the extraction cache
/ sidecar so that any code path that loads a `GuiaDeRemision` from storage has access to
`fecha_entrega` without an additional SUNAT fetch.

The `delivery_dates` map (guia_id → `fecha_entrega`) MUST be rebuilt from the loaded guías
before EVERY call to `ReconciliationService.reconcile`. This rebuild MUST occur on ALL the
following code paths:

1. `application/pipeline.py` `_stage_reconcile` (pipeline run)
2. `services/review_service.py` — guía reassign path
3. `services/review_service.py` — guía line-edit path
4. `services/review_service.py` — guía field-edit path

The rebuild pattern MUST be:

```python
delivery_dates = {
    g.id: g.fecha_entrega
    for g in guias
    if g.fecha_entrega is not None
}
```

This pattern MUST NOT rely on a separately maintained in-memory map (e.g., a pipeline-scoped
`sunat_fetch_map`) that is not available on the ReviewService path.

**Rationale**: without this invariant, any review action (reassign, edit) that triggers
`ReconciliationService.reconcile` internally would do so with `delivery_dates={}`,
silently dropping the R9b floor, the R9c ceiling, and the crossed-bounds guard. The result
would be incorrect bracket behavior on every row touched by a review action — a correctness
regression invisible to the material MATCH/MISMATCH status but actively wrong for the
date-bracket domain invariant.

#### Acceptance Scenarios

**Scenario FDR-S30 — fecha_entrega survives review reassign: bracket applied post-reassign**

Given a guía with `fecha_entrega = date(2026, 5, 20)` persisted on `GuiaDeRemision`
And a review action reassigns that guía to a different Registro N°
When `ReviewService.reassign_guia` calls `ReconciliationService.reconcile` internally
Then `delivery_dates` is rebuilt from guías (including the reassigned guía's `fecha_entrega`)
And the R9b floor and R9c ceiling bracket is applied on the post-reassign reconcile result
And `delivery_floor_applied` and `reception_ceiling_applied` reflect the current bracket state

**Scenario FDR-S31 — SUNAT disabled: delivery_dates is empty; bracket is no-op**

Given `sunat.enabled = false` (default)
And all guías have `fecha_entrega = None` (no SUNAT data)
When `delivery_dates` is rebuilt from guías
Then `delivery_dates = {}` (empty — no floor/ceiling inputs available)
And `ReconciliationService.reconcile(delivery_dates={})` produces output byte-identical to the R9 baseline
And neither `delivery_floor_applied` nor `reception_ceiling_applied` is set on any guía

**Scenario FDR-S32 — fecha_entrega backward-compat: old cache without field deserializes cleanly**

Given a serialized extraction cache produced before R9c (no `fecha_entrega` field on guías)
When the cache is deserialized into `GuiaDeRemision` objects
Then `GuiaDeRemision.fecha_entrega` defaults to `None`
And no deserialization error occurs
And the run degrades gracefully (bracket is no-op for all guías, per FDR-S31)
