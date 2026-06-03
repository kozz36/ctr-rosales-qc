# Design — r9-fecha-divergence-review

**Change**: `r9-fecha-divergence-review`
**Phase**: design (resolves the proposal §6 open forks)
**Artifact store**: hybrid (engram `sdd/r9-fecha-divergence-review/design` + this file)
**Date**: 2026-06-02
**Reads**: proposal (engram #2790 + `proposal.md`), `architecture/reception-date-authority` (#2709), r8 `design.md`, live code (pipeline.py, reconciliation.py, models.py, ports.py, date_inference.py, api/schemas.py + routes.py, vision adapter, digital_text_extractor.py, Vue review components).
**Patterns**: Ports & Adapters (Hexagonal), Dependency Inversion, pure domain Service, Value Object, DTO / anti-corruption layer, provenance side-channel (mirrors rev-3 `year_inferred`).

---

## 0. Architectural through-line

Two defects survive Slice 1 (r8/MAT-001, which removed `fecha` from `_GroupKey`):

1. **Wrong declared-date source.** The declared reception date is the electronic
   `Registro.fecha_declarada` (parsed by `extract_registro_from_proto_page`, line 354). Per #2709 the
   authoritative declared date is the **handwritten "Fecha:"** on the Protocolo de Recepción page — and
   the declared side has **no vision step at all** today.
2. **Divergence is invisible.** `reconciliation.py` (module docstring, line 9) explicitly defers
   fecha-divergence as "rev-4, out of scope." A guía whose handwritten reception date differs from the
   registro's is summed with no signal — the misfiled-guía cue is lost.

**The whole change is therefore: one new declared-side vision read behind the EXISTING `VisionLLMPort`,
a pure-domain divergence comparison run as a side-channel after grouping, and a provenance flag threaded
line→contribution→row→DTO→Vue — structurally identical to how `year_inferred` (rev-3 D5) was added.**
The group key, MATCH/MISMATCH logic, and quantity math stay untouched (proposal §5 reversibility).

```
Protocolo page ─▶ VisionLLMPort.read_handwritten_date(proto_crop)   [NEW declared-side read]
                       │ raw day/month → infer_reception_year(bounded)
                       ▼
            Registro.fecha_declarada_handwritten  ◀── authoritative declared date
            Registro.fecha_declarada_confidence / protocolo_page
                       │
guía fecha (already vision-read + year-inferred, Stage 7)
                       │
                       ▼
   reconcile(): per-guía pure-domain divergence check  ── DateDivergenceChecker
                       │  compare guía fecha vs registro declared fecha (day-month)
            diverge? ──┤
                yes ──▶ GuiaContribution.fecha_divergence=True
                        + .divergence_reason="fecha_divergence"
                        + row.requires_review |= True   (MATCH status UNCHANGED)
                       ▼
   DTO: GuiaContributionResponse.{fecha, fecha_divergence, divergence_reason}
        ReconciliationRowResponse.has_fecha_divergence (group indicator)
                       ▼
   Vue: GuiaDrillDown red row + page chip; ReconciliationRow group badge → manual reassign
```

---

## ADR-1 — Declared-side Protocolo date read: REUSE `VisionLLMPort`, new pipeline sub-stage, NEW port

**Decision.** Add no new port. Reuse `VisionLLMPort.read_handwritten_date(image)` (ports.py:71) — its
prompt (`openai_compatible.py:38`) is already generic ("logistics document stamp", returns
`{date, confidence}`), so it reads any handwritten-date crop. The ONLY new things are *which page* is
cropped and *where the result lands* (`Registro`, not `GuiaDeRemision`). Adding a new port would
duplicate the Strategy/DI contract already proven for guía dates (proposal §3, #2790 Learned).

**Where it fires.** A new declared-date pass folded into `extract_declared` flow, NOT a free-standing
top-level stage. Concretely, after `_stage_extract_declared` produces the deduped `Registro` list, run a
new private `_stage_extract_declared_date(registros, classifications, decode_map)`:

1. For each `Registro`, locate its Protocolo source page. **Blocker found in code:**
   `extract_registro_from_proto_page` (digital_text_extractor.py:338) *receives* `source_page` but drops
   it — `Registro` has no page field. Fix: add `protocolo_page: int | None = None` to `Registro`
   (models.py) and set it in the parser (`Registro(..., protocolo_page=source_page)`). This is the
   page-number propagation seam for the declared side.
2. Render/crop that page reusing the `decode_map` cached bytes (render-cache invariant, EXT-019) +
   `_prepare_vision_image` (pipeline.py:1347) with a **Protocolo-specific crop region** (ADR-6).
3. Call `self._vision.read_handwritten_date(crop)` — **one call per registro**, counted against the
   SAME `vision.max_vision_calls` cost cap (cap accounting is amended to include declared reads).
4. Year reconstruction via the existing bounded `infer_reception_year` (date_inference.py) — the
   declared handwritten year is as unreliable as guía years (#2753); trust day-month, infer year,
   upper=today, lower=None (no SUNAT bound on the declared side).
5. Write the result onto the `Registro` (ADR-2 fields).

**Why a sub-stage, not folded into `extract_declared` directly.** `_stage_extract_declared` is
text-only (digital parse, no image access). The declared date needs the rendered page + vision, which
is image-domain work; keeping it a separate pass preserves SRP and lets the vision cost-cap accounting
live in one place. It runs after `extract_declared` and before `reconcile` (it has no ordering
dependency on guía vision; placing it adjacent to the guía vision stage keeps all `VisionLLMPort` calls
contiguous for cap bookkeeping).

**Rejected:** a brand-new `DeclaredDatePort` — duplicates `VisionLLMPort` for no gain (ISP not served;
the operation and return shape are identical). A new top-level stage decoupled from declared — would
re-walk classifications to re-find Protocolo pages already known to `extract_declared`.

---

## ADR-2 — `Registro` gains the handwritten declared date as a flagged, provenance-bearing field

**Decision.** Extend the pure domain `Registro` (models.py:115) with additive, backward-compatible
fields (defaults preserve old serialized runs — same strategy as D5/D6):

```python
class Registro(BaseModel):
    numero: str
    fecha_declarada: date | None                       # electronic — KEPT for provenance/audit
    declared_lines: list[MaterialLine]
    # r9: handwritten Protocolo date (the authoritative declared reception date, #2709)
    protocolo_page: int | None = None                  # source page of the Protocolo (page-number seam)
    fecha_declarada_handwritten: date | None = None    # vision-read + year-inferred; authoritative
    fecha_declarada_confidence: float | None = None    # vision confidence (gating, ADR-7)
    fecha_declarada_year_inferred: bool = False        # provenance, mirrors guía year_inferred

    @computed_field
    @property
    def fecha_authoritative(self) -> date | None:
        # Authoritative declared date: handwritten when read, else electronic fallback (rollback path).
        return self.fecha_declarada_handwritten or self.fecha_declarada
```

**Why keep `fecha_declarada`.** Proposal §2 ("keep the electronic value for provenance/audit") and §5
rollback ("revert to the electronic `fecha_declarada` without touching the divergence machinery"). The
`fecha_authoritative` computed property is the single read-point the reconciler and the divergence
checker consume, so flipping the source is one property edit, not a scatter of call-site changes.

**Reconciler display fecha.** `reconcile()` currently seeds `declared_fecha` from
`registro.fecha_declarada` (reconciliation.py:92). Change that single line to
`registro.fecha_authoritative` so the row's display `fecha` becomes the authoritative handwritten date.
Grouping is untouched (`fecha` is not in `_GroupKey`).

**Domain purity.** New fields are stdlib `date`/`float`/`int` + a `computed_field` — no IO, no SDK.
Vision stays strictly behind the port; the `Registro` only *holds* the result the pipeline wrote.

---

## ADR-3 — Divergence check: a PURE domain service `DateDivergenceChecker`, day-month predicate

**Decision (resolves proposal §6 "divergence predicate" — the key open fork).** Compare on
**day + month only**, ignoring the year. New pure domain service
`domain/date_divergence.py: DateDivergenceChecker` (sibling to `date_inference.py`), a stateless
function-style service:

```python
DivergenceReason = Literal["fecha_divergence"]

@dataclass(frozen=True)
class DivergenceResult:
    diverges: bool
    reason: DivergenceReason | None          # "fecha_divergence" when diverges else None
    declared_fecha: date | None
    guia_fecha: date | None

def check_fecha_divergence(
    declared_fecha: date | None,
    guia_fecha: date | None,
) -> DivergenceResult:
    # Null-safety (proposal risk table): if EITHER side is None → cannot validate → NOT divergent.
    if declared_fecha is None or guia_fecha is None:
        return DivergenceResult(False, None, declared_fecha, guia_fecha)
    diverges = (declared_fecha.month, declared_fecha.day) != (guia_fecha.month, guia_fecha.day)
    return DivergenceResult(diverges, "fecha_divergence" if diverges else None,
                            declared_fecha, guia_fecha)
```

**Why day-month, not full date (the decisive rationale).** Both sides run through
`infer_reception_year`, whose bounds differ: a guía with a successful SUNAT fetch gets a `lower` bound
(D3); the declared side has `lower=None` (no SUNAT on the Protocolo). The same physical date can
therefore reconstruct to **different years** on the two sides (proposal risk "Year-inference interplay")
→ a full-date comparison would emit spurious divergence on a year neither side read reliably (#2753:
2016/2022 vs 2026). Day-month is the trusted signal end-to-end (it is exactly what `_parse_day_month` +
`infer_reception_year` are built on); comparing day-month neutralizes year-inference noise. **Spec
ambiguity resolved:** predicate = strict equality on `(month, day)`, **tolerance 0** (no ±1-day window).
A ±1 grace would mask genuine adjacent-day misfiles and contradicts the project's EXACT(0) posture for
the material gate; if stamp-read off-by-one proves noisy in real data, that is the engineer's manual
call, not a silent tolerance.

**Why null = not-divergent.** A null guía date is the existing ~13/35 case already flagged by
`requires_review` (reconciliation.py:162, `any(g.fecha is None ...)`). A null *declared* date means
"cannot validate" and must flag the **registro** (ADR-7), not paint every guía red against a null
baseline (proposal risk "Null declared date"). Either-side-null short-circuits to `diverges=False` so no
false red highlight is produced.

**Where it runs.** Inside `ReconciliationService.reconcile()` (reconciliation.py), in the existing
per-key loop where `contributing_guias_list` and the declared fecha are already in scope (around
line 159-166). It is a *side-channel*: it sets per-contribution flags and OR-s `requires_review`. It
**never** touches `_GroupKey`, `status`, `delta`, or `summed_qty` (proposal "additive, never
destructive"; #2709 "validation side-channel, never a grouping axis").

**Why a separate service, not inline in `reconcile`.** SRP + isolated unit testing (matching dates → no
warning; diverging → warning; null handling — proposal Tests). `reconcile` orchestrates; the predicate
is one pure function with its own invariants, mirroring how `infer_reception_year` was extracted from
the pipeline.

---

## ADR-4 — Per-guía divergence carried on `GuiaContribution`; the guía fecha threaded onto it

**Decision.** The divergence signal is per-guía, so it rides `GuiaContribution` (models.py:56), exactly
where `year_inferred` already rides (rev-3 D5). Add:

```python
class GuiaContribution(BaseModel):
    ...                                      # existing fields incl. source_pages, year_inferred
    fecha: date | None = None               # this guía's handwritten reception date (for compare/display)
    fecha_divergence: bool = False          # True when guia fecha diverges from registro declared
    divergence_reason: Literal["fecha_divergence"] | None = None
```

**Why `fecha` must be added to the contribution.** `GuiaContribution` today carries no date — only
`GuiaDeRemision.fecha` has it, and the contribution is built from `contrib_map.values()`
(reconciliation.py:132). To compare and to display the divergent date next to the page chip, the
reconciler copies `g.fecha` onto the contribution when building it. `source_pages` already exists on
`GuiaContribution` (line 68) → **page-number propagation for the guía side is already in place**; no new
page field is needed there, only the wiring already present (guia.source_pages).

**Reconciler wiring (reconciliation.py, contribution build at line 132).** For each
`(g, total_qty)` in `contrib_map.values()`, call
`check_fecha_divergence(declared_fecha=row_declared_authoritative, guia_fecha=g.fecha)` and populate
`fecha`, `fecha_divergence`, `divergence_reason`. OR the per-guía `diverges` into `row_requires_review`
(extend the existing block at line 162). `row_declared_authoritative` is the group's declared date
already computed for `declared_fecha` (now sourced from `fecha_authoritative`, ADR-2).

**Row-level group indicator.** Add a computed field on `ReconciliationRow` (models.py:139), mirroring
`any_year_inferred`:

```python
@computed_field
@property
def has_fecha_divergence(self) -> bool:
    return any(g.fecha_divergence for g in self.guias)
```

This drives the frontend group badge when multiple guías under one registro diverge (proposal §2
"individually, or grouped"), without storing redundant state — derived from the contributions.

**Page-number propagation trace (proposal §6 / decision 3), end to end:**

| Hop | Field | Status |
|-----|-------|--------|
| classification → raw guía | `_RawGuia.source_page` (pipeline.py:1285) | exists |
| raw → block | `_GuiaBlock.source_pages` (pipeline.py:1306) | exists |
| block → guía | `GuiaDeRemision.source_pages` (models.py:99) | exists |
| guía → contribution | `GuiaContribution.source_pages` (models.py:68) | exists |
| contribution → DTO | `GuiaContributionResponse.source_pages` (schemas.py:31) | exists |
| **declared Protocolo page** | **`Registro.protocolo_page` (NEW, ADR-2)** | **add** |

The only missing page hop is the declared Protocolo page (currently discarded by the parser). The guía
page chain is fully intact — the red-highlight page reference reuses the existing `source_pages`.

---

## ADR-5 — API surface: additive DTO fields, no new endpoint

**Decision.** Mirror the rev-3 `year_inferred` / `any_year_inferred` precedent — pure read-only
additions to existing response DTOs (schemas.py), wired in `_row_to_response` (routes.py:83). No new
route, no editing control (resolution stays the existing `POST /reassign`).

```python
class GuiaContributionResponse(BaseModel):
    ...                                      # existing
    fecha: date | None = None
    fecha_divergence: bool = Field(default=False, description="Handwritten guía date diverges from the registro's declared date.")
    divergence_reason: Literal["fecha_divergence"] | None = None

class ReconciliationRowResponse(BaseModel):
    ...                                      # existing
    has_fecha_divergence: bool = Field(default=False, description="At least one contributing guía's date diverges (group indicator).")
```

`_row_to_response` maps `g.fecha`, `g.fecha_divergence`, `g.divergence_reason` per contribution and
`row.has_fecha_divergence` at the row level. **Export round-trip (proposal §6):** out of scope for this
change — divergence is a review-grid signal, like the human-review workflow; xlsx/csv already carry
`requires_review`, which is OR-set on divergence, so the export still flags the row for review without a
dedicated column. (Flagged as a deliberate scope cut; revisit if the engineer needs it in the sheet.)

---

## ADR-6 — Protocolo-specific crop region via a second `stamp_crop`-style config block

**Decision.** The guía stamp crop (`vision.stamp_crop`, upper-right quadrant — R7 fix, pipeline.py:1347)
is tuned for the GUIA "Recibí conforme" stamp. The Protocolo "Fecha:" field sits in a different layout
position, so reusing the guía crop would miss it. Add a sibling crop config
`vision.protocolo_crop` (same fractional-coordinate shape as `stamp_crop`, independently tunable) and
pass it to `_prepare_vision_image` for the declared read. **Option B fallback** (D4): when
`protocolo_crop` is disabled, send a ≥300-dpi full-page render so the model sees the whole Protocolo and
can find the field (mirrors the existing `_VISION_FALLBACK_DPI` path).

**Why a second crop block, not reuse.** The crop box is a per-document-layout constant; one box cannot
serve two layouts. A dedicated config keeps the guía crop stable (no regression to R7) and makes the
Protocolo box independently tunable from a bake-off — same Separation-of-Concerns rationale the r8
design used for `inference:` vs `vision:`. Defaulting `protocolo_crop` to full-page-fallback keeps the
declared read safe before the box is tuned (conservative default).

---

## ADR-7 — Confidence gating: low-confidence Protocolo read flags the registro, never asserts a baseline

**Decision (resolves proposal risk "Vision misreads the Protocolo date").** After the declared vision
read, gate on `fecha_declarada_confidence` against the existing `config.confidence.threshold` (0.85):

- **confidence ≥ threshold** → trust the handwritten date as the authoritative declared baseline.
- **confidence < threshold (or date None)** → do **NOT** assert the handwritten date as the divergence
  baseline. Set `fecha_declarada_handwritten=None` (so `fecha_authoritative` falls back to the
  electronic date for *display* only) and flag every row of that registro `requires_review` with the
  reason that the declared date could not be read confidently. **Crucially, the divergence check is
  SKIPPED for that registro** — `check_fecha_divergence` already returns `diverges=False` when
  `declared_fecha is None`, so a low-confidence/None declared date can never paint guías red against an
  untrusted baseline (proposal risk: "wrong declared date → every guía falsely diverges (or none)").

**Why flag-not-assert.** The OCR-validation-gate invariant (CLAUDE.md): a vision read is never silently
trusted; below threshold it is surfaced for human review, not used as ground truth. A confidently-wrong
declared date would cascade false divergence across the whole registro — the most damaging failure mode
in the proposal risk table — so the gate fails *closed* (no divergence emitted) and *loud* (registro
flagged).

**Where.** The gate lives in the new `_stage_extract_declared_date` (pipeline.py) when writing the
`Registro` fields; the skip-on-null behavior is intrinsic to `check_fecha_divergence` (ADR-3), so the
domain stays correct even if the pipeline gate were bypassed.

---

## ADR-8 — Frontend: red row in `GuiaDrillDown`, group badge in `ReconciliationRow`, reuse page chip

**Decision.** Read-only review affordance, no new workflow concept; reuse the established badge +
drill-down + `SourcePages` surfaces and the existing `GuiaReassignDialog` resolution path.

**Per-guía (individual) — `GuiaDrillDown.vue`:**
- The contribution row gains a **RED** treatment when `guia.fecha_divergence` is true: a
  `guia-drill-down__row--divergent` class (red left-border + subtle red tint, consistent with the
  existing `recon-row--mismatch` red tokens `--status-mismatch-*`), plus a **`FechaDivergenceBadge`**
  (new component, modeled exactly on `YearInferredBadge.vue` but RED, not yellow — icon `⚠` + label
  "Fecha no coincide", `role="img"`, a11y by icon+label not color-only, WCAG 1.4.1). The badge sits in
  the existing Fecha column (drill-down `<td>` at line 91), replacing the year-inferred placeholder when
  divergent.
- The guía's date and page are already available: render `guia.fecha` in that cell and the existing
  `guia.source_pages` (already shown, line 31) is the page reference for "locate and reassign." The
  existing per-guía **Reassign** button (line 101) is the resolution path — no new control.

**Group indicator — `ReconciliationRow.vue`:**
- When `row.has_fecha_divergence` is true, render the `FechaDivergenceBadge` in the review/flags cell
  (Col 9, beside the existing `requires_review` ⚠ and `YearInferredBadge`, line 82-99). This is the
  "grouped" signal (multiple guías diverging under one registro) the proposal asks for — it tells the
  engineer to expand the drill-down to see which guías are red, paralleling the `UnresolvedGuiasPanel`
  grouping intuition without a new panel.

**Types (`@/api/types`).** Add `fecha`, `fecha_divergence`, `divergence_reason` to
`GuiaContributionResponse` and `has_fecha_divergence` to `ReconciliationRowResponse` (generated/mirrored
from the backend DTO).

**Why reuse, not a new panel.** The proposal scopes the resolution path to the existing
`GuiaReassignDialog`; divergence is an attribute of a contribution already rendered in the drill-down, so
the lowest-friction, design-system-consistent surface is a red row + badge there, with a roll-up badge on
the parent row. A separate "divergent guías" panel would fragment the review flow and duplicate the
drill-down. Keeping it inline honours the "local, inspectable component patterns" frontend hard rule.

---

## Component & data-flow summary

| New / changed | Layer | Responsibility |
|---------------|-------|----------------|
| `DateDivergenceChecker` / `check_fecha_divergence` | domain (`date_divergence.py`) | Pure day-month divergence predicate; null-safe; sibling to `date_inference.py` |
| `Registro.{protocolo_page, fecha_declarada_handwritten, fecha_declarada_confidence, fecha_declarada_year_inferred, fecha_authoritative}` | domain (`models.py`) | Authoritative handwritten declared date + provenance + page seam |
| `GuiaContribution.{fecha, fecha_divergence, divergence_reason}` | domain (`models.py`) | Per-guía divergence signal + date for compare/display |
| `ReconciliationRow.has_fecha_divergence` (computed) | domain (`models.py`) | Group-level divergence indicator (derived) |
| `reconcile()` divergence wiring | domain (`reconciliation.py`) | Call checker per contribution; set flags; OR `requires_review`; display fecha = `fecha_authoritative` (grouping UNCHANGED) |
| `extract_registro_from_proto_page` page capture | adapter (`digital_text_extractor.py`) | Set `Registro.protocolo_page = source_page` (was discarded) |
| `_stage_extract_declared_date` | application (`pipeline.py`) | Crop Protocolo page (render-cache) → `VisionLLMPort.read_handwritten_date` → `infer_reception_year` → write Registro fields; cost-cap + confidence gate (ADR-7) |
| `vision.protocolo_crop` config | application (`config.py`) | Protocolo-specific crop box; full-page ≥300dpi fallback default |
| Vision cost-cap accounting | application (`pipeline.py`) | Declared reads counted against `vision.max_vision_calls` |
| `GuiaContributionResponse.{fecha, fecha_divergence, divergence_reason}` + `ReconciliationRowResponse.has_fecha_divergence` | infrastructure (api `schemas.py`) | Read-only DTO surface |
| `_row_to_response` mapping | infrastructure (api `routes.py`) | Map new domain fields → DTO |
| `FechaDivergenceBadge.vue` (NEW) | frontend | RED advisory badge (icon+label, a11y), modeled on `YearInferredBadge` |
| `GuiaDrillDown.vue` red row + badge + fecha cell | frontend | Per-guía red highlight + page ref → existing reassign |
| `ReconciliationRow.vue` group badge | frontend | Row-level grouped divergence indicator |
| `@/api/types` additions | frontend | DTO type parity |

---

## Invariants preserved (verification checklist)

- **Domain purity**: `date_divergence.py` imports only stdlib `datetime`; vision strictly behind
  `VisionLLMPort` (no new port); pipeline writes Registro fields, domain only holds them.
- **`fecha` out of the group key**: `_GroupKey` unchanged (MAT-001/Slice 1); divergence is a pure
  side-channel — never `status`/`delta`/`summed_qty` (#2709 "validation side-channel, never a grouping
  axis").
- **MATCH EXACT(0)**: reconciliation comparison untouched; divergence is additive to `requires_review`
  only.
- **OCR-validation gate**: low-confidence declared date flags the registro, never asserts a baseline
  (ADR-7); divergence flags for human review, never auto-reassigns.
- **Units never converted**: dates only; unit axis untouched.
- **`fecha` is the handwritten reception date**: declared side now ALSO handwritten (Protocolo),
  completing #2709; both sides trust day-month + bounded year inference.
- **Local-first / air-gap**: declared read is the SAME local/Anthropic/Ollama vision path; no new
  egress; SUNAT untouched.
- **Per-run isolation & read-only PDF**: all in-memory within a run; render-cache reused (no extra
  renders beyond the existing per-page budget plus the Protocolo crop reuse).
- **Reversibility**: `fecha_authoritative` falls back to electronic `fecha_declarada`; disabling the
  declared read or the checker leaves Slice 1 MATCH/MISMATCH exactly as-is (proposal §5).

## Resolved spec ambiguities (flagged)

- **Divergence predicate** → **day-month strict equality, tolerance 0** (ADR-3). Resolves proposal §6
  primary fork; eliminates year-inference false positives.
- **Where the declared read fires** → a new `_stage_extract_declared_date` sub-stage adjacent to guía
  vision, counted against the same cost cap (ADR-1, proposal §6).
- **Warning shape** → per-guía flag on `GuiaContribution` + computed row indicator, NOT a standalone
  warning list — rides the existing rev-2 contribution/row structure (ADR-4, proposal §6).
- **Grouped highlight semantics** → group by **registro** (the reconciliation row's natural grouping);
  the row badge rolls up its contributions (ADR-4/ADR-8, proposal §6).
- **Export round-trip** → out of scope; `requires_review` already reaches export (ADR-5, proposal §6).
- **Tolerance** → strict (0); no ±1-day grace (ADR-3, proposal §6).

## Open items for `sdd-tasks` / risks

- **Protocolo crop box is unknown until a bake-off** — ship with full-page ≥300dpi fallback default;
  tune `vision.protocolo_crop` against the Registro 232 Protocolo (expected `28-05-26 → 2026-05-28`).
- **Cost-cap impact**: one extra `VisionLLMPort` call per registro. Verify `max_vision_calls` headroom
  on the 493-page real run; declared reads must not starve guía reads (consider ordering or a separate
  declared sub-budget if the cap is tight).
- **Multi-Protocolo / detail-only registros**: a registro deduped from a detail page (no Protocolo) has
  `protocolo_page=None` → no handwritten read → `fecha_authoritative` falls back to electronic, and the
  divergence baseline is the electronic date (or null → no divergence). Confirm this fallback is
  acceptable or flag the registro.
- **Day-month equality vs locale stamp formats**: `infer_reception_year` already normalizes day/month;
  the checker compares the reconstructed `date` objects, so format noise is upstream of the predicate.
- **`first_page` vs `protocolo_page`**: the declared page is distinct from any guía page; the row's
  `source_pages` are guía pages — the Protocolo page surfaces only via the registro/declared context,
  not the guía chip. Confirm whether the frontend needs to show the Protocolo page too (currently
  declared page is backend-only provenance).
