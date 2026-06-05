# Delta for Extraction Domain
**Change**: guia-classification-keystone
**Phase**: spec (revision 6 — QR-evidence guard is INVARIANT across all positions)
**Date**: 2026-06-05

> **rev-6 amendment (JD round-5, both blind judges)**: the QR-evidence guard MUST be
> applied to EVERY page as a single gate BEFORE the start-new-block logic, not only
> in the continuation path. The rev-5 guard was consulted only in start-new-block
> **condition (d)**; at run-start (condition a) and at a section boundary (condition
> b) a block was opened UNCONDITIONALLY, so a no-QR-evidence material page (a
> spurious non-materials table) landing in those slots opened a PHANTOM `ocr_fallback`
> guía whose lines were admitted with `requires_review=False` — silent bogus material.
> The gate is now positional-independent:
> ```python
> is_ocr_fallback_material = (
>     identity is None and len(raw.lines) > 0 and page_hashqr_url is not None
> )
> has_guia_evidence = identity is not None or is_ocr_fallback_material
> if not has_guia_evidence:
>     continue  # NEVER opens or extends a block, at ANY position
> ```
> A page with NO QR evidence (identity None AND `page_hashqr_url` None) is dropped
> uniformly at run-start, section-boundary, and continuation. The `requires_review`
> flagging (keyed on `is_ocr_fallback_material` inside `if start_new_block:`) now
> applies at run-start and section-boundary too. Real-data invariant unchanged
> (0-line FHH photo dropped; reg228 → source_pages=[98], 140/140). Bug-2
> `errored_guias` not affected (a QR-identified 0-line guía has `identity is not None`
> → `has_guia_evidence` True → still opens + still scanned). REQ2 unchanged.
>
> **rev-5 amendment (FIX 1 + FIX 2)**:
> 1. **Case 3 requires positive QR evidence (FIX 1).** The rev-4 case-3 condition
>    `is_ocr_fallback_material = identity is None and len(raw.lines) > 0` is too
>    broad: a sheet with NO QR at all that carries a NON-materials table (OCR emits
>    spurious "lines") would wrongly open a phantom `ocr_fallback` guía with bogus
>    material. A page MUST be treated as an `ocr_fallback` guía ONLY with positive
>    QR evidence — the URL-variant `hashqr=` QR (`page_hashqr_url is not None`),
>    captured even when the compact identity QR fails (EXT-012). New condition:
>    `... and page_hashqr_url is not None`. A no-QR page with a spurious table is
>    dropped (case 2b). Residual accepted edge: a real guía where BOTH QRs fail is
>    ignored (rare other-provider / manual-entry, out of scope).
> 2. **SUNAT preserves `requires_review` on `ocr_fallback` blocks (FIX 2,
>    JD round-4 WARNING).** When SUNAT enriches an `ocr_fallback` block (compact QR
>    failed but URL QR decoded → SUNAT fetch succeeded), the SUNAT-built replacement
>    `MaterialLine`s MUST carry `requires_review=True` so the C1 uncertain-identity
>    flag is not erased. QR-identified blocks keep `requires_review=False`.
> REQ2 (reconciliation errored_guias) is unchanged — do NOT redo it.
>
> **rev-4 amendment (C1)**: REQ1 keeps the rev-3 `absorb = identity is not None`
> else-branch gate, but adds **case 3**: a non-QR page that *carries OCR material*
> (`identity is None`, `len(raw.lines) > 0` — the EXT-S24 `ocr_fallback` path) MUST
> NOT be silently dropped. It opens its OWN `ocr_fallback` block (counted in the
> registro total) and is flagged `requires_review` (uncertain identity). Only a non-QR
> page with **0 lines** (FHH photo) is dropped. This restores the validation-gate
> invariant ("never silently drop; flag `requires_review`"). REQ2 (reconciliation
> errored_guias) is unchanged — do NOT redo it.
>
> **rev-3 correction**: REQ1 (extraction delta) is rewritten to align with design
> Decision-1 rev-3. The positional/adjacency gate (spec revision 2) was built on a premise
> that real-data run `67e4e7a1` proved false: no genuine no-QR guía continuation exists in
> the PDF. QR identity is the ONLY valid block-extender. REQ2 (reconciliation errored_guias)
> is unchanged and validated — do NOT redo it.

