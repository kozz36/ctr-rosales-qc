# Design: Guía Classification Keystone (backend, change #2)

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

**Known limitation + recovery path**: a real guía page that is a text-title
"GUIA DE REMISION" page WITHOUT a QR is dropped by this gate (treated like any
non-QR page). Per domain authority this is unseen/rare and explicitly OUT OF
SCOPE. Recovery: the #3 reprocess flow / a future MANUAL-ENTRY feature. Documented,
not silently handled.

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
