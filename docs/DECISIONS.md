# Decisions & Findings — material-reconciliation

Versioned record of every significant decision and audit finding (mirrors the local engram).
Newest context at the bottom of each section.

---

## Domain rules (locked)

- **Two identifiers**: `#4252` = Autodesk Forma section/Contents ID; `232` = business
  **Registro N°** (from detail Description + Protocolo). Group by the **Registro N°**.
- **Grouping key**: `(registro, material_canonical, unidad)` — **`fecha` removed** (rev-3 R8/MAT-001;
  it split declared↔guía groups on vision-date noise and killed MATCH). Material reconciliation is
  date-independent; `fecha` is a divergence/misfiled signal only (see §dates, R8, R9).
- **Units** KG / TN / RD / Rollo are **summed independently — never converted**.
- **Page classification by document TITLE**, not supplier name. `GUÍA DE REMISIÓN` feeds the
  sum; `Planilla Resumen`, `Listado de Barras`, photos, cover, contents do **not**.
- **Declared side is trusted digital text** (Protocolo canonical, detail = cross-check).
  Reconciliation vs declared **is the validation gate** that surfaces OCR errors.
- **Dates** (§dates, R9): the declared reception date is the **DIGITAL printed `Fecha:` on the
  Protocolo de Recepción** (deterministic parse, linked to the Registro N°, **no vision call**) —
  handwritten dates exist only on the guías. Guías should carry that same declared date. `fecha`
  is **not** a grouping axis; a guía whose handwritten date **diverges** (compared by **day-month**;
  year reconstructed by bounded inference)
  is the **misfiled-guía** signal → non-blocking no-match **WARNING** with page number + red
  highlight (individual/group) → human review + manual reassign. Never auto-corrected.

## Stack & architecture (locked)

- Hexagonal / Ports & Adapters, greenfield, **local-first**.
- Backend: Python 3.12 + FastAPI; PyMuPDF; PaddleOCR (deskew + printed tables); polars;
  pydantic. Vision is **provider-agnostic** behind `VisionLLMPort`: `AnthropicVisionAdapter`
  + `OpenAICompatibleVisionAdapter` (OpenAI cloud **and** Ollama via `base_url` swap).
  Selected by config `provider: anthropic | openai | ollama`. Domain never imports an SDK.
- Frontend: Vue 3 + TS + Vite + Pinia (client state) + TanStack Query (server state) + PrimeVue + Tailwind.
- Deterministic single pipeline (no agent/orchestration framework): `split → classify →
  deskew → extract[OCR+vision] → normalize → reconcile → review → export`.

## Locked defaults

1. MATCH tolerance: **EXACT (0)** — any nonzero delta is a MISMATCH (no rounding epsilon).
2. Confidence auto-flag threshold: **0.85** (values below flag for review; MISMATCH always flags).
3. Deskew scope: **guía pages only**, post-classification, with orientation fallback.
4. Review-edit persistence: **per-run sidecar `<run_dir>/review.json`** (resumable across restarts).
5. Export xlsx: **10 columns** (Registro, Fecha, Material, Unidad, Declarado, Sumado(guías),
   Delta, Estado, Confianza mín, Páginas origen) + summary sheet.

---

## §audit — e2e integration audit (real 493-page PDF) — 5 bugs, ALL FIXED

Unit tests were green but the real pipeline was broken. A real-data e2e audit found:

- **C-1** `_stage_extract_declared` used `Registro(numero="page_N")` instead of the real
  parsers → wrong key, null fecha. **Fixed.**
- **C-2** detail + protocolo both DECLARED → 22 registros not 11, declared qty doubled.
  **Fixed** (protocolo canonical, dedupe by numero).
- **C-3** page map keyed on Contents-ID (4252) ≠ Registro N° (232) → MATCH impossible.
  **Fixed** (keyed on numero).
- **C-4** `page_to_registro` computed but never applied to guías. **Fixed.**
- **H-5** scanned guía pages never got `ocr_title` → all UNCLASSIFIED. **Fixed** (deskew title-OCR seam).
- **M-6** protocolo material regex anchored on `BARRA` → non-BARRA materials silently dropped. **Fixed** (de-anchored).

Result: **9 real-data integration tests** added (`backend/tests/integration/test_pipeline_e2e.py`); 455 backend tests green.

## §frontend-review — Opus + Playwright visual+contract review (Phase 5)

