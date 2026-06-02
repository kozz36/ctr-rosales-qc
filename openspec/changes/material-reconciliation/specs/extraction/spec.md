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

## Out of scope for this domain

- Summation of extracted quantities (handled by the reconciliation domain).
- Normalization of material descriptions (handled by the normalization step in the reconciliation domain).
- MATCH/MISMATCH detection (handled by the reconciliation domain).
- Export (handled by the export domain).
