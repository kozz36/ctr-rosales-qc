# Proposal — r9-fecha-divergence-review

**Change**: `r9-fecha-divergence-review`
**Phase**: proposal (done) → spec / design (next, parallel)
**Artifact store**: hybrid (engram + openspec)
**Date**: 2026-06-02
**Parent**: Slice 2 of the reception-date-authority decision (engram `architecture/reception-date-authority` #2709). Builds on Slice 1 (`r8-material-matching` / MAT-001), which removed `fecha` from the material grouping key.

---

## 1. Intent

### Problem
The reconciliation engine has two date defects that survive Slice 1:

1. **Wrong declared-date source.** The declared reception date today is the electronic `Registro.fecha_declarada` (parsed from digital text). The user's authoritative domain decision (#2709) is that the **declared reception date is the HANDWRITTEN "Fecha:" field on the Protocolo de Recepción sheet**, vision-read — confirmed by the Registro 232 Protocolo showing handwritten `28-05-26`. The electronic date is not the truth the engineer signs against.

2. **Date divergence is invisible.** Slice 1 correctly stopped folding `fecha` into the group key (vision-read date noise — unreliable year, day variance — was killing MATCH). But it left the divergence signal *unhandled*: `reconciliation.py` explicitly defers fecha-divergence as "rev-4, out of scope." Right now a guía whose handwritten reception date does not match the registro's reception date is summed into the group with **no warning** — the misfiled-guía signal is silently lost.

### Why now
Slice 1 makes material MATCH resolve, but it does so by dropping the only mechanism that previously surfaced misfiled guías. Without Slice 2 the tool matches quantities while hiding the exact problem the engineer needs to catch: a guía filed under the wrong reception event. The declared-date source and the divergence check are the missing half of the #2709 decision; they must land together so the date axis is correct end-to-end (authoritative source + reviewable divergence) rather than half-migrated.

### Success looks like
- The declared reception date for a registro is the **handwritten Protocolo date** (vision-read), not the electronic `fecha_declarada`. Registro 232 reports declared `2026-05-28` from the handwritten `28-05-26`.
- Each guía's handwritten reception date is **compared** against its registro's handwritten declared date.
- On divergence → a **no-match WARNING** is emitted that does **NOT** block the material MATCH (grouping is date-independent after Slice 1) but flags the guía as potentially misfiled.
- The warning carries the guía's **page number(s)** and drives a **RED highlight** of the guía in the frontend — individually, or grouped when several guías diverge — so the engineer can locate and reassign it manually.
- Year inference behavior is preserved: day/month from vision are trusted, the year is reconstructed via bounded inference on **both** sides (Protocolo declared date and guía dates).

---

## 2. Scope

### In scope

**Backend — declared-date source**
- **New vision extraction**: read the handwritten "Fecha:" field on the **Protocolo de Recepción** page via the existing `VisionLLMPort` (provider-agnostic, lazy-import, stamp/field-crop). The declared side currently has no vision step — this adds one for the Protocolo page.
- **Set the authoritative declared date** on `Registro` from that vision read (replacing the electronic `fecha_declarada` as the reconciliation/display authority). Keep the electronic value available for provenance/audit if cheap, but it is no longer the truth.
- **Year reconstruction for the declared date** via the existing bounded `infer_reception_year` path (D5 / EXT-021) — the Protocolo handwritten year is as unreliable as the guía years; trust day-month, infer year.

**Backend — divergence check (pure domain)**
- **Per-guía fecha-divergence comparison** in the domain: compare each contributing guía's handwritten reception date against its registro's handwritten declared date. This is a **validation check, not a grouping axis** (Slice 1 owns the key).
- **Divergence → review WARNING**: emit a structured no-match warning per diverging guía. Material MATCH/MISMATCH status is unaffected; the warning is additive and flags `requires_review` for that guía with a `fecha_divergence` reason.
- **API response surface**: expose, per diverging guía, the **page number(s)** (`source_pages` already exist) and a **divergence flag/reason** so the frontend can locate and highlight it.

**Frontend — review affordances (explicit surface)**
- **RED highlight** of the diverging guía in the review UI — reusing the existing per-guía surfaces (`GuiaDrillDown.vue`, `ReconciliationRow.vue`, `SourcePages.vue`, and the badge pattern of `YearInferredBadge.vue` / `ConfidenceBadge.vue`).
- Highlight works **individually** (one diverging guía) and as a **group** (multiple guías diverging under the same registro), consistent with the existing `UnresolvedGuiasPanel.vue` grouping pattern.
- Surface the **page reference** alongside the highlight so the engineer can jump to the physical page; the existing manual edit/reassign flow (`GuiaReassignDialog.vue`) is the resolution path — no new reassignment logic.

**Tests**
- Declared-date vision extraction unit test (Registro 232 Protocolo → `2026-05-28`).
- Domain divergence-check unit tests (matching dates → no warning; diverging → warning with page + reason; null date handling).
- A real-data e2e assertion that a diverging guía produces a warning without changing the material MATCH.

### Out of scope (this change)
- **Changing the material grouping key** — Slice 1 (`r8` / MAT-001) owns removing `fecha` from `_GroupKey`. This change does NOT touch the key; it only adds the declared-date source + divergence comparison + surfacing.
- **Auto-reassignment** — divergence only flags + highlights. Reassignment stays a **manual** engineer action through the existing `GuiaReassignDialog` flow. No automatic move, no automatic registro inference.
- **SUNAT integration / online fetch** — the air-gap stays. SUNAT `fecha_entrega` is already an optional bounded-inference lower bound; this change adds no network dependency.
- **Material matching / canonical key** — owned by Slice 1; unchanged here.
- **New vision providers, batching changes, or persistence/DB changes** beyond adding the Protocolo-date read and the divergence fields.
- **Unit conversion** (forbidden domain invariant) — irrelevant to dates, called out for completeness.

---

## 3. Approach

The declared-date source change is an **additional vision read behind the existing `VisionLLMPort`**, reusing the stamp/field-crop + bounded-year-inference pipeline already proven for guía dates (rev-3 D4/D5). The divergence check is **pure domain** — a comparison over already-extracted dates, producing a warning, never mutating quantities or the group key. The frontend change is a **read-only review affordance** reusing established per-guía/grouped surfaces.

```
Protocolo de Recepción page ─▶ VisionLLMPort.read_handwritten_date (field-crop)
                                      │  raw day/month  → infer_reception_year (bounded)
                                      ▼
                         Registro.fecha (handwritten declared)  ◀── authoritative
                                      │
guía handwritten dates  ──────────────┤
(already vision-read + year-inferred)  │
                                      ▼
                  domain divergence check: guia.fecha vs registro declared fecha
                                      │
                 diverge? ──── yes ──▶ WARNING (no-match): guia_id, source_pages,
                     │                  reason="fecha_divergence", requires_review=True
                     no                 (material MATCH status UNCHANGED)
                     │                                │
                     ▼                                ▼
                MATCH/MISMATCH unaffected     API response carries page + flag
                                                      │
                                      Frontend: RED highlight (individual / grouped)
                                                + page reference → manual reassign
```

| Stage | Layer | Responsibility | Adapter / Port |
|-------|-------|----------------|----------------|
| **extract-declared-date** | application + adapter | Vision-read Protocolo handwritten "Fecha:" | `VisionLLMPort` (existing, field-crop) |
| **infer-year (declared)** | domain | Reconstruct declared year from day/month + bounds | `infer_reception_year` (existing D5) |
| **divergence-check** | domain (pure) | Compare guía date vs declared date → warning | `ReconciliationService` (new check, no key change) |
| **surface** | api + frontend | Page number + divergence flag → red highlight | API schema field + Vue review components |

### Key rationale
- **Reuse `VisionLLMPort`, do not add a new port.** Reading the Protocolo date is the same operation already done for guías (handwritten date, stamp/field crop, provider-agnostic, lazy-import). Adding a new port would duplicate the Strategy/Dependency-Inversion contract already in place. The only new thing is *which page* is read and *where the result lands* (`Registro` vs `GuiaDeRemision`).
- **Divergence is a validation check, not a grouping axis.** Per #2709 and Slice 1, folding `fecha` back into the key would re-break MATCH. The comparison runs *after* grouping and emits a side-channel warning — the misfiled-guía signal is restored without re-coupling it to the material key.
- **Trust day-month, infer year on both sides.** Vision year is unreliable (#2753: 2016/2022 instead of 2026). The declared date must go through the **same bounded year inference** as guías, otherwise a declared `2026` vs a guía inferred `2026` could spuriously diverge on a year the model never read correctly. Comparing reconstructed dates (or day-month) avoids year-inference-induced false positives.
- **Additive, never destructive.** The warning never changes a MATCH, never edits a quantity, never auto-moves a guía. It respects the OCR-validation-gate: surface for human review, the engineer decides.
- **Frontend reuses existing per-guía + grouped surfaces.** The red highlight and page reference plug into `GuiaDrillDown` / `ReconciliationRow` / `SourcePages` and the grouped `UnresolvedGuiasPanel` pattern; resolution goes through the existing `GuiaReassignDialog`. No new workflow concept.

---

## 4. Risks & Mitigations

> **Assumption:** handwritten Protocolo and guía dates are noisy (variable stamp/field position, unreliable year). Day-month is the trusted signal; the year is always reconstructed; the engineer is the final gate via manual reassign.

| Risk | Runtime trigger | What breaks if ignored | Mitigation |
|------|-----------------|------------------------|------------|
| **Vision misreads the Protocolo handwritten date** | Field crop misses the "Fecha:" box or OCR-vision returns garbage | Wrong declared date → every guía falsely diverges (or none does) | Field/stamp crop tuned for the Protocolo layout with a full-page fallback (D4 Option B); low-confidence declared read flags the **registro** `requires_review` rather than asserting a date; declared date is a flagged value, not silently trusted. |
| **False-positive divergence noise** | Day-month genuinely equal but year-inference diverges, or trivial off-by-one read | Engineer drowns in red highlights, ignores real misfiles | Compare reconstructed dates (year already inferred via the same bounds on both sides), or compare on day-month when year is inferred; tune the divergence predicate to the authoritative-date semantics; warnings are grouped so noise is scannable, not per-line spam. |
| **Year-inference interplay** | Declared and guía run through `infer_reception_year` with different bounds (SUNAT lower bound present for one, absent for the other) | Same physical date reconstructs to different years → spurious divergence | Apply consistent bounds; prefer day-month comparison or compare after a shared year reconstruction; spec must pin the exact divergence predicate (day-month vs full date) to avoid inference-driven false diverge. |
| **Null declared date** | Vision returns no date for the Protocolo (no stamp / unreadable) | Divergence check has nothing to compare against | Treat null declared date as "cannot validate" → flag the registro `requires_review`, do NOT emit per-guía divergence warnings against a null baseline. |
| **Null guía date** | Guía date already null (existing ~13/35 case) | Comparison undefined | Reuse existing null-fecha `requires_review` handling; a null guía date is "unknown", not "divergent" — no false red highlight. |
| **Highlight without page reference** | API omits `source_pages` for a diverging guía | Engineer cannot locate the page to reassign | Page number is a mandatory field on the divergence warning; e2e asserts the warning carries `source_pages`. |

---

## 5. Rollback / Abort plan
- **Additive and isolated.** Adds one declared-date vision read, a pure-domain divergence check, two API fields, and read-only frontend highlighting. No data migration, no new external service, no group-key change.
- **Declared-date source is revertible.** If the Protocolo vision read proves unreliable, revert to the electronic `fecha_declarada` as the declared authority without touching the divergence machinery.
- **Divergence is non-blocking by design.** Disabling the check (or reverting the branch) leaves material MATCH/MISMATCH exactly as Slice 1 produces it; no downstream cleanup.
- **Per-run isolation preserved.** All work is in-memory within a run; aborting discards only that run's output dir. Input PDF stays read-only; air-gap intact.

---

## 6. Open questions (for spec/design)
- **Divergence predicate**: compare full reconstructed dates, or day-month only (ignoring inferred year)? This decides the false-positive profile and must be pinned in the spec.
- **Where the Protocolo date read fires**: a new dedicated stage, or folded into `extract_declared` / `extract_vision`? Cost-cap accounting for the extra declared-side vision call.
- **Warning shape**: standalone warning list vs a `fecha_divergence` flag on the existing per-guía `requires_review`/contribution structure; how it rides the API `ReconciliationRow`/guía schema.
- **Grouped highlight semantics**: group diverging guías by registro, or by divergent target date? Drives the `UnresolvedGuiasPanel`-style grouping in the frontend.
- **Export round-trip**: does the divergence flag need to reach xlsx/csv, or is it review-grid-only for this change?
- **Tolerance**: is any date tolerance allowed (e.g. ±1 day for stamp ambiguity), or strict equality on the chosen predicate?
