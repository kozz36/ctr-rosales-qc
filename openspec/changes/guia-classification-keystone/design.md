# Design: Guía Classification Keystone (backend, change #2)

## Technical Approach

Two additive, layer-respecting fixes. Bug 1 (over-classification + continuation
absorption) is solved by moving the absorb-vs-ignore decision from the **page-local
classifier** (which cannot see context) to the **`_stage_assemble_blocks`** stage
(which sees the preceding block). Bug 2 (silent 0-line guías) is solved with an
additive `PipelineResult.errored_guias` side-channel populated after `_stage_sunat_fetch`,
mirroring the existing `warnings` precedent. Q1 RESOLVED: expose only
`(registro, guia_id, source_pages)`; transient/systematic probe deferred to #3.

## The crux (Bug 1) — why the discriminator is positional, not page-local

A Condition-B page (cleaned body `<= 200` chars, `image_dominant`, no QR, no title)
is **signal-identical** whether it is a genuine continuation of a real guía
(T112-0065421 p152) or a non-guía photo/annex page. Real-code proof: test
`EXT-S24` (`test_hybrid_classifier.py:231`) asserts a real scanned guía with
`image_dominant=True, qr_is_guia=False` MUST classify as GUIA via
`FORMA_HEADER_HEURISTIC`. So Condition B legitimately fires for BOTH a genuine
guía page and a non-guía image page. **No page-local predicate can separate them.**

The only signal that separates them is **adjacency to an identified guía block**:
a Condition-B page is a genuine continuation **iff** it is contiguous with, and
shares the `registro` of, an immediately-preceding block whose identity came from
a strong signal (QR `Condition A`, or `GUIA DE REMISION` text `Condition C`).
That context exists only in `_stage_assemble_blocks`, never in the pure classifier.

## Architecture Decisions

### Decision: Keep Condition B = GUIA in the classifier; gate absorption in assembly
| Option | Tradeoff | Decision |
|--------|----------|----------|
| Classifier Condition B → `IGNORED` (proposal literal) | Breaks EXT-S24: kills genuine no-QR guía first-pages AND continuations; they never enter `raw_guias`. Page-local layer lacks context. | Rejected |
| Classifier keeps GUIA + `title_matched="FORMA_HEADER_HEURISTIC"`; assembly demotes orphan heuristic pages | Heuristic pages still reach `assemble_blocks` (already filtered only by `kind=="GUIA"` at :789); assembly has the preceding-block context to decide absorb vs. drop. Domain stays pure. | **Chosen** |

**Rationale**: The classifier is page-local and pure; it cannot and should not make
a context-dependent call. `title_matched` already uniquely tags Condition-B pages
(no new enum value needed). The decision belongs where the context lives.

### Decision: Absorption predicate in `_stage_assemble_blocks` (pipeline.py:952-959)
A heuristic page (`identity is None` AND its classification `title_matched ==
"FORMA_HEADER_HEURISTIC"`) is absorbed as a **continuation** ONLY when a
`current_block` exists, shares its `registro`, AND that block was opened by a
**strong identity** (`identity_source == "qr"` OR `title_matched` of its first page
was a `GUIA DE REMISION` text match). Otherwise the heuristic page is **dropped**
(not appended, no new block) and recorded for the side-channel.

```
absorb = (
    current_block is not None
    and identity is None
    and raw.registro == current_block.registro
    and current_block.identity_source == "qr"      # strong-signal anchor
)
```
- Genuine continuation (p152 after a QR p151, same registro) → absorbed (unchanged behaviour).
- Non-guía image page with no preceding strong block, or registro mismatch → dropped.
- A heuristic page that DOES carry its own QR is `Condition A`, not Condition B → unaffected.

**Distinguishing condition stated precisely**: "continuation of a real guía" =
no-QR Condition-B page contiguous-and-same-registro to a QR-anchored open block;
"non-guía image page" = a Condition-B page failing that adjacency test.

`_stage_assemble_blocks` must therefore receive the per-page `title_matched`
(via the `classifications` list it already takes, indexed by page) so it can tell
a heuristic page from a real no-QR first page. No new dependency.

