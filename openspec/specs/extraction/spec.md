# Spec — Extraction Domain
**Change**: material-reconciliation
**Domain**: extraction
**Phase**: spec
**Date**: 2026-05-31

---

## Purpose

The extraction domain is responsible for two distinct responsibilities:

1. **Page classification** — tagging each rendered page by its document title so downstream processes know which pages contribute to summation and which are excluded or flagged.
2. **Value extraction** — pulling declared-side material lists from digital text, printed material+quantity tables from guía pages via OCR, and handwritten reception dates via a provider-agnostic vision LLM.

All extraction engines are adapters behind ports. The domain never imports a vendor SDK.

---

## Requirements

### EXT-001 — Page classification by document title

The `PageClassifier` MUST classify each page by inspecting the document title text present on the page.
Classification MUST NOT use the supplier name (e.g., "Aceros Arequipa" or "Corporación Aceros Arequipa S.A.") as a classifier signal, because this name appears on multiple page types including non-guía sheets.

The following title-to-class mapping MUST be enforced:

| Document title (canonical) | Class | Included in sum |
|---|---|---|
| `GUÍA DE REMISIÓN` | `guia` | YES |
| `Sistema de Gestión de la Calidad - Planilla Resumen` | `planilla_resumen` | NO |
| `Sistema de Gestión de la Calidad - Listado de Barras` | `listado_barras` | NO |
| Detail page (registro notes) | `declared` | N/A (declared side) |
| Protocolo de Recepción | `protocolo` | N/A (declared side) |
| Carátula / índice | `cover_index` | NO |
| Photos / unrecognized | `photo` / `unclassified` | NO |

Any page that does not match a known title with sufficient confidence MUST be assigned class `unclassified` and a `classification_confidence` score.

### EXT-002 — Low-confidence classification surfaced in review

A page whose classification confidence falls below the configured threshold MUST be placed in the `unclassified` bucket and surfaced in the review UI.
Low-confidence pages MUST NOT be silently dropped.
Low-confidence pages MUST NOT contribute to material summation.

### EXT-003 — Declared-side extraction (digital text, no OCR)

The declared material list (registro number, fecha de entrega, list of materials with declared weights) MUST be extracted from embedded digital text of `declared` (detail page Notes) and `protocolo` pages.
OCR MUST NOT be applied to declared-side pages.
A vision LLM MUST NOT be invoked for declared-side content.
The declared material list is the trusted reference; no further validation of its content is performed at extraction time.

### EXT-004 — Printed table extraction (OCR)

Printed material+quantity tables on `guia`-class pages MUST be extracted using `PaddleOCR` via the `PrintedTableAdapter`.
Each extracted row MUST carry:
- material description (raw, pre-normalization)
- quantity (numeric value)
- unit (raw string, pre-normalization — MUST be preserved exactly as OCR reads it)
- OCR confidence score for the quantity field
- source page index

An extracted quantity with confidence < 0.85 MUST be flagged `requires_review: true`.
A MISMATCH result for the group MUST always flag the contributing rows for review regardless of their individual confidence scores.
(Previously: threshold was described as "below configured threshold" — now locked to 0.85.)

The `PrintedTableAdapter` MUST be implemented as an adapter behind `ExtractionPort`; the domain service MUST NOT reference PaddleOCR directly.

### EXT-005 — Handwritten date extraction (vision LLM)

The handwritten reception date on the stamp area of `guia`-class pages MUST be extracted using the `VisionLLMPort`.
The vision LLM MUST NOT be invoked on pages of any other class.
The `VisionLLMPort` MUST return a structured result: `{ date: str | null, confidence: float }`.
When `date` is `null` or `confidence` is below **0.85**, the date field MUST be flagged for human review.
(Previously: threshold was described as "below configured threshold" — now locked to 0.85.)

### EXT-006 — Provider-agnostic vision port

The `VisionLLMPort` interface MUST be defined in the domain layer with no dependency on any vendor SDK.
Concrete adapters (`AnthropicVisionAdapter`, `OpenAICompatibleVisionAdapter`) MUST be defined in the adapter layer.
The active vision provider MUST be selected via configuration (`provider: anthropic | openai | ollama`) with accompanying `model`, `base_url`, and `api_key` fields.

The following adapter–provider mapping MUST be supported:

| Config `provider` | Adapter | Notes |
|---|---|---|
| `anthropic` | `AnthropicVisionAdapter` | Uses Anthropic SDK; base64 image input; Message Batches API for batch calls |
| `openai` | `OpenAICompatibleVisionAdapter` | Uses OpenAI SDK; standard cloud endpoint |
| `ollama` | `OpenAICompatibleVisionAdapter` | Uses OpenAI SDK; `base_url` = `http://localhost:11434/v1`; data stays on-machine |

Switching the provider MUST require only a configuration change; no code change and no domain layer modification is permitted.

### EXT-007 — Vision batching capability flag

The `VisionLLMPort` contract MUST expose a `supports_batch: bool` capability flag.
When `supports_batch` is `true`, the adapter MAY submit guía page date-extraction requests as a batch (e.g., Anthropic Message Batches API, OpenAI Batch API).
When `supports_batch` is `false` (e.g., Ollama), the adapter MUST fall back to sequential per-page calls.
The domain MUST NOT hard-code batching behavior; it MUST delegate to the adapter via the capability flag.

### EXT-008 — Vision scope limitation

Vision LLM calls MUST be restricted to the handwritten date field crop on `guia`-class pages only.
The full page image MUST NOT be sent to the vision LLM unless the date field crop cannot be isolated.
Non-guía pages MUST NEVER be sent to the vision LLM.
A hard cap on the maximum number of vision calls per run MUST be configurable; exceeding the cap MUST abort the vision stage with a structured error (not a crash) and surface the affected pages in the review UI for manual date entry.

### EXT-009 — ExtractionPort unification

All extraction results (declared side, printed tables, handwritten dates) MUST be surfaced through a single `ExtractionPort` interface at the application layer.
Callers of `ExtractionPort` MUST NOT need to know which underlying mechanism (digital text, OCR, or vision) produced each value.

### EXT-010 — Per-field provenance

Every extracted value MUST carry provenance metadata:
- source page index
- extraction method (`digital_text` | `ocr` | `vision_llm`)
- confidence score (where applicable; `1.0` for digital text)

This provenance MUST be preserved through normalization and reconciliation so the review UI can display it.

---

## Acceptance Scenarios

### Scenario EXT-S01 — Correct classification of a guía page

**Given** a rendered page image whose visible title text is `GUÍA DE REMISIÓN`
**When** `PageClassifier` processes the page
**Then** the page is assigned class `guia`
**And** the page is eligible to contribute to material summation

### Scenario EXT-S02 — Planilla Resumen is excluded from summation

**Given** a rendered page whose visible title includes `Planilla Resumen`
**When** `PageClassifier` processes the page
**Then** the page is assigned class `planilla_resumen`
**And** the page is NOT included in any summation computation
**And** the page does NOT appear in the guía bucket for extraction

### Scenario EXT-S03 — Listado de Barras is excluded from summation

**Given** a rendered page whose visible title includes `Listado de Barras`
**When** `PageClassifier` processes the page
**Then** the page is assigned class `listado_barras`
**And** the page is NOT included in any summation computation

### Scenario EXT-S04 — Unclassified page surfaces in review

**Given** a rendered page whose title cannot be matched to any known document title with sufficient confidence
**When** `PageClassifier` processes the page
**Then** the page is assigned class `unclassified`
**And** the page's `classification_confidence` score is recorded
**And** the page appears in the review UI under the "unclassified pages" bucket
**And** the page contributes zero quantity to any summation

### Scenario EXT-S05 — Declared side extracted without OCR

**Given** a `declared` page with embedded digital text containing material names and declared weights
**When** `ExtractionPort` processes the page
**Then** material names and declared weights are extracted from digital text
**And** PaddleOCR is NOT invoked for this page
**And** the vision LLM is NOT invoked for this page
**And** each extracted value has `extraction_method = digital_text` and `confidence = 1.0`

### Scenario EXT-S06 — OCR extracts printed table from guía

**Given** a `guia`-class page after deskew
**When** `PrintedTableAdapter` processes the page via `ExtractionPort`
**Then** each table row is extracted as (material_description_raw, quantity, unit_raw, confidence, source_page)
**And** the raw unit string is preserved without modification
**And** rows with quantity confidence below threshold are flagged in provenance metadata

### Scenario EXT-S07 — Vision LLM extracts handwritten date

**Given** a `guia`-class page after deskew
**And** the configured `provider` is `anthropic`
**When** `VisionLLMPort` is invoked with the stamp area crop
**Then** the adapter calls the Anthropic API with a base64-encoded image
**And** the response is parsed into `{ date: "DD/MM/YYYY", confidence: 0.92 }`
**And** the extraction result carries `extraction_method = vision_llm`

### Scenario EXT-S08 — Low-confidence handwritten date flagged for review

**Given** a `guia`-class page
**When** the vision LLM returns `{ date: "15/03/2025", confidence: 0.45 }`
**And** the locked confidence threshold is 0.85
**Then** the date field is flagged `requires_review: true`
**And** the field surfaces in the review UI for the engineer to confirm or correct
**And** the partially-extracted date value IS shown as a suggestion (not discarded)

### Scenario EXT-S08b — Low-confidence OCR quantity flagged for review

**Given** a `guia`-class page
**When** `PrintedTableAdapter` extracts a quantity row with OCR confidence = 0.72
**And** the locked confidence threshold is 0.85
**Then** the row is flagged `requires_review: true`
**And** the row surfaces in the review UI alongside its source page thumbnail
**And** the row IS included in the group summation (with its flag visible)

### Scenario EXT-S09 — Vision LLM returns null date

**Given** a `guia`-class page
**When** the vision LLM returns `{ date: null, confidence: 0.0 }`
**Then** the date field is flagged `requires_review: true`
**And** the page surfaces in the review UI for manual date entry
**And** the page is NOT dropped from the pipeline

### Scenario EXT-S10 — Provider switch via config only

**Given** the config `provider` is changed from `anthropic` to `ollama`
**And** `base_url` is set to `http://localhost:11434/v1`
**When** the pipeline is invoked
**Then** `OpenAICompatibleVisionAdapter` is used for all vision calls
**And** no page images are sent to an external API
**And** no code change is required

### Scenario EXT-S11 — Vision call cap exceeded

**Given** the configured maximum vision calls per run is 50
**And** the pipeline has identified 51 guía pages requiring date extraction
**When** the vision stage reaches the 51st call
**Then** the vision stage aborts with a structured error (not a crash)
**And** all 50 successfully extracted dates are preserved
**And** the remaining pages are flagged `requires_review: true` and surface in the review UI for manual date entry

### Scenario EXT-S12 — Non-guía page never sent to vision LLM