---

## MODIFIED Requirements

### EXT-019 — [MODIFIED: replaces existing EXT-019] Hybrid page classifier — Condition B verdict UNCHANGED; absorb gate is QR-identity-only in assembly

`PageClassifier` MUST classify a page using the three-condition hybrid logic (Conditions A, B,
C) as defined in EXT-019 (rev-3 delta), with **no change to Condition B's output verdict**:

A page that satisfies **Condition B only** (Forma-header heuristic — body ≤ 200 chars,
image-dominant, no title match) MUST continue to be classified as **`GUIA`** with
`title_matched="FORMA_HEADER_HEURISTIC"`. The classifier MUST NOT emit a new enum value
(`IGNORED` or otherwise) for Condition-B pages. Conditions A and C are unchanged.

**Rationale (real-data validated):** Real-data run `67e4e7a1` classified 165 pages:
83 `QR_IDENTITY` (real guías, 140/140 material lines) and 68 `FORMA_HEADER_HEURISTIC`
(all 68 photos/annexes, 0 material lines). Zero guías opened by an FHH page; zero genuine
no-QR continuation pages. Domain authority confirms: every SUNAT guía page carries a QR;
non-QR pages inside a registro section are photos/annexes.

The absorb-vs-drop decision MUST be made in `_stage_assemble_blocks`. The gate predicate is:

```
absorb = identity is not None
```

The else-branch `absorb` gate stays `absorb = identity is not None`. The disposition
of a non-QR (`identity is None`) candidate page now depends on whether it carries OCR
material (rev-4, three cases):

1. **QR page** (`identity is not None`): extends/opens a block as in rev-3.
2. **Non-QR page with 0 material lines** (`identity is None`, `len(raw.lines) == 0` —
   `FORMA_HEADER_HEURISTIC` photo / annex): **DROPPED** — not appended to any block,
   not creating a block, not inflating any `source_pages` (rev-3, unchanged).
3. **Non-QR page WITH material AND QR evidence** (`identity is None`,
   `len(raw.lines) > 0`, AND `page_hashqr_url is not None` — the EXT-S24
   `ocr_fallback` path, compact QR-decode failed but the URL `hashqr=` QR decoded
   and OCR read lines): the page MUST **start its OWN block**
   (`page_guia_id = "ocr_{source_page}"`, `identity_source = "ocr_fallback"`), be
   counted in the registro total, and be flagged **`requires_review` on its
   `MaterialLine`s** (uncertain identity). It MUST NOT be silently dropped. This
   uses the existing `MaterialLine.requires_review` field; the domain propagation
   (`domain/reconciliation.py`: any contributing-guía line `requires_review` →
   `row_requires_review`) surfaces it. No parallel flagging system.
4. **(rev-5, case 2b; rev-6 invariant) Non-QR page WITH material but NO QR evidence**
   (`identity is None`, `len(raw.lines) > 0`, AND `page_hashqr_url is None`): the
   page is NOT a guía — its OCR "lines" are a spurious non-materials table. It MUST
   be **DROPPED at ANY position** (run-start, section-boundary, or continuation) by
   the invariant guía-evidence gate (`has_guia_evidence` False → `continue`). It MUST
   NOT open a phantom `ocr_fallback` guía with bogus material — rev-6 closes the
   run-start / section-boundary gap where rev-5 still opened one.

