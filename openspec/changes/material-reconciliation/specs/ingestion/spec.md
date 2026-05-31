# Spec — Ingestion Domain
**Change**: material-reconciliation
**Domain**: ingestion
**Phase**: spec
**Date**: 2026-05-31

---

## Purpose

The ingestion domain accepts a single PDF file as immutable input, splits it into individual pages, renders each page as a raster image for downstream processing, and extracts digital text from text-bearing pages. Orientation correction (deskew) is applied post-classification to `guia`-class pages only, with an orientation fallback for pages whose title OCR is empty or low-confidence.

---

## Requirements

### INJ-001 — Read-only input

The system MUST treat the input PDF as read-only throughout the entire pipeline.
No write, move, rename, or delete operation on the source file is permitted.
Any adapter that must reference the file MUST open it in read-only mode.

### INJ-002 — Single-file input contract

The pipeline MUST accept exactly one PDF file per ingestion run.
The PDF MUST conform to the CTR-PLC01-FR001 Autodesk Forma export format (493-page layout).
The system SHOULD validate that the input is a readable PDF and MUST surface a structured error if the file is missing, corrupt, or cannot be opened — rather than entering an undefined state.

### INJ-003 — Per-run output isolation

Each ingestion run MUST write all outputs (rendered images, extracted text, intermediate artifacts) to a uniquely identified output directory.
The input PDF MUST NOT be overwritten or modified as a side-effect of any run.
Aborting a run MUST leave the output directory in a partially-written state that is recoverable (i.e., subsequent stages MUST NOT process an incomplete run silently).

### INJ-004 — Page splitting

The system MUST split the PDF into individual pages.
Each page MUST be individually addressable by a zero-based or one-based integer page index.
The page index MUST be preserved and propagated through all downstream stages so that every extracted value can be traced back to its source page.

### INJ-005 — Page rendering

The system MUST render each page to a raster image (PNG or JPEG).
Rendered images MUST be produced at a resolution sufficient for PaddleOCR to operate correctly (RECOMMENDED minimum: 150 DPI; SHOULD prefer 200–300 DPI for degraded scans).
The `DocumentSourcePort` adapter (implemented by `PdfStructureAdapter` using PyMuPDF) SHALL be the sole component that performs rendering.

### INJ-006 — Digital text extraction

For pages that contain embedded digital text (e.g., detail pages and Protocolo de Recepción), the system MUST extract the raw text without OCR.
Digital text extraction MUST NOT invoke OCR or a vision LLM on text-bearing pages.
The declared material list MUST be sourced exclusively from digital text; OCR MUST NOT be applied to declared-side content.

### INJ-007 — Deskew (orientation correction)

The system MUST apply orientation correction (deskew) to `guia`-class pages ONLY.
Deskew MUST be applied post-classification — after the `PageClassifier` has assigned class `guia` to a page — not to all scanned pages indiscriminately.
(Previously: deskew was applied to every scanned page before classification.)

The deskew stage MUST support all four primary orientations: 0°, 90°, 180°, and 270°.
Deskew MUST be implemented using `PaddleOCR DocImgOrientationClassification` via a `DeskewAdapter`.

**Orientation fallback (MUST):** When the title OCR result for a page is empty or the classification confidence is below threshold, the system MUST attempt deskew on that page before re-running classification. This prevents a rotated guía page from being silently misclassified and dropped.
The system MUST NOT silently drop a guía page due to orientation. If post-fallback classification still cannot assign `guia`, the page MUST be flagged `orientation_fallback_failed: true` and surfaced in the review UI.

A page whose post-deskew OCR yields an empty table MUST be flagged for review and MUST NOT be silently dropped.

### INJ-008 — Stage idempotency and abort safety

Each ingestion stage (split, render, deskew) MUST be individually idempotent: re-running a stage on already-processed pages MUST produce the same result and MUST NOT duplicate output artifacts.
If the pipeline aborts after the render/split stage, the cached page renders MUST be preserved so a subsequent run can skip re-splitting.

### INJ-009 — Page count audit

The system MUST emit an audit record containing the total number of pages detected in the input PDF.
This count MUST be made available to downstream stages and to the review UI.

---

## Acceptance Scenarios

### Scenario INJ-S01 — Successful ingestion of a valid PDF

**Given** a readable PDF file at the configured input path with 493 pages
**When** the ingestion pipeline is invoked
**Then** the system creates an isolated output directory for this run
**And** all 493 pages are split and rendered as individual raster images
**And** digital text is extracted from text-bearing pages without invoking OCR
**And** each page classified as `guia` has been deskewed to 0° rotation (non-guía pages are NOT deskewed)
**And** an audit record reports page_count = 493
**And** the source PDF is unchanged

### Scenario INJ-S02 — Pipeline abort after split preserves renders

**Given** 493 pages have been split and rendered into the output directory
**When** the pipeline is aborted before the extract stage begins
**Then** all 493 rendered images are present in the output directory
**And** a subsequent pipeline invocation MUST reuse the cached renders without re-splitting
**And** the source PDF is unchanged

### Scenario INJ-S03 — Missing or corrupt input file

**Given** the configured input path does not exist or the file is not a valid PDF
**When** the ingestion pipeline is invoked
**Then** the system returns a structured error identifying the failure reason
**And** no output directory or partial artifacts are created
**And** no downstream stage is invoked

### Scenario INJ-S04 — Orientation fallback triggered for potential guía with empty title OCR

**Given** a scanned page whose initial title OCR is empty or below classification confidence threshold
**When** the ingestion pipeline processes that page
**Then** the system applies deskew to the page as an orientation fallback before re-running classification
**And** if post-fallback classification assigns class `guia`, the page proceeds to extraction as a guía page
**And** if post-fallback classification still cannot assign a known class, the page is flagged `orientation_fallback_failed: true`
**And** the flagged page surfaces in the review UI under the "pages requiring attention" bucket
**And** the page is NOT silently dropped from the audit trail

### Scenario INJ-S05 — Post-deskew OCR yields empty table

**Given** a page that has been classified as GUÍA DE REMISIÓN
**And** deskew has been applied
**When** OCR on the deskewed image returns zero table rows
**Then** the page is flagged `ocr_empty_after_deskew: true`
**And** the page surfaces in the review UI
**And** the page's contribution to summation is zero — it MUST NOT be silently excluded from the audit trail

---

## Out of scope for this domain

- Page classification by document title (handled by the extraction domain classifier).
- OCR of printed tables (handled by `PrintedTableAdapter`).
- Vision LLM calls for handwritten content (handled by `VisionLLMPort`).
- Normalization, reconciliation, review, and export.
