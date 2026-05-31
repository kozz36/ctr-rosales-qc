# Decisions & Findings — material-reconciliation

Versioned record of every significant decision and audit finding (mirrors the local engram).
Newest context at the bottom of each section.

---

## Domain rules (locked)

- **Two identifiers**: `#4252` = Autodesk Forma section/Contents ID; `232` = business
  **Registro N°** (from detail Description + Protocolo). Group by the **Registro N°**.
- **Grouping key**: `(registro, fecha, material_canonical, unidad)`.
- **Units** KG / TN / RD / Rollo are **summed independently — never converted**.
- **Page classification by document TITLE**, not supplier name. `GUÍA DE REMISIÓN` feeds the
  sum; `Planilla Resumen`, `Listado de Barras`, photos, cover, contents do **not**.
- **Declared side is trusted digital text** (Protocolo canonical, detail = cross-check).
  Reconciliation vs declared **is the validation gate** that surfaces OCR errors.
- **Dates** (§dates): the grouping `fecha` is the **handwritten reception date** read by
  vision from the scan — NOT the electronic GRE date. They can differ; divergence = the
  **misfiled-guía** case → reassignment.

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