Verdict: hot fix required (slice 2). Visuals + a11y judged **strong** (industrial dark QC
aesthetic, JetBrains Mono tabular numerics, status by icon+text = colorblind-safe, focus
trap, aria-sort). Bugs are functional/contract:

- **CRITICAL-1 (reassign)** `GuiaReassignDialog` sends `row_id` as `guia_id`; the real
  `GuiaDeRemision.guia_id` is never exposed in the row DTO. Root cause: a row is a SUM over
  many guías → a single id is ambiguous. **Fix:** expose `contributing_guias` in the row DTO;
  dialog targets a specific guía.
- **CRITICAL-2 (edit)** editable `summed_qty` cell sends `field:'fecha'` with a number →
  `date.fromisoformat("845")` → 422 / silent date corruption. `summed_qty` is computed.
  **Fix:** edit the underlying guía **line `cantidad`**; never alias quantity to fecha.
- **HIGH-3** `aria-rowcount` missing `:` binding. **HIGH-4** status column scrolls off at 768px.
  **HIGH-5** `SourcePages` uses raw `new Image()` bypassing the API base. **MED-6** dialog not
  localized. **MED-7** UNCLASSIFIED rows show a green ✓ confidence badge (conflicting signal).

## §QR — SUNAT GRE QR/barcode evaluation (validated on real data, 150+ guías decoded)

- **QR format** (compact, pipe-delimited, parse by position):
  `RUC_emisor | tipo(09=remitente,31=transportista) | serie | numero | doc_type_code | RUC_receptor`.
  Example: `20370146994|09|T009|0741770|6|20613231871`. **fecha and quantities are ABSENT**;
  field4=`6` is a doc-type code, not an amount.
- A second **URL-variant** QR appears: `…/descargaqr?hashqr=<base64>` (official-download link).
- **Decoder**: pyzbar (zbar) **and** zxing-cpp, union; render PyMuPDF ~150dpi (2×) grayscale.
  zbar needs the 2× upscale; zxing catches the URL variant + pages zbar misses. ~0.1s/page.
- **QR is on the FIRST page** of each multi-page guía block → must propagate id to continuation pages.
- **Decision**: `QrBarcodeExtractionAdapter` (LOCAL, Tier-0, behind new `IdentityExtractionPort`)
  yields deterministic `guia_id = serie-numero` (conf 1.0) → solves reassignment CRITICAL-1.
  It does **not** give quantities/fecha → OCR+vision stay load-bearing. `SunatGreFetchAdapter`
  (uses the hashqr URL → official structured doc) **breaks air-gap** → opt-in, off by default,
  **deferred** to a follow-on slice; its electronic date is cross-check only, never the grouping key.

## §rev-2 — design delta (A–F) — to be specced in slice 0

Canonical full version with code snippets + sequence diagrams: **engram #2662** and
`openspec/changes/material-reconciliation/design.md` (sections A–F).

- **A** Tiered deterministic-first extraction (QR identity → OCR quantities → vision date);
  `IdentityExtractionPort`, `SunatGreFetchPort` seam off by default.
- **B** Multi-page guía **block grouping**; first-page QR id propagates to continuation pages.
- **C** Authoritative `fecha` = handwritten reception date (vision).
- **D** Guía-granularity review: row exposes `contributing_guias`; reassign by `guia_id`;
  edit guía-line `cantidad`; `summed_qty` read-only. (Fixes frontend CRITICAL-1 & -2.)
- **E** `_derive_numero` returns `UNRESOLVED:<id>` on parse failure — never the Contents-ID.
- **F** Fix `test_reconciliation.py`/`test_models.py` fixtures using `"4252"` as a registro.

## §dates — reception-date authority

The business date is the **handwritten reception date + signature** on the scanned guía
(when material was physically received). Only **vision** can read it (it is not in the
electronic document). It MAY differ from the electronic GRE date. Therefore: vision is
irreplaceable even with QR/fetch; a SUNAT fetch's electronic date is at most a cross-check,
never the grouping key.

---

## §rev-3 — real-run validation findings & fixes (2026-06-02)

All seven items below surfaced during the first real-data run of rev-2 (subset PDF, pages
0–45, registros 230/231/232, Ollama qwen3.5:9b, real paddle + real QR libs). They were
**masked by injected mocks** (`HybridDocSource`) in the prior integration tests — classic
"green-with-mocks / broken-on-real" failure mode; see §recurring-mock-gap below.