**Given** a page classified as `planilla_resumen`
**When** the extraction pipeline runs
**Then** the vision LLM is NOT invoked for this page under any circumstances

---

## Delta — rev 2 (2026-06-01): QR identity tier + multi-page guía block grouping + authoritative fecha + UNRESOLVED fallback

> The requirements below ADD or MODIFY behaviour relative to EXT-001 through EXT-012 above.
> Each entry is marked [ADDED] or [MODIFIED: replaces <id>].

### EXT-011 — [ADDED] IdentityExtractionPort (QR / barcode tier)

A new domain port `IdentityExtractionPort` MUST be defined in the domain layer with no
dependency on any vendor SDK or imaging library.
`IdentityExtractionPort` MUST expose a single operation:
`decode_identity(image) → GuiaIdentity | None`

`GuiaIdentity` MUST carry the following fields:
- `guia_id: str` — deterministic identifier in the form `{serie}-{numero}`
  (e.g. `T009-0741770`)
- `ruc_emisor: str` — 11-digit RUC of the issuing party
- `ruc_receptor: str` — 11-digit RUC of the receiving party
- `tipo: str` — document type code (`"09"` = remitente, `"31"` = transportista)
- `hashqr_url: str | None` — URL-variant QR value (`…/descargaqr?hashqr=<base64>`),
  present only when a second URL-variant QR is decoded on the same page; `None` otherwise
- `confidence: float` — `1.0` when all gating conditions pass (see EXT-012);
  lower values MUST NOT be returned as a valid identity result (see EXT-012 for the
  failure contract)

The concrete implementation (`QrBarcodeExtractionAdapter`) MUST be placed in the adapter
layer and MUST NOT be imported by the domain or application layer directly.

### EXT-012 — [ADDED] QrBarcodeExtractionAdapter — local decode specification

`QrBarcodeExtractionAdapter` MUST implement `IdentityExtractionPort` using exclusively
local image processing — no network call is permitted.

**Rendering**: pages MUST be rendered via PyMuPDF at a nominal 150 DPI with a 2× upscale
applied as a grayscale conversion step, yielding an effective decode resolution of 300 DPI.

**Decoder union**: `pyzbar` and `zxing-cpp` MUST both be attempted; the union of their
results is used. Either decoder alone is insufficient because zbar requires the 2× upscale
and zxing-cpp catches the URL-variant QR and pages that pyzbar misses.

**QR payload parse**: the compact SUNAT GRE QR format is pipe-delimited, positional:
`RUC_emisor | tipo | serie | numero | doc_type_code | RUC_receptor`

Example: `20370146994|09|T009|0741770|6|20613231871`

Parsing MUST be **position-defensive** — fields are extracted by index, not by key name.
The parsed fields MUST be:
- index 0 → `ruc_emisor`
- index 1 → `tipo`
- index 2 → `serie`
- index 3 → `numero`
- index 4 → `doc_type_code` (not exposed in GuiaIdentity)
- index 5 → `ruc_receptor`

`guia_id` MUST be computed as `"{serie}-{numero}"` (dash-separated).
Date, quantities, and amounts are ABSENT from the QR payload and MUST NOT be inferred.

**Confidence gating**: `confidence = 1.0` MUST be returned if and only if ALL of the
following conditions hold:
1. `ruc_emisor` is exactly 11 digits (numeric)
2. `ruc_receptor` is exactly 11 digits (numeric)
3. `tipo ∈ {"09", "31"}`
4. `serie` is present and non-empty
5. `numero` is present and non-empty

If any condition fails, the decode attempt MUST return `None` (not a partial
`GuiaIdentity`); the failure MUST be logged for audit; the page MUST fall back to
OCR-derived identity (see EXT-014).

**URL-variant QR**: if a second QR code is decoded whose payload begins with a URL pattern
(`http://` or `https://` and contains `hashqr=`), the URL MUST be stored in
`GuiaIdentity.hashqr_url`; it MUST NOT be parsed as a data QR.

**Performance**: the combined decode step MUST complete in ≤ 200 ms per page on commodity
hardware (the QR step alone targets ~0.1 s/page).

Lazy import: `pyzbar` and `zxing-cpp` MUST be imported inside the adapter method, NOT at
module level, so that the test suite can run without these libraries installed.

### EXT-013 — [ADDED] Extraction precedence invariant (tiered)

The extraction pipeline MUST enforce the following strict precedence order for each guía
page:

| Tier | Responsibility | Authority |
|------|---------------|-----------|
| Tier 0 — QR local decode | `guia_id`, `ruc_emisor`, `ruc_receptor`, `tipo`, `hashqr_url` | `IdentityExtractionPort` (highest — overrides OCR for identity) |
| Tier 1 — OCR | Printed material rows: `description`, `cantidad`, `unidad` | `PrintedTableAdapter` (owns quantities) |
| Tier 2 — Vision LLM | Handwritten reception date (`fecha`) | `VisionLLMPort` (owns reception fecha) |
| Tier 3 — SUNAT GRE fetch | Electronic structured data | `SunatGreFetchPort` (SEAM ONLY — off by default; see EXT-016) |

These responsibilities MUST NOT cross tier boundaries:
- QR identity MUST override OCR-derived identity for the fields listed in Tier 0; it MUST NOT
  override OCR quantities or vision fecha.
- OCR MUST NOT be used to derive `guia_id` when a successful Tier-0 decode is available.
- Vision MUST NOT be used to derive quantities.
- Electronic GRE date (Tier 3) MUST NOT override vision-read handwritten fecha for grouping;
  it MAY be stored as a cross-check field only.

### EXT-014 — [ADDED] OCR-derived identity fallback

When `IdentityExtractionPort.decode_identity` returns `None` for a page (QR absent or
confidence gate fails), the pipeline MUST fall back to an OCR-derived identity for that
page.
OCR-derived identity MUST produce a provisional `guia_id` from the visible document header
text.
An OCR-derived `guia_id` MUST be flagged with `identity_source: "ocr_fallback"` in
provenance metadata; a QR-derived id carries `identity_source: "qr"`.
Pages with `identity_source: "ocr_fallback"` MUST be surfaced in the review UI so the
engineer can confirm or correct the identity before reassignment.

### EXT-015 — [ADDED] Multi-page guía block grouping

**[MODIFIED: replaces the implicit per-page guia_id assignment scheme in EXT-004 and
EXT-010]**

A **guía block** is defined as a maximal run of consecutive `guia`-class pages that:
- belongs to the same Autodesk Forma section range (determined by the contents map
  from `DocumentSourcePort.contents_offsets()`), AND
- has not been interrupted by the start of a new QR-decoded identity on any page
  within the run.

The pipeline MUST NOT assign a new guía block for each individual page; it MUST
accumulate consecutive `guia` pages into the same `GuiaDeRemision` object unless one of
the block-break conditions above is met.

**Block-break conditions** (any one triggers a new block):
1. The page is the first `guia` page of a run (run-start).
2. The page crosses a section boundary (section cross).
3. A QR code is successfully decoded on the page and its `guia_id` differs from the
   current block's `guia_id`.

**Identity propagation within a block**: the `guia_id`, `ruc_emisor`, `ruc_receptor`,
`tipo`, and `hashqr_url` decoded from the FIRST page of a block MUST be propagated to all
subsequent (QR-less continuation) pages within the same block.
Continuation pages' OCR-extracted material rows MUST be appended to the same
`GuiaDeRemision.lines` list as the first page's rows.

The `guia_id = "guia_page_{n}"` per-page naming scheme MUST be removed and MUST NOT appear
in any `GuiaDeRemision` produced by the pipeline after this delta is applied.

**`GuiaDeRemision` MUST carry** (updated model, supersedes the base definition in design §7):
- `guia_id: str` — `{serie}-{numero}` from QR, or OCR-fallback value
- `registro: str | None`
- `fecha: date | None` — handwritten reception date (vision); see EXT-017
- `lines: list[MaterialLine]`
- `ruc_emisor: str | None`
- `ruc_receptor: str | None`
- `tipo: str | None`
- `gre_hashqr_url: str | None`
- `identity_confidence: float` — 1.0 for QR, lower for OCR fallback
- `identity_source: Literal["qr", "ocr_fallback"]`
- `first_page: int` — page index of the first page of the block

### EXT-016 — [ADDED] SunatGreFetchPort — seam only, off by default

A domain port `SunatGreFetchPort` MUST be defined in the domain layer as a seam for
future opt-in SUNAT GRE integration:
`fetch(hashqr_url: str) → OfficialGre | None`

`SunatGreFetchPort` MUST be off by default. Any configuration that enables it MUST
require an explicit opt-in flag (`sunat_fetch.enabled: true`).
When disabled, the port MUST return `None` without any network call.

**Electronic date and quantity from SUNAT GRE are cross-check data only** — they MUST
NOT be used as the grouping `fecha`, MUST NOT override the handwritten reception date
for any reconciliation key, and MUST NOT override OCR quantities for reconciliation.

Enabling `SunatGreFetchPort` breaks the local-first / air-gap invariant. Any
documentation or review UI that exposes this option MUST label it explicitly as
"requires internet access" and "deferred / experimental."

### EXT-017 — [ADDED] Authoritative fecha = handwritten reception date

