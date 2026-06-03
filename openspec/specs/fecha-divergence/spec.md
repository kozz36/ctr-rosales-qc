# Spec — Fecha Divergence Review
**Change**: r9-fecha-divergence-review
**Domain**: fecha-divergence (delta over reconciliation domain)
**Phase**: spec
**Date**: 2026-06-02

---

## Purpose

Restore the misfiled-guía detection signal that Slice 1 (`r8-material-matching` / MAT-001)
left unhandled by removing `fecha` from the grouping key. This spec mandates:

1. The authoritative declared reception date for a Registro N° is the HANDWRITTEN "Fecha:"
   on the Protocolo de Recepción sheet (vision-read), NOT the electronic `fecha_declarada`.
2. A per-guía fecha-divergence check (pure domain) comparing each guía's handwritten date
   against the registro's handwritten declared date.
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

### FDR-001 — [ADDED] Declared reception date authority: handwritten Protocolo date

The authoritative declared reception date for a Registro N° MUST be the HANDWRITTEN
"Fecha:" field on the **Protocolo de Recepción** sheet, vision-read via the existing
`VisionLLMPort`.

The existing electronic `fecha_declarada` field (parsed from digital text) MUST NOT be
used as the declared date for divergence comparison or for any display that represents the
authoritative reception event. The electronic value MAY be retained for provenance/audit
purposes but MUST NOT be the comparison baseline.

One handwritten declared date MUST be extracted per Registro N°. The existing `VisionLLMPort`
MUST be reused for this extraction — no new port is introduced. The crop target is the
"Fecha:" field area on the Protocolo page (stamp/field-crop strategy, with a full-page
fallback) using the same handwritten-date read operation already applied to guía pages.

#### Acceptance Scenarios

**Scenario FDR-S01 — Registro 232: Protocolo declared date reads 2026-05-28**

Given the Protocolo de Recepción page for Registro 232 contains handwritten text "Fecha: 28-05-26"
When `VisionLLMPort` is called against that page with the Protocolo field-crop
Then the extracted raw date is `day=28, month=05, year=26` (or equivalent day/month signal)
And after bounded year inference the resolved declared date is `2026-05-28`
And `Registro.fecha_declarada_handwritten` (or equivalent field) is set to `2026-05-28`
And the electronic `fecha_declarada` is NOT used as the declared baseline

**Scenario FDR-S02 — Declared date source is Protocolo page, not guía pages**

Given a Registro N° whose Protocolo page and guía pages are both present in the run
When the pipeline reads the declared reception date
Then the declared date originates from the Protocolo page read
And the guía page reads are NOT used to set the declared date
And the two extraction paths (declared vs guía) operate independently

---

### FDR-002 — [ADDED] Declared date year inference: same bounds as guías

The handwritten year on the Protocolo page MUST be treated as unreliable (see discovery
#2753: vision year may read as 2016 or 2022 instead of 2026). Day and month from vision
are the trusted signals.

The declared date MUST be reconstructed via the existing bounded `infer_reception_year`
function, with the same bounding parameters applied to guía dates. The declared date and
guía dates MUST go through year inference with consistent bounds so the same physical
calendar date does not reconstruct to different years on each side.

If year inference is applied to guía dates using a SUNAT `fecha_entrega` as a lower bound,
that lower bound MUST NOT be applied exclusively to one side — either it applies to both or
neither, so the reconstructed years remain comparable.

#### Acceptance Scenario

**Scenario FDR-S03 — Declared and guía year inference use the same bounds**

Given a Protocolo handwritten date `28-05-26` (day 28, month 05, year 26)
And a guía handwritten date `28-05-26` on a guía belonging to that Registro
And no SUNAT lower-bound information is available for either side
When year inference runs on both
Then both resolve to `2026-05-28`
And the divergence check produces no divergence (same reconstructed date)
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

### FDR-005 — [ADDED] Null declared date: registro-level review flag, no guía warnings

When vision extraction of the Protocolo handwritten date returns null (no "Fecha:" field
found, unreadable, or extraction confidence is zero), `ReconciliationService` MUST:

- Flag the **Registro** `requires_review = True` with `review_reason` including
  `"declared_date_missing"`.
- MUST NOT emit per-guía fecha-divergence WARNINGs against a null declared baseline.
  A null baseline means "cannot validate", NOT "all guías diverge".
- The material MATCH/MISMATCH statuses for all groups under that Registro MUST be unaffected.

#### Acceptance Scenario

**Scenario FDR-S10 — Null declared date: registro flagged, no guía divergence WARNINGs**

Given a Registro N° where VisionLLMPort returns no date for the Protocolo page (null)
And that Registro has three contributing guías with handwritten dates
When `ReconciliationService` runs
Then the Registro is flagged `requires_review = True` with reason `"declared_date_missing"`
And NO fecha-divergence WARNING is emitted for any of the three guías
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

### FDR-007 — [ADDED] Low-confidence Protocolo date read: registro review flag

When `VisionLLMPort` returns a date for the Protocolo page but with confidence below
**0.85**, the declared date MUST NOT be used as the authoritative divergence baseline.
Instead the system MUST:

- Flag the **Registro** `requires_review = True` with `review_reason` including
  `"declared_date_low_confidence"`.
- MUST NOT emit per-guía fecha-divergence WARNINGs based on a low-confidence declared date.
  The low-confidence case is treated identically to the null case (FDR-005) for the purpose
  of divergence emission.
- The low-confidence date value MAY be stored as a `fecha_declarada_tentative` field for
  display in the review UI (read-only, labelled as unconfirmed), but MUST NOT be the
  comparison baseline.

This requirement prevents the "all guías falsely diverge" failure mode where a vision
misread of the Protocolo date causes every guía to appear misfiled.

#### Acceptance Scenarios

**Scenario FDR-S12 — Low-confidence declared date: registro flagged, no guía WARNINGs**

Given a Registro N° where VisionLLMPort returns a date with confidence = 0.72 (below 0.85)
And that Registro has two contributing guías with handwritten dates
When `ReconciliationService` runs
Then the Registro is flagged `requires_review = True` with reason `"declared_date_low_confidence"`
And NO fecha-divergence WARNING is emitted for either guía
And all material MATCH/MISMATCH outcomes are unaffected

**Scenario FDR-S13 — High-confidence declared date: divergence check proceeds normally**

Given a Registro N° where VisionLLMPort returns a date with confidence = 0.92 (above 0.85)
And a guía with day-month matching the declared date
When `ReconciliationService` runs
Then no `declared_date_low_confidence` flag is set on the Registro
And the divergence check runs normally
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

No new `Port` (Protocol) is introduced for the divergence check. The existing `VisionLLMPort`
is the sole new I/O surface (used for Protocolo date extraction only, covered by FDR-001).

#### Acceptance Scenario

**Scenario FDR-S18 — Divergence check: no I/O, independently unit-testable**

Given `Registro` with `fecha_handwritten = date(2026, 5, 28)` (or day/month equivalent)
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
