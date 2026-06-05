# Design: Guía Classification Keystone (backend, change #2)

> **rev-5 (2026-06-05): Decision-1 case 3 GUARDED + SUNAT review-flag preserved.**
> Two surgical fixes:
> 1. **QR-evidence guard on case 3 (FIX 1).** The rev-4 condition
>    `is_ocr_fallback_material = identity is None and len(raw.lines) > 0` is TOO
>    BROAD: a sheet with NO QR at all that carries a NON-materials table (OCR emits
>    spurious "lines") would wrongly open a phantom `ocr_fallback` guía with bogus
>    material. A page is treated as an `ocr_fallback` guía ONLY with **positive QR
>    evidence**: the URL-variant `hashqr=` QR (`page_hashqr_url is not None`),
>    captured even when the compact identity QR fails (adapter EXT-012). New
>    condition adds `and page_hashqr_url is not None`. A no-QR page with a spurious
>    table now falls to the else-branch and is dropped. Residual accepted edge: a
>    real guía where BOTH QRs fail is ignored (rare other-provider / manual-entry,
>    out of scope).
> 2. **SUNAT preserves `requires_review` on `ocr_fallback` blocks (FIX 2,
>    JD round-4 WARNING).** `_apply_sunat_result` rebuilt block lines from the
>    OfficialGre with the default `requires_review=False`, ERASING the C1
>    uncertain-identity flag when SUNAT enriched an `ocr_fallback` block (compact
>    QR failed but URL QR decoded → SUNAT fetch succeeded). Fix: SUNAT lines for an
>    `ocr_fallback` block carry `requires_review=True`; QR blocks stay `False`.
>    Default app mode is SUNAT-enabled + OCR-on, so this is a production path.
>
> See **Decision-1 (rev-5)** below. rev-4 case-3 own-block behavior holds WITH QR
> evidence. Decision-2 (`errored_guias`) is unchanged — do NOT redo it.
>
> **rev-4 (2026-06-05): Decision-1 AMENDED (C1 fix).** The rev-3 gate
> `absorb = identity is not None` silently dropped a non-QR page that *carries OCR
> material* (the EXT-S24 `ocr_fallback` path: QR-decode failed but OCR read lines)
> when it was same-registro as an open block — material lost, no block, no
> `requires_review`. This violates the validation-gate invariant ("never silently
> drop; flag `requires_review`"). **Fix (case 3):** a non-QR page WITH material now
> opens its OWN `ocr_fallback` block (counted in the registro total) and is flagged
> `requires_review` (uncertain identity). The else-branch gate stays
> `absorb = identity is not None`; a material non-QR page no longer reaches it. The
> rev-3 0-line FHH-photo drop (case 2) is unchanged. See **Decision-1 (rev-4)** below.
> Decision-2 (`errored_guias`) is unchanged — do NOT redo it.
>
> **rev-3 (2026-06-05): Decision-1 REVISED.** The positional/adjacency gate was
> built on a premise the real-data e2e gate (run `67e4e7a1`) proved FALSE. See
> **Decision-1 (REVISED)** below. Decision-2 (`errored_guias`) is unchanged and
> validated — do NOT redo it.

## Technical Approach

Two additive, layer-respecting fixes. Bug 1 (continuation absorption): the
absorb-vs-drop decision lives in `_stage_assemble_blocks` (the only stage with
preceding-block context); rev-3 simplifies the predicate to **QR-identity-only
extension**. Bug 2 (silent 0-line guías): additive
`PipelineResult.errored_guias` side-channel populated after `_stage_sunat_fetch`,
mirroring `warnings`. Q1 RESOLVED: expose only `(registro, guia_id, source_pages)`;
probe deferred to #3.

## The crux (Bug 1) — REVISED by real data

The original design assumed a Condition-B page (`image_dominant`, no QR, no title,
classified `FORMA_HEADER_HEURISTIC` = "FHH") could be EITHER a genuine no-QR
continuation of a real guía OR a non-guía photo, and that adjacency to a
QR-anchored block disambiguated them. **The real data refutes the first horn:**

Hard evidence — run `67e4e7a1` / `/tmp/cache_67e4e7a1.json` (165 classifications,
83 guías, 140 material lines):
- 83 pages are `QR_IDENTITY` (real guías); 68 pages are FHH and **all 68 are photos/annexes**.
- **Material provenance: 140/140 lines on QR pages, 0 on FHH pages.**
- Guías opened by an FHH page: **0**. All 83 `identity_source == "qr"`.
- Every "multipage" block = ONE QR page + a tail of FHH photos: reg228 QR p98 + 39
  photos pp99-137; reg229 QR p56 + 25; reg231 QR p32 + 3; reg227 QR p151 + 1
  (T112-0065421, which has **0 lines** — it is a photo, not a continuation).

So the design's load-bearing example (preserve "genuine FHH continuation p152")
was wrong: p152 is an FHH photo and its guía has zero lines. **No genuine
multi-page guía has a QR-less continuation in the data.** The original gate
(`absorb = not is_heuristic_only or (identity_source=="qr" and same registro)`,
pipeline.py:981-984) KEEPS every same-registro FHH photo — the opposite of the fix.

**Domain authority (the engineer, ground-truth):** every SUNAT guía de remisión
carries a QR on EACH page (the page also prints "GUIA DE REMISION" in its
orientation). A non-QR page inside a registro section is a photo/annex.
Other-provider guías without QR are unseen/rare → a future one-off MANUAL-ENTRY
feature, **OUT OF SCOPE here**. Therefore: assume every guía page has a QR; a
multi-page guía is held together by **QR identity** (same `guia_id` on each page),
not by adjacency.

## Architecture Decisions

### Decision-1 (REVISED, rev-3): QR identity is the ONLY block-extender

**Choice**: In the `_stage_assemble_blocks` continuation else-branch
(pipeline.py:967-991), a continuation candidate is absorbed **iff it carries a QR
identity**. Replace the positional predicate with:

```python
absorb = identity is not None
```

(Equivalently: drop the `not is_heuristic_only or (...)` clause entirely.) A
non-QR page (`identity is None` — FHH photo, or any text-title-no-QR page) is
**dropped** (no append, no new block). A QR page is absorbed.

**Alternatives considered**:
| Option | Tradeoff | Decision |
|--------|----------|----------|
| Original positional gate (`not is_heuristic_only or (qr anchor and same registro)`) | Built on a false premise; KEEPS all same-registro FHH photos (reg228 → pp98-137 inflation). Refuted by 140/140 lines on QR pages. | **Rejected (was rev-2)** |
| `absorb = identity is not None` (QR-only extension) | Drops every non-QR page (all photos); a same-`guia_id` 2nd QR page (true multi-QR-page guía) is still absorbed by the existing start-new-block logic + this branch. Loses zero material (0 lines on FHH). | **Chosen (rev-3)** |
| Classifier emits FHH→IGNORED | Breaks EXT-S24 (classifier is page-local, pure); still cannot see QR context. | Rejected |

**Control-flow verification (against pipeline.py:935-991):** the else-branch is
reached only when `start_new_block == False`, which (lines 936-947) requires
`current_block is not None` AND `raw.registro == current_block.registro` AND NOT
(`identity is not None` AND `page_guia_id != current_block.guia_id`). So inside the
else-branch the registro already matches and `identity` is either `None` **or** a
QR with the **same `guia_id`**. Under `absorb = identity is not None`:
- `identity is None` (FHH photo, or a hypothetical text-title-no-QR page) → **dropped**. It does not append and does not start a block, so it CANNOT become a phantom 0-line guía. ✓
- `identity is not None` (same-`guia_id` 2nd QR page = a true multi-QR-page guía) → **absorbed** into the open block. ✓ This is exactly the desired multi-page-guía behaviour, now driven by QR identity, not adjacency.

**Rationale**: QR identity is the authoritative, deterministic guía boundary
(domain ruling). Adjacency was a proxy for a continuation class that does not
exist in the data. Removing it eliminates photo inflation (reg228 source_pages
collapses to `[98]`) with zero material loss, and remains correct for the only
real multi-page case (repeated QR identity).

**Known limitation + recovery path (rev-3, SUPERSEDED by rev-4 for the
material case)**: a real guía page that is a text-title "GUIA DE REMISION" page
WITHOUT a QR is dropped by the rev-3 gate (treated like any non-QR page). For a
page with **0 material lines** (FHH photo) this remains the intended behaviour.
For a page **with material** (`ocr_fallback`), rev-4 (Decision-1 rev-4 below)
no longer drops it — it opens its own reviewable block. The recovery path (#3
reprocess / future MANUAL-ENTRY) still applies to a no-material text-title page.

### Decision-1 (rev-4): non-QR page WITH material → own ocr_fallback block + requires_review

**Problem (C1).** The rev-3 gate is correct for 0-line photos but WRONG for a
genuine guía page whose QR failed to decode yet whose OCR read material lines
(EXT-S24 `ocr_fallback`: `identity_source == "ocr_fallback"`, `raw.lines`
non-empty). When such a page is same-registro as an open block it reaches the
else-branch (`identity is None` → not absorbed, not a new block) and is **silently
dropped** — material lost, no `errored_guia`, no `requires_review`. Empirically
(unit C1 regression): 400 KG across 3 guías → 250 KG, B's 150 KG vanished. QR
decode failure is real and documented (HANDOFF §QR fragility); OCR is ON by default.

**Choice.** Three cases, decided at block-assembly:
| Case | Condition | Action |
|------|-----------|--------|
| 1 | `identity is not None` (QR page) | extends/opens block as in rev-3 |
| 2 | `identity is None` AND `len(raw.lines) == 0` (FHH photo) | **dropped** (rev-3, unchanged) |
| 3 | `identity is None` AND `len(raw.lines) > 0` (`ocr_fallback` material) | **starts its OWN block**, flagged `requires_review` |

Implementation (`_stage_assemble_blocks`, pipeline.py ~935-993):
- Compute `is_ocr_fallback_material = identity is None and len(raw.lines) > 0`.
- Add start-new-block **condition (d)**: an `is_ocr_fallback_material` page opens a
  new block (a distinct `ocr_fallback` guía; `page_guia_id = f"ocr_{source_page}"`,
  `identity_source = "ocr_fallback"`).
- On opening that block, set `requires_review=True` on its `MaterialLine`s
  (`line.model_copy(update={"requires_review": True})`). This reuses the existing
  `MaterialLine.requires_review` field; the domain propagation in
  `domain/reconciliation.py` (any contributing-guía line `requires_review` →
  `row_requires_review`) surfaces it — **no parallel flagging system**.
- The else-branch gate stays `absorb = identity is not None`. After condition (d),
  only TWO kinds of page reach it: a same-`guia_id` 2nd QR page (absorbed) and a
  non-QR 0-line photo (dropped). A material non-QR page never reaches the else-branch.

**Alternatives considered**:
| Option | Tradeoff | Decision |
|--------|----------|----------|
| Keep rev-3 (drop the material page) | Silent material loss; violates the validation-gate invariant. | **Rejected** |
| Absorb the material page into the open block | Wrong identity assigned to its lines; would merge two distinct guías; hides the QR-decode failure. | Rejected |
| Route to `errored_guias` instead of a block | `errored_guias` is for 0-line phantom blocks (Decision-2); this page HAS material and must be counted in the registro total, not just listed. | Rejected |
| Own `ocr_fallback` block + `requires_review` (case 3) | Material retained and counted; uncertain identity surfaced for human review; uses the existing flag; additive. | **Chosen (rev-4)** |

**Real-data invariant preserved.** The 68 FHH photos in run `67e4e7a1` all have
0 lines → condition (d) NOT triggered → still dropped (case 2). reg228 still
collapses to `source_pages=[98]`, 140/140 material retained. Confirmed by unit
`test_zero_line_photo_still_dropped`.

### Decision-1 (rev-5): case 3 requires QR evidence + SUNAT preserves the review flag

**FIX 1 — QR-evidence guard.** Case 3 (rev-4) opened a block for ANY non-QR page
with `len(raw.lines) > 0`. Domain authority: a page is a guía ONLY with positive
QR evidence; OCR "lines" on a non-guía sheet (a non-materials table) are spurious.
The implementable QR-evidence proxy is the URL-variant `hashqr=` QR
(`page_hashqr_url`), a SUNAT GRE URL by definition, captured in the identity-None
branch even when the compact identity QR fails (adapter EXT-012; pipeline.py sets
`page_hashqr_url = page_hashqr_url_candidate`). The system CANNOT detect "compact
QR present-but-unreadable" when there is also no URL QR, so `page_hashqr_url is not
None` is the proxy.

Corrected condition (`_stage_assemble_blocks`, pipeline.py ~935-960):
```python
is_ocr_fallback_material = (
    identity is None
    and len(raw.lines) > 0
    and page_hashqr_url is not None
)
```
| Case | Condition | Action |
|------|-----------|--------|
| 1 | `identity is not None` (QR page) | extends/opens block |
| 2 | `identity is None` AND `len(raw.lines) == 0` (FHH photo) | dropped |
| 2b (rev-5) | `identity is None` AND `len > 0` AND `page_hashqr_url is None` (no QR evidence; spurious table) | **dropped** (falls to else-branch, `absorb = identity is not None` = False) |
| 3 | `identity is None` AND `len > 0` AND `page_hashqr_url is not None` (`ocr_fallback` material WITH QR evidence) | starts its OWN block, `requires_review` |

**Residual accepted edge.** A real guía where BOTH the compact QR and the URL QR
fail to decode is ignored (no QR evidence). Rare — other-provider / manual-entry
territory — and out of scope; the validation gate still never silently drops a
page that carries QR evidence.

**FIX 2 — SUNAT preserves `requires_review` on `ocr_fallback` blocks.**
`_apply_sunat_result` (pipeline.py ~1138-1185) replaces `block.lines` with fresh
`MaterialLine`s built from the OfficialGre items, which default to
`requires_review=False`. For an `ocr_fallback` block (compact QR failed but the URL
QR decoded → SUNAT fetch by `hashqr_url` succeeded), this erased the C1
uncertain-identity flag even though the material was enriched. Fix:
`preserve_review = block.identity_source == "ocr_fallback"`; each SUNAT line is
built with `requires_review=preserve_review`. QR-identified blocks keep `False`.
The material side-channel (key/status/delta/qty) is untouched — only the additive
review flag is carried.

**Real-data invariant preserved (rev-5).** The 68 FHH photos (run `67e4e7a1`) have
0 lines → not case 3 (and mostly no QR) → still dropped. reg228 still collapses to
`source_pages=[98]`, 140/140 retained. Confirmed by `test_zero_line_photo_still_dropped`
and `TestEXTS19bRealDataReg228PhotosNotAbsorbed`.

### Decision-2 (UNCHANGED — validated): additive `PipelineResult.errored_guias`

| Option | Tradeoff | Decision |
|--------|----------|----------|
| New field on `ReconciliationRow` | Violates "never touch row key/status/delta/qty"; a 0-line guía is an input gap, not a row. | Rejected |
| New `PipelineResult.errored_guias` (mirrors `warnings`) | Purely additive; consumers ignoring it are unaffected; report port reads it optionally. | **Chosen** |

Pure pydantic entry in `domain/models.py`:
```python
class ErroredGuia(BaseModel):
    registro: str | None
    guia_id: str
    source_pages: list[int]
```
`PipelineResult` gains `errored_guias: list[ErroredGuia] = field(default_factory=list)`
(mirrors `warnings`, pipeline.py:227). Detected in `run()` right after
`_stage_sunat_fetch` (:370) by scanning `blocks` with `lines == []`. Never touches
key/status/delta/qty; the 0-line guía still flows through reconcile and surfaces
flagged as today.

## Layer Placement (hexagonal — verified)
- `domain/classifier.py`: verdict UNCHANGED (pure).
- `domain/models.py`: add pure `ErroredGuia` (no IO).
- `application/pipeline.py`: simplified absorb predicate + side-channel populate — ports/config only, zero concrete-adapter imports.
- `ReportPort`: consumes `errored_guias` only if #3 needs export; out of scope here.

## Data Flow
```
classify (Cond B → GUIA, title=FORMA_HEADER_HEURISTIC)
   → raw_guias (kind=="GUIA", :789)
       → assemble_blocks ── absorb = identity is not None ──> QR page extends block
            │                                                non-QR page DROPPED (photo)
            └─> blocks ─> sunat_fetch ─> [0-line scan] ─> errored_guias
                                  └─> vision ─> normalize ─> reconcile ─> rows
PipelineResult(rows=..., warnings=..., errored_guias=...)   # all additive
```

## File Changes
| File | Action | Description |
|------|--------|-------------|
| `application/pipeline.py` | Modify | Replace absorb predicate in `_stage_assemble_blocks` else-branch (:967-991) with `absorb = identity is not None` (drop `is_heuristic_only` computation/clause); keep 0-line scan after `_stage_sunat_fetch` (:370); `errored_guias` on `PipelineResult` (:227) + `run()` return (:431) — unchanged from rev-2 |
| `domain/models.py` | Modify | Add pure `ErroredGuia` (unchanged from rev-2) |

## Testing Strategy (strict-TDD ACTIVE — `cd backend && uv run pytest`)

Test changes required for apply (Decision-1 rev-3):

| Test | Action | Why |
|------|--------|-----|
| `TestEXTS19cGenuineContinuationRegression` (EXT-S19c, test_positional_gate.py:328) | **REMOVE/INVERT** | Guards a non-existent case (QR p151 + no-QR FHH p152 → one block). Real data: p152 is a photo with 0 lines and must NOT be absorbed. Invert to assert source_pages==[151] only. |
| `TestConditionCContinuationAbsorbed` (test_positional_gate.py:210) | **REMOVE/INVERT** | Fix-agent added; pins `absorb=True` for a text-title non-QR continuation. Now WRONG — non-QR pages are never absorbed. Invert to assert the text-title-no-QR page is dropped (block source_pages==[0]). |
| **NEW real-data-shaped test** | **ADD** | Model reg228: a QR guía page followed by FHH photo page(s) of the SAME registro → photos NOT absorbed; assert `block.source_pages == [<QR page>]` only. RED-first against the current gate. |
| `TestEXTS19aConditionBNoQrBlockNotAbsorbed` (EXT-S19a) | KEEP | Still correct: non-QR FHH page not absorbed. Passes under `absorb = identity is not None`. |
| `TestEXTS19eRegistroMismatchNotAbsorbed` (EXT-S19e) | KEEP | Registro mismatch → start_new_block; unaffected. |
| `TestEXTS19dClassifierVerdictUnchanged` (EXT-S19d) | KEEP | Classifier verdict untouched. |
| True multi-QR-page guía (same `guia_id` 2nd QR page) | ADD/KEEP if represented | Assert two QR pages with the same `guia_id`/registro assemble into ONE block (absorbed via `identity is not None`). |
| `errored_guias` tests (test_errored_guias.py) | KEEP (Decision-2 unchanged) | Validated. |

| Layer | What to test | Approach |
|-------|--------------|----------|
| Unit (assembly) | non-QR FHH page in same registro → dropped (`source_pages` excludes it) | direct `_stage_assemble_blocks` call, injected classifications/decode_map |
| Unit (assembly) | same-`guia_id` 2nd QR page → absorbed into one block | direct call, two `_decode_qr` with same identity |
| Unit (side-channel) | block `lines==[]` post-fetch → one `ErroredGuia`; with lines → none | direct call (Decision-2) |
| Integration / real-data | run `67e4e7a1` subset: reg228 `source_pages==[98]` (no pp98-137 inflation); 0-line guías in `errored_guias` | subset e2e gate |

## Migration / Rollout
No migration. Both changes additive. Rollback: restore the previous absorb
predicate + drop `errored_guias`/`ErroredGuia`.

## Open Questions
- None blocking. Single documented limitation: a non-QR text-title "GUIA DE
  REMISION" page is dropped (out of scope; recovery via #3 reprocess / future
  MANUAL-ENTRY). Per domain authority this case is unseen/rare.