**INVARIANT QR-evidence gate (rev-6).** The disposition above MUST be enforced by a
single gate applied to EVERY page BEFORE the start-new-block logic, so the guard is
positional-independent (it holds at run-start, section-boundary, and continuation):
```
is_ocr_fallback_material = (
    identity is None
    and len(raw.lines) > 0
    and page_hashqr_url is not None
)
has_guia_evidence = identity is not None or is_ocr_fallback_material
if not has_guia_evidence:
    continue  # dropped uniformly — never opens or extends a block
```
The `continue` skips the page without touching `current_block`. Consequently the
start-new-block conditions (a run-start / b section-boundary / c new QR / d
`ocr_fallback` material) AND the `requires_review` flagging only ever run for
evidence-bearing pages. Case 3 remains start-new-block **condition (d)**
(`is_ocr_fallback_material`); after the gate the else-branch sees only a
same-`guia_id` 2nd QR page (absorbed). A no-QR-evidence material page (case 2b) is
now dropped at ANY position — NOT only when it would have been a continuation.
`page_hashqr_url` is the URL `hashqr=` QR captured in the identity-None branch even
when the compact identity QR fails (EXT-012) — the implementable QR-evidence proxy
(the system cannot detect "compact QR present-but-unreadable" when there is also no
URL QR).

**SUNAT enrichment preserves the review flag (rev-5, FIX 2).** When SUNAT is enabled
and replaces an `ocr_fallback` block's OCR lines with SUNAT-authoritative line items
(`_apply_sunat_result`), the replacement `MaterialLine`s MUST carry
`requires_review=True` (the block's `identity_source == "ocr_fallback"` uncertain-identity
signal is preserved, not erased by the default `requires_review=False`). QR-identified
blocks keep `requires_review=False`. Default app mode is SUNAT-enabled + OCR-on, so
this is a production path; only the additive review side-channel is touched, never
qty/unit/key/status.

The classifier verdict for Condition-B pages MUST remain `GUIA` / `FORMA_HEADER_HEURISTIC`
(page-local; no QR context available at classification time). The drop/own-block decision
lives exclusively in assembly.

**Spec revision history:**
- rev-1: Condition B → `IGNORED` at classifier (wrong — breaks EXT-S24).
- rev-2: Condition B stays `GUIA`; positional gate (`identity is None AND same registro AND
  qr-anchored`) in assembly (wrong premise — FHH continuation class does not exist).
- rev-3: Condition B stays `GUIA`; gate simplified to `absorb = identity is not None`;
  ALL non-QR pages dropped. Backed by real data + domain authority.
- rev-4: else-branch gate kept; **case 3** added — a non-QR page WITH OCR
  material opens its own `ocr_fallback` block + `requires_review` instead of being
  dropped (C1 fix; restores the never-silently-drop validation-gate invariant). Only
  0-line non-QR photos are dropped.
- rev-5: case 3 GUARDED by positive QR evidence
  (`page_hashqr_url is not None`) — a no-QR page with a spurious table is dropped
  (case 2b), NOT opened as a phantom guía. SUNAT enrichment of an `ocr_fallback`
  block preserves `requires_review` (FIX 2). Real-data invariant holds (reg228
  photos 0-line → still dropped).
- **rev-6 (this):** the QR-evidence guard is INVARIANT across all start-new-block
  positions — a single `has_guia_evidence` gate (`continue` if False) before the
  start-new-block logic. rev-5 applied the guard only in condition (d), so a
  no-QR-evidence material page at run-start (condition a) or a section boundary
  (condition b) opened a phantom block with UNFLAGGED bogus material; rev-6 drops it
  uniformly. Round-5 SUGGESTION: `block.identity_source` (required field) replaces
  `getattr(block, "identity_source", None)` in `_apply_sunat_result`.

#### Scenario EXT-S19a — Non-guía image page (no open block) is NOT absorbed (unchanged, still passes)