### R1 — CRITICAL: hybrid classifier gap (classification-gap #2749)

**Root cause**: `PageClassifier` classifies by reading the PDF digital-text layer. Scanned
guía pages carry only the 4-line Autodesk Forma header in their text layer (~158 chars) and
NO "GUÍA DE REMISIÓN" string. Classification runs **before** OCR in the pipeline, so every
guía page is classified UNCLASSIFIED → QR decode, block grouping, OCR, and vision all never
execute on real input. Result: 24 rows GUIA_MISSING, summed_qty=0.

**Why it was masked**: `test_pipeline_e2e_rev2` and PR-8 used `HybridDocSource` to inject the
"GUIA DE REMISION" string into the digital layer, bypassing the real text. The "20/20 guías
QR-decoded" result in PR-8 was entirely artifact of the injected text.

**Fix (R1, PR-12)**: hybrid OR-gate classifier with a decode_identities pre-pass:

```
Condition A: QR decode succeeded → GUIA (deterministic, conf 1.0)
Condition B: body < 200 chars AND Forma-header signature AND image_dominant → GUIA (heuristic)
Condition C: digital/OCR title match → existing logic
```

`_stage_decode_identities` runs first, rendering each page once and storing a
`DecodeOutcome` map (identity, hashqr_url, decoded). `_stage_classify` consumes this cached
map — no second render. `assemble_blocks` also reuses the same map. Guard: Condition B must
not fire on pages with body >= 200 chars (EXT-S25).

### R2 — Vision bake-off + stamp-crop fix (vision-model-evaluation #2747, vision-crop-region #2760)

**Bake-off (real guía pages 6/10/15, air-gapped RTX 5070 Ti 16GB)**:

| Model | Full-page 200dpi | Stamp-crop |
|-------|-----------------|------------|
| gemma4:e4b | NINGUNA (fails) | 28-05 ✓, year wrong (2016) |
| gemma3:12b | hallucinates | 28-05 on p6 only, year wrong |
| **qwen3.5:9b** | **28-05 on all 3** | **28-05 on all 3** |

**Decision**: qwen3.5:9b is the only local model that reads the date from the full page.
Use it for air-gapped runs. gemma models require a cropped stamp region and still
underperform.

**Year reliability**: day-month is robust across all local models; YEAR is consistently wrong
(2016/2022/2024 instead of 2026). This is expected for 4B–12B air-gapped vision models.

