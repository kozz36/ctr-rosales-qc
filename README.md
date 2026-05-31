# ctr-rosales-qc

Local-first QC reconciliation tool for construction material receipts. It ingests a
large Autodesk Forma PDF export (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and
reconciles, per **Registro N° + reception date**, the **declared** materials (digital text
from the detail page + Protocolo de Recepción) against the **sum of materials** extracted
from the scanned **guías de remisión** (SUNAT GRE). It flags mismatches, lets a quality
engineer reassign misfiled guías, and exports the reconciled table to xlsx/csv.

> Built with Spec-Driven Development. **Work in progress** — backend and frontend are
> implemented and tested; a design rev-2 (deterministic QR identity + guía-granularity
> review) is specced and pending implementation. See **[`docs/HANDOFF.md`](docs/HANDOFF.md)**
> to resume.

## Why it exists

Manually cross-checking material receipts across a ~500-page PDF (11 reception records, each
fanning out into multiple rotated, scanned delivery notes) is slow and error-prone. This tool
automates the reconciliation and surfaces only what needs human judgment.

## Architecture

Hexagonal / Ports & Adapters, Python 3.12 + FastAPI backend, Vue 3 + TypeScript frontend,
fully local-first. Extraction is tiered and deterministic-first:

1. **QR identity** (local, deterministic) — SUNAT GRE QR → `guia_id = serie-numero`.
2. **OCR** (PaddleOCR) — printed material/quantity tables.
3. **Vision** (provider-agnostic: Anthropic / OpenAI / Ollama) — handwritten reception date.

The reconciliation against the trusted digital declared side is the validation gate. See
**[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** for the full layout and
**[`docs/DECISIONS.md`](docs/DECISIONS.md)** for every design decision and audit finding.

## Quick start

```bash
# Backend (Python 3.12)
cd backend
pip install -e ".[dev]"          # add [ml] for PaddleOCR, [llm] for vision SDKs
python -m pytest -q              # 455 tests
uvicorn reconciliation.infrastructure.api.main:app --host 127.0.0.1 --port 8000 --reload
# API docs at http://127.0.0.1:8000/docs

# Frontend (Vue 3 + Vite)
cd frontend
npm install
npm run test:unit               # 85 tests
npm run dev
```

Heavy/optional dependencies (`paddleocr`, `anthropic`, `openai`, `pyzbar`, `zxing-cpp`) are
lazy-loaded; the test suites run green without them installed.

## Privacy

Local-first by design. The input PDF is treated read-only and never leaves the machine.
Page images go only to the configured vision provider (which can be a local Ollama for full
air-gap). An optional SUNAT document-fetch feature is off by default because it would make an
external call.

## Status

| Area | State |
|---|---|
| Backend (domain, pipeline, adapters, FastAPI) | ✅ implemented, 455 tests, validated on real data |
| Frontend (upload, review grid, reassign, export) | ✅ implemented, 85 tests |
| Design rev-2 (QR identity tier, guía-granularity review) | 📝 specced, pending implementation |
| Verify · adversarial review · archive | ⏳ pending |

## License

[Apache License 2.0](LICENSE).
