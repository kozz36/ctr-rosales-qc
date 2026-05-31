# Architecture — material-reconciliation

Hexagonal / Ports & Adapters. Dependencies point inward only. The full design (with code
snippets + sequence diagrams) is in `openspec/changes/material-reconciliation/design.md`
(base §1–7 + rev-2 delta §A–F).

## Rings

```
backend/src/reconciliation/
├── domain/          # PURE: no SDK/framework/IO imports
│   ├── models.py        # MaterialLine, GuiaDeRemision, Registro, ReconciliationRow,
│   │                    #   PageClassification, VisionResult  (+rev2: GuiaIdentity, GuiaContribution)
│   ├── classifier.py    # PageClassifier — classify by TITLE (validated vs real PDF)
│   ├── normalizer.py    # MaterialNormalizer — canonicalizes DESCRIPTION only, never unit
│   ├── reconciliation.py# ReconciliationService — per-unit EXACT-0 sum, MATCH/MISMATCH, reassign
│   ├── ports.py         # Protocols: DocumentSourcePort, ExtractionPort, VisionLLMPort,
│   │                    #   ReportPort  (+rev2: IdentityExtractionPort, SunatGreFetchPort seam)
│   └── errors.py
├── application/     # orchestration; depends on domain ports only (no concrete adapters)
│   ├── config.py        # AppConfig (pydantic-settings); provider selection; cost cap; 0.85; deskew=guia_only
│   ├── run_context.py   # per-run dir, immutable extraction cache, review.json sidecar
│   ├── pipeline.py      # ReconciliationPipeline — the deterministic stage sequence
│   └── review_service.py# edits + reassignment, sidecar persistence/replay
├── adapters/
│   ├── pdf/             # PdfStructureAdapter (PyMuPDF, read-only), DigitalTextExtractionAdapter
│   ├── ocr/             # paddle_deskew (DocImgOrientationClassification), paddle_table
│   ├── vision/          # anthropic_vision, openai_compatible (OpenAI+Ollama), factory
│   ├── report/          # xlsx_report (openpyxl, 10 cols + summary, csv)
│   └── barcode/         # (rev2, to build) qr_identity — QrBarcodeExtractionAdapter
└── infrastructure/
    ├── container.py     # composition root: CompositeExtractionAdapter, build_page_to_registro_map,
    │                    #   build_pipeline (wires adapters; section↔registro correlation)
    └── api/             # FastAPI: main.py (create_app), routes.py, schemas.py

frontend/src/
├── api/            # client.ts + types.ts — mirror backend schemas exactly
├── stores/         # Pinia: run, reconciliation (client state)
├── composables/    # TanStack Query (server state)
├── design/         # tokens.css (industrial QC palette, semantic MATCH/MISMATCH colors)
└── features/
    ├── run/        # UploadPanel, RunProgress
    └── review/     # ReviewGrid, ReconciliationRow, ConfidenceBadge, SourcePages,
                    #   GuiaReassignDialog, ExportButton, ReviewPage
```

## Pipeline (rev-2)

```
split → classify → deskew(guía-only) → [assemble guía blocks → QR identity tier]
      → extract[OCR quantities + vision reception-date] → normalize → reconcile → review → export
```

- **Tier-0 identity** (QR, local, conf 1.0): `guia_id = serie-numero`, RUCs, tipo — overrides OCR/vision identity.
- **Tier-1 quantities** (PaddleOCR). **Tier-2 fecha** (vision, handwritten reception date).
- **Tier-3 fetch** (SunatGreFetch) — seam only, OFF (breaks air-gap), deferred.

## Key invariants

- Domain imports no SDK/framework (verified). Adapters lazy-import heavy deps so the suite
  runs without `paddleocr`/`anthropic`/`openai`/`pyzbar`/`zxing-cpp` installed.
- `pipeline.py` depends only on domain ports + config/run_context — **no concrete adapter imports** (Dependency Inversion).
- Input PDF is read-only; each run writes its own isolated output dir.

## API surface (FastAPI, local-first, base `/api/v1`)

`POST /runs` · `GET /runs/{id}` · `GET /runs/{id}/table` · `PATCH /runs/{id}/rows/{row_id}`
· `POST /runs/{id}/reassign` · `POST /runs/{id}/export` · `GET /runs/{id}/audit`
· (rev-2) guía-line cantidad edit · (Phase 6/7) `GET /runs/{id}/pages/{page}/thumbnail`.

## How to run

```bash
# Backend
cd backend && pip install -e ".[dev]"        # add [ml] / [llm] for OCR / vision adapters
python -m pytest -q                          # 455 tests
uvicorn reconciliation.infrastructure.api.main:app --host 127.0.0.1 --port 8000 --reload

# Frontend
cd frontend && npm install
npm run test:unit                            # 85 tests
npm run dev                                   # Vite dev server (proxies /api)
```