**[MODIFIED: makes EXT-005's intent explicit as a domain invariant at the extraction tier]**

The `fecha` field on `GuiaDeRemision` MUST be the handwritten reception date as read by
the `VisionLLMPort` from the stamp area of the first page of the guía block.
The electronic GRE date (if available from Tier 3) MUST NOT be assigned to `fecha`.

This requirement reinforces REC-C01 (reconciliation delta) and the grouping-key
invariant: the handwritten reception date is the authoritative business date because it
records when materials were physically received on site.

### EXT-018 — [ADDED] UNRESOLVED page → registro fallback

**[MODIFIED: restricts EXT-010 provenance; supersedes any implicit fallback that emits a
Contents/section ID as a registro numero]**

The function responsible for mapping a guía page to its Registro N° (whether implemented
as `build_page_to_registro_map`, `_derive_numero`, or an equivalent pipeline step) MUST
return `None` or a sentinel value of the form `"UNRESOLVED:<source_id>"` when the Registro
N° cannot be reliably derived from the section map or page content.

The function MUST NOT emit a Contents/section ID (e.g., `"4252"`, `"4251"`) as a Registro
N°. A section ID is never a valid Registro N° (domain invariant: Contents-ID ≠ Registro N°
— see §decisions/§QR).

A guía page whose `registro` is `None` or `"UNRESOLVED:*"` MUST be surfaced in the review
UI under an "unresolved guías" bucket so the engineer can assign it to the correct registro
manually.
The unresolved guía MUST NOT be silently dropped; it MUST appear in the reconciliation
audit trail with its unresolved status.

A test MUST be able to assert: for any `GuiaDeRemision` produced by the pipeline, if
`registro` contains only digits and has fewer than 3 digits, or matches the pattern of a
known section-ID range, an error MUST be raised (this makes the §F fixture fix verifiable
without encoding it as a spec requirement).

---

## Acceptance Scenarios — Delta rev 2

### Scenario EXT-S13 — [ADDED] QR identity decoded with confidence 1.0

**Given** a `guia`-class page whose QR payload is `20370146994|09|T009|0741770|6|20613231871`
**When** `QrBarcodeExtractionAdapter.decode_identity` processes the page
**Then** a `GuiaIdentity` is returned with:
  - `guia_id = "T009-0741770"`
  - `ruc_emisor = "20370146994"`
  - `ruc_receptor = "20613231871"`
  - `tipo = "09"`
  - `confidence = 1.0`
**And** `identity_source` on the resulting `GuiaDeRemision` is `"qr"`

### Scenario EXT-S14 — [ADDED] QR confidence gate rejects malformed RUC

**Given** a `guia`-class page whose QR payload contains a 10-digit RUC (`2037014699|09|T009|0741770|6|20613231871`)
**When** `QrBarcodeExtractionAdapter.decode_identity` processes the page
**Then** `None` is returned (confidence gate fails on 11-digit check)
**And** the failure is logged in the audit trail
**And** the page proceeds to OCR-derived identity fallback
**And** `identity_source` on the resulting `GuiaDeRemision` is `"ocr_fallback"`

### Scenario EXT-S15 — [ADDED] Multi-page guía block propagates QR identity

**Given** pages 47, 48, and 49 are all classified `guia`
**And** page 47 has a successfully decoded QR with `guia_id = "T009-0741770"`
**And** pages 48 and 49 have no QR code
**And** all three pages are within the same section range
**When** the block grouping stage processes these pages
**Then** a single `GuiaDeRemision` is created for pages 47–49
**And** `guia_id = "T009-0741770"` is set on the block
**And** `ruc_emisor`, `ruc_receptor`, and `tipo` from page 47 are propagated to the block
**And** OCR rows from pages 47, 48, and 49 are all present in `GuiaDeRemision.lines`
**And** `first_page = 47`

### Scenario EXT-S16 — [ADDED] New QR on continuation page starts a new block

**Given** pages 50, 51, 52 are all `guia` pages within the same section range
**And** page 50 decodes `guia_id = "T009-0741770"`
**And** page 51 decodes a DIFFERENT `guia_id = "T009-0741771"`
**When** the block grouping stage processes these pages
**Then** TWO separate `GuiaDeRemision` objects are created:
  - Block A: pages 50 only, `guia_id = "T009-0741770"`
  - Block B: pages 51–52, `guia_id = "T009-0741771"` (page 52 has no QR, propagated from 51)

### Scenario EXT-S17 — [ADDED] Section boundary starts a new guía block

**Given** page 60 is classified `guia` and belongs to section range for registro 232
**And** page 61 is classified `guia` and belongs to section range for registro 233 (different section)
**And** neither page has a QR code
**When** the block grouping stage processes these pages
**Then** TWO separate `GuiaDeRemision` objects are created (one per section)
**And** `guia_id = "guia_page_{n}"` DOES NOT appear in either object

### Scenario EXT-S18 — [ADDED] guia_page_{n} naming absent from output

**Given** the pipeline processes 10 `guia`-class pages
**When** block grouping completes
**Then** no `GuiaDeRemision.guia_id` value matches the pattern `guia_page_\d+`

### Scenario EXT-S19 — [ADDED] UNRESOLVED returned on derivation failure

**Given** a `guia` page whose section map does not yield a valid Registro N°
**When** `build_page_to_registro_map` (or `_derive_numero`) processes the page
**Then** the returned registro is `None` or matches `"UNRESOLVED:*"`
**And** the value is NOT a plain integer string that coincides with a section-ID (e.g. `"4252"`)
**And** the guía surfaces in the review UI under the "unresolved guías" bucket
**And** the guía is present in the reconciliation audit trail with status `unresolved`

### Scenario EXT-S20 — [ADDED] Section ID never emitted as Registro N°

**Given** a page whose section/Contents map entry is ID `4252`
**And** the actual Registro N° for that section is `232`
**When** the pipeline derives the registro for that page
**Then** `GuiaDeRemision.registro` is `"232"` (or `None`/`"UNRESOLVED:4252"` on failure)
**And** `GuiaDeRemision.registro` is NEVER `"4252"`

### Scenario EXT-S21 — [ADDED] Vision fecha takes precedence over electronic date

**Given** a guía block where:
  - Vision LLM returns handwritten fecha `2025-03-15` (confidence 0.91)
  - SUNAT GRE fetch (if enabled) returns electronic date `2025-03-18`
**When** the pipeline assigns `fecha` to `GuiaDeRemision`
**Then** `GuiaDeRemision.fecha = 2025-03-15` (handwritten)
**And** the electronic date is stored as a cross-check field only (never as the grouping key)
**And** `identity_source` for the fecha is `"vision_llm"`

### Scenario EXT-S22 — [ADDED] SunatGreFetchPort disabled by default

**Given** the pipeline configuration does NOT include `sunat_fetch.enabled: true`
**When** the pipeline processes any guía page
**Then** `SunatGreFetchPort` is never invoked
**And** no network call is made for SUNAT GRE data
**And** the absence of SUNAT data does NOT affect grouping or reconciliation

---

## Delta — rev 3 (2026-06-02): real-pipeline findings — hybrid classifier, vision input resolution, year inference, first_page sentinel

> The requirements below ADD or MODIFY behaviour relative to EXT-001 through EXT-018 above.
> Findings originate from a real pipeline run: provider=ollama qwen3.5:9b vision + real PaddleOCR
> + real pyzbar/zxing-cpp, over the raw 493-page PDF (registros 230/231/232, pages 0–45).
> Prior injected-fake e2e tests (HybridDocSource text injection) masked all four gaps.
> Each entry is marked [ADDED] or [MODIFIED: replaces <id>].

### EXT-019 — [MODIFIED: replaces EXT-001] Hybrid page classifier for scanned guías

**[MODIFIED: EXT-001 is insufficient for image-only pages — digital-title-only classification
gates out all scanned guías on real input because their digital text layer contains only the
4-line Autodesk Forma header with no "GUIA DE REMISION" string.]**

`PageClassifier` MUST classify a page as `guia` if ANY of the following conditions holds:

**Condition A — QR-decoded identity (deterministic tier)**:
The page bears a decodable SUNAT GRE QR that passes the `IdentityExtractionPort` confidence
gate (all five gating conditions in EXT-012). A page satisfying Condition A MUST be
classified as `guia` regardless of its digital text content.

**Condition B — Forma-header-only heuristic (scanned-page fallback)**:
The page's digital text matches ALL of the following simultaneously:
1. Total character count is < 200.
2. The text matches the known Autodesk Forma header pattern (contains a recognisable Forma
   header signature such as "Autodesk Forma" or an equivalent project-document header token
   — exact pattern is an adapter concern, not a domain invariant).
3. The page is image-dominant: a raster image covers the majority of the page area (as
   determined by the `DocumentSourcePort` or an equivalent rendering heuristic).

A page satisfying Condition B MUST be classified as `guia` even when Condition A fails
(e.g., QR present but undecodable, or QR absent entirely).

**Condition C — Digital title match (original EXT-001 logic)**:
The digital text layer contains a title string matching `GUÍA DE REMISIÓN` with sufficient
confidence (unchanged from EXT-001).

The classifier MUST evaluate Conditions A, B, and C in any order; any one is sufficient.
Condition A carries the highest epistemic weight: if a QR passes confidence gating, the
classification is authoritative. Condition B is a heuristic fallback; it MUST NOT override
a non-guía classification on pages that have substantial digital text (>= 200 chars).

Classification MUST NOT rely solely on digital title text for image-only pages. The Condition
A check (QR decode) MUST reuse the `IdentityExtractionPort` already invoked in the QR
identity tier (EXT-011); a second independent QR scan MUST NOT be introduced.

The supplier name (e.g., "Aceros Arequipa") MUST NOT be used as a classification signal;
this constraint from EXT-001 is preserved unchanged.

Non-guía page types (declared, protocolo, planilla_resumen, listado_barras, cover_index,
photo, unclassified) MUST NOT be classified as `guia` solely on the basis of Condition B.
In particular, declared/protocolo pages have > 200 chars of digital text and will not satisfy
Condition B.

**Assembly-side QR-evidence invariant (guia-classification-keystone rev-6).** Classification
(Conditions A/B/C, page-local) is distinct from block assembly. In `_stage_assemble_blocks`,
a single INVARIANT QR-evidence gate MUST be applied to EVERY page BEFORE the start-new-block
logic:

```python
is_ocr_fallback_material = (
    identity is None and len(raw.lines) > 0 and page_hashqr_url is not None
)
has_guia_evidence = identity is not None or is_ocr_fallback_material
if not has_guia_evidence:
    continue  # dropped uniformly — never opens or extends a block
```

A page MUST open or extend a guía block ONLY when it carries positive QR evidence — a decoded
compact identity QR (`identity is not None`) or, when the compact QR fails, the URL `hashqr=`
QR (`page_hashqr_url is not None`) together with OCR material lines. A page with NO QR evidence
(a photo, or a no-QR sheet whose OCR emitted a spurious non-materials table) MUST be dropped
UNIFORMLY at every position — run-start, section boundary, and continuation — via this single
gate. This prevents a no-evidence page from opening a phantom `ocr_fallback` block with unflagged
bogus material in the registro total. An `ocr_fallback` block (QR evidence present, compact QR
failed) carries `requires_review = True` on its lines at any position (including run-start and
section-boundary, rev-6 fix). The else-branch absorb gate simplifies to `absorb = identity is not None`.

**Real-data validation (rev-6)**: Run `67e4e7a1` classified 165 pages: 83 `QR_IDENTITY` (real
guías, 140/140 material lines) and 68 `FORMA_HEADER_HEURISTIC` (all 68 photos/annexes, 0 material
lines). Zero phantom blocks opened at run-start or section-boundary. Domain authority confirms every
SUNAT guía page carries a QR; non-QR pages inside a registro are photos/annexes.

### EXT-020 — [MODIFIED: replaces EXT-005 / EXT-008] Vision input MUST be adequate for handwritten date legibility

**[MODIFIED: EXT-005 requires vision-LLM invocation with the stamp area; EXT-008 states the
full page MUST NOT be sent unless the crop cannot be isolated. Real-pipeline finding confirms
that full-page-200dpi input causes local vision models (gemma variants) to return null or
hallucinate; adequate resolution is therefore a first-class requirement, not an advisory.]**

The `VisionLLMPort` MUST receive input that is adequate for a local vision model to read the
handwritten reception date from the guía stamp area. "Adequate" MUST satisfy at least one
of the following:

**Option A — Cropped stamp region**: A crop of the stamp/reception area of the guía page,
at any DPI sufficient for the model to read handwritten DD/MM characters. The crop region
MAY be determined by a configurable heuristic (e.g., lower-right quadrant) or by a
configurable fixed bounding box; the exact crop strategy is an adapter concern.

**Option B — Higher-DPI full page**: The full page rendered at a DPI sufficient for
handwritten date legibility (empirically >= 300 DPI for local 9B-class models). "Sufficient"
is adapter-specific and MUST be validated against real data.

The existing constraint from EXT-008 — that vision MUST NOT be invoked on non-guía pages —
is unchanged.

The adapter (concrete implementation behind `VisionLLMPort`) MUST document which option it
uses and the rationale. Switching between options MUST require only an adapter change; the
domain port contract `read_handwritten_date(image) → VisionResult` is unchanged.

Full-page-200dpi-only input is insufficient and MUST NOT be the sole available mode for
providers where it produces null/hallucinated output on real data.

### EXT-021 — [ADDED] Bounded year inference for handwritten reception date

**[Context: local vision models (4B–12B class) reliably read DAY-MONTH from the handwritten
reception stamp but consistently produce the wrong YEAR. The year MUST therefore be
reconstructed from domain bounds when vision confidence for the year component is low.]**

When the vision result for the handwritten reception date provides DAY-MONTH with sufficient
confidence but the YEAR component is absent, garbled, or low-confidence, the pipeline MUST
reconstruct the year via bounded inference:

**Bounds**:
- Lower bound: `delivery_GRE_date` — the printed electronic GRE delivery date on the guía
  (read from OCR of the printed table/header, NOT from the QR payload which does not carry a
  date). When `delivery_GRE_date` is unavailable (OCR failed or SUNAT fetch is off), the
  lower bound MAY be omitted (year is inferred from upper bound only, accepting higher
  uncertainty).
- Upper bound: `reference_date` — the PDF document/export date if available, otherwise the
  pipeline run date.

**Inference rule**: compute `date(Y, MM, DD)` for each candidate year Y satisfying
`delivery_GRE_date <= date(Y, MM, DD) <= reference_date`. When exactly one valid Y exists,
use it. When multiple valid years exist, use the most recent. When no valid Y exists (DD/MM
is physically impossible within bounds), the date MUST be flagged `requires_review: true`
and surfaced for manual correction; inference MUST NOT produce a date known to violate the
bounds.

**Provenance**: the `VisionResult` / date extraction output MUST carry `year_inferred: bool`.
When the year was reconstructed by this rule, `year_inferred = true` MUST be recorded.
`year_inferred` MUST be propagated through reconciliation to the review UI and audit trail.

**Audit-gate integrity**: a `year_inferred = true` reception date MUST be surfaced in the
review UI as a yellow/advisory flag (distinct from the red `requires_review` flag used for
confidence failures) so the engineer can inspect and confirm the inferred date.
The OCR validation gate remains honest: an inferred year is visually distinguishable from a
directly-read year; the system MUST NOT present an inferred date as a fully confident read.

The lower bound delivery date MUST be read from OCR of printed content; the compact SUNAT
GRE QR payload (format `RUC|tipo|serie|numero|code|RUC`) does NOT carry a date and MUST NOT
be used as a source for the lower bound.

### EXT-022 — [MODIFIED: replaces EXT-015 field definition] first_page MUST use None sentinel, not 0

**[MODIFIED: EXT-015 defines `GuiaDeRemision.first_page: int — page index of the first page
of the block`. The default value 0 is ambiguous: a guía genuinely starting at page index 0
is indistinguishable from an uninitialised / absent first_page. This causes the
`UnresolvedGuiaResponse.first_page` fallback logic to mishandle page-0 guías.]**

`GuiaDeRemision.first_page` MUST be typed as `int | None` with a default of `None` (not 0).

The value MUST be set to the concrete page index (>= 0 is a valid value) when the first page
of the block is known, and `None` exclusively when the first page is genuinely unknown (e.g.,
a guía block constructed without any page reference).

Any fallback or API serialisation logic that reads `first_page` MUST treat `None` as "absent"
and `0` as "page index zero" — these two states MUST be distinguishable.

The pattern `g.first_page if g.first_page != 0 else source_pages[0]` is PROHIBITED because
it incorrectly overrides a valid page-0 assignment. The correct pattern is
`g.first_page if g.first_page is not None else source_pages[0]`.

### EXT-023 — [ADDED] SUNAT descargaqr opt-in deterministic guía-data source

**[Context: the SUNAT descargaqr spike (engram `sdd/material-reconciliation/sunat-fetch-spike`)
CONFIRMED that the URL-variant QR (`…/descargaqr?hashqr=<base64>`) resolves to the official SUNAT
GRE representation PDF via a plain no-auth GET. This promotes `SunatGreFetchPort` from the
future-seam stance of EXT-016 to a first-class OPT-IN deterministic source. The spec phase
predated the confirmed spike; this requirement records the confirmed behaviour.]**

`SunatGreFetchPort` MUST support an opt-in concrete adapter that, when enabled, GETs the
QR-derived `hashqr_url` (`…/descargaqr?hashqr=<base64>`) with NO OAuth and NO Clave SOL (the
`hashqr` is the token), receives the official SUNAT GRE representation document
(`Content-Type: application/pdf`, full digital text — not a scan), and parses it with PyMuPDF
`get_text()` to extract deterministically:
- line items: `cantidad`, `unidad`, `descripción`, `código producto SUNAT`
- `fecha de emisión` and `fecha de entrega`
- emisor and receptor RUCs

The parsed result MUST be returned as a pure domain `OfficialGre` value object.

**Precedence (extends EXT-013)**:
- When SUNAT data is available for a guía block, SUNAT line-item quantities and units MUST take
  precedence over OCR-extracted quantities for that same block. OCR quantities (Tier 1) become the
  FALLBACK, used only when the fetch is disabled, unavailable, fails, or the page has no
  `hashqr_url`.
- The SUNAT `fecha de entrega` MUST be usable as the deterministic lower bound for bounded year
  inference (EXT-021).
- SUNAT electronic dates (emisión/entrega) MUST NOT be used as the grouping `fecha`. The grouping
  `fecha` remains the handwritten reception date read by `VisionLLMPort` (EXT-017 / REC-C01) —
  this invariant is absolute and unaffected by enabling SUNAT.

**Air-gap default (local-first preserved)**: `SunatGreFetchPort` MUST remain OFF by default behind
an explicit configuration flag (`sunat.enabled: false` in the committed config). Enabling it is the
ONLY network egress in the system and MUST be documented as the explicit air-gap exception. When
disabled, no network call is made and OCR quantities are authoritative.

**Failure handling**: a fetch failure (timeout, non-200, non-PDF, parse error) MUST degrade
gracefully — the block retains its OCR-extracted quantities and the run MUST NOT abort. The
downloaded GRE PDF MUST be cached in the run output directory for audit and idempotency; a cached
copy MUST be reused on re-run within the same run directory rather than re-fetched.

**Hexagonal**: `OfficialGre` MUST be a pure domain value object (no IO/SDK). The concrete adapter
MUST lazy-import its HTTP client inside the fetch method so the test suite runs without it. The
pipeline/application layer MUST depend only on `SunatGreFetchPort`, never on the concrete adapter.

---

## Acceptance Scenarios — Delta rev 3

### Scenario EXT-S23 — [ADDED] Scanned guía with QR classified via Condition A

**Given** a page whose digital text layer contains only the Autodesk Forma 4-line header
  (158 chars, no "GUIA DE REMISION" string)
**And** the page bears a SUNAT GRE QR that decodes to a passing `GuiaIdentity`
  (all five confidence-gate conditions satisfied)
**When** `PageClassifier` processes the page
**Then** the page is classified as `guia` (Condition A)
**And** the classification is recorded as authoritative (not heuristic)
**And** the page is eligible for QR identity, OCR quantity, and vision date extraction

### Scenario EXT-S24 — [ADDED] Scanned guía with unreadable QR classified via Condition B

**Given** a page whose digital text layer contains only the Autodesk Forma 4-line header
  (< 200 chars, matching the Forma header pattern)
**And** the page is image-dominant (raster covers majority of the page area)
**And** the QR on the page fails to decode (or is absent)
**When** `PageClassifier` processes the page
**Then** the page is classified as `guia` (Condition B heuristic)
**And** `identity_source` on the resulting `GuiaDeRemision` is `"ocr_fallback"` (no QR)
**And** the page is eligible for OCR quantity and vision date extraction

### Scenario EXT-S25 — [ADDED] Genuine declared page NOT misclassified as guía

**Given** a declared/detail page whose digital text layer contains 1200 chars of embedded
  material text (registro notes, declared weights — far above the 200-char threshold)
**When** `PageClassifier` processes the page
**Then** the page is classified as `declared` (not `guia`)
**And** Condition B (Forma-header-only heuristic) does NOT fire because char count >= 200
**And** the page is processed by declared-side extraction only (no OCR quantities, no vision)

### Scenario EXT-S26 — [ADDED] Vision receives adequate stamp-region input

**Given** a `guia`-class page after deskew
**And** the configured vision adapter uses Option A (stamp-region crop)
**When** `VisionLLMPort.read_handwritten_date` is invoked
**Then** the adapter sends a cropped stamp-area image (not the full-page-200dpi render)
**And** the returned `VisionResult.date` is non-null and contains a parseable DD/MM value

### Scenario EXT-S27 — [ADDED] Year inferred from bounds; year_inferred=true recorded

**Given** vision returns DD=28, MM=05 with high day-month confidence, but year component
  is absent or low-confidence
**And** the printed GRE delivery date (OCR-read) is 2026-05-20
**And** the pipeline reference date is 2026-06-01
**When** the year inference rule is applied
**Then** candidate year 2026 satisfies: `2026-05-20 <= 2026-05-28 <= 2026-06-01`
**And** the reception date is set to `2026-05-28`
**And** `year_inferred = true` is recorded in the extraction provenance
**And** the review UI surfaces an advisory flag for this date field

### Scenario EXT-S28 — [ADDED] Year inference when no lower bound available

**Given** vision returns DD=15, MM=03 with high day-month confidence, year absent
**And** `delivery_GRE_date` is unavailable (OCR failed on printed date)
**And** the pipeline reference date (upper bound) is 2026-06-01
**When** the year inference rule is applied with upper-bound-only
**Then** the most recent year Y such that `date(Y, 03, 15) <= 2026-06-01` is chosen (2026)
**And** `year_inferred = true` is recorded

### Scenario EXT-S29 — [ADDED] first_page=0 preserved correctly; None sentinel used for absent

**Given** a guía block whose first page is genuinely page index 0 (first page of the PDF)
**When** the block grouping stage assigns `first_page = 0` to the `GuiaDeRemision`
**Then** `GuiaDeRemision.first_page = 0` (not None)
**And** any fallback logic reading `first_page` treats `0` as a valid concrete page index
**And** the fallback is triggered ONLY when `first_page is None`, not when `first_page == 0`

### Scenario EXT-S30 — [ADDED] SUNAT fetch overrides OCR quantities when enabled

**Given** `sunat.enabled = true`
**And** a guía block whose first page yielded a `hashqr_url`
**When** `SunatGreFetchPort.fetch` returns an `OfficialGre` with line items
  (e.g. cantidad 0.192, unidad TONELADAS, descripción "BARRA A A615-G60 3/8\" X 9M")
**Then** the block's quantities and units are sourced from the SUNAT line items (not OCR)
**And** the SUNAT `fecha de entrega` is recorded as the year-inference lower bound (EXT-021)
**And** the grouping `fecha` is STILL the handwritten reception date (vision), NOT the SUNAT date

### Scenario EXT-S31 — [ADDED] SUNAT disabled by default preserves the air-gap

**Given** the committed configuration (`sunat.enabled = false`)
**When** the pipeline processes any guía block
**Then** `SunatGreFetchPort` is never invoked and no network call is made
**And** OCR-extracted quantities are authoritative for every block

### Scenario EXT-S32 — [ADDED] SUNAT fetch failure degrades gracefully to OCR

**Given** `sunat.enabled = true`
**And** the descargaqr GET times out, returns a non-200, or returns non-PDF content
**When** the pipeline processes the affected guía block
**Then** the block retains its OCR-extracted quantities
**And** the run does NOT abort
**And** the year-inference lower bound falls back to the OCR-printed GRE date (or is omitted)

---

## Superseded note (rev-3): SUNAT fetch promoted from deferred seam to opt-in tier

> The rev-2 "Deferred / seam item" stance for `SunatGreFetchPort` is SUPERSEDED by EXT-023 above.
> The descargaqr spike CONFIRMED the no-auth endpoint returns the official GRE PDF with deterministic
> line items and dates (engram `sdd/material-reconciliation/sunat-fetch-spike`). `SunatGreFetchPort`
> is now a first-class OPT-IN deterministic source (EXT-023), still OFF by default and still the only
> network egress (air-gap exception). It is part of the rev-3 design; implementation is sequenced as
> rev-3 slice 3 (behind the off-by-default flag).

---

## Delta — vision-config-defaults (2026-06-03): disable_thinking default

> The requirement below ADDS new behaviour relative to EXT-001 through EXT-023 above.
> Source change: `vision-config-defaults` (merged via PR #3).
> Gate: strict-TDD — 118 config + vision tests passing.
> Marked [ADDED].

### EXT-024 — [ADDED] Vision model thinking phase MUST be disabled by default

`VisionConfig.disable_thinking` MUST default to `True`. When `True`, the system MUST prepend
a `/no_think` instruction (or provider-equivalent) to vision requests so that the model
skips the chain-of-thought `<think>` phase before generating the date-extraction response.

The setting MUST be overridable per-environment via the environment variable
`RECONCILIATION__VISION__DISABLE_THINKING` (Pydantic-settings env_prefix
`RECONCILIATION__`, nested delimiter `__`). Setting it to `false` restores thinking-enabled
behaviour.

**Rationale**: for structured OCR/date-extraction tasks (reading DD/MM from a stamp), the
`<think>` phase adds ~12 s per call on qwen3.5:397b-class models with no measurable accuracy
benefit. The default fast path reduces median vision latency without a quality regression.

The `disable_thinking` flag MUST be applied to the guía date-extraction calls
(`VisionLLMPort.read_handwritten_date` for guía pages). It MUST NOT be applied to non-vision
pipeline stages. (The Protocolo declared date is parsed deterministically from the digital
text layer — no vision call — so `disable_thinking` does not apply to it.)

#### Acceptance Scenarios

**Scenario EXT-S33 — disable_thinking=True by default: /no_think prefix applied**

Given a default `VisionConfig` (no env override)
When `VisionConfig.disable_thinking` is read
Then `disable_thinking = True`
And the vision adapter prepends `/no_think` (or provider-equivalent) to the prompt for
  every vision call
And the adapter does NOT send the chain-of-thought `<think>` phase

**Scenario EXT-S34 — disable_thinking overridable via env var**

Given `RECONCILIATION__VISION__DISABLE_THINKING=false` is set in the environment
When `VisionConfig` is instantiated
Then `disable_thinking = False`
And the vision adapter omits the `/no_think` prefix (thinking enabled)

**Scenario EXT-S35 — disable_thinking applies to the guía vision date path**

Given `disable_thinking = True` (default)
And a run that processes guía pages (handwritten date) via `VisionLLMPort.read_handwritten_date`
When the guía vision call path executes
Then the call includes the `/no_think` prefix
And OCR/text-extraction stages (PaddleOCR, digital text — including the deterministic Protocolo
  declared-date parse) are unaffected

---

## Delta — vision-off-mode (2026-06-03): SUNAT-authoritative date mode

> The requirement below ADDS new behaviour relative to EXT-001 through EXT-024 above.
> Source change: `vision-off-sunat-date-mode`.
> Gate: strict-TDD — vision-off config + NullVisionAdapter + container wiring + R9b-floor pin tests passing.
> Marked [ADDED].

### EXT-025 — [ADDED] Vision MAY be fully disabled for a deterministic SUNAT-authoritative date mode

`VisionConfig.enabled` MUST default to `True`. When set to `False` (env
`RECONCILIATION__VISION__ENABLED=false`, Pydantic-settings env_prefix `RECONCILIATION__`,
nested delimiter `__`), the composition root (`build_pipeline`) MUST inject a
`NullVisionAdapter` (Null Object pattern implementing `VisionLLMPort`) INSTEAD of the
provider-agnostic `build_vision_adapter`, so that **ZERO** LLM/vision calls are made — no SDK
import, no client initialised. This mirrors the existing `ocr.enabled=false → NullOcrExtractor`
escape hatch.

In this mode every guía's vision-read date is `None`; the EXISTING R9b Rule-2 delivery floor
(`apply_delivery_floor(None, fecha_entrega)` in `_stage_normalize_dates`) resolves each guía's
reception date to its SUNAT `fecha_entrega` and sets `delivery_floor_applied=True`. No new date
logic is introduced. The declared reception date is UNAFFECTED (already the deterministic
digital Protocolo parse — no vision). The result is deterministic (enables a real ETA) and
air-gap-friendly when a SUNAT cache exists.

**Caveat (accepted)**: `fecha_entrega` is the SUNAT delivery date = a LOWER bound used AS the
reception date (reception ≥ delivery). This is an approximation; it is safe because a guía whose
date diverges from the Protocolo is still flagged `requires_review`, never auto-corrected.

**Fail-fast invariant (make-invalid-states-unrepresentable)**: `AppConfig` MUST reject
construction when `vision.enabled=false` AND `sunat.enabled=false` — that combination leaves no
reception-date source. The validator MUST raise a `ValueError` with a clear message.

#### Acceptance Scenarios

**Scenario EXT-S36 — vision.enabled defaults to True**

Given a default `VisionConfig` (no env override)
When `VisionConfig.enabled` is read
Then `enabled = True`
And the container wires the real provider-agnostic vision adapter

**Scenario EXT-S37 — vision-off injects NullVisionAdapter (zero LLM calls)**

Given `RECONCILIATION__VISION__ENABLED=false` and `sunat.enabled=true`
When `build_pipeline` constructs the pipeline
Then a `NullVisionAdapter` is injected in place of the real vision adapter
And no vision SDK/client is imported or initialised
And every guía vision-read date is `None`, resolving to SUNAT `fecha_entrega` via the R9b Rule-2
  delivery floor (`delivery_floor_applied=True`)

**Scenario EXT-S38 — vision-off with SUNAT off is rejected at construction**

Given `vision.enabled=false` AND `sunat.enabled=false`
When `AppConfig` is instantiated
Then construction raises `ValueError` (no reception-date source available)

---

---

## Delta — sunat-progress-port (2026-06-04): on_progress callback on SunatGreFetchPort

> The requirement below ADDS new behaviour relative to EXT-001 through EXT-025 above.
> Source change: #21 — SUNAT fetch progress instrumentation (merged to main).
> Full progress semantics are owned by `run-progress/spec.md` (RPG-006/RPG-007).
> Marked [ADDED].

### EXT-026 — [ADDED] SunatGreFetchPort.fetch_many MUST accept an optional on_progress callback

The `SunatGreFetchPort` Protocol's batch-fetch operation (`fetch_many`) MUST expose an
optional `on_progress(done: int, total: int) -> None` callback parameter:

```python
def fetch_many(
    self,
    requests: list[SunatFetchRequest],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[OfficialGre | None]: ...
```

The concrete adapter MUST invoke `on_progress(done, total)` once per completed concurrency
wave (or per iteration on the sequential fallback path), passing the cumulative completed
item count as `done` and the total request count as `total`.

The callback parameter MUST default to `None`. When `None`, the adapter MUST NOT raise;
it proceeds identically to the pre-#21 baseline (behaviour is observational-only — same
result regardless of callback presence).

This is a port-level contract change: any future concrete `SunatGreFetchPort` adapter MUST
honour this signature. The `NullSunatFetchPort` (disabled-SUNAT seam) MUST also accept the
parameter without error (it performs no fetches and need not call `on_progress`).

No domain value, quantity, MATCH/MISMATCH status, or reconciliation output MUST change
because of the presence or absence of the `on_progress` callback. This mirrors the
observational-only contract established by RPG-002.

#### Acceptance Scenarios

**Scenario EXT-S39 — on_progress called per wave; cumulative count advances**

Given `fetch_many` is called with 9 requests and wave size 3
And `on_progress` is a recording callable
When the adapter processes wave 1 (3 items done)
Then `on_progress(3, 9)` is called
When wave 2 completes (6 done)
Then `on_progress(6, 9)` is called
When wave 3 completes (9 done)
Then `on_progress(9, 9)` is called

**Scenario EXT-S40 — on_progress=None: no error; result identical**

Given `fetch_many` is called with `on_progress = None`
When the fetch completes
Then no exception is raised
And the returned `list[OfficialGre | None]` is byte-identical to the on_progress-wired run

**Scenario EXT-S41 — NullSunatFetchPort accepts on_progress without error**

Given `sunat.enabled = false` (NullSunatFetchPort in use)
When `fetch_many(requests=[], on_progress=some_callable)` is called
Then no exception is raised
And an empty list is returned (no fetches performed)

---

---

## Delta — deterministic-ocr-backend (SDD#1, 2026-06-10): RapidOCR as primary quantity extractor

> The requirements below ADD or MODIFY behaviour relative to EXT-001 through EXT-026 above.
> Source change: `deterministic-ocr-backend` — PR#1 (#51) · PR#2 (#52) · PR#3 (#53) · PR#4 (#54).
> All merged to `main`. Dual-blind judgment-day PASS×2 (Opus 4.8 + Fable 5) on all PRs.
> Real-data gate 13/13 GREEN (pages 148/156/160 + F1 regression-locks 0141/0164).
> Marked [ADDED] or [MODIFIED].

### EXT-027 — [MODIFIED: replaces EXT-004 engine coupling] Engine-agnostic OCR config and factory

**[MODIFIED: EXT-004 binds `extract_printed_table` to PaddleOCR via `PrintedTableAdapter`
specifically. This change makes engine selection provider-agnostic via config and a factory,
so the deploy target (RapidOCR, no paddle) and the dev alternative (paddle) coexist without
code change.]**

`OcrConfig` MUST expose an `engine` field of type `Literal["paddle", "rapidocr"]` with a
default of `"paddle"` (backward-compatible). The field MUST be settable via the environment
variable `RECONCILIATION__OCR__ENGINE` (Pydantic-settings env_prefix `RECONCILIATION__`,
nested delimiter `__`).

A factory function `build_ocr_extractor(cfg: OcrConfig) -> ExtractionPort` MUST exist in the
adapter layer (at `adapters/ocr/factory.py`). This factory is the **sole** module that imports
any concrete OCR adapter class. It MUST lazy-import concrete adapters inside its body (not at
module top level) so the test suite runs without any OCR SDK installed.

`build_ocr_extractor` MUST apply the following selection logic:

| `ocr.enabled` | `ocr.engine` | Resolved adapter |
|---|---|---|
| `False` | any | `NullOcrExtractor` (zero OCR calls) |
| `True` | `"rapidocr"` | `RapidOCRAdapter` |
| `True` | `"paddle"` | `PrintedTableAdapter` (existing) |

`application/pipeline.py` MUST NOT import `RapidOCRAdapter`, `PrintedTableAdapter`, or any
concrete OCR adapter class — it depends only on `ExtractionPort`.

The deploy defaults MUST be `RECONCILIATION__OCR__ENABLED=true` and
`RECONCILIATION__OCR__ENGINE=rapidocr`.

#### Scenario EXT-S027a — engine=rapidocr resolves to RapidOCRAdapter

Given `ocr.enabled=true` and `ocr.engine="rapidocr"` in config
When `build_ocr_extractor(cfg)` is called
Then an instance satisfying `ExtractionPort` is returned
And the instance is a `RapidOCRAdapter`
And no `paddle` or `paddleocr` symbol is imported during the call

#### Scenario EXT-S027b — engine=paddle resolves to PrintedTableAdapter

Given `ocr.enabled=true` and `ocr.engine="paddle"` in config
When `build_ocr_extractor(cfg)` is called
Then an instance satisfying `ExtractionPort` is returned
And the instance is a `PrintedTableAdapter`
And no `rapidocr` symbol is imported during the call

#### Scenario EXT-S027c — enabled=false resolves to NullOcrExtractor regardless of engine

Given `ocr.enabled=false` in config (any engine value)
When `build_ocr_extractor(cfg)` is called
Then a `NullOcrExtractor` is returned
And neither `rapidocr` nor `paddle` nor `paddleocr` is imported

#### Scenario EXT-S027d — pipeline.py imports zero concrete OCR adapters

Given the `deterministic-ocr-backend` change fully applied
When `import backend.src.reconciliation.application.pipeline` is executed
Then the module-level namespace contains no reference to `RapidOCRAdapter`,
  `PrintedTableAdapter`, or any concrete OCR adapter class
And the import succeeds without installing `rapidocr` or `paddleocr`

#### Scenario EXT-S027e — engine selector configurable via env var

Given `RECONCILIATION__OCR__ENGINE=rapidocr` set in the environment
And `RECONCILIATION__OCR__ENABLED=true`
When `OcrConfig` is instantiated and `build_ocr_extractor` is called
Then the active extractor is a `RapidOCRAdapter`
And no code modification is required

---

### EXT-028 — [ADDED] RapidOCRAdapter — printed table extraction contract

`RapidOCRAdapter` MUST implement `ExtractionPort` with the following contract:

- `extract_printed_table(image: bytes) -> list[MaterialLine]` — the primary operation.
  Given a guía page image (PNG bytes), it MUST return one `MaterialLine` per valid table row.
- `extract_declared(text: str) -> list[DeclaredMaterial]` — MUST return `[]` (the declared
  side is always sourced from digital text, never from this adapter).

`RapidOCRAdapter` MUST lazy-import `rapidocr`, `onnxruntime`, `numpy`, and `PIL` (Pillow)
INSIDE its methods — never at module top level. The test suite MUST run without these
packages installed when the adapter is not exercised.

`RapidOCRAdapter` MUST own the orientation retry loop (see EXT-030) — this is an adapter
concern, not a domain concern.

The adapter MUST NOT be imported by `domain/` or `application/pipeline.py`.

#### Scenario EXT-S028a — extract_declared always returns empty list

Given a `RapidOCRAdapter` instance
When `extract_declared(text="any text")` is called
Then `[]` is returned with no exception raised
And no OCR engine call is made

#### Scenario EXT-S028b — heavy imports absent at module load

Given the `adapters/ocr/rapid_table.py` module is imported at module level
When the module is first imported
Then `sys.modules` does NOT contain `rapidocr`, `onnxruntime`, or `numpy`
  (these are imported inside methods only)

---

### EXT-029 — [ADDED] Box-row parser — pure function, engine-independent

A standalone pure function (module: `adapters/ocr/box_row_parser.py`) MUST convert a list
of `(box: Sequence[Sequence[float]], text: str, score: float)` OCR cells into a
`list[MaterialLine]`.

"Pure" MUST mean: the function has no import of `rapidocr`, `onnxruntime`, `paddleocr`, or
any IO library. It MUST be importable and unit-testable with NO OCR SDK installed.

**DESC↔QTY pairing algorithm (MUST):**

1. Compute the centroid Y coordinate for each cell.
2. Group cells into row bands using a DPI-scaled tolerance:
   `row_band_px = round(40 * (dpi / 200))` where `dpi` is the render DPI of the image.
   Two cells are in the same row band when `|centroid_y_A − centroid_y_B| <= row_band_px`.
3. For each row band, classify cells as:
   - **QTY**: text is EITHER (a) a decimal number `^\d+[.,]\d{1,3}$` (1–3 fractional digits;
     4-digit fraction is a year/date shape and MUST be STRUCTURALLY rejected), OR (b) a bare
     integer `^\d+$` that has an adjacent UNIT cell in its row band (the unit-suffix
     disambiguator). A bare integer with NO adjacent unit is NOT a QTY.
   - **DESC**: text matching a material descriptor pattern recognizing at least: `BARRA`,
     `ACERO`, `A615`, `A706`, `FIERRO`, `VARILLA`, `ALAMBRE`, codes like `40xxxx`, diameter
     notations `\d+/\d+"` or `\d+[Mm][Mm]`. Any token containing a ≥3-letter alphabetic run
     also qualifies.
   - **IGNORED**: cells matching neither (header cells, row-number cells, supplier text).
4. For each QTY cell, the DESC cell in the SAME row band nearest to its LEFT MUST be the
   description for that quantity. A QTY cell with no DESC to its left MUST be ignored.
   Ownership is decided PER QTY by geometric nearness (smallest `|Δcy|`); a noise/header
   desc MUST NOT greedily claim the real material's qty and cause a real material row to
   silently vanish.
5. **Preferred column order** (real GRE physical layout: `DETALLE | UNIDAD | CANTIDAD`):
   A UNIT cell found in the PREFERRED column position — `desc.cx < unit.cx < qty.cx` (UNIT
   between DESC and QTY centroids) — yields a CONFIDENT line (`requires_review=False`).
   A unit claimed via the relaxed out-of-column fallback (any in-band unit regardless of
   column order) MUST set `requires_review=True`.
6. **Table-region detection**: `_infer_table_region(cells)` MUST estimate the y-band of the
   material table from cell geometry (topmost structural cluster carrying paired QTY+UNIT
   cells). Cells whose `cy` falls outside the detected region are excluded from the
   DESC/QTY/UNIT partition before the pairing loop. Detection MUST be position-based only —
   no keyword list (M-6 anti-pattern guard). Returns `None` on inconclusive detection;
   falls back to the unrestricted pairing loop. MUST NOT silently drop any cell within the
   detected table region. The anchor is the TOPMOST cluster of structural pairs (not the
   largest — the largest-cluster popularity contest silently drops real material rows when
   a noisy footer out-signals a small/garbled table; PR#4 JD F1 finding).

**Unit normalization (label-only — NOT a conversion):**

`TNE` MUST be normalized to `TN` in the output `MaterialLine.unidad`. No other unit
conversion is permitted. KG, TN, RD, Rollo MUST remain as-is.

**Incidental-number guard (MUST):** standalone integers with NO adjacent unit and standalone
diameter leads (`1"`, `1 3/8"`) MUST NOT be classified as QTY.

The parser function MUST accept a `dpi: int` parameter (default `200`).

#### Scenario EXT-S029a — correct DESC↔QTY pairing on a multi-row table

Given a list of OCR cells representing a 4-row GRE table at Y centroids [120,160,200,240] (DPI=200)
When `parse_box_rows(cells, dpi=200)` is called
Then 4 `MaterialLine`-shaped rows are returned with QTY values {0.008,0.136,0.191,0.041}
And each row pairs QTY with the DESC in its own row band (never cross-band)

#### Scenario EXT-S029b — TNE normalized to TN; KG/RD/Rollo unchanged

Given a row with unidad `TNE` and cantidad `0.136`
Then the emitted row has `unidad="TN"` and `cantidad=0.136` (value unchanged)

Given rows with unidad values `KG`, `RD`, `Rollo`
Then the emitted rows have `unidad` values `KG`, `RD`, `Rollo` respectively

#### Scenario EXT-S029c — columnar GRE table where _LINE_RE yields zero lines

Given OCR cells from a columnar GRE table with non-contiguous bounding boxes
When `parse_box_rows(cells, dpi=200)` is called
Then at least 1 valid `MaterialLine`-shaped row is returned

#### Scenario EXT-S029d — incidental numbers not misread as QTY

Given cells containing `1` (leftmost), `1"` (diameter), `408916` (product code), `0.037` (valid qty)
When `parse_box_rows` is called
Then only `0.037` is classified as QTY

#### Scenario EXT-S029e — non-rebar descriptor still recognized

Given a DESC cell with text `FIERRO CORRUGADO 1/2"`
When `parse_box_rows` is called
Then the cell is classified as DESC and its associated QTY is included in output

#### Scenario EXT-S029f — pure function: importable without any OCR SDK

Given `rapidocr`, `onnxruntime`, `paddleocr` are NOT installed
When the box-row parser module is imported and `parse_box_rows` is called with synthetic data
Then no `ImportError` is raised and the function returns the expected rows

#### Scenario EXT-S029g — DPI-scaled band: 150 DPI yields 30px; 300 DPI yields 60px

Given `dpi=150`: `row_band_px = round(40 * (150/200)) = 30`
Given `dpi=300`: `row_band_px = round(40 * (300/200)) = 60`

#### Scenario EXT-S029h — trusted read on DETALLE|UNIDAD|CANTIDAD column order

Given synthetic cells: DESC at `cx=50`, UNIT `TNE` at `cx=200`, QTY at `cx=350` (same band)
When `parse_box_rows` is called
Then the emitted row has `requires_review=False` (UNIT between DESC and QTY → preferred column)

#### Scenario EXT-S029i — stamp rows excluded by position, not keyword

Given a 2-row material table at cy≈[100,140] plus a stamp-region pair at cy≈[420]
When `parse_box_rows` is called
Then only 2 rows are returned (stamp row excluded by table-region geometry)
And no keyword appears in the exclusion logic (M-6 anti-pattern guard)

---

### EXT-030 — [ADDED] Self-scoring orientation auto-fix (adapter strategy)

`RapidOCRAdapter.extract_printed_table` MUST apply an orientation auto-fix before returning
rows, using the box-row parser as the oracle:

**Default rotation**: rotate the input image by **−90°** before the first OCR call.
(All 165 reg227 pages scanned sideways — this default handles the known convention.)

**Retry fallback**: if the box-row parser returns 0 valid rows after −90°, retry with
rotations `{0°, 90°, 180°, 270°}` and select the rotation yielding the most valid rows.
Ties broken by order `[0, 90, 180, 270]`. The orientation retry MUST apply ONLY inside
`extract_printed_table`; it MUST NOT apply to `extract_declared` or non-guía pages.

#### Scenario EXT-S030a — default -90° rotation applied on a sideways page

Given a guía page scanned sideways (the default reg227 convention)
When `RapidOCRAdapter.extract_printed_table(image)` is called
Then the image is rotated -90° before the first OCR pass
And the parser returns N > 0 valid rows with no retry triggered

#### Scenario EXT-S030b — retry triggered when default yields 0 rows

Given a guía page that is upright (0°)
When `extract_printed_table(image)` is called
Then the first pass (−90°) yields 0 rows
And the adapter retries {0°,90°,180°,270°} and returns rows from the 0° candidate

#### Scenario EXT-S030c — extract_declared path never force-rotated

Given `extract_declared(text)` is called
Then no image rotation is applied and no RapidOCR engine call is made

---

### EXT-031 — [ADDED] Ground-truth real-data accuracy gate

`RapidOCRAdapter.extract_printed_table` MUST pass a real-data accuracy gate against confirmed
ground-truth pages (keyed on `CTR_PDF_PATH` env var, `@pytest.mark.slow`).

**Ground-truth targets (from `docs/eval/ground_truth.md`):**

| Page | guia_id | Expected rows (cantidad, unidad after normalization) |
|---|---|---|
| 0148 | T112-0065418 | (0.037,TN), (0.014,TN), (0.102,TN) |
| 0156 | T112-0065426 | (0.008,TN), (0.136,TN), (0.191,TN), (0.041,TN) |
| 0160 | T009-0739440 | (1.616,TN), (0.238,TN), (1.643,TN), (0.121,TN) |
| 0141 | — | exactly 1 confident row: (2.489,TN); no confident spurious |
| 0164 | — | exactly 1 confident row: (0.213,TN); no confident spurious |

The gate MUST verify: row count matches expected; each `cantidad` matches GT exactly (3dp
rounding); each `unidad` is `"TN"`. No confident spurious rows. The gate helper
`_assert_gt_complete_no_confident_spurious` MUST consume GT slots with CONFIDENT lines first
(order-independent budget) so a review-flagged duplicate of a GT quantity does not pre-empt
the confident read and cause a false-positive assertion failure.

Pages 0141 and 0164 are F1 regression-locks: they are 1-line guías where the pre-PR#4
popularity-contest anchor (largest cluster) silently dropped the single real material row when
a noisy footer out-signaled the small table. PR#4 topmost-structural anchor fixes this.

#### Scenario EXT-S031a — page 0156 (4 rows, 4/4 exact)

Given the real guía page 0156 rendered from `CTR_PDF_PATH`
When `RapidOCRAdapter.extract_printed_table(image)` is called
Then exactly 4 rows are returned with `cantidad` values [0.008,0.136,0.191,0.041] (TN each)

#### Scenario EXT-S031b — page 0148 (3 rows, all exact)

Given the real guía page 0148
Then exactly 3 rows: (0.037,TN), (0.014,TN), (0.102,TN)

#### Scenario EXT-S031c — page 0160 (4 rows, ACERO DIMENSIONADO, all exact)

Given the real guía page 0160
Then exactly 4 rows: (1.616,TN), (0.238,TN), (1.643,TN), (0.121,TN)

#### Scenario EXT-S031d — pages 0141 and 0164 (1-line guía F1 regression-lock)

Given page 0141 or 0164 (single-row guías)
Then exactly 1 confident row is returned for each
And no confident spurious rows appear
And the GT quantity value matches exactly

---

### EXT-032 — [ADDED] Domain invariants preserved through OCR path

1. **Units never converted**: `RapidOCRAdapter` MUST NOT multiply, divide, or adjust any
   numeric quantity. Only `TNE → TN` label normalization is permitted. KG, TN, RD, Rollo
   values MUST sum independently through reconciliation.
2. **Reconciliation as validation gate**: an OCR misread producing a quantity differing from
   the declared value MUST result in `status=MISMATCH` and `requires_review=True`. MUST NOT
   auto-correct.
3. **Grouping key unchanged**: key remains `(registro, material_canonical, unidad)`. `fecha`
   is NEVER part of the key. `RapidOCRAdapter` output MUST NOT carry `fecha`.
4. **Domain purity**: no file under `backend/src/reconciliation/domain/` MUST import or
   reference `rapidocr`, `onnxruntime`, `RapidOCRAdapter`, or `box_row_parser`.
5. **Input PDF read-only**: OCR processing MUST NOT modify the source PDF.

#### Scenario EXT-S032a — OCR misread flagged; never auto-corrected

Given `RapidOCRAdapter` reads `cantidad=0.190` but declared is `0.191`
When reconciliation runs
Then `status=MISMATCH` and `requires_review=True` on the affected row

#### Scenario EXT-S032b — mixed-unit table: KG and TN sum independently

Given a page with rows (descripción_A, 500, KG) and (descripción_A, 0.5, TN)
Then the KG and TN quantities are summed in separate groups — never converted to each other

#### Scenario EXT-S032c — domain/ files unchanged after SDD#1 applied

Given the `deterministic-ocr-backend` change fully applied
When `git diff main -- backend/src/reconciliation/domain/` is inspected
Then zero domain files are modified, added, or removed

---

### EXT-033 — [ADDED] RapidOCR dependencies and Docker air-gap

1. **Optional dependency group**: project MUST expose a `[project.optional-dependencies]`
   group named `ocr` containing at minimum `rapidocr`, `onnxruntime`, `Pillow>=10.0`,
   `numpy>=1.26`. The Dockerfile builder layer MUST install this group (`--extra ocr`).
2. **Paddle absence retained**: the existing CONT-S02 assertion — `import paddle` and
   `import paddleocr` are NOT present in the runtime image — MUST remain satisfied.
3. **RapidOCR runtime assertion**: a startup CONT smoke test MUST verify `import rapidocr`
   succeeds in the deployed image.
4. **Model bundling (air-gap)**: PP-OCRv5-server ONNX model weights (~165 MB) MUST be
   baked into the deployed image at build time so the first OCR call succeeds with NO network
   access. Strategy: build-time warm-up `RUN python -c "from rapidocr import RapidOCR; RapidOCR()"`.
5. **uv.lock updated**: committed lockfile MUST pin `rapidocr` and `onnxruntime` versions.

#### Scenario EXT-S033a — RapidOCR import passes; paddle import fails in deployed image

Given a container built from the SDD#1 Dockerfile
When `python -c "import rapidocr"` runs inside the container
Then the import succeeds
And `import paddle` raises `ImportError` (paddle absence retained)

#### Scenario EXT-S033b — OCR call succeeds in a network-isolated container

Given a network-isolated container with weights baked at build time
When `RapidOCRAdapter.extract_printed_table(image)` is called
Then the call succeeds with no ConnectionError

#### Scenario EXT-S033c — rapidocr does not pull paddle as a transitive dep

Given the `ocr` optional-dependency group installed via `uv sync --extra ocr`
When transitive deps are listed
Then neither `paddlepaddle`, `paddlepaddle-gpu`, nor `paddleocr` appear

---

## Non-goal Boundary — SDD#1 scope guard

### EXT-NG-001 — #50 dropped-page sentinel is NOT part of SDD#1

Issue #50 (silent drop of identity-less GUIA pages at `pipeline.py:976-982`) is addressed in
SDD#2 (discarded-pages-recovery). SDD#1 addressed it only implicitly by re-enabling OCR so
fewer pages produce `len(lines)==0`.

SDD#1 did NOT add any new API field, new domain model, new HTTP endpoint, or new UI element
to surface dropped pages. The explicit surfacing of dropped/identity-less guía pages was
completed in **SDD#2** — see EXT-034 through EXT-037 below.

---

## SDD#2 Delta — discarded-pages-recovery (merged 2026-06-11)

> Additive delta from `openspec/changes/archive/discarded-pages-recovery/specs/extraction/spec.md`.
> All existing extraction requirements (EXT-001 through EXT-033) remain in force.
> **Non-goal boundary inherited from the proposal**: this delta does NOT change classification
> (EXT-001/EXT-019), the QR-evidence gate's blocking semantics, or any block-grouping logic.
> The gate's blocking semantics are UNCHANGED — a no-evidence page never opens or extends a
> block. What changes is that the drop is no longer invisible.

### EXT-034 — [ADDED] ZERO silent drops: discarded entry at the QR-evidence gate

**[ADDED: previously, a `guia`-classified page at `_stage_assemble_blocks` that fails
`has_guia_evidence` (identity is None AND the OCR-fallback material condition is False) was
silently discarded with `continue`. This caused issue #50: the operator had zero signal that
a guía was lost. This requirement closes that hole.]**

The `_stage_assemble_blocks` stage (or its equivalent in `application/pipeline.py`) MUST NOT
silently discard any page classified `guia`. Every page that fails the `has_guia_evidence`
gate MUST instead produce a **discarded entry** and append it to the `PipelineResult`
discarded collection (see EXT-035).

The discarded entry MUST carry:
- `source_page: int` — zero-based page index of the dropped page.
- `registro: str | None` — the section registro resolved from `page_to_registro` (or
  `raw.registro`) at the time of the drop. MAY be `None` when the section map yields no
  registro for this page.
- `cached_lines: list[MaterialLine]` — the `raw.lines` populated by the OCR stage before the
  QR-evidence check. MAY be empty (`[]`) if OCR produced no rows for this page.

The model shape is `DiscardedPage(BaseModel)` in `domain/models.py` — domain-pure, zero IO/SDK.

#### Scenario EXT-S034a — page with no QR evidence produces a discarded entry

Given a `guia`-classified page whose `identity` is `None`
And `page_hashqr_url` is `None` (no URL-variant QR found)
And `raw.lines` contains 2 `MaterialLine` objects from the OCR stage
And `raw.registro` is `"232"`
When `_stage_assemble_blocks` processes this page
Then the page does NOT open or extend any guía block
And a discarded entry is appended to `PipelineResult` with:
  - `source_page` = the correct page index
  - `registro = "232"`
  - `cached_lines` = the 2 `MaterialLine` objects
And no `GuiaDeRemision` is created for this page

#### Scenario EXT-S034b — page with no QR evidence and empty OCR lines still produces discarded entry

Given a `guia`-classified page whose `identity` is `None` and `page_hashqr_url` is `None`
And `raw.lines` is `[]` (OCR found nothing on this page)
And `raw.registro` is `"229"`
When `_stage_assemble_blocks` processes this page
Then the page does NOT open or extend any guía block
And a discarded entry is appended with `source_page`, `registro="229"`, `cached_lines=[]`
And no `GuiaDeRemision` is created for this page

#### Scenario EXT-S034c — page with valid QR evidence is NOT discarded

Given a `guia`-classified page whose `identity` is a valid `GuiaIdentity` (QR decoded)
When `_stage_assemble_blocks` processes this page
Then the page opens or extends a guía block normally
And NO discarded entry is produced for this page

#### Scenario EXT-S034d — page with OCR-fallback evidence (hashqr_url + lines) is NOT discarded

Given a `guia`-classified page where `identity` is `None`
And `page_hashqr_url` is a non-None URL QR value
And `raw.lines` contains at least 1 `MaterialLine`
When `_stage_assemble_blocks` processes this page
Then the page opens or extends an `ocr_fallback` guía block normally (EXT-019 rev-6 rule)
And NO discarded entry is produced for this page

#### Scenario EXT-S034e — registro=None discarded entry is valid and surfaced

Given a `guia`-classified page with no QR evidence
And `page_to_registro` returns `None` for this page (section map yields no registro)
When the discarded entry is produced
Then `discarded_entry.registro` is `None`
And the entry is still appended to the `PipelineResult` discarded collection
And the entry is still surfaced in the review API response

---

### EXT-035 — [ADDED] PipelineResult carries a discarded collection

`PipelineResult` MUST expose a `discarded_pages: list[DiscardedPage]` field defaulting to
`[]` so that:

1. Existing callers that do not read the discarded collection are unaffected.
2. Old serialized `PipelineResult` objects that lack the field hydrate without error
   (tolerant `cache.get("discarded_pages", [])` deserialization).

The discarded collection MUST be populated ONLY from the `_stage_assemble_blocks` EXT-034
drop path. It MUST NOT receive entries from the existing errored-guía path (zero-OCR-lines
guías with a valid identity).

The existing `PipelineResult.errored_guias` collection MUST retain its current semantics:
guías with a valid identity (QR or OCR fallback) whose OCR yielded zero material lines.
The two collections MUST be semantically distinct and MUST NOT be mixed.

#### Scenario EXT-S035a — PipelineResult has separate discarded and errored collections

Given a run that produces:
  - 1 guía with valid QR identity but 0 OCR lines (existing errored case)
  - 1 guía-classified page with no QR evidence (new discarded case)
When the pipeline completes
Then `PipelineResult.errored_guias` contains the identity-valid zero-lines guía
And `PipelineResult.discarded_pages` contains the no-evidence page entry
And neither collection contains the other's entries

#### Scenario EXT-S035b — old PipelineResult cache without discarded field hydrates cleanly

Given an existing serialized `PipelineResult` (from a run before SDD#2) that has no
  discarded collection field
When the deserialization/hydration step processes the cached result
Then no `ValidationError` or `KeyError` is raised
And the discarded collection defaults to `[]` (empty)

---

### EXT-036 — [ADDED] Cached OCR lines preserved in discarded entry; reused on recovery

When the discarded entry is produced at the drop site, the `cached_lines` field MUST be
populated from `raw.lines` at that exact moment — the OCR stage has already run and the
lines are available. Persisting them avoids a redundant re-OCR call on recovery.

On recovery, the recovery service MUST:
1. Read `cached_lines` from the discarded entry.
2. If `cached_lines` is **non-empty**: use those lines directly as the recovered material
   lines WITHOUT invoking `ExtractionPort.extract_printed_table`. The deterministic OCR
   engine (SDD#1 `RapidOCRAdapter`) is idempotent — same image → same output; re-running
   adds no value.
3. If `cached_lines` is **empty**: invoke `ExtractionPort.extract_printed_table` on the
   page image (rendered at recovery DPI) and use the resulting lines.
4. If OCR also returns empty lines in step 3: fall back to `VisionLLMPort` for material
   line extraction. Vision is the LAST resort, after both cached-lines and OCR paths are
   exhausted.

The recovery service MUST NOT invoke OCR when step 2 applies. The recovery service MUST
NOT invoke vision when step 3 succeeds (non-empty OCR result).

#### Scenario EXT-S036a — recovery with cached lines: OCR not re-run

Given a discarded entry with `cached_lines = [MaterialLine(cantidad=0.191, unidad="TN", ...)]`
When the recovery service processes this entry
Then the `cached_lines` are used directly as the recovered material lines
And `ExtractionPort.extract_printed_table` is NOT called for this page
And `VisionLLMPort` is NOT called for this page

#### Scenario EXT-S036b — recovery with empty cached lines: OCR is re-run

Given a discarded entry with `cached_lines = []`
And `ExtractionPort.extract_printed_table` is available (OCR enabled)
When the recovery service processes this entry
Then `ExtractionPort.extract_printed_table` is called with the rendered page image
And the returned lines are used as the recovered material lines (if non-empty)

#### Scenario EXT-S036c — recovery with empty cached lines and empty OCR result: vision fallback

Given a discarded entry with `cached_lines = []`
And `ExtractionPort.extract_printed_table` returns `[]` for this page
When the recovery service processes this entry
Then `VisionLLMPort` is called for material extraction as the last fallback
And if vision also returns nothing, the recovery fails with a structured error
  (the entry stays in the discarded collection; it is NOT silently removed)

---

### EXT-037 — [ADDED] Synthetic identity for recovered pages (design-level contract)

A recovered guía page MUST receive a **synthetic identity** because no QR `serie-numero`
exists. The spec constrains the semantics:

1. The synthetic identity MUST NEVER collide with a real QR-derived `guia_id`
   (format `{serie}-{numero}`). Implementation: `guia_id=f"recovered_{page}"`.
2. The synthetic identity MUST NOT be confused with the three domain identifiers:
   Contents-ID (e.g. `#4252`) ≠ Registro N° (e.g. `232`) ≠ QR `serie-numero`.
3. `identity_source` on the recovered `GuiaDeRemision` MUST use `"operator"` — an additive
   Literal value distinct from `"qr"` and `"ocr_fallback"`.
4. The API DTO `identity_source` field MUST be updated in lockstep with the new Literal
   value at all four sites (the `match_method` 500-lesson). Sites: `domain/models.py` (×2),
   `infrastructure/api/schemas.py`, `frontend/src/api/types.ts`.
5. The recovered guía MUST carry `requires_review=True` on ALL recovered material lines,
   regardless of OCR confidence. Recovery is never a confirmed-accurate read.
6. The recovered guía MUST land under the `registro` inherited from the discarded entry's
   `registro` field (the section registro). No mandatory assignment dialog on recovery.
   Registro reassignment is the exceptional [Acciones] flow.

#### Scenario EXT-S037a — synthetic identity does not collide with QR format

Given a recovered page at page index 152 (decimal)
When the synthetic identity is assigned
Then the `guia_id` does NOT match the pattern `[A-Z]\d+-\d+` (the QR `serie-numero` format)
And the `guia_id` does NOT equal `"152"` (a bare page index could be confused with
  a section/registro N°)
And `identity_source` is NOT `"qr"` and NOT `"ocr_fallback"`

#### Scenario EXT-S037b — all recovered lines carry requires_review=True

Given a recovered page where OCR returns 3 `MaterialLine` objects
And the OCR confidence for all 3 rows is >= 0.95 (high confidence)
When the recovered `GuiaDeRemision` is assembled
Then all 3 `MaterialLine` objects have `requires_review=True`
And the reconciliation gate will flag the recovered group for human review

#### Scenario EXT-S037c — recovered guía lands under section registro

Given a discarded entry with `registro="232"` and `source_page=152`
When recovery is completed and the `GuiaDeRemision` is assembled
Then `guia_de_remision.registro = "232"`
And no assignment dialog is triggered
And the guía appears in the reconciliation result under registro 232

---

## Acceptance Scenarios Summary — SDD#1 additions

| Requirement | Scenario IDs | TDD tier |
|---|---|---|
| EXT-027 (factory/config) | S027a–S027e | adapter unit + config tests |
| EXT-028 (RapidOCRAdapter contract) | S028a–S028b | adapter unit tests |
| EXT-029 (box-row parser) | S029a–S029i | pure unit tests |
| EXT-030 (orientation auto-fix) | S030a–S030c | adapter unit tests (injected mock engine) |
| EXT-031 (GT real-data gate) | S031a–S031d | @pytest.mark.slow, CTR_PDF_PATH |
| EXT-032 (domain invariants) | S032a–S032c | unit tests + git diff assertion |
| EXT-033 (deps/Docker/air-gap) | S033a–S033c | containerized-verify (Makefile/Compose) |

## Acceptance Scenarios Summary — SDD#2 additions (discarded-pages-recovery)

| Requirement | Scenario IDs | TDD tier |
|---|---|---|
| EXT-034 (zero silent drops) | S034a–S034e | unit tests (`test_pipeline_discarded_pages.py`) |
| EXT-035 (PipelineResult discarded collection) | S035a–S035b | unit tests (`test_pipeline_discarded_pages.py`, `test_container_discarded.py`) |
| EXT-036 (cached lines + 3-tier recovery) | S036a–S036c | unit tests (`test_apply_page_recovery.py`) + real-data gate (`test_discarded_recovery_gate.py`) |
| EXT-037 (synthetic identity + 4-site lockstep) | S037a–S037c | unit tests (`test_schemas_discarded.py`, `test_apply_page_recovery.py`) |

---

## Out of scope for this domain

- Summation of extracted quantities (handled by the reconciliation domain).
- Normalization of material descriptions (handled by the normalization step in the reconciliation domain).
- MATCH/MISMATCH detection (handled by the reconciliation domain).
- Export (handled by the export domain).
