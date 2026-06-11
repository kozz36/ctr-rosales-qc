# Design — discarded-pages-recovery (SDD#2)

> Status: **DESIGNED** (2026-06-11). Artifact store: hybrid (engram
> `sdd/discarded-pages-recovery/design` + this file).
> Reads: `proposal.md` (product decisions 1–5 are fixed constraints).
> All file:line references verified against `main` (post PR #51–#54).

## 0. Summary

A new parallel domain side-channel `DiscardedPage` (Option B) carries every
GUIA-classified page dropped by the rev-6 QR-evidence gate from the pipeline drop site
(`application/pipeline.py:977-982`) through `PipelineResult` → extraction cache →
`ReviewService` → API → a third frontend tab. Recovery is a new
`ReprocessService.apply_page_recovery` with a three-tier chain (cached OCR lines →
OCR re-run via a new `ExtractionPort` constructor port → vision fallback), committing
through a new `ReviewService.recover_discarded_page` mutation hook that mirrors the
`add_recovered_guia` contract (fail-closed `requires_review` guard, audit event,
single persist). Recovered guías get the deterministic synthetic identity
`recovered_{page}` with the additive `identity_source="operator"` value, updated in
lockstep across all four Literal sites.

---

## 1. Decision D1 — Domain model: **Option B, new `DiscardedPage`** (rejects Option A)

**Pattern applied**: parallel additive side-channel (the same convention `errored_guias`
itself established, REC-EG-001) + Interface Segregation over a boolean/enum-discriminated
god-model. Option A is rejected as latent Shotgun Surgery: a `reason` discriminator forces
a filter into *every existing consumer* of `errored_guias`, and a missed filter is not a
cosmetic bug — it is a behavior change.

**Code evidence (decisive, verified)**:

1. **Bulk-batch enrollment leak** — `reprocess_registro` (routes.py:1198) selects
   `[e for e in review_service.errored_guias if e.registro == registro]`. Discarded
   pages DO carry a section registro (decision 2 of the proposal), so under Option A the
   existing "Procesar todos con IA" button would silently sweep discarded pages into a
   **vision-first** batch — violating both the OCR-first constraint (decision 4) and the
   operator-selection gate (decision 3).
2. **Recovery-lifecycle mismatch** — `add_recovered_guia` (review_service.py:507-526)
   distinguishes placeholder-replace vs append: errored guías have a 0-line
   `GuiaDeRemision` placeholder in `_guias` (registro inheritance at :519-521 depends on
   it). Discarded pages have **no placeholder** — the rev-6 invariant forbids the page
   from ever opening a block. The lifecycles are not the same rail; Option A's "one rail"
   appeal is illusory.
3. **Semantic inversion** — `ErroredGuia` (models.py:389) = *valid identity, zero lines*;
   a discarded page = *no identity, possibly cached lines*. Decision 4's `lines` field
   would be dead weight on every genuine errored guía, and `retry_attempted`
   (SUNAT-REINTENTAR-only, review_service.py:552) would leak into entries where SUNAT
   retry is meaningless (no hashqr URL by definition).
4. **Risk 4 of the proposal (REINTENTAR UX leakage) dissolves by construction** under B:
   discarded entries never enter `ErroredGuiasPanel` / `PendientesPorProcesarTab`.

**Model** (domain-pure, `domain/models.py`, BaseModel per domain convention):

```python
class DiscardedPage(BaseModel):
    """A GUIA-classified page dropped by the rev-6 QR-evidence gate (issue #50).

    Additive side-channel — NEVER opens a block, never alters grouping
    key/status/delta/qty. Surfaced for operator review and recovery.
    """
    page: int                                  # 0-based PDF page index (natural key)
    registro: str | None                       # section registro (page_to_registro)
    lines: list[MaterialLine] = []             # cached OCR lines at drop time (may be empty)
```

Nothing else (YAGNI): no image bytes (thumbnail endpoint renders on demand,
routes.py:662-772), no `recovered` flag (recovery REMOVES the entry, mirroring
errored-guía removal in `add_recovered_guia` :530).

## 2. Decision D2 — Synthetic identity: `recovered_{page}` + `identity_source="operator"`

**`guia_id` format**: `recovered_{page}` (e.g. `recovered_152`).

- **Deterministic over UUID** — `add_recovered_guia`'s idempotency contract
  (review_service.py:507-509) keys on `guia_id`: a double-click or replayed batch hits
  the true-idempotency no-op path instead of duplicating material. A UUID would defeat it.
  Pattern: deterministic synthetic identifier from the natural key (mirrors the existing
  `ocr_{page}` precedent, pipeline.py:928).
- **Collision-free by prefix**: real QR ids are `{serie}-{numero}` (`T009-0741770`,
  models.py:51-53); OCR-fallback ids are `ocr_{page}`. `recovered_` collides with
  neither, and the forbidden post-assembly scheme `guia_page_{n}` (pipeline.py:1681) is
  avoided. The three-identifier rule is preserved: this is a guía-rail identifier, never
  a Contents-ID and never a Registro N°.

**`identity_source` additive value**: `"operator"` — the identity assertion ("this page
IS a guía of this registro") comes from the operator's confirmation in the tab, not from
QR/OCR/vision. Reuses the exact vocabulary `match_method="operator"` already established
for operator line edits (schemas.py:372).

**Lockstep complete-enum update (the `match_method` 500-lesson — schemas.py:254
discipline)** — all FOUR sites in one commit, with a test asserting DTO validation of an
`identity_source="operator"` contribution:

| Site | File:line | Change |
|---|---|---|
| `GuiaContribution.identity_source` | `domain/models.py:72` | `Literal["qr","ocr_fallback","vision","operator"]` |
| `GuiaDeRemision.identity_source` | `domain/models.py:131` | same Literal |
| `GuiaContributionResponse.identity_source` | `infrastructure/api/schemas.py:35` | same Literal |
| Frontend union | `frontend/src/api/types.ts` | `'qr' \| 'ocr_fallback' \| 'vision' \| 'operator'` |

(`UnresolvedGuiaResponse.identity_source` is a plain `str` — no change needed; noted so
the audit does not flag it.)

## 3. Decision D3 — Recovery endpoints: page-keyed resource, dedicated batch + poll

Reusing `/errored-guias/{guia_id}/reprocess` is rejected: its lookup
(reprocess_service.py:505) requires the id to exist in `errored_guias` (synthetic ids do
not), and the path is vision-only — OCR-first cannot be retrofitted without forking its
semantics. Route conventions followed: resources under `/runs/{run_id}/…`, 202 + registry
status record + poll for batches (the SA-5 pattern, routes.py:1161-1268).

| Endpoint | Method | Contract |
|---|---|---|
| `/runs/{run_id}/discarded-pages/{page}/recover` | POST (async) | Single-page recovery. 200 → `RecoverPageResponse { recovered, page, guia_id, reason, rows, discarded_pages }` (updated rows + remaining discarded list — mirrors `RetryGuiaResponse` returning `errored_guias`, schemas.py:508). 404 page not in discarded list; 409 run not ready. |
| `/runs/{run_id}/discarded-pages/recover-batch` | POST | Body `{ pages: list[int] }` (operator-selected subset). 202 → `{ run_id, count }`; status record in registry entry `discarded_batches` under the fixed key `"discarded"`. **One active batch per run**: 409 if a batch is running (single-operator local-first tool; avoids inventing batch-id plumbing the registro-keyed precedent does not have). |
| `/runs/{run_id}/discarded-pages/recover-status` | GET | `{ total, recovered, failed, done }` — exact SA-5 shape of `ReprocessBatchStatusResponse` (schemas.py:539); terminal shape `total=0, done=True` when no batch fired (client never hangs, routes.py:1258-1261). |

Batch internals mirror `_run_reprocess_batch` (routes.py:1110-1158): `asyncio.gather`
over per-page `apply_page_recovery`, synchronous counter updates (race-free by
single-threaded asyncio construction), `done=True` only after gather resolves —
never settle prematurely (PR #49 SA-5 lesson).

**Surface**: `ReconciliationTableResponse` gains
`discarded_pages: list[DiscardedPageResponse] = []` (schemas.py:304, next to
`errored_guias` :321). Table-response **only** — the tab lives in ReviewPage, which
polls `GET /table`; a second surface on `RunStatusResponse` is redundant (no consumer)
and doubles the DTO maintenance.

## 4. Decision D4 — `apply_page_recovery` on `ReprocessService` + new `ExtractionPort` constructor port

**Verified correction to the exploration**: the constructor (reprocess_service.py:328-350)
takes `DocumentSourcePort` but **not** `ExtractionPort`. Additive port:
`extractor: ExtractionPort | None = None` — ports-only constructor preserved (Dependency
Inversion; zero concrete adapter imports in `application/`). Wiring in
`infrastructure/container.py::build_reprocess_service` reuses the existing OCR selection
logic from `build_pipeline` (container.py:378-407: `ocr.enabled=False` →
`NullOcrExtractor` / engine factory → `build_ocr_extractor(config.ocr)`) — extract that
branch into a small shared helper so the two builders cannot drift.

```
async apply_page_recovery(page: int) -> PageRecoveryResult        # dataclass, mirrors ReprocessResult
  1. entry = review_service.discarded_pages lookup by page        → reason="not_found"
  2. TIER 1 — cached lines: entry.lines non-empty → use directly
     (RapidOCR is deterministic: same image → same lines; zero render, zero OCR call)
  3. TIER 2 — OCR re-run: doc_source.render_page(page, dpi=300)
     → extractor.extract_printed_table(image) in run_in_executor
     (OCR is CPU-blocking — same executor discipline as the vision call, :540)
     Skipped when extractor is None / NullOcrExtractor yields nothing.
  4. TIER 3 — vision fallback: _downscale_image (REV-R11) +
     vision.read_material_table under the existing Semaphore (REV-R15)
  5. All tiers empty → PageRecoveryResult(recovered=False, reason="empty"); entry STAYS
  6. Normalize via the EXISTING _build_recovered_guia_lines_from_vision (:205-250) —
     it is generic over list[MaterialLine] (unit filter + key_resolver.resolve +
     requires_review=True unconditionally). Reused as-is for OCR and cached lines;
     rename to _build_recovered_lines is optional polish. This is where
     requires_review=True is set (line level); the fail-closed guard in
     ReviewService (:493-499) enforces it a second time (defense in depth).
  7. guia = GuiaDeRemision(
        guia_id=f"recovered_{page}", registro=entry.registro,   # set HERE — no
        fecha=None, fecha_entrega=None,                          # placeholder to inherit
        lines=..., source_pages=[page],                          # from (append path)
        identity_source="operator")
  8. Under the existing commit Lock: review_service.recover_discarded_page(page, guia)
```

**`fecha=None` is intentional**: no vision date read for recovered pages (material
recovery only). The existing null-fecha reconciliation rule flags the row
`requires_review` (pipeline.py:1718-1721 `_NULL_VISION_RESULT` precedent); no SUNAT for
these pages → no R9b floor, no R9c ceiling — graceful per the reception-date-authority
skill (off → no bracket). R9 divergence does not apply (no read date to diverge).

**New ReviewService mutation hook** — `recover_discarded_page(page, guia) ->
list[ReconciliationRow]`, mirroring the `add_recovered_guia` contract (T-3 convention):
fail-closed `requires_review` guard, append guía (no placeholder exists), drop the
`DiscardedPage` entry from `_discarded_pages`, re-reconcile with `_delivery_dates()`,
emit ONE audit event `kind="recovered_discarded_page"` with
`target={"guia_id", "page"}`, single `_persist()`. Rationale for a dedicated hook
instead of `add_recovered_guia` + a second call: one lock-scoped commit, one audit
event, one persist — and `add_recovered_guia`'s errored-list removal (:530) is a no-op
noise path for this flow. Pattern: same mutation-hook convention, second entry point
(Open/Closed over the existing hook rather than modifying it).

`ReviewService` state: constructor + `restore_from_sidecar` gain
`discarded_pages: list[DiscardedPage] | None = None` (defaulted — backward compatible),
`discarded_pages` read-only property mirrors `errored_guias` (:145-150).

## 5. Decision D5 — Extraction-cache evolution: additive key + tolerant hydration (no version bump)

**Pattern**: tolerant deserialization / additive schema evolution (Postel's law) — the
exact convention the cache already uses (`cache.get("errored_guias", [])`,
container.py:768; defaulted Pydantic fields throughout `models.py`).

Cache schema delta (`_stage_persist`, pipeline.py:1617-1628):

```python
cache_data = {
    ...,
    "errored_guias": [...],
    "discarded_pages": [d.model_dump(mode="json") for d in discarded_pages],  # NEW
}
```

Hydration (`build_review_service`, container.py:764-778):

```python
discarded_pages = [DiscardedPage.model_validate(d) for d in cache.get("discarded_pages", [])]
```

- **Old caches** (no key) → `[]` — loads without error, zero migration.
- **`MaterialLine` round-trip** already proven by the `guias` cache path (same model).
- `PipelineResult` gains `discarded_pages: list[DiscardedPage] = field(default_factory=list)`
  (pipeline.py:229); `_stage_assemble_blocks` returns the discarded list alongside blocks
  (collected at the :977-982 drop site — the `continue` stays; the gate's *blocking*
  semantics are untouched, the page still never opens or extends a block).
- No image bytes in the cache: page index + on-demand thumbnail render is the
  established pattern (routes.py:677-682) — keeps the cache small and the input PDF the
  single read-only source of truth.

**Sidecar-replay constraint (flagged, not invented — SA-2)**: recovery mutates in-memory
state + audit sidecar; restart correctness depends on `restore_from_sidecar` replaying
the new `recovered_discarded_page` event (re-append guía + re-drop entry), exactly as
the existing `recovered_guia` replay does for errored guías. The tasks phase MUST verify
the existing replay mechanism for `recovered_guia` (review_service.py:596+, not fully
read here) and mirror it; a strict-TDD restart round-trip test is mandatory (see §9).

## 6. Decision D6 — Frontend: third tab, selection-bulk component, reuse map

**Tab wiring** (`features/review/ReviewPage.vue:242-276`):
`type TabKey = 'reconciliacion' | 'pendientes' | 'descartadas'`;
`TAB_ORDER = ['reconciliacion', 'pendientes', 'descartadas']`; new `descartadasTabEl`
ref + `tabElFor` branch. `onTabKeydown` is already generic over `TAB_ORDER` (modular
arithmetic, :260-276) — arrow/Home/End a11y works with zero logic change. New tab button
mirrors the `tab-pendientes` markup (role="tab", aria-selected, aria-controls,
roving tabindex, count badge like `erroredCount` :75).

**New `DescartadasTab.vue`** (`features/review/`), mirroring
`PendientesPorProcesarTab.vue` conventions:

| Concern | Reused | New |
|---|---|---|
| Data in / refresh out | props `{ discardedPages, runId }` + `emit('refetch')` (no Pinia change — Pendientes precedent) | — |
| Thumbnails | `GET /runs/{run_id}/pages/{page}/thumbnail` (on-demand render, issue-#17 fallback chain) | thumbnail **grid** layout |
| Sheet viewer | `PageSheetViewer.vue` (PR #48) per page | — |
| Bulk progress | poll-until-`done` pattern + immediate first poll (Pendientes :337-365); settle ONLY on `done=true` (PR #49 SA-5 lesson) | polls the new `recover-status` endpoint |
| Selection | — | `selected: Set<number>` + per-card checkbox + select-all + confirm dialog before batch (decision 3: operator excludes non-guía sheets) |
| Per-page action | — | single "Recuperar" button → single-page endpoint |

**REINTENTAR leakage**: structurally impossible — discarded entries never reach
`ErroredGuiasPanel`/`PendientesPorProcesarTab` (D1/Option B); `DescartadasTab` renders
only Recuperar actions. No `reason`-based hiding logic needed anywhere.

**API client/types**: `types.ts` — `DiscardedPageResponse`, `RecoverPageResponse`,
batch request/status types, `identity_source` union + `'operator'` (D2 lockstep).
`client.ts` — `recoverDiscardedPage(runId, page)`, `recoverDiscardedBatch(runId, pages)`,
`getDiscardedRecoverStatus(runId)`.

---

## 7. Data flow (end-to-end)

```
PIPELINE (run time)
  _stage_assemble_blocks @ pipeline.py:977-982
    no QR evidence → DiscardedPage(page, registro=raw.registro, lines=raw.lines)
    (gate still `continue`s — rev-6 blocking semantics untouched)
  → PipelineResult.discarded_pages
  → _stage_persist: cache["discarded_pages"]                  [durable]

REVIEW (read)
  build_review_service: cache.get("discarded_pages", []) → ReviewService._discarded_pages
  GET /table → ReconciliationTableResponse.discarded_pages
  → ReviewPage → DescartadasTab (thumbnails via /pages/{page}/thumbnail)

RECOVERY (operator-triggered)
  operator selects pages → POST recover-batch {pages} (202)
  per page: ReprocessService.apply_page_recovery
    Tier1 cached lines → Tier2 OCR (ExtractionPort, executor) → Tier3 vision (Semaphore)
    → normalize (requires_review=True per line)
    → GuiaDeRemision(recovered_{page}, registro=entry.registro, identity_source="operator")
    → [commit Lock] ReviewService.recover_discarded_page(page, guia)
        guard → append guía → drop entry → re-reconcile → audit → persist
  frontend polls recover-status until done → emit('refetch') → updated grid;
  recovered rows visible, flagged requires_review (validation gate — never auto-accepted)
```

Grouping is untouched: recovered lines enter the standard
`(registro, material_canonical, unidad)` key via `key_resolver.resolve` (the
material-canonical-matching skill path — dual-spec normalization + grade-tolerant Tier 2
apply unchanged); `fecha` never enters the key; units never converted.

## 8. File-touch map (per hexagonal layer)

| Layer | File | Change |
|---|---|---|
| Domain (pure) | `domain/models.py` | `DiscardedPage`; `identity_source` Literal + `"operator"` (×2 sites). No IO/SDK imports. |
| Application | `application/pipeline.py` | drop-site emit (:977-982); `PipelineResult.discarded_pages`; `_stage_persist` cache key; `_stage_assemble_blocks` return shape. |
| Application | `application/review_service.py` | `_discarded_pages` state + property; `recover_discarded_page` hook; `restore_from_sidecar` param + replay for the new audit kind. |
| Application | `application/reprocess_service.py` | `extractor: ExtractionPort \| None` port; `apply_page_recovery`; `PageRecoveryResult`. Ports-only — unchanged discipline. |
| Infrastructure | `infrastructure/container.py` | wire `ExtractionPort` into `build_reprocess_service` (shared OCR-selection helper with `build_pipeline`); hydrate `discarded_pages` in `build_review_service`. |
| Infrastructure | `infrastructure/api/schemas.py` | `DiscardedPageResponse`, `RecoverPageResponse`, batch DTOs; `GuiaContributionResponse.identity_source` + `"operator"`; `ReconciliationTableResponse.discarded_pages`. |
| Infrastructure | `infrastructure/api/routes.py` | 3 endpoints (recover / recover-batch / recover-status) mirroring routes.py:1110-1268. |
| Adapters | — | none new (reuses SDD#1 OCR factory + vision factory via ports). |
| Frontend | `ReviewPage.vue`, new `DescartadasTab.vue`, `api/types.ts`, `api/client.ts` | third tab, selection-bulk, DTOs/client fns. |

## 9. Test strategy (strict-TDD — RED tests per slice)

**PR-1 (backend surface)**
- RED: pipeline unit test — a GUIA-classified page with `lines` and no QR evidence lands
  in `PipelineResult.discarded_pages` with its section registro (fails today:
  attribute absent). Companion assertion: it still does NOT open a block (rev-6 guard
  regression lock).
- RED: cache round-trip — persist → `build_review_service` hydrates the entry; **old
  cache without the key loads to `[]`** (backward compat).
- RED: `GET /table` surfaces `discarded_pages` (FastAPI TestClient, fake registry entry).
- Real-data gate: re-run the e2e fixture containing page 0152 (#50) — page appears in
  the API output (the unit-green ≠ correct lesson, docs/DECISIONS.md §audit).

**PR-2 (recovery)**
- RED: tier selection — entry WITH cached lines → zero `render_page`/OCR/vision calls
  (spy ports); empty lines → OCR called; OCR empty → vision called; all empty →
  `recovered=False, reason="empty"`, entry retained.
- RED: every recovered line `requires_review=True`; `recover_discarded_page` raises on a
  violating line (fail-closed guard parity).
- RED: recovered guía carries `registro` from the entry (lands IN the registro group,
  not unresolved) and `guia_id="recovered_{page}"`; double-recover is idempotent.
- RED: `identity_source="operator"` round-trips domain → DTO (the complete-enum
  500-lock: `GuiaContributionResponse.model_validate` must accept it).
- RED: endpoint contracts — 404 unknown page, 409 concurrent batch, 202 + status
  lifecycle `done=False → done=True` with real counts; restart round-trip (sidecar
  replay rebuilds recovered guía + removed entry).

**PR-3 (frontend)**
- RED vitest: TabKey/TAB_ORDER render 3 tabs + arrow-key cycle; selection set drives the
  batch payload; bulk summary renders ONLY after `done=true` (SA-5 premature-settlement
  regression); REINTENTAR absent in the tab.
- **SA-5 runtime gate (mandatory before "done")**: Playwright against the running app —
  upload → Descartadas tab → thumbnails visible → select subset → recover → progress
  settles → recovered row appears flagged in Reconciliación.

## 10. PR slicing (stacked-to-main, each < 400 changed lines)

1. **PR-1 `feat(pipeline): surface discarded GUIA pages`** — D1 model, drop-site emit,
   PipelineResult, cache persist/hydrate, ReviewService state + property, table DTO.
   Closes the #50 silent-drop visibility hole on its own (independently shippable, no UI).
   ~250–300 lines incl. tests.
2. **PR-2 `feat(recovery): OCR-first page recovery`** — D2 identity + Literal lockstep,
   D4 service + hook, D3 endpoints. Depends on PR-1. ~350 lines incl. tests (the
   largest; if tests push it over budget, the Literal lockstep + `recover_discarded_page`
   hook can split out as PR-2a).
3. **PR-3 `feat(review): Descartadas para revisión tab`** — D6 frontend (opus apply per
   session preference; SA-5 Playwright evidence required). ~300–350 lines.

## 11. Risks / open items for sdd-tasks

1. **Sidecar replay for `recovered_discarded_page`** (§5) — the existing `recovered_guia`
   replay path was not fully read here; tasks must verify and mirror it (restart
   round-trip test is the safety net). SA-2: flagged, not invented.
2. **Real discarded-page inventory** — the in-flight 493-page e2e run sizes the real
   list; a much larger count may justify pagination in the tab (not designed — YAGNI
   until evidence).
3. **`_stage_assemble_blocks` return-shape change** — internal signature touch; keep it
   a tuple return, no behavioral change to block assembly (regression lock in PR-1 tests).
4. **OCR port wiring duplication** — extracting the shared OCR-selection helper from
   `build_pipeline` must not change `build_pipeline` behavior (covered by existing
   container tests; verify `ocr.enabled=false` → Tier 2 skipped gracefully).
5. **Issue #56 (RapidOCR air-gap model download)** — out of scope but Tier-2 OCR re-run
   inherits it on air-gapped deploys; Tier 1 (cached lines) and Tier 3 (local Ollama
   vision) still function.

---

## Addendum — 343-page scale (2026-06-11 evidence)

> Status: **ADDENDUM** (2026-06-11). Resolves open item §11.2 ("pagination — YAGNI until
> evidence"). Evidence: full-PDF run `18504a03` (engram `eval/full-run-ocr-on-2026-06-11`):
> **343 of 469** GUIA pages dropped, forming **11 contiguous runs** (0-based): 33–35, 57–81,
> 99–137, 152, 165–222, 239–276, 279, 293–347, 358–376, 379–452, 463–492. All dropped:
> `FORMA_HEADER_HEURISTIC` conf 0.99, zero error flags; all 126 kept: QR identity.
> **Nothing settled above changes** (D1–D5 untouched; D6 extended, not rewritten).

### A1 — Grouping: contiguous page-runs, computed frontend-side

**Choice**: group the flat sorted `discarded_pages` list into contiguous runs in a Vue
`computed` (derived view-model — same pattern as `groups` in PendientesPorProcesarTab.vue:207).
Break a group at (a) a page-index gap OR (b) a `registro` change. O(n) single pass; no API,
DTO, or cache change — the settled flat list (D3/D5, PR-1) stays exactly as designed.
**Rejected**: backend-provided ranges — couples a presentation affordance into the DTO and
reopens settled PR-1/PR-2 surfaces for zero information gain (runs are fully derivable from
the sorted flat list).

### A2 — Rendering: collapsed groups by default + native lazy thumbnails

**Choice**: groups render **collapsed** (header only: page range, count badge, registro —
the Pendientes group-header pattern). Collapsed body is `v-if` → **zero `<img>` elements
exist** until expand; tab open costs 0 thumbnail requests instead of 343. On expand, reuse
the `SourcePages.vue` thumbnail pattern verbatim: `<img loading="lazy">` + `@load`/`@error`
Sets + persistent page-number overlay (issue #27/#17 conventions inherited for free).
Largest group is 74 pages (379–452); native lazy loading bounds off-screen fetches inside an
expanded group. **Rejected**: virtual list (new dependency, no codebase precedent, DOM node
count at 343 is trivial — the cost is image fetches, which collapse + `loading="lazy"`
already bounds) and hand-rolled IntersectionObserver (native attribute suffices).

### A3 — Selection at scale: group tri-state + global select-all + ETA confirm

Per-page checkboxes stay (settled). Added: **per-group header checkbox** (tri-state:
checked / indeterminate / unchecked) toggling that run's pages — works on a **collapsed**
group, so selecting an attachment block never requires loading its thumbnails; and a global
"Seleccionar todas (N)" control (satisfies REV-R29 select-all; N = all discarded entries,
independent of expansion — label carries the count so scope is explicit). Confirm dialog
reuses the Pendientes dialog (focus trap, W2 focus restore) with: **selected count prominent**
in title and confirm button; **ETA line** — `K` pages without cached lines (the
`has_cached_lines=false` indicator, REV-R33) × ~10 s OCR → "≈ X min"; Tier-1 pages are
near-instant; **conditional vision-cost warning** shown only when `K > 0` (only OCR-empty
pages can fall through to Tier-3 cloud calls). Worst case is honest: 343 × ~10 s ≈ 1 h.

### A4 — Batch semantics: no cap; per-page granularity; cancel deferred; re-attach on mount

- **No per-batch cap**: operator selection + the A3 ETA confirm is the gate; an arbitrary cap
  forces repeated select/confirm cycles on an already-deliberate action. One active batch per
  run (409) stays settled.
- **Status semantics**: `total` = `len(pages)` submitted; `recovered`/`failed` increment as
  each `apply_page_recovery` resolves (per-page granularity — pages leave the list
  incrementally per REV-R30-S02); `done=true` only after gather resolves (SA-5).
- **Cancel: OUT of scope.** Safe to defer because every recovered page commits durably and
  independently (single-persist per page); abandoning mid-batch loses nothing and corrupts
  nothing — worst case is unwanted local compute. Cancellation infra belongs to issue #41
  (deadline-guard in-flight cancel); do not invent a parallel mechanism here.
- **New (1 h batches make this matter)**: on tab mount, poll `recover-status` ONCE; if
  `done=false`, re-attach (resume polling, disable buttons). The settled terminal shape
  (`total=0, done=true` when no batch fired) makes this safe. Single-page "Recuperar" is also
  disabled while a batch is in-flight (REV-R30-S05 spirit).

### A5 — registro at scale: uniform per run (derived), registro-break as structural guarantee

`page_to_registro` is per-page; each `DiscardedPage` carries its own registro (settled).
**Derived argument**: a section starts at a DECLARED page; a contiguous run of dropped GUIA
pages contains no DECLARED page by construction, so a run cannot span a section boundary —
each of the 11 runs should map to exactly one registro (11 runs / 11 registros is consistent
with this). **Flag (SA-2)**: per-page registro values for the 343 pages were not in the
evidence dump — this is derived, not observed. The A1 registro-break rule is the structural
guarantee: if the derivation ever fails (e.g. `registro=None` stretches), groups simply
split; group headers always display a single registro (or "sin registro") — never a range.
Verify the 1:1 mapping in the PR-1 real-data gate.

### A6 — PR slicing: PR-3 splits into PR-3a / PR-3b

PR-1 and PR-2 are unaffected (A4 status semantics are clarification, not new code). PR-3 at
~300–350 lines cannot absorb grouping + collapse + tri-state + ETA + re-attach + their tests
within the 400-line budget. Split:

1. **PR-3a `feat(review): Descartadas tab — grouped list + selection`** — third tab wiring,
   A1 grouping, A2 collapsed groups + lazy thumbnails, A3 selection (per-page, per-group,
   global), single-page Recuperar. Independently shippable and SA-5-checkable. ~350 lines.
2. **PR-3b `feat(review): bulk recovery at scale`** — A3 confirm dialog + ETA, batch fire,
   poll-until-done, A4 mount re-attach, completion summary. Final SA-5 Playwright gate
   (upload → tab → expand group → select → recover → settle → flagged row). ~300 lines.