**Stamp-crop region bug (R7, #2760)**: R2 configured the stamp crop as lower-right
(x0=0.5, y0=0.6). The CTR reception stamp on these guías is in the **upper-right** region.
This caused vision confidence=0.00 (no date read) for all guías after R6, even though SUNAT
quantities were flowing. Fix: config.yaml `stamp_crop` corrected to
`x0=0.55, y0=0.05, x1=1.0, y1=0.45` (empirically validated on pages 4,5,6,8,20,25,30).

**max_tokens bug (R7, #2760)**: `max_tokens=128` is exhausted by qwen3.5:9b's
`<think>…</think>` extended-thinking phase before any content is emitted. The empty string
is parsed as confidence=0.00, date=None. Fix: `max_tokens` raised to 4096; `_THINK_RE`
regex strips think-blocks before JSON parse. Three new unit tests cover the stripping logic.

**Real-run verification (run 7fd67700)**: 35 vision calls; 22/35 guías non-null fecha;
sample T009-0741771 fecha=2026-05-28 (day=28, month=05 confirmed ground truth).

### R3 — Bounded year inference (year-inference-rule #2748, year-normalize-gap #2753)

**Decision (domain rule)**: trust vision's day-month; INFER the year via bounded constraint:

```
delivery_GRE_date <= reception <= doc_date (PDF export date or current date)
```

Pick the year making `date(Y, DD, MM)` satisfy both bounds (unique in practice). Record
`year_inferred=True` on `VisionResult` and `GuiaContribution`; surface
`any_year_inferred` on `ReconciliationRow` so the UI can show an advisory indicator.

**Year-normalize gap (R3, #2753)**: the initial R2 implementation only ran inference when
`guia.fecha is None`. But local models often return a PARSEABLE date with the WRONG year
(2016/2022/2024 instead of 2026) — `fecha` is not None, so inference never fires. Fix: trust
ONLY the day-month from vision; ALWAYS reconstruct the year via bounds. Edge: if the vision
year is already within bounds and matches the most-recent candidate, leave it
(`year_inferred=False`). Lower bound source: SUNAT `fecha_entrega` (when enabled) or
OCR-printed GRE delivery date; upper bound: PDF doc date or run date.

**Real-run (R7)**: 13/35 guías had `year_inferred=True` (year reconstructed from bounds;
day-month from vision was correct).

### R4 — SUNAT descargaqr opt-in (sunat-fetch-spike #2750)

**Discovery**: scanned guía pages carry a SECOND, URL-variant QR (missed by initial
grayscale@2x decode). Multi-resolution COLOR decode (200dpi + 400dpi, pyzbar + zxing-cpp)
finds 2–3 QRs/page including this variant. The URL:

```
https://e-factura.sunat.gob.pe/v1/contribuyente/gre/comprobantes/descargaqr?hashqr=<HASH>
```

A plain GET (no OAuth — the `hashqr` IS the token) returns HTTP 200, Content-Type
application/pdf (~4KB). It is the official SUNAT GRE PDF with full digital text
(PyMuPDF get_text() — ~1544 chars), not a scan. Deterministic fields extracted:
identity (RUC, N°, destinatario), dates (fecha emisión, fecha entrega), and line items
(Bienes por transportar: cantidad, unidad, descripción, código SUNAT).

**SUNAT PDF format (token-per-line)**: the real PDF does NOT use slash-separated fields. The
parser (`_parse_line_items`) was rewritten (R6): anchor on "Bienes por transportar:", skip
column headers (sentinel = "SUNAT"), then group 6-token repeating value blocks
[desc, codigo, unidad, N°, indicator, cantidad]. Unit normalisation: TONELADAS→TN,
KILOGRAMOS→KG. Verified against live data: `T073-00680258` → cantidad=0.192, unidad=TN,
desc="BARRA A A615-G60 3/8\" X 9M".

**Fetch resilience**: timeout raised to 30s; exponential-backoff retry (`_MAX_RETRIES=3`,
`_BACKOFF_BASE=1.0`).

**Architecture**: `SunatDescargaqrAdapter` implements `SunatGreFetchPort`; wired in
`container.py` only when `config.sunat.enabled=True`. Config default: `enabled: false`.
SUNAT quantities override OCR quantities (`extraction_method="sunat_gre"`). Grouping `fecha`
ALWAYS from vision (handwritten stamp) — the SUNAT electronic date is a year-inference lower
bound only, never the grouping key.

**Air-gap invariant**: this adapter BREAKS the local-first air-gap. It must remain opt-in
(`sunat.enabled=False` default, documented in `config.yaml`). The integration tests verify
zero HTTP calls when disabled. SUNAT fetch is the only source for guía QUANTITIES when
paddle OCR is unavailable on the current env (see §R5-R7 below).

### R5 — PaddleOCR 3.6 API compat + graceful degradation (paddle-compat-gap #2755)

**API breaking changes (2.x → 3.6)**: `use_gpu` and `show_log` removed; `use_angle_cls=True`
replaced by `use_textline_orientation=True`; `ocr()` deprecated in favour of `predict()`;
3.x `OCRResult` format completely different from 2.x nested list:

```python
# 3.x: predict() → list[OCRResult], each dict-like:
item["rec_texts"]   # list[str]
item["rec_scores"]  # list[float]
# 2.x: ocr() → [[ [bbox, (text, conf)], ... ]]  ← wrong; causes silent KeyError
```

**Graceful degradation (R5.2)**: `extract_printed_table()` catches all exceptions, returns
`[]`, sets `_ocr_failed=True`. Pipeline continues; `PipelineResult.warnings` records the
degradation. Load failure sets permanent `_unavailable=True`; predict failure is transient
(`_ocr_failed=True`, not `_unavailable`).

### R6–R7 — Paddle runtime env (paddle-runtime-env #2757)

**Env bug**: `paddle 3.3.1` + `paddleocr 3.6.0` on CachyOS CPU (oneDNN/PIR build) raises
`NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support` at `predict()` time.
The adapter instantiates fine; the bug manifests only at inference. R5's graceful degradation
catches it: the run completes, OCR quantities are empty, `_ocr_failed=True` is flagged.

**Impact on this env**: air-gapped OCR quantity path yields nothing here. The two quantity
sources are paddle OCR (broken on this paddle build) and SUNAT descargaqr (functional, but
breaks air-gap). A fully air-gapped real run cannot produce guía quantities until the paddle
runtime is resolved.

**Mitigations to try**: `FLAGS_enable_pir_api=0`, `FLAGS_use_mkldnn=0`, or a GPU/different
paddlepaddle build. SUNAT fetch (quantities only) may be used as an explicit bounded air-gap
exception during validation.

### R8 — canonical material matching (MAT-001; own SDD change `r8-material-matching`)

Declared (Forma) and guía (SUNAT GRE) name the same rebar with different text → exact-string
grouping = zero MATCH. Fix: a **canonical key** `(familia, grado, diámetro, presentación, unidad)`
via deterministic regex (grade collapse `A615 G60`, diameter table, `9M` vs `DOB` never merged) +
local-LLM fallback (qwen3.5:9b) for the ambiguous tail, LLM-inferred rows always `requires_review`.
Domain stays pure (`MaterialInferencePort` Protocol + lazy Ollama adapter). **`fecha` removed from
the grouping key** — it split groups on vision-date noise. See `docs/MATERIAL-MATCHING.md`.

### R9 — reception-date authority + fecha-divergence review (own SDD change `r9-fecha-divergence-review`)

Declared reception date = **DIGITAL `Fecha:` on the Protocolo de Recepción** (deterministic parse
by `digital_text_extractor.py`, deterministic 20XX year (2000+YY), no vision call), per Registro N°. This is the ceiling
and the baseline for divergence checks. Guías carry **handwritten** reception dates (vision-read,
stamp region), compared **day-month** against the declared baseline; year via bounded inference.
A guía whose handwritten date diverges → non-blocking **WARNING** that flags `requires_review`
with the guía **page number** + **red highlight** (individual / per-registro group) for human
review + manual reassign. Never auto-corrected.

**Domain-correctness correction (2026-06-03)**: the prior premise recorded in #2709 stated that
the declared date was the *handwritten* `Fecha:` on the Protocolo, read via vision. The domain
authority confirmed with real PDF evidence that the Protocolo `Fecha:` is **DIGITAL/printed**
(from Forma), not handwritten. Handwritten reception dates exist **only on the guías de remisión**
(stamp+signature). The vision sub-stage `_stage_extract_declared_date` and its supporting fields
(`fecha_declarada_handwritten`, `fecha_declarada_confidence`, `fecha_declarada_year_inferred`) have
been removed. `Registro.fecha_authoritative` now returns `fecha_declarada` directly (the digital
parse). The divergence review logic is **unchanged** — only the declared-date *source* changed.
The `[floor, ceiling]` bracket (R9b/R9c), the day-month predicate, and the `requires_review`
flagging are all intact.

Engram: `architecture/reception-date-authority` (#2709).

---

## §recurring-mock-gap — recurring failure mode: green-with-mocks, broken-on-real

The following bugs ALL passed mocked unit/integration tests while failing on real input:

| Issue | What passed | What failed |
|-------|-------------|-------------|
| Classification gap (R1) | `HybridDocSource` injected "GUIA DE REMISION" | Real Forma-only digital layer → UNCLASSIFIED |
| Container identity-port wiring | Mock identity adapter fed directly | Real DI container didn't wire the port |
| Paddle API compat (R5) | Mocks assumed 2.x nested-list format | Real `predict()` returns 3.x dict-like |
| SUNAT parser (R6) | Fixture used slash-separated format | Real PDF uses token-per-line format |

**Lesson (reinforced)**: unit tests with injected fake document sources are not a substitute
for a real-data e2e gate. The check is: run the pipeline against the real PDF **without
bypassing any adapter via `HybridDocSource`** and assert on the reconciliation output
structure, not just on whether the pipeline terminates.

Minimum real-data e2e assertions that must pass before any slice is declared complete:
1. At least one `GuiaDeRemision.identity_source == "qr"` (QR decode reaching a real guía page).
2. No row with `status=GUIA_MISSING` and `guias==[]` when guía pages are physically present.
3. `PipelineResult.warnings` must be inspected — silent degradation is not success.

---

## §known-open — open issues after rev-3 (as of 2026-06-02)

| Issue | Severity | Notes |
|-------|----------|-------|
| MATCH not resolving on subset PDF | Medium | Declared extraction on the subset (pages 0–45) returns `material=None`. Pre-existing subset limitation. Full-PDF run needed to confirm MATCH resolves. Not a rev-3 regression. |
| ~13/35 guías null fecha | Medium | Stamp region varies by guía layout; some pages don't have the stamp in the upper-right quadrant. Residual, not a blocker — those guías show `requires_review=True`. |
| paddle OCR broken on this env | Medium | paddle 3.3.1 oneDNN/PIR CPU bug. Graceful degradation active. Quantities only available via SUNAT fetch (breaks air-gap) or a working paddle runtime / GPU. |
| SUNAT UNIDADES items skipped | Low | Domain only accepts KG/TN/RD/Rollo; GRE PDF items with `unidad=UNIDAD` (UND) are silently dropped. Out-of-scope for current domain rules. |
| max_tokens=4096 latency | Low | qwen3.5:9b with 4096 tokens is slower than the prior 128 budget. Acceptable for the current 35-guía PDF; monitor on full 493-page run. |

---

## §engram-mirror-rev3 — engram topic → versioned location map (rev-3 additions)

| Engram topic / ID | Versioned in |
|---|---|
| `sdd/material-reconciliation/classification-gap` (#2749) | `docs/DECISIONS.md` §rev-3 R1 |
| `sdd/material-reconciliation/vision-model-evaluation` (#2747) | `docs/DECISIONS.md` §rev-3 R2 |
| `sdd/material-reconciliation/vision-crop-region` (#2760) | `docs/DECISIONS.md` §rev-3 R2 |
| `sdd/material-reconciliation/year-inference-rule` (#2748) | `docs/DECISIONS.md` §rev-3 R3 |
| `sdd/material-reconciliation/year-normalize-gap` (#2753) | `docs/DECISIONS.md` §rev-3 R3 |
| `sdd/material-reconciliation/sunat-fetch-spike` (#2750) | `docs/DECISIONS.md` §rev-3 R4 |
| `sdd/material-reconciliation/paddle-compat-gap` (#2755) | `docs/DECISIONS.md` §rev-3 R5 |
| `sdd/material-reconciliation/paddle-runtime-env` (#2757) | `docs/DECISIONS.md` §rev-3 R6–R7 |
| `vision-quantity-accuracy-eval` (#2995 SA-5 / #3021 session) | `docs/EVAL-RESULTS.md` §1 |
| `ocr-engine-eval` (#3023) | `docs/EVAL-RESULTS.md` §2 |
| `ocr-off-vision-only-dropped-guia` (#3022) | `docs/DECISIONS.md` §2026-06-06 |
| `plan/ocr-deterministic-and-discarded-ui` (#3024) | `docs/HANDOFF.md` §SDD-plan + `docs/DECISIONS.md` §2026-06-06 |
| `sdd/guia-reprocess-bulk-viewer/archive-report` (#3019) | `docs/DECISIONS.md` §2026-06-06 |
| `pr46-reprocess-canonical-merge` (#3003) | `docs/DECISIONS.md` §2026-06-06 |

---

## §2026-06-06 — session decisions and findings

### bulk-viewer feature (SDD: guia-reprocess-bulk-viewer) — DELIVERED

Four UX features implemented via SDD (explore→propose→spec→design→tasks→apply→verify→archive)
across three stacked-to-main PRs merged to main:

- **PR-A #47 (backend)**: `POST /runs/{id}/registros/{registro}/reprocess` (202 async,
  `_run_reprocess_batch` bounded by shared `Semaphore(3)`); `GET .../reprocess-status`
  `{total,recovered,failed,done}` batch-status signal; operator-assign
  (`match_method="operator"`, `requires_review=True`); #42 fix (`_retry_batch`
  `mark_retry_attempted`).
- **PR-B #48 (frontend)**: tabs **Reconciliación | Pendientes por procesar** (count badge);
  per-Registro **"Procesar todos con IA"** (confirm dialog w/ call count, live progress,
  N/M summary).
- **PR-C #49 (frontend)**: drill-down guía serie-número + Páginas chips → PageSheetViewer;
  [Acciones] menu (Reasignar / Reprocesar / Corregir manual = operator picks a declared
  material of the registro + cantidad). Phase 9 bug fixed: bulk live-progress settled at
  2/22 (frontend time-heuristic) vs backend truth 17/24 → replaced with real
  `GET .../reprocess-status` done-signal; re-validated UI shows 18/6.

**SA-5 lesson**: poll-based progress MUST use a real backend completion signal, never a
timing heuristic. The elapsed-floor/observed-shrink heuristic passed unit tests (fake
timers) but failed on real latency. Only Playwright SA-5 exposed it.

Spec REV-R20–R26 merged into `openspec/specs/review/spec.md`. Change archived to
`openspec/changes/archive/2026-06-06-guia-reprocess-bulk-viewer/`.

### PR #46 — Reprocesar con IA + canonical-matching — MERGED

PR#3 (Reprocesar con IA) + dual-spec normalization + grade-tolerant recovery merged as single
size:exception PR (#46) to main. JD×2 + ctr-review (fresh opus APPROVE, 0 CRITICAL) + SA-5
all passed. Key canonical-matching fix: illegible-grade guard context-anchored (not
whole-string scan); `{2,3}` digit quantifier excludes diameter leads (`1"`, `1 3/8"`) — the
JD-caught data-corrupting regression the green suite masked.

### Vision quantity-accuracy eval (#40) — VERDICT

N=5, 5 curated guías, 65 line-runs each. kimi-k2.5:cloud 83.1% vs qwen3.5:397b-cloud 76.9%.
**Neither model reliable alone.** `requires_review=True` on every vision-recovered line is
mandatory.

**Failure modes are qualitatively complementary**: qwen errors are deterministic (same wrong
value all 5 runs; 0.608 for 0.008, 1.843 for 1.643) — retry cannot fix them. kimi errors are
stochastic empty-returns (~20–40% on some pages) — retry has non-zero success probability.
Where qwen errs systematically, kimi is correct and they disagree — basis for consensus #44.

**Decision**: kimi-k2.5:cloud selected (faster, no systematic misreads). Consensus (#44) is
the accuracy upgrade path. See `docs/EVAL-RESULTS.md` §1.

### OCR disabled in deploy — root cause of vision-only extraction

Investigation triggered by domain authority: reg227 guía pages contain only the Forma
header in their PDF text layer (`text_len≈159`). GRE table (printed quantities) is inside
a raster image. `RECONCILIATION__OCR__ENABLED=false` + paddle excluded from the runtime image
→ vision is the sole extractor for all 24 reg227 guías. This is the structural root cause of
#40. Recommendation: re-enable OCR with a deployable ONNX engine (SDD#1).

### OCR engine eval — VERDICT

RapidOCR PP-OCRv5-server (ONNX) + de-rotation reads printed GRE table quantities exactly
(3/3, 4/4, 4/4 on the three test guías), tying paddle in accuracy at ~3s/page with no
paddlepaddle dependency → deployable in the runtime image. The two real blockers are the
parser (not layout-aware: `_LINE_RE` one-line vs columnar TNE table → 0 lines) and
orientation (sideways scans need auto de-rotation). Both are implementation work, not engine
limitations. See `docs/EVAL-RESULTS.md` §2.

### Issue #50 — GUIA-classified page silently dropped (root cause confirmed)

Page 0152 is classified `kind: GUIA` (confidence 0.99, `FORMA_HEADER_HEURISTIC`) but its QR
did not decode. With OCR off → no identity → `assemble_blocks` rev-6 QR-evidence gate
(`pipeline.py:964-982`) silently drops it. Not in recovered/errored/unresolved. The operator
has no signal a guía is missing; declared totals look short with no explanation.

**Fix required**: a GUIA-classified page with no resolvable identity must surface as an
errored/unidentified entry (page number + thumbnail), never be silently dropped. Addressed
in SDD#1 backend root fix and/or SDD#2 UI.

### SDD plan approved (2026-06-06)

Two sequential SDDs:
1. **SDD#1 — Deterministic OCR backend**: RapidOCR ONNX PP-OCRv5-server + auto page-orientation
   + layout-aware box parser (TNE→TN, column association) + re-enable OCR path. No UI changes.
   Fix #50 backend root (surface identity-less GUIA pages as errored, not dropped).
2. **SDD#2 — [Descartadas para revisión] tab + recover-specific-page + history UI**: surface
   dropped GUIA pages; operator recovery via OCR (SDD#1 path) or IA fallback; later:
   processing history hamburger menu.

Execution: SDD interactive · hybrid artifact store · ask-on-risk delivery · stacked-to-main
chains. Frontend-visual apply → opus model.
