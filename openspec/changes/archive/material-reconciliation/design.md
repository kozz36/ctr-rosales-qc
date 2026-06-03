# Design — material-reconciliation

> Rebuilt 2026-05-31 from the canonical engram copy (`sdd/material-reconciliation/design`, #2662)
> after a concurrent-write corruption. Sections A–F (rev 2) are authoritative on conflict with the base.

## 1. Hexagonal Architecture & Folder Structure

Backend `backend/src/reconciliation/` split into four rings (dependencies point inward only):

```
domain/        # pure: entities, value objects, ports (Protocols), domain services
application/   # pipeline orchestration, config, run context, review service
adapters/      # pdf, ocr, vision, report — implement domain ports
infrastructure/# container (composition root), api (FastAPI)
```

Domain depends on nothing; application depends on domain; adapters implement domain ports; infrastructure wires everything. Verified: domain imports no SDK/framework.

## 2. Port Contracts (driven ports)

- `DocumentSourcePort`: page_count, page_text(i), render_page(i,dpi), contents_offsets() → section map.
- `ExtractionPort`: extract_declared(text)→Registro; extract_printed_table(image)→MaterialLine[]; extract_registro_from_detail_page / proto_page.
- `VisionLLMPort`: read_handwritten_date(image)→VisionResult{date,confidence}; supports_batch flag.
- `ReportPort`: write(rows, out_dir)→paths (xlsx+csv).
- `IdentityExtractionPort` (NEW, rev2): decode_identity(image)→GuiaIdentity{serie,numero,ruc_emisor,ruc_receptor,tipo,hashqr_url,confidence}.
- `SunatGreFetchPort` (NEW, rev2, SEAM ONLY, off by default): fetch(hashqr_url)→OfficialGre | None.

## 3. Vision Provider Adapters (provider-agnostic)

Factory selects by config.vision.provider: anthropic|openai|ollama. AnthropicVisionAdapter (Messages + Batches); OpenAICompatibleVisionAdapter (base_url swap → OpenAI cloud or Ollama). Domain never imports an SDK.

## 4. Pipeline Sequence (deterministic, single-pass)

split → classify → deskew → [rev2: assemble guía blocks → QR identity tier] → extract(OCR quantities + vision reception-date) → normalize → reconcile → review → export.

## 5. Frontend (Vue 3) Review UX

Pinia (client state) + TanStack Query (server state). ReviewGrid (10 cols, grouped registro+fecha, MATCH/MISMATCH semantic tokens), ConfidenceBadge (<0.85 flag), GuiaReassignDialog, ExportButton, SourcePages. rev2: row drill-down to contributing guías; reassign by guia_id; edit guía-line cantidad.

## 6. API Surface (FastAPI, local-first)

POST /runs; GET /runs/{id}; GET /runs/{id}/table; PATCH /runs/{id}/rows/{row_id}; POST /runs/{id}/reassign; POST /runs/{id}/export; GET /runs/{id}/audit. rev2: PATCH /runs/{id}/guias/{guia_id}/lines (guía-line cantidad edit); ReconciliationRowResponse gains guias[].

## 7. Data Model (pydantic)

MaterialLine{description_canonical, unidad, cantidad, confidence, source_page}; GuiaDeRemision{guia_id, registro, fecha, lines, + rev2: ruc_emisor, ruc_receptor, tipo, gre_hashqr_url, identity_confidence, first_page}; Registro{numero, fecha_declarada, declared_lines}; ReconciliationRow{registro, fecha, material_canonical, unidad, declared_qty, summed_qty, delta, status, source_pages, min_confidence, + rev2: guias[]}; PageClassification; VisionResult; GuiaIdentity (rev2); GuiaContribution (rev2).

## 8. Resolved Defaults

xlsx 10 cols + summary; confidence flag 0.85; deskew guía-only+fallback; review sidecar review.json.

# Delta — rev 2 (QR tiered extraction + guía-granularity review)

## A. Tiered deterministic-first extraction
IdentityExtractionPort + QrBarcodeExtractionAdapter (LOCAL: PyMuPDF 150dpi/2x grayscale, pyzbar+zxing-cpp union, position-defensive parse of compact GRE QR RUC|tipo|serie|numero|doccode|RUC → guia_id=serie-numero + RUCs + tipo + hashqr_url; confidence 1.0 gated on 11-digit RUC + tipo∈{09,31} + serie/numero present). Precedence: QR identity overrides OCR/vision; OCR owns quantities; vision owns reception fecha. SunatGreFetchPort seam off by default (breaks air-gap; electronic date/qty = cross-check only, never grouping key).

## B. Multi-page guía block grouping
Guía block = maximal run of consecutive GUIA pages within one section range; new block on run-start / section cross / new decoded QR. First-page guia_id+RUCs+tipo propagate to QR-less continuation pages; their OCR lines append to the same GuiaDeRemision. Replaces per-page guia_id=guia_page_{n}.

## C. Authoritative fecha = handwritten reception date (vision)
Grouping key fecha = handwritten reception date from the scan (vision), NEVER the electronic GRE date. fecha ≠ registro expected fecha → misfiled guía → reassignment.

## D. Guía-granularity review model
ReconciliationRow stays aggregate but gains guias: list[GuiaContribution]{guia_id, source_pages, cantidad, confidence} inline in the DTO (chosen over GET /guias to avoid N+1). Reassign targets guia_id (serie-numero). Quantity edit corrects a guía-line cantidad → recompute; summed_qty read-only. Removes the summed_qty→field:'fecha' bug.

## E. Page→registro fallback fix
build_page_to_registro_map / _derive_numero return None (UNRESOLVED) on derivation failure, NEVER the Contents/section ID. Unresolved guías surface for human review.

## F. Test-fixture correction (slice 1)
test_reconciliation.py / test_models.py use "4252" (section ID) as registro numero — replace with realistic registro numeros so the §E fix is verifiable.

## Delta summary — slice plan
0. spec-delta (this) → 1. backend-hotfix (contracts A/D/E + fixtures F) → 2. frontend-hotfix (drill-down/reassign/line-edit + a11y/visual) → 3. cleanup. Deferred opt-in: SunatGreFetchAdapter (Tier 4).

---

# Delta — rev 3 (2026-06-02): real-pipeline gap closure — hybrid classifier, dual-QR, SUNAT first-class opt-in, vision adequacy, year inference, sentinel

> Source of truth for this delta: a REAL pipeline run (provider=ollama qwen3.5:9b, real PaddleOCR,
> real pyzbar/zxing-cpp, raw 493-page PDF, pages 0–45, registros 230/231/232) plus the CONFIRMED
> SUNAT descargaqr spike (engram `sdd/material-reconciliation/sunat-fetch-spike`, #2750).
> Rev-2 §A–F remain canonical; this delta ADDS/MODIFIES on top of them. Engram #2662 is STALE — ignore.
> Spec coverage: EXT-019..023, REC-C07, REV-C05/C06.
>
> **Architectural through-line**: the rev-2 deterministic-first tiering was correct, but it was
> *unreachable* on real input because the classifier — the very first gate — could not see scanned
> guías. Rev-3 closes that gap and promotes SUNAT descargaqr from seam to a first-class OPT-IN
> deterministic tier, while preserving every domain invariant and the local-first/air-gap default.

## Pipeline stage graph — before vs after (the architectural meat)

**BEFORE (rev-2, broken on real input)** — linear, classify is a hard gate before any decode:

```
split → classify(digital-title|ocr_title) → [extract_declared]
      → extract_ocr(GUIA only) → assemble_blocks(QR decode here, GUIA only)
      → vision → normalize → reconcile → persist
```

Failure: `_stage_classify` decides GUIA from the digital text layer (or deskew `ocr_title`).
Scanned guías carry only the 4-line Autodesk Forma header (<200 chars, no "GUIA DE REMISION")
→ UNCLASSIFIED → they NEVER enter `_stage_extract_ocr`/`_stage_assemble_blocks` → the entire
rev-2 QR/OCR/vision guía side is dead code on real input (24 rows all GUIA_MISSING, guias=[]).

**AFTER (rev-3)** — a single page-identity pre-pass feeds classification; the decode result is
computed ONCE and reused everywhere (no second QR scan):

```
split
  → decode_identities  (NEW pre-pass: IdentityExtractionPort.decode_identity per page,
                        dual-QR robust; result cached in a page→DecodeOutcome map)
  → classify           (HYBRID: Condition A = QR-pass from the cached map;
                        Condition B = Forma-header-only + image-dominant heuristic;
                        Condition C = digital/ocr title — original EXT-001 logic)
  → extract_declared
  → [sunat_fetch]      (NEW, OPT-IN, OFF by default: SunatGreFetchPort.fetch(hashqr_url)
                        per block first page → OfficialGre digital line-items + GRE dates)
  → extract_ocr        (GUIA pages; OCR is FALLBACK for quantities when SUNAT data present)
  → assemble_blocks    (reuses the SAME cached decode map — NO re-scan; propagates hashqr_url
                        from block first page to continuation pages)
  → vision             (adequate input: stamp-crop or ≥300dpi — EXT-020)
  → normalize_dates    (NEW: bounded year inference — EXT-021/REC-C07; provenance year_inferred)
  → normalize          (material canonicalization, unchanged)
  → reconcile          (precedence: SUNAT qty > OCR qty; fecha ALWAYS handwritten/vision)
  → persist
```

Key invariant of the new graph: `decode_identities` is the ONLY place QR bytes are scanned.
`classify` (Condition A) and `assemble_blocks` both READ the cached `page → DecodeOutcome`;
neither re-invokes `IdentityExtractionPort`. This satisfies EXT-019's "MUST reuse … a second
independent QR scan MUST NOT be introduced."

---

## D1 — Hybrid classifier + pipeline re-sequencing (CRITICAL, spec risk #1)

**Decision**: Option (b) refined — **insert a `decode_identities` pre-pass between `split` and
`classify`, and make `classify` a hybrid OR-gate (Condition A ∨ B ∨ C) that consumes the cached
decode result.** Reject the alternatives:

- Reject (a) "lightweight QR probe inside classify": it would couple the domain `PageClassifier`
  to the `IdentityExtractionPort` (the classifier is a pure domain service; injecting an adapter
  port into it violates the dependency rule) AND it risks a second scan. The probe must live in
  the application pipeline, not the domain classifier.
- Reject (c) "two-pass classify": redundant — the QR decode IS the second signal; a separate
  second classify pass adds a stage without new information and re-renders pages.

**Concrete design**:

1. **Decode location**: new `_stage_decode_identities(page_count) → dict[int, DecodeOutcome]`.
   `DecodeOutcome` is an application-layer dataclass: `{identity: GuiaIdentity | None,
   hashqr_url: str | None, decoded: bool}`. It renders each page once (via `DocumentSourcePort`),
   calls `self._identity.decode_identity(image, page_idx)` when the identity adapter is wired,
   and stores the outcome. When `self._identity is None`, the map is empty (graceful: classifier
   falls back to Condition B/C only).

2. **Classifier stays pure**: `PageClassifier` does NOT learn about ports. Instead the pipeline
   passes two NEW pure inputs into `classify_page(...)`:
   - `qr_is_guia: bool` — `True` when the page's cached `DecodeOutcome.identity` passed the
     EXT-012 confidence gate (Condition A). This is a plain boolean computed by the pipeline from
     the cached map; the classifier never touches the adapter.
   - `image_dominant: bool` — `True` when the page is image-dominant (raster covers the majority
     of page area). Computed by the pipeline from `DocumentSourcePort` (new optional method
     `image_coverage_ratio(idx) → float`, or a render-size heuristic). The domain classifier
     receives only the boolean verdict.

3. **Hybrid OR-gate inside `PageClassifier.classify`** (EXT-019), evaluated with this precedence:
   - **Condition A (authoritative)**: `qr_is_guia is True` → `GUIA`, confidence `_HIGH_CONFIDENCE`,
     `title_matched="QR_IDENTITY"`. Highest epistemic weight; overrides digital-text outcome.
   - **Condition C (existing)**: digital/ocr title match `GUIA DE REMISIÓN` → `GUIA`
     (unchanged EXT-001 path). PROTOCOLO precedence preserved (protocolo pages carry a "GUIA"
     field label and MUST classify DECLARED first — existing `_match_protocolo` ordering kept).
   - **Condition B (heuristic fallback)**: cleaned-body char count `< 200` AND text matches the
     Forma-header signature AND `image_dominant is True` → `GUIA`, confidence `_HIGH_CONFIDENCE`
     but `title_matched="FORMA_HEADER_HEURISTIC"` so review can distinguish heuristic vs. titled
     classification. **Guard against false positives** (EXT-019, EXT-S25): Condition B MUST NOT
     fire when the cleaned body has `>= 200` chars — declared/protocolo/planilla pages always
     exceed this, so they can never be misclassified as GUIA via the heuristic. Supplier name is
     never a signal (EXT-001 constraint preserved).

   Evaluation order in code: protocolo/declared title checks FIRST (so a declared page with real
   text wins), then Condition A, then Condition C, then Condition B, else UNCLASSIFIED. Rationale:
   a page with substantial declared text must never be stolen by the QR/heuristic path; but a page
   that is image-only with a passing QR is unambiguously a guía.

4. **Misclassification avoidance for declared/protocolo**: the `>= 200` char gate on Condition B
   plus the "declared title checks first" ordering means EXT-S25 holds (1200-char declared page →
   DECLARED, never GUIA). A QR is never present on a declared page in practice; even if a stray QR
   decoded, the declared-title check runs before Condition A only when real declared text exists —
   we deliberately let Condition A win for image-only pages but lose to a positive declared-title
   match, because a page cannot be both a 1200-char Form Detail page and a scanned guía.

**Runtime risk if wrong**: if the pre-pass renders every page at QR DPI, run cost grows by one
render+decode per page (~0.1–0.2 s/page per EXT-012). Mitigation: the render is shared — the same
rendered bytes can be reused by `extract_ocr`/`assemble_blocks` via the cache rather than
re-rendering, keeping total renders ≈ unchanged vs. rev-2.

## D2 — Robust dual-QR decode (`QrBarcodeExtractionAdapter` upgrade)

**Decision**: replace the single grayscale@2x decode with a **multi-resolution COLOR decode**
(render at 200 dpi AND 400 dpi, decode each in COLOR with pyzbar ∪ zxing-cpp), and return BOTH
the compact identity AND the descargaqr `hashqr_url` from the union of all decoded payloads.

**Why the current adapter misses the URL QR**: `_preprocess` does `img.convert("L")` (grayscale)
at a fixed 2× upscale. The spike found the URL-variant QR is only reliably decoded in COLOR at
200+400 dpi; grayscale@2x catches the compact QR but drops the URL QR on ~12/20 pages.

**Concrete changes** (adapter-only; port contract unchanged):
- `decode_identity(image)` accepts the already-rendered page bytes as today, but the adapter MUST
  attempt decode at multiple effective resolutions. Because the pipeline pre-pass owns rendering,
  the cleanest split is: the pipeline renders at 200 dpi AND 400 dpi and passes both to a new
  `decode_identity_multi(images: list[bytes])`, OR the adapter internally re-scales the single
  input to two target resolutions. **Chosen**: keep the single-image port signature
  (`decode_identity(image)`), and have the adapter internally produce two scaled variants
  (200-equivalent and 400-equivalent) and run COLOR decode on each — no port change, no double
  render in the pipeline. `_decode_union` drops the grayscale `convert("L")` step and decodes the
  COLOR `np.array(img)` for zxing and the COLOR `PIL` image for pyzbar; the 2× upscale is retained
  as the lower-res tier.
- The union loop already separates URL-variant payloads (`http(s)://…hashqr=`) from the compact
  data payload. Keep that logic; the only change is the decode now yields BOTH on more pages.
- `GuiaIdentity.hashqr_url` is populated whenever the URL QR decodes on the page; EXT-012's
  "only-URL-variant ⇒ return None ⇒ OCR fallback" rule is preserved (identity still requires the
  compact data QR for a confident id).

**hashqr_url propagation** (D2 ↔ D1/D3): the URL QR may appear only on the block's FIRST page.
`_stage_assemble_blocks` already propagates `gre_hashqr_url` from the first page to continuation
pages (line ~587). Rev-3 keeps that: the block's `gre_hashqr_url` is the first non-null
`hashqr_url` across the block's pages (defensive: if the first page lacks it but a continuation
page has it, take the first available — a one-line change in block assembly).

**Performance budget**: two renders + two decodes per page raises the QR step from ~0.1 s to
~0.3–0.4 s/page; acceptable for a local batch tool. Lazy imports unchanged (pyzbar/zxing/PIL/numpy
imported inside methods).

## D3 — SUNAT descargaqr fetch — first-class OPT-IN deterministic tier (NEW)

**Decision**: implement `SunatGreFetchPort` as a real adapter `SunatDescargaqrAdapter` that does a
plain HTTP GET on the `hashqr_url` (the `hashqr` is the token — NO OAuth, NO Clave SOL), receives
the official SUNAT GRE representation PDF (`application/pdf`, full digital text), parses it with
PyMuPDF `get_text()`, and returns an `OfficialGre` carrying **deterministic line items
(cantidad / unidad / descripción / código producto SUNAT), fecha de emisión, fecha de entrega,
RUCs**. This PROMOTES SUNAT from "seam only" (rev-2 EXT-016) to a first-class tier — but it stays
**OFF by default** behind an explicit config flag; the committed default remains air-gapped.

**Why now**: the spike (#2750) CONFIRMED the endpoint works no-auth and returns the same
quantities as the physical printed guía (GRE = shipped = printed guía), so SUNAT quantities are
*more accurate than OCR* and the GRE delivery date gives a deterministic lower bound for year
inference (D5). This is the single highest-leverage accuracy upgrade available.

**Precedence (extends rev-2 EXT-013 tiering)**:

| Field | Authority when SUNAT enabled & fetch succeeds | Authority otherwise (default/air-gap) |
|-------|-----------------------------------------------|----------------------------------------|
| Identity (guia_id, RUCs, tipo) | QR (Tier 0) — unchanged | QR, else OCR-fallback |
| Quantities / units / material | **SUNAT GRE (Tier 4)** — overrides OCR | **OCR (Tier 1)** |
| Electronic GRE emisión/entrega date | SUNAT GRE (cross-check + year lower bound) | OCR-printed date if any, else absent |
| **Grouping `fecha` (reception)** | **Vision handwritten — ALWAYS** (NEVER SUNAT) | Vision handwritten — ALWAYS |

The invariant from REC-C01/EXT-017 is absolute: SUNAT gives quantities and electronic dates, but
the grouping `fecha` is the HANDWRITTEN reception date (vision), full stop. SUNAT quantities
replace OCR quantities *for the same physical guía* (they are the authoritative source of what was
shipped); OCR becomes the fallback used only when the fetch is off/unavailable or the page has no
hashqr_url.

**Domain purity (Hexagonal)**:
- `SunatGreFetchPort` stays in `domain/ports.py` (already present). `OfficialGre` is promoted from
  a bare Protocol to a domain Pydantic model: `OfficialGre{guia_id, ruc_emisor, ruc_receptor,
  fecha_emision: date|None, fecha_entrega: date|None, lines: list[MaterialLine]}` — pure, no IO.
- `SunatDescargaqrAdapter` lives in `adapters/sunat/descargaqr.py`. It **lazy-imports** the HTTP
  client (`httpx` or stdlib `urllib`) and PyMuPDF inside `fetch()` — the test suite imports the
  module without network deps. PDF text parsing reuses the existing PyMuPDF dependency.
- The pipeline/application depends ONLY on `SunatGreFetchPort`; `build_pipeline` wires the concrete
  adapter ONLY when `config.sunat.enabled is True`, else passes `None` (the new
  `_stage_sunat_fetch` is a no-op when the port is `None`).

**Config flag** (local-first default preserved): add `sunat` block to `AppConfig`:
`sunat: { enabled: bool = False, timeout_s: float = 10.0, cache: bool = True }`. Committed
`config.yaml` ships `enabled: false`. Enabling it is the ONLY network egress in the system and MUST
be documented as the explicit air-gap exception (mirror to `docs/DECISIONS.md`).

**Stage placement & flow** (`_stage_sunat_fetch`, runs AFTER block assembly so each block's
first-page `hashqr_url` is known):
1. For each block with a non-null `gre_hashqr_url` and `config.sunat.enabled`, call
   `self._sunat.fetch(hashqr_url)`.
2. On success → cache the downloaded PDF bytes in the run dir
   (`<run_dir>/sunat/{guia_id}.pdf`) for audit/idempotency; set the block's lines from the parsed
   SUNAT line-items (these become the authoritative quantities) and record the GRE `fecha_entrega`
   as the year-inference lower bound (D5). Provenance: each resulting `MaterialLine`/contribution
   carries `extraction_method`/source = `sunat_gre`.
3. On failure (timeout, non-200, non-PDF, parse error) → **graceful fallback**: log, leave the
   block's OCR lines intact, lower bound falls back to OCR-printed date. A fetch failure MUST NOT
   abort the run.

**Caching**: if `<run_dir>/sunat/{guia_id}.pdf` already exists (resume/re-run within the same run
dir), reuse it instead of re-fetching — keeps the run reproducible and minimises egress.

**Spec addition (EXT-023)**: a matching requirement is appended to `extraction/spec.md` in rev-3
format (the spec phase predated the confirmed spike). See "Spec addition" below for the text.

## D4 — Vision input adequacy for the handwritten reception date (EXT-020)

**Decision**: change the vision input from the current full-page-200dpi render to an **adequate
input** with two adapter-selectable modes, defaulting to **Option A (stamp-region crop)** with
**Option B (≥300 dpi full page)** as the configured alternative. The domain port
`read_handwritten_date(image)` is UNCHANGED; the *what-image-we-send* decision moves into the
pipeline/adapter (an adapter concern per EXT-020).

**Why**: the bake-off (#2747) showed full-page-200dpi makes gemma models return "NINGUNA"/
hallucinate; qwen3.5:9b reads day-month even on full-page-200dpi but the YEAR is always wrong
(handled by D5). Cropping the stamp region or rendering at ≥300 dpi materially improves the read
and makes the system robust to model choice (provider-agnostic requirement preserved).

**Stamp-region heuristic** (Option A): the CTR "Recibí conforme" reception stamp sits in the
lower-right region of the guía page. The pipeline computes a crop box as a configurable fraction
of the page (default: lower-right quadrant, e.g. `x∈[0.5,1.0], y∈[0.55,1.0]`) at the page's render
DPI, and passes the cropped PNG bytes to `read_handwritten_date`. The crop fraction is config
(`vision.stamp_crop: {x0,y0,x1,y1}`); if the crop yields an empty/too-small image, fall back to
Option B (≥300 dpi full page). The crop is computed in the pipeline (it owns rendering); the
vision adapter remains a thin "bytes-in → VisionResult-out" port impl, keeping it provider-agnostic.

**Model**: committed run override stays `provider=ollama, model=qwen3.5:9b` (the bake-off winner);
NOT hard-coded — selected via existing `vision.provider`/`vision.ollama.model` config.

**Stage**: `_stage_extract_vision` changes ONLY in which bytes it feeds the port (crop vs. higher
DPI from `DocumentSourcePort.render_page(idx, dpi=300)`), not in its cap/batch logic.

## D5 — Bounded year inference (post-vision normalize stage) (EXT-021 / REC-C07)

**Decision**: add a NEW pure normalize stage `_stage_normalize_dates(guias, blocks, sunat_data,
reference_date)` that runs AFTER vision and BEFORE material normalization. It reconstructs the
year for any guía whose vision day-month is confident but year is absent/garbled/low-confidence,
using the bounded rule and records `year_inferred` provenance.

**Bounds**:
- **Lower bound** = `delivery_GRE_date`: **deterministic from SUNAT `fecha_entrega`** when D3 is
  enabled and the fetch succeeded; else the OCR-printed GRE delivery date; else omitted
  (upper-bound-only inference with higher uncertainty). The compact QR carries NO date and MUST
  NOT be used (EXT-021 explicit).
- **Upper bound** = `reference_date`: the PDF document/export date if available, else the pipeline
  run date.

**Rule** (EXT-021): for `DD/MM` from vision, pick year `Y` such that
`delivery_GRE_date <= date(Y,MM,DD) <= reference_date`. Exactly one valid Y → use it; multiple →
most recent; none → flag `requires_review=True` (do NOT emit an out-of-bounds date).

**Provenance & purity**: the inference logic is a PURE domain function
(`domain/date_inference.py: infer_reception_year(day, month, lower, upper) → (date|None,
year_inferred: bool)`) — no IO, fully unit-testable. Add `year_inferred: bool = False` to
`VisionResult`, `GuiaContribution` (REC-C07), and a derived `any_year_inferred: bool` computed
property on `ReconciliationRow`. Default `False` preserves backward compatibility with existing
serialized runs. The stage lives in the application pipeline; the math lives in the domain.

**Audit-gate integrity**: `year_inferred=True` surfaces as a YELLOW/advisory flag (distinct from
the red `requires_review`/MISMATCH flag) — the OCR validation gate stays honest (an inferred year
is visually distinct from a directly-read year). Propagated to review UI (REV-C05) and export audit.

## D6 — `first_page` None sentinel (REV-C05 → EXT-022 / REV-C06)

**Decision**: change `GuiaDeRemision.first_page` from `int` (default `0`) to `int | None`
(default `None`). `0` becomes a valid concrete page index; `None` exclusively means "first page
unknown". Fix every fallback that used the `!= 0` idiom to use `is not None`.

**Concrete changes**:
- `domain/models.py`: `first_page: int | None = None`.
- `_GuiaBlock`/`_build_guia_from_block` already set `first_page` to the concrete page index, so the
  happy path is unaffected (page-0 guías now correctly retain `0`).
- Locate and fix the `UnresolvedGuiaResponse` fallback: replace
  `g.first_page if g.first_page != 0 else source_pages[0]` with
  `g.first_page if g.first_page is not None else (source_pages[0] if source_pages else None)`.
  UI (`UnresolvedGuiasPanel`, REV-C06) must treat `None` as "use source_pages / unknown page" and
  `0` as a real "page 0/1" reference.

**Backward-compat for serialized runs**: existing `review.json`/extraction-cache entries serialized
`first_page: 0`. After the type change, a deserialized `0` is now interpreted as "page index 0"
(was previously "absent"). For old runs this is a benign re-interpretation (a guía that genuinely
started at page 0 is now correctly shown; a guía that had `0` as a sentinel becomes "page 0").
Because the field had no None state before, no migration is required; the model accepts both `0`
and `None`. Document the semantic shift in `docs/DECISIONS.md`.

## Cross-cutting: domain invariants preserved (verification checklist)

- Units KG/TN/RD/Rollo summed independently, NEVER converted — untouched; SUNAT lines carry their
  own `unidad` (e.g. TONELADAS→TN normalization is a description/unit-mapping concern, NOT a
  cross-unit conversion).
- Grouping `fecha` = handwritten reception date (vision) — reinforced; SUNAT/electronic dates are
  cross-check + year-lower-bound only, NEVER the grouping key (D3 precedence table, D5 bounds).
- MATCH tolerance EXACT (0); confidence auto-flag 0.85 — unchanged.
- Three identifiers distinct (Contents-ID ≠ Registro N° ≠ QR serie-numero) — unchanged; rev-2 §E
  UNRESOLVED guard intact.
- Local-first / air-gap — preserved as the committed default; SUNAT fetch is the single, explicit,
  config-gated network exception (off by default).
- Hexagonal — domain stays pure (date_inference + OfficialGre are pure; classifier receives
  booleans, not ports); SUNAT/QR/vision are adapters behind ports; pipeline depends only on ports.

## Spec addition — EXT-023 (appended to extraction/spec.md in rev-3 format)

### EXT-023 — [ADDED] SUNAT descargaqr opt-in deterministic guía-data source

`SunatGreFetchPort` is promoted from a future seam (EXT-016) to a first-class OPT-IN deterministic
data source. When enabled, a concrete adapter MUST GET the QR-derived `hashqr_url`
(`…/descargaqr?hashqr=<base64>`) — no OAuth/Clave SOL (the hashqr is the token) — receive the
official SUNAT GRE representation PDF (`application/pdf`, full digital text), and parse it with
PyMuPDF to extract deterministically: line items (cantidad, unidad, descripción, código producto
SUNAT), fecha de emisión, fecha de entrega, and the emisor/receptor RUCs, returned as `OfficialGre`.

**Precedence**: when SUNAT data is available, SUNAT line-item quantities/units MUST take precedence
over OCR-extracted quantities for the same guía block (OCR becomes the fallback used only when the
fetch is off, unavailable, fails, or the page has no `hashqr_url`). The SUNAT `fecha_entrega` MUST
be usable as the deterministic lower bound for bounded year inference (EXT-021). SUNAT electronic
dates MUST NOT override the handwritten reception `fecha` for grouping (EXT-017 / REC-C01 absolute).

**Air-gap default**: `SunatGreFetchPort` MUST remain OFF by default behind an explicit config flag
(`sunat.enabled: false` in committed config). Enabling it is the ONLY network egress and MUST be
documented as the air-gap exception. When disabled, no network call is made and OCR quantities are
authoritative.

**Failure handling**: a fetch failure (timeout, non-200, non-PDF, parse error) MUST degrade
gracefully to OCR — it MUST NOT abort the run. The downloaded PDF MUST be cached in the run output
dir for audit/idempotency; a cached copy MUST be reused on re-run within the same run dir.

**Hexagonal**: `OfficialGre` is a pure domain model; the concrete adapter MUST lazy-import its HTTP
client; the pipeline/application MUST depend only on `SunatGreFetchPort`.

#### Scenario EXT-S30 — [ADDED] SUNAT fetch overrides OCR quantities when enabled
**Given** `sunat.enabled = true` and a guía block whose first page yields `hashqr_url`
**When** `SunatGreFetchPort.fetch` returns an `OfficialGre` with line items (e.g. 0.192 TONELADAS)
**Then** the block's quantities/units come from the SUNAT line items (not OCR)
**And** the SUNAT `fecha_entrega` is recorded as the year-inference lower bound
**And** the grouping `fecha` is STILL the handwritten reception date (vision), not the SUNAT date

#### Scenario EXT-S31 — [ADDED] SUNAT disabled by default keeps air-gap
**Given** committed config (`sunat.enabled = false`)
**When** the pipeline processes any guía block
**Then** no network call is made and OCR quantities are authoritative

#### Scenario EXT-S32 — [ADDED] SUNAT fetch failure degrades to OCR
**Given** `sunat.enabled = true` and the descargaqr GET times out or returns non-PDF
**When** the pipeline processes the affected block
**Then** the block retains its OCR-extracted quantities and the run does NOT abort
**And** the year-inference lower bound falls back to the OCR-printed GRE date (or omitted)

## Slice plan — rev 3
0. spec-delta rev-3 (DONE) → **sdd-tasks refresh** → 1. classifier+pipeline re-sequence (D1) +
   dual-QR (D2) + first_page sentinel (D6) [the unblock slice] → 2. vision adequacy (D4) + bounded
   year inference (D5) + REC-C07/REV-C05 provenance → 3. SUNAT opt-in tier (D3, EXT-023) behind
   off-by-default flag → 4. frontend year_inferred advisory + first_page=None panel (REV-C05/C06).
