# Delta for Extraction Domain
**Change**: guia-classification-keystone
**Phase**: spec (revision 2 — design-aligned correction)
**Date**: 2026-06-04

---

## MODIFIED Requirements

### EXT-019 — [MODIFIED: replaces existing EXT-019] Hybrid page classifier — Condition B verdict unchanged; absorb-vs-skip decision is positional, not page-local

`PageClassifier` MUST classify a page using the three-condition hybrid logic (Conditions A, B,
C) as defined in EXT-019 (rev-3 delta), with **no change to Condition B's output verdict**:

A page that satisfies **Condition B only** (Forma-header heuristic — body ≤ 200 chars,
image-dominant, no title match) MUST continue to be classified as **`GUIA`** with
`title_matched="FORMA_HEADER_HEURISTIC"`. The classifier MUST NOT emit a new enum value
(`IGNORED` or otherwise) for Condition-B pages. Conditions A and C are unchanged.

**Rationale (design-proven):** A Condition-B page is signal-identical at the page level
whether it is a genuine scanned guía continuation (e.g. T112-0065421 page 152) or a
non-guía photo/annex. Test EXT-S24 (`test_hybrid_classifier.py:231`) asserts that a real
scanned guía with `image_dominant=True, qr_is_guia=False` MUST classify as `GUIA` via
`FORMA_HEADER_HEURISTIC`. Changing the verdict would break that case.

The absorb-vs-skip decision MUST be made in `_stage_assemble_blocks` using a **positional
gate**, not in the classifier. The gate predicate is:

```
absorb = (
    current_block is not None
    AND identity is None
    AND raw.registro == current_block.registro
    AND current_block.identity_source == "qr"
)
```

A Condition-B (`FORMA_HEADER_HEURISTIC`) page is absorbed as continuation ONLY when it is
positionally adjacent to an open block whose registro matches AND that block was opened by a
real QR identity (Condition A). Otherwise the page MUST NOT be absorbed — it is dropped from
the assembly loop and MUST NOT inflate any guía's `source_pages`.

(Previously (spec revision 1): Condition B was spec'd to yield `IGNORED` at the classifier
level. The design phase proved this regresses EXT-S24 — a genuine no-QR scanned guía that
legitimately classifies via heuristic. The discriminator is positional, not page-local; it
belongs in assembly, not the classifier.)

#### Scenario EXT-S19a — Non-guía image page NOT adjacent to a QR-opened block is NOT absorbed

- GIVEN a page satisfying Condition B only (`image_dominant=True`, `qr_is_guia=False`, body ≤ 200 chars)
- AND `_stage_assemble_blocks` has no currently-open block whose `identity_source == "qr"` with a matching registro
- WHEN `_stage_assemble_blocks` processes the page
- THEN the page is NOT appended to any guía block
- AND no new guía block is created for the page
- AND no existing guía's `source_pages` includes this page

#### Scenario EXT-S19b — Genuine scanned guía with passing QR classified as guia (Condition A — unchanged)

- GIVEN a page whose digital text is the 4-line Autodesk Forma header (≤ 200 chars)
- AND the page bears a SUNAT GRE QR that passes all five confidence-gate conditions
- WHEN `PageClassifier` processes the page
- THEN the page is classified as `guia` (Condition A — authoritative)
- AND it enters `raw_guias` normally
- AND it is eligible for QR identity, OCR quantity, and vision date extraction

#### Scenario EXT-S19c — Genuine multi-page guía continuation still assembles correctly (regression guard)

- GIVEN page 151 is classified `guia` via Condition A (QR `T112-0065421` passes confidence gate, `identity_source = "qr"`)
- AND page 152 has no QR but satisfies Condition B (≤ 200 chars, `image_dominant=True`, same registro)
- AND `current_block` at assembly time is the block opened by page 151 (`identity_source = "qr"`, registro matches)
- WHEN `_stage_assemble_blocks` evaluates the positional gate for page 152
- THEN `absorb` is `True`; page 152 is appended to the same block
- AND a SINGLE `GuiaDeRemision` is produced for `guia_id = "T112-0065421"`
- AND `source_pages = [151, 152]`

#### Scenario EXT-S19d — Standalone scanned guía (EXT-S24 regression guard): Condition B still classifies GUIA

- GIVEN a page with `image_dominant=True`, `qr_is_guia=False`, body ≤ 200 chars (satisfies Condition B only)
- WHEN `PageClassifier` processes the page
- THEN the page is classified as `guia` with `title_matched = "FORMA_HEADER_HEURISTIC"`
- AND no new classifier enum value is introduced

#### Scenario EXT-S19e — Condition-B page adjacent to open block but registro mismatch: NOT absorbed

- GIVEN a Condition-B page with `registro = "228"`
- AND `current_block` at assembly time has `registro = "232"` and `identity_source = "qr"`
- WHEN `_stage_assemble_blocks` evaluates the positional gate
- THEN `absorb` is `False`; the page is NOT appended to the open block
- AND registro 232's `source_pages` does NOT include this page

---

## Known Limitation (accepted, non-blocking)

A real guía whose FIRST page is pure-heuristic (Condition B only — no QR, no text title) and
is the first occurrence for its registro will fail the positional gate (`current_block is None`
at that moment) and be dropped from assembly. It is indistinguishable from a non-guía image
page at this layer. Recovery (manual re-decode / REINTENTAR flow) is deferred to change #3.
This is a single accepted residual edge, non-blocking for the current change.

---

## Out of scope for this delta

- Frontend REINTENTAR / "Reprocesar con IA" UI — deferred to change #3.
- Transient-vs-systematic classification probe — deferred to change #3.
- openspec documentation pass — deferred to change #7.
- Any change to OCR, vision, SUNAT fetch, or date logic.
- Introduction of any new enum value at the classifier level.
