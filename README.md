# ctr-rosales-qc — v1.0.0

Local-first QC reconciliation tool for construction material receipts. It ingests a
large Autodesk Forma PDF export (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and
reconciles, per **Registro N°**, the **declared** materials (digital text from the detail
page + Protocolo de Recepción) against the **sum of materials** extracted from the scanned
**guías de remisión** (SUNAT GRE). It flags mismatches, lets a quality engineer reassign
misfiled guías, and exports the reconciled table to xlsx/csv.

> **Operator guide:** see **[`docs/USAGE.md`](docs/USAGE.md)** for how to run and use the
> tool (run commands, operating modes, the upload → review → reassign → export flow, and how to
> read the review table).
>
> Built with Spec-Driven Development. See **[`docs/HANDOFF.md`](docs/HANDOFF.md)** for
> current state, known issues, and next steps.

## Installation / Quick Start

**Prerequisite:** Docker and Docker Compose (no Python or Node.js required on the host).

```bash
./install.sh
```

`install.sh` checks prerequisites, builds backend and frontend images from source, and
launches the stack via Docker Compose in deterministic mode. The app is ready at:

- **Frontend:** http://localhost:5173
- **Backend API:** http://localhost:8000 (docs at `/docs`)

To stop:

```bash
make app-down
```

**Default operating mode — deterministic vision-off + SUNAT-authoritative:**

Out of the box the stack runs with `vision.enabled=false` and `sunat.enabled=true`. In this
mode there are **zero LLM calls**: material quantities come from SUNAT GRE data and guía
reception dates resolve to the SUNAT `fecha_entrega` delivery date. The pipeline is fully
deterministic and produces stable output across runs once the SUNAT cross-run cache is warm.

Requirements for this mode:
- Network access to SUNAT on first run (the QR-decoded GRE document is fetched and cached).
  Subsequent runs use the cache and are air-gap-friendly.
- The SUNAT cache persists via a named Docker volume; it survives `make app-down` / `make
  app-up` restarts but is reset by `make app-clean`.

To enable cloud or local Ollama vision (handwritten guía date reads), set `vision.enabled=true`
and configure `RECONCILIATION__VISION__PROVIDER` / `RECONCILIATION__VISION__MODEL` in the
environment (see `.env.example` and `backend/.env.example` for the full reference).

## Why it exists

Manually cross-checking material receipts across a ~500-page PDF (11 reception records, each
fanning out into multiple rotated, scanned delivery notes) is slow and error-prone. This tool
automates the reconciliation and surfaces only what needs human judgment.

## Architecture

Hexagonal / Ports & Adapters, Python 3.12 + FastAPI backend, Vue 3 + TypeScript frontend,
fully local-first. Extraction is tiered and deterministic-first:

1. **QR identity** (local, deterministic) — SUNAT GRE QR → `guia_id = serie-numero`; dual-decoder
   COLOR union; multi-page guía blocks assembled by shared QR id.
2. **OCR** (PaddleOCR, optional) — printed material/quantity tables; disable with
   `ocr.enabled = false` (NullOcrExtractor — zero PaddleOCR dependency).
3. **Vision** (provider-agnostic: Anthropic | OpenAI-compatible incl. Ollama via `base_url`) —
   handwritten guía date stamps only. The Protocolo de Recepción `Fecha:` is **not** vision-read;
   it is parsed deterministically from the digital text layer (no vision).

Reconciliation groups by `(Registro N°, material_canonical, unidad)`. The `fecha` field is
**not** a grouping axis (R8 domain rule). The trusted digital declared side is the validation
gate; mismatches are flagged for human review, never auto-corrected.

See **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** for the full layout and
**[`docs/DECISIONS.md`](docs/DECISIONS.md)** for every design decision and audit finding.

## Quick start

```bash
# Backend (Python 3.12, uv)
cd backend
uv sync --extra dev          # add [ml] for PaddleOCR, [llm] for vision SDKs
uv run pytest tests/unit/{domain,application,adapters,infrastructure}  # 886 targeted tests
uvicorn reconciliation.infrastructure.api.main:app --host 127.0.0.1 --port 8000 --reload
# API docs at http://127.0.0.1:8000/docs  (routes under /api/v1)

# Frontend (Vue 3 + Vite)
cd frontend
npm install
npm test          # 188 vitest tests
npm run dev       # http://localhost:5173 — proxies /api → :8000
```

> **Note on backend tests:** run via targeted paths (`tests/unit/{...}`), not monolithic
> `pytest -q`. The monolithic run hangs on a PaddleOCR import on machines where the runtime
> is partially installed.

Heavy/optional dependencies (`paddleocr`, `anthropic`, `openai`, `pyzbar`, `zxing-cpp`) are
lazy-loaded; the test suites run green without them installed.

### Containerized verification (paddle-free, cloud vision)

```bash
# Start the API server in a container (no PaddleOCR required)
docker compose up -d backend

# Run integration gates against the container
docker compose run --rm \
  -v /tmp/ctr_section1.pdf:/data/section1.pdf:ro \
  -e CTR_PDF_PATH=/data/section1.pdf \
  -e OLLAMA_BASE_URL=http://localhost:11435/v1 \
  backend python -m pytest tests/integration/test_pipeline_r9_gate.py -v -s
```

Ollama endpoint and model are configurable via `OLLAMA_BASE_URL` / `OLLAMA_MODEL` environment
variables. For full air-gap, point to a local Ollama instance; `ocr.enabled=false` removes
the PaddleOCR dependency entirely (SUNAT-supplied quantities are used instead).

## Status & roadmap

### Current status — v1.0.0 (2026-06-04)

| Area | State |
|---|---|
| rev-2: QR identity tier, guía-granularity ReviewService, reassign + line-edit, thumbnail, export | ✅ implemented |
| R8: canonical material matching (declared↔guía MATCH via canonical key; `fecha` removed from grouping key) | ✅ implemented |
| R9: reception-date authority (digital Protocolo `Fecha:` authoritative — deterministic parse, no vision; per-guía day-month divergence → non-blocking `requires_review` WARNING + page ref + red highlight; bounded year inference applies to the guía side) | ✅ implemented |
| R9b/R9c: reception-date floor (SUNAT `fecha_entrega`) + ceiling (Protocolo date) | ✅ implemented |
| r10: paddle-free containerized verification (`ocr.enabled` escape hatch, provider-agnostic cloud vision, bounded-concurrency SUNAT) | ✅ implemented |
| Deterministic vision-off + SUNAT-authoritative mode (`vision.enabled=false`) | ✅ implemented |
| Page-sheet viewer (lightbox, 200 DPI, zoom/rotate/pan) | ✅ implemented |
| a11y: viewer focus-trap + restore focus (WCAG 2.4.3) + layout-safe zoom keys | ✅ implemented (PR #32) |
| Determinate progress bar (stage label, count, elapsed, ETA) | ✅ implemented |
| XLSX/CSV export (13 columns) | ✅ implemented |
| Backend unit/targeted tests | ✅ 886 passing |
| Frontend vitest | ✅ 188+ passing |
| Judgment-Day adversarial review | ✅ APPROVED (R8/R9/r10 — 3 rounds; rev-2 base — 2 rounds) |
| Real-PDF gate (25-page subset, deterministic mode) | ✅ #4252 1/2"×9M = 4.124 TN MATCH |
| Playwright visual validation | ✅ 0 console errors |

### Known environment limits

- **KI-2** — `qwen3.5:397b-cloud` (Ollama cloud) throttles under rapid sequential calls
  (>25 s/call under load vs. 5–9 s isolated). The 25-page section-1 subset is the tractable
  fixture for vision-on mode. Not a code bug; use deterministic mode for full-PDF runs.
- **KI-3** — Intermittent SUNAT read timeout under load; cross-run cache persists only via
  the container named volume.

### Deferred follow-ups (post-v1.0.0)

1. **`disable_thinking` perf lever** — `VisionConfig.disable_thinking` defaults to `true` since
   PR #3; verify the compose default propagates correctly under load with the full 493-page PDF.
2. **Determinate progress bar ETA calibration** — ETA accuracy improves as the pipeline
   accumulates real timing samples; current estimate is linear interpolation from first 5%.
3. **Date-read variance verify** — vision-read year reconstruction under high-load
   `qwen3.5` throttling can produce year inference edge cases; monitor on multi-page runs.

## Privacy

Local-first by design. The input PDF is treated read-only and never leaves the machine.
Page images go only to the configured vision provider (which can be a local Ollama instance
for full air-gap). The SUNAT document-fetch feature and cloud vision are opt-in and off by
default.

## License

[Apache License 2.0](LICENSE).
