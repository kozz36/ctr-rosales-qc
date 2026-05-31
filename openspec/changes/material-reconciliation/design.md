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