- GIVEN a page satisfying Condition B only (`image_dominant=True`, `qr_is_guia=False`, body ≤ 200 chars)
- AND `_stage_assemble_blocks` has no currently-open block with a matching registro
- WHEN `_stage_assemble_blocks` processes the page
- THEN the page is NOT appended to any guía block
- AND no new guía block is created for the page
- AND no existing guía's `source_pages` includes this page

#### Scenario EXT-S19b — QR guía page p98 followed by FHH photo pages in same registro: photos NOT absorbed (RED against rev-2 gate)

**Real-data model: registro 228 — QR page 98 + 39 FHH photo pages 99-137.**

- GIVEN page 98: Condition A (QR identity `T009-XXXXXX`, `identity_source = "qr"`, registro `228`)
- AND pages 99-137 (representative: page 99): Condition B only (no QR, `identity is None`, same registro `228`)
- AND the open block after processing page 98 has `registro = "228"` and `identity_source = "qr"`
- WHEN `_stage_assemble_blocks` evaluates the gate for pages 99-137
- THEN `absorb` is `False` for each photo page (`identity is None`)
- AND no photo page is appended to the block
- AND the block for registro 228 has `source_pages == [98]` only (no inflation)
- AND the FHH pages do NOT appear in any guía's `source_pages`

#### Scenario EXT-S19c — ~~Genuine multi-page FHH continuation~~ [INVERTED — see NOTE]

> **NOTE (rev-3):** The scenario previously at EXT-S19c (spec rev-2) asserted that a QR
> page 151 followed by a no-QR page 152 (same registro) should absorb page 152 into the
> block (`source_pages = [151, 152]`). Real-data run `67e4e7a1` proves that premise false:
> page 152 is an FHH photo with 0 material lines; it MUST NOT be absorbed. The test
> `TestEXTS19cGenuineContinuationRegression` (test_positional_gate.py:328) and
> `TestConditionCContinuationAbsorbed` (test_positional_gate.py:210) MUST be **inverted**
> in apply to assert the photo is dropped and `source_pages == [151]` only.

The inverted scenario is:

- GIVEN page 151: Condition A (QR `T112-0065421`, `identity_source = "qr"`, registro `227`)
- AND page 152: Condition B only (no QR, `identity is None`, same registro `227`)
- WHEN `_stage_assemble_blocks` evaluates the gate for page 152
- THEN `absorb` is `False` (`identity is None`)
- AND page 152 is NOT appended to the block
- AND the block for `guia_id = "T112-0065421"` has `source_pages == [151]` only

#### Scenario EXT-S19d — Standalone scanned guía (EXT-S24 regression guard): Condition B still classifies GUIA (unchanged)

- GIVEN a page with `image_dominant=True`, `qr_is_guia=False`, body ≤ 200 chars (satisfies Condition B only)
- WHEN `PageClassifier` processes the page
- THEN the page is classified as `guia` with `title_matched = "FORMA_HEADER_HEURISTIC"`
- AND no new classifier enum value is introduced

#### Scenario EXT-S19e — Condition-B page with registro mismatch: NOT absorbed (unchanged, still passes)

- GIVEN a Condition-B page with `registro = "228"`
- AND `current_block` at assembly time has `registro = "232"` and `identity_source = "qr"`
- WHEN `_stage_assemble_blocks` evaluates the gate
- THEN `absorb` is `False`; the page is NOT appended to the open block
- AND registro 232's `source_pages` does NOT include this page

#### Scenario EXT-S19f — True multi-QR-page guía (same guia_id): both QR pages absorbed into ONE block

**New scenario confirming `absorb = identity is not None` handles real multi-page guías correctly.**

- GIVEN page P1: Condition A (QR `T112-0065900`, `identity_source = "qr"`, registro `230`)
- AND page P2: Condition A (QR `T112-0065900`, same `guia_id`, same registro `230`)
- AND at assembly time the else-branch is reached for P2 (same `guia_id` — not a block-open case)
- WHEN `_stage_assemble_blocks` evaluates the gate for P2
- THEN `identity is not None` → `absorb = True`
- AND P2 is appended to the block opened by P1
- AND ONE `GuiaDeRemision` is produced for `guia_id = "T112-0065900"` with `source_pages = [P1, P2]`

