# Delta for Extraction Domain
**Change**: guia-classification-keystone
**Phase**: spec (revision 4 — QR-only absorb gate + ocr_fallback material page → own reviewable block)
**Date**: 2026-06-05

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
3. **Non-QR page WITH material** (`identity is None`, `len(raw.lines) > 0` — the
   EXT-S24 `ocr_fallback` path, QR-decode failed but OCR read lines): the page MUST
   **start its OWN block** (`page_guia_id = "ocr_{source_page}"`,
   `identity_source = "ocr_fallback"`), be counted in the registro total, and be
   flagged **`requires_review` on its `MaterialLine`s** (uncertain identity). It MUST
   NOT be silently dropped. This uses the existing `MaterialLine.requires_review` field;
   the domain propagation (`domain/reconciliation.py`: any contributing-guía line
   `requires_review` → `row_requires_review`) surfaces it. No parallel flagging system.

Case 3 is implemented as start-new-block **condition (d)**
(`is_ocr_fallback_material = identity is None and len(raw.lines) > 0`); after it, a
material non-QR page never reaches the else-branch, so the else-branch sees only a
same-`guia_id` 2nd QR page (absorbed) or a 0-line photo (dropped).

The classifier verdict for Condition-B pages MUST remain `GUIA` / `FORMA_HEADER_HEURISTIC`
(page-local; no QR context available at classification time). The drop/own-block decision
lives exclusively in assembly.

**Spec revision history:**
- rev-1: Condition B → `IGNORED` at classifier (wrong — breaks EXT-S24).
- rev-2: Condition B stays `GUIA`; positional gate (`identity is None AND same registro AND
  qr-anchored`) in assembly (wrong premise — FHH continuation class does not exist).
- rev-3: Condition B stays `GUIA`; gate simplified to `absorb = identity is not None`;
  ALL non-QR pages dropped. Backed by real data + domain authority.
- **rev-4 (this):** else-branch gate kept; **case 3** added — a non-QR page WITH OCR
  material opens its own `ocr_fallback` block + `requires_review` instead of being
  dropped (C1 fix; restores the never-silently-drop validation-gate invariant). Only
  0-line non-QR photos are dropped.

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

#### Scenario EXT-S19h — Non-QR page WITH OCR material → own ocr_fallback block + requires_review (rev-4, C1)

**Case 3. RED against the rev-3 gate (where this page is dropped); GREEN after condition (d).**

- GIVEN page A: Condition A (QR `T112-0001`, registro `232`) with material lines
- AND page B: Condition B (`FORMA_HEADER_HEURISTIC`, registro `232`) whose QR FAILED to
  decode (`identity is None`) but whose OCR read material lines (`len(raw.lines) > 0`)
- AND page C: Condition A (QR `T112-0003`, registro `232`) with material lines
- WHEN `_stage_assemble_blocks` processes [A, B, C]
- THEN B is NOT dropped and is NOT absorbed into A's block
- AND B starts its OWN block with `guia_id = "ocr_1"`, `identity_source = "ocr_fallback"`,
  `source_pages = [B]`
- AND every `MaterialLine` on B's block has `requires_review = True`
- AND THREE blocks are produced (A, B, C) and the registro total includes A + B + C material
  (B's quantity is NOT lost)

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

**rev-4 narrows this:** a non-QR page that DOES carry OCR material is no longer dropped — it
opens its own `ocr_fallback` block flagged `requires_review` (case 3 / EXT-S19h). The
remaining limitation applies only to non-QR pages with **no extractable material**.

This replaces the rev-2 framing of "first-page pure-heuristic guía opening its registro".

---

## Out of scope for this delta

- Frontend REINTENTAR / "Reprocesar con IA" UI — deferred to change #3.
- Transient-vs-systematic classification probe — deferred to change #3.
- openspec documentation pass — deferred to change #7.
- Any change to OCR, vision, SUNAT fetch, or date logic.
- Introduction of any new enum value at the classifier level.
- Other-provider non-QR guía support — future MANUAL-ENTRY, out of scope.
