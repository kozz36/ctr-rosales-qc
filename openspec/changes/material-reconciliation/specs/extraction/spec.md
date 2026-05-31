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

## Out of scope for this domain

- Summation of extracted quantities (handled by the reconciliation domain).
- Normalization of material descriptions (handled by the normalization step in the reconciliation domain).
- MATCH/MISMATCH detection (handled by the reconciliation domain).
- Export (handled by the export domain).
