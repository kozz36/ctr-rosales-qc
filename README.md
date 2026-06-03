# ctr-rosales-qc

Local-first QC reconciliation tool for construction material receipts. It ingests a
large Autodesk Forma PDF export (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and
reconciles, per **Registro N°**, the **declared** materials (digital text from the detail
page + Protocolo de Recepción) against the **sum of materials** extracted from the scanned
**guías de remisión** (SUNAT GRE). It flags mismatches, lets a quality engineer reassign
misfiled guías, and exports the reconciled table to xlsx/csv.

> Built with Spec-Driven Development. See **[`docs/HANDOFF.md`](docs/HANDOFF.md)** for
> current state, known issues, and next steps.

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
   handwritten Protocolo de Recepción date and guía date stamps.

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

### Current status (as of 2026-06-03)

| Area | State |
|---|---|
| rev-2: QR identity tier, guía-granularity ReviewService, reassign + line-edit, thumbnail, export | ✅ implemented |
| R8: canonical material matching (declared↔guía MATCH via canonical key; `fecha` removed from grouping key) | ✅ implemented |
| R9: reception-date authority (handwritten Protocolo date authoritative; per-guía day-month divergence → non-blocking `requires_review` WARNING + page ref + red highlight; bounded year inference) | ✅ implemented |
| r10: paddle-free containerized verification (`ocr.enabled` escape hatch, provider-agnostic cloud vision, bounded-concurrency SUNAT) | ✅ implemented |
| Backend unit/targeted tests | ✅ 886 passing |
| Frontend vitest | ✅ 188 passing |
| Judgment-Day adversarial review | ✅ APPROVED (R8/R9/r10 — 3 rounds; rev-2 base — 2 rounds) |
| Real-PDF gate (25-page subset, cloud vision) | ✅ #4252 1/2"×9M = 4.124 TN MATCH |
| Playwright visual validation | ✅ 0 console errors |
| PR #1 → main | ⏳ open, awaiting merge |

### Known environment limits

- **KI-2** — `qwen3.5:397b-cloud` (Ollama cloud) throttles under rapid sequential calls
  (>25s/call under load vs. 5–9s isolated). Full 493-page e2e is impractical; the 25-page
  section-1 subset is the tractable fixture. Not a code bug.
- **KI-3** — Intermittent SUNAT read timeout under load; cross-run cache only persists via
  the container named volume.

### Post-merge roadmap

The following are deferred SDD slices, not blockers for the current PR:

1. **Reception-date floor = guía SUNAT delivery date (`fecha_entrega`)** — if the vision-read
   date is earlier than the physical delivery date, use `fecha_entrega` as the floor and raise
   a non-blocking verify-warning. Physical invariant: goods cannot be received before delivery.
2. **`disable_thinking` default `true`** — `VisionConfig.disable_thinking` defaults to `false`;
   switch to `true` (and `RECONCILIATION__VISION__DISABLE_THINKING=true` in compose). Disabling
   `<think>` improves OCR/vision captures and removes ~12s/call overhead.
3. **`.env.example`** — full config reference covering all `RECONCILIATION__*` settings and
   compose interpolation variables (`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, etc.).
4. **Determinate progress bar** — add stage + count reporting to `GET /runs/{id}` and a
   frontend progress bar with ETA for operator monitoring during processing.

## Privacy

Local-first by design. The input PDF is treated read-only and never leaves the machine.
Page images go only to the configured vision provider (which can be a local Ollama instance
for full air-gap). The SUNAT document-fetch feature and cloud vision are opt-in and off by
default.

## License

[Apache License 2.0](LICENSE).
