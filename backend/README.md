Backend pipeline for CTR Rosales QC material reconciliation — hexagonal Python service
(FastAPI + optional PaddleOCR + provider-agnostic vision LLM).

## Stack

- Python 3.12, managed with [uv](https://github.com/astral-sh/uv)
- FastAPI (routes under `/api/v1`)
- PaddleOCR (optional — disable with `ocr.enabled = false`)
- Vision LLM: Anthropic or any OpenAI-compatible endpoint (Ollama, OpenAI cloud) via `base_url`

## Running tests

```bash
cd backend
# Targeted paths only — monolithic pytest -q hangs on a PaddleOCR import
uv run pytest tests/unit/{domain,application,adapters,infrastructure}   # 886 tests
```

Heavy optional deps (`paddleocr`, `anthropic`, `openai`, `pyzbar`, `zxing-cpp`) are
lazy-loaded inside adapter methods; the unit test suite runs green without them installed.

## Running the server

```bash
cd backend
uvicorn reconciliation.infrastructure.api.main:app --host 127.0.0.1 --port 8000 --reload
# Swagger UI: http://127.0.0.1:8000/docs
```

## Containerized verification (paddle-free)

See `../docker-compose.yml` and `../Makefile`. Ollama endpoint and model are configurable
via `OLLAMA_BASE_URL` / `OLLAMA_MODEL` environment variables. Setting `ocr.enabled=false`
activates `NullOcrExtractor` (zero PaddleOCR dependency; SUNAT-supplied quantities used).

## Architecture

Hexagonal / Ports & Adapters. The domain core (`src/reconciliation/domain/`) is pure — no
SDK, framework, or IO imports. Adapters lazy-load heavy dependencies. Vision is
provider-agnostic behind `VisionLLMPort`; the active provider is selected by config, never
hard-coded in the domain.

See `../docs/ARCHITECTURE.md` for the full folder layout and pipeline description.