#### Scenario EXT-S19g — Section boundary (different registro) opens a new block (unchanged)

- GIVEN current_block has `registro = "228"`
- AND the next page is Condition A with `registro = "229"` (different registro)
- WHEN `_stage_assemble_blocks` processes the page
- THEN the open block for registro 228 is closed
- AND a new block is opened for registro 229

#### Scenario EXT-S19h — Non-QR page WITH OCR material AND QR evidence → own ocr_fallback block + requires_review (rev-4 case 3, rev-5 guarded)

**Case 3. GREEN after condition (d); rev-5 requires the URL QR evidence to be present.**

- GIVEN page A: Condition A (QR `T112-0001`, registro `232`) with material lines
- AND page B: Condition B (`FORMA_HEADER_HEURISTIC`, registro `232`) whose compact QR
  FAILED to decode (`identity is None`) but whose URL `hashqr=` QR decoded
  (`page_hashqr_url is not None`) and whose OCR read material lines (`len(raw.lines) > 0`)
- AND page C: Condition A (QR `T112-0003`, registro `232`) with material lines
- WHEN `_stage_assemble_blocks` processes [A, B, C]
- THEN B is NOT dropped and is NOT absorbed into A's block
- AND B starts its OWN block with `guia_id = "ocr_1"`, `identity_source = "ocr_fallback"`,
  `source_pages = [B]`, `gre_hashqr_url` = B's URL QR