### Decision: Bug 2 side-channel as additive `PipelineResult.errored_guias`
| Option | Tradeoff | Decision |
|--------|----------|----------|
| New field on `ReconciliationRow` | Violates "never touch row key/status/delta/qty"; 0-line guías are an input gap, not a row. | Rejected |
| New `PipelineResult.errored_guias` field (mirrors `warnings`) | Purely additive; consumers ignoring it are unaffected; report port reads it optionally. | **Chosen** |

Entry type lives in `domain/models.py` (pure pydantic):
```python
class ErroredGuia(BaseModel):
    registro: str | None
    guia_id: str
    source_pages: list[int]
```
`PipelineResult` gains `errored_guias: list[ErroredGuia] = field(default_factory=list)`
(dataclass default, mirrors `warnings`, pipeline.py:227).

### Decision: 0-line detection point — after SUNAT fetch, before reconcile
Detected in `run()` immediately after `_stage_sunat_fetch` (:370) by scanning
`blocks` whose `lines == []` (OCR returned nothing AND SUNAT did not enrich).
Built into `errored_guias` from `(block.registro, block.guia_id, block.source_pages)`.
This NEVER touches group key / status / delta / qty — the 0-line guía still flows
through reconcile and surfaces as a flagged row exactly as today; the side-channel
only *exposes* the gap for #3's UI.

## Layer Placement (hexagonal — verified)
- `domain/classifier.py`: unchanged verdict (stays pure booleans-in / value-out).
- `domain/models.py`: add pure `ErroredGuia` (no IO).
- `application/pipeline.py`: assembly gating + side-channel population — ports/config only, zero concrete-adapter imports.
- Report port (`ReportPort`): consumes `errored_guias` only if #3 needs export; out of scope here.

## Data Flow
```
classify (Cond B → GUIA, title=FORMA_HEADER_HEURISTIC)
   → raw_guias (kind=="GUIA", :789)
       → assemble_blocks  ── adjacency gate ──> absorb genuine continuation
            │                                   drop non-guía image page
            └─> blocks ─> sunat_fetch ─> [0-line scan] ─> errored_guias
                                  └─> vision ─> normalize ─> reconcile ─> rows
PipelineResult(rows=..., warnings=..., errored_guias=...)   # all additive
```

## File Changes
| File | Action | Description |
|------|--------|-------------|
| `application/pipeline.py` | Modify | Adjacency gate in `_stage_assemble_blocks` (:952-959); 0-line scan after `_stage_sunat_fetch` (:370); add `errored_guias` to `PipelineResult` (:227) and `run()` return (:431) |
| `domain/models.py` | Modify | Add pure `ErroredGuia` model |

## Testing Strategy (strict-TDD ACTIVE — `cd backend && uv run pytest`)
| Layer | Failing-first test | Proves |
|-------|--------------------|--------|
| Unit (assembly) | Non-guía Condition-B image page with NO preceding QR block (or registro mismatch) → NOT absorbed, NOT a block, recorded errored/dropped | Bug 1 fix |
| Unit (assembly REGRESSION GUARD) | QR guía p151 + no-QR Condition-B p152 same registro → ONE block, `source_pages==[151,152]` | Continuation preserved (must FAIL if gate over-drops) |
| Unit (classifier) | EXT-S24 still GUIA via heuristic | Classifier verdict unchanged |
| Unit (side-channel) | Block with `lines==[]` post-fetch → one `ErroredGuia(registro,guia_id,source_pages)`; block with lines → none | Bug 2 detection |
| Unit (additive invariant) | Run with errored guías present → every correctly-processed row keeps identical key/status/delta/qty vs. baseline | Side-channel additive-only |
| Integration / real-data | Run `67e4e7a1` subset: reg228 `source_pages` matches real range (no pp98-137 inflation); 0-line guías appear in `errored_guias` | End-to-end gate |

## Migration / Rollout
No migration. Both changes additive. Rollback: revert assembly gate + drop
`errored_guias`/`ErroredGuia`.

## Open Questions
- None blocking. Q1 resolved (defer probe to #3). Assembly must read per-page
  `title_matched`; if a real guía first-page legitimately has NO QR AND NO text
  title (pure heuristic) AND is the FIRST page of its registro, the gate drops it —
  acceptable per scope (such a guía has no strong identity anchor and is
  indistinguishable from an image page; #3's retry flow covers recovery). Flagged
  as the single residual edge.
