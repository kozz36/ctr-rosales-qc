# Delta for Extraction Domain
**Change**: guia-classification-keystone
**Phase**: spec (revision 3 — QR-only absorb gate; real-data-validated)
**Date**: 2026-06-05

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

A continuation candidate page without a QR (`identity is None`) — including
`FORMA_HEADER_HEURISTIC` photos AND any text-title-no-QR page — is **DROPPED**: not appended
to any block, not creating a new block, not inflating any guía's `source_pages`. A QR-carrying
continuation page (`identity is not None`, same `guia_id`) is absorbed into the open block.

The classifier verdict for Condition-B pages MUST remain `GUIA` / `FORMA_HEADER_HEURISTIC`
(page-local; no QR context available at classification time). The drop decision lives
exclusively in assembly.

**Spec revision history:**
- rev-1: Condition B → `IGNORED` at classifier (wrong — breaks EXT-S24).
- rev-2: Condition B stays `GUIA`; positional gate (`identity is None AND same registro AND
  qr-anchored`) in assembly (wrong premise — FHH continuation class does not exist).
- **rev-3 (this):** Condition B stays `GUIA`; gate simplified to `absorb = identity is not None`;
  non-QR pages always dropped. Backed by real data + domain authority.

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

---

## Known Limitation (accepted, non-blocking)

A real guía whose page carries a text-title "GUIA DE REMISION" but **no QR** (other-provider
format) will be dropped by the `identity is not None` gate — treated identically to a photo
page. Per domain authority this case is unseen/rare in the current PDF corpus and is
explicitly OUT OF SCOPE. Recovery: the #3 reprocess flow / a future MANUAL-ENTRY feature.

This replaces the rev-2 framing of "first-page pure-heuristic guía opening its registro".
The core limitation is the same (non-QR page dropped) but the scope is clarified: it applies
to ALL non-QR pages, regardless of position, because the genuine FHH-continuation class does
not exist in the data.

---

## Out of scope for this delta

- Frontend REINTENTAR / "Reprocesar con IA" UI — deferred to change #3.
- Transient-vs-systematic classification probe — deferred to change #3.
- openspec documentation pass — deferred to change #7.
- Any change to OCR, vision, SUNAT fetch, or date logic.
- Introduction of any new enum value at the classifier level.
- Other-provider non-QR guía support — future MANUAL-ENTRY, out of scope.