- AND every `MaterialLine` on B's block has `requires_review = True`
- AND THREE blocks are produced (A, B, C) and the registro total includes A + B + C material
  (B's quantity is NOT lost)

#### Scenario EXT-S19j — Non-QR page WITH material but NO QR evidence → DROPPED (rev-5, case 2b, FIX 1)

**RED against the rev-4 condition (which opened a phantom block); GREEN after the
`page_hashqr_url is not None` guard.**

- GIVEN page A: Condition A (QR `T112-0001`, registro `232`) with material lines
- AND page B: Condition B (`FORMA_HEADER_HEURISTIC`, registro `232`) with `identity is None`,
  `len(raw.lines) > 0` (a spurious non-materials table), AND `page_hashqr_url is None`
  (no compact QR, no URL QR — NO QR evidence)
- WHEN `_stage_assemble_blocks` processes [A, B]
- THEN B is DROPPED — it does NOT open an `ocr_fallback` block and is NOT absorbed
- AND only ONE block (A) is produced with `source_pages = [0]`

#### Scenario EXT-S19l — No-QR-evidence material page at RUN-START → DROPPED (rev-6, invariant guard)

**RED against the rev-5 code (guard only in condition d); GREEN after the invariant
`has_guia_evidence` gate is applied before the start-new-block logic.**

- GIVEN page A is the FIRST page of the run (`current_block is None`, run-start) with
  `identity is None`, `len(raw.lines) > 0` (a spurious non-materials table), AND
  `page_hashqr_url is None` (NO QR evidence)
- AND page B: Condition A (QR `T112-0002`, registro `232`) with material lines
- WHEN `_stage_assemble_blocks` processes [A, B]
- THEN A is DROPPED — it does NOT open a phantom block at run-start
- AND only ONE block (B) is produced; A's page is not in any `source_pages`

#### Scenario EXT-S19m — No-QR-evidence material page at SECTION BOUNDARY → DROPPED (rev-6, invariant guard)

**RED against the rev-5 code (a section-boundary page opened a block
unconditionally); GREEN after the invariant gate.**

- GIVEN current_block has `registro = "232"` opened by a QR page A
- AND the next page B has a DIFFERENT registro `231` (section boundary) with
  `identity is None`, `len(raw.lines) > 0`, AND `page_hashqr_url is None` (NO QR evidence)
- WHEN `_stage_assemble_blocks` processes [A, B]
- THEN B is DROPPED — the section boundary does NOT open a phantom block for B
- AND only the QR block A (registro 232) is produced

#### Scenario EXT-S19n — ocr_fallback material page WITH QR evidence at RUN-START → own block + requires_review (rev-6)

- GIVEN page A is the FIRST page of the run with `identity is None`,
  `len(raw.lines) > 0`, AND `page_hashqr_url is not None` (URL QR evidence)
- WHEN `_stage_assemble_blocks` processes [A]
- THEN A opens its OWN block with `identity_source = "ocr_fallback"`, `source_pages = [A]`
- AND every `MaterialLine` on the block has `requires_review = True` (flagging applies
  at run-start, not only continuation)

#### Scenario EXT-S19k — SUNAT enrichment preserves requires_review on an ocr_fallback block (rev-5, FIX 2)

**RED against the pre-fix `_apply_sunat_result` (which built fresh lines with the
default `requires_review=False`, erasing the C1 flag); GREEN after.**

- GIVEN an `ocr_fallback` block (`identity_source == "ocr_fallback"`, `gre_hashqr_url`
  present) whose OCR lines are flagged `requires_review = True`
- AND SUNAT is enabled and returns an `OfficialGre` with line items for that block's URL
- WHEN `_apply_sunat_result` replaces the block's OCR lines with SUNAT line items
- THEN every resulting `MaterialLine` still has `requires_review = True`
- AND a QR-identified block enriched the same way keeps `requires_review = False`

#### Scenario EXT-S19i — Non-QR page with 0 material lines (FHH photo) still dropped (case 2, rev-3 unchanged)

**Real-data model: registro 228, QR p98 + FHH photo p99 (0 lines).**

- GIVEN page p98: Condition A (QR, registro `228`) with material
- AND page p99: Condition B (`FORMA_HEADER_HEURISTIC`, registro `228`), `identity is None`,
  `len(raw.lines) == 0` (photo)
- WHEN `_stage_assemble_blocks` processes [p98, p99]
- THEN condition (d) is NOT triggered for p99 (`len(raw.lines) == 0`)
- AND p99 is DROPPED — ONE block, `source_pages = [98]`; the registro total is unchanged

---

## Known Limitation (accepted, non-blocking) — rev-4 narrowed

A real guía whose page carries a text-title "GUIA DE REMISION" but **no QR** AND **no OCR
material** (other-provider format, body unreadable) is dropped by the `identity is not None`
gate — treated identically to a photo page. Per domain authority this case is unseen/rare in
the current PDF corpus and is explicitly OUT OF SCOPE. Recovery: the #3 reprocess flow / a
future MANUAL-ENTRY feature.

**rev-4 narrowed this:** a non-QR page that DOES carry OCR material opened its own
`ocr_fallback` block flagged `requires_review` (case 3 / EXT-S19h).

**rev-5 re-bounds it:** case 3 now requires positive QR evidence
(`page_hashqr_url is not None`). A non-QR page with material but NO QR evidence is
dropped as a spurious table (case 2b / EXT-S19j). The accepted residual: a real guía
where BOTH the compact QR and the URL `hashqr=` QR fail to decode is ignored (no QR
evidence) — rare other-provider / manual-entry territory, OUT OF SCOPE. Recovery:
the #3 reprocess flow / a future MANUAL-ENTRY feature.

This replaces the rev-2 framing of "first-page pure-heuristic guía opening its registro".

---

## Out of scope for this delta

- Frontend REINTENTAR / "Reprocesar con IA" UI — deferred to change #3.
- Transient-vs-systematic classification probe — deferred to change #3.
- openspec documentation pass — deferred to change #7.
- Any change to OCR, vision, SUNAT fetch, or date logic.
- Introduction of any new enum value at the classifier level.
- Other-provider non-QR guía support — future MANUAL-ENTRY, out of scope.
