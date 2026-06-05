# ctr-rosales-qc — v1.0.0

Local-first QC reconciliation tool for construction material receipts. It ingests an
Autodesk Forma PDF export (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) and
reconciles, per **Registro N°**, the **declared** materials (digital text from the detail
page + Protocolo de Recepción) against the **sum of materials** extracted from the scanned
**guías de remisión** (SUNAT GRE). It flags mismatches, lets a quality engineer reassign
misfiled guías, and exports the reconciled table to XLSX/CSV.

> **Operator guide:** see **[`docs/USAGE.md`](docs/USAGE.md)** for how to run and use the
> tool (run commands, operating modes, the upload → review → reassign → export flow, and how
> to read the review table).

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation — Linux / macOS](#installation--linux--macos)
- [Installation — Windows](#installation--windows)
- [Opening the app](#opening-the-app)
- [Stopping the app](#stopping-the-app)
- [Default run mode](#default-run-mode)
- [Troubleshooting](#troubleshooting)
- [Where to download](#where-to-download)
- [Architecture](#architecture)
- [Development quick start](#development-quick-start)
- [Status](#status)
- [Privacy](#privacy)
- [License](#license)

---

## Prerequisites

The installer builds and runs the stack inside Docker containers. You do **not** need
Python, Node.js, or any runtime installed on the host.

| Platform | Requirement |
|---|---|
| **Windows** | [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/) (includes Docker Compose) |
| **macOS** | [Docker Desktop for Mac](https://docs.docker.com/desktop/install/mac-install/) (includes Docker Compose) |
| **Linux** | [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose plugin](https://docs.docker.com/compose/install/) v2 |

Verify your installation before proceeding:

```bash
docker --version          # Docker 24+ recommended
docker compose version    # must be v2 (the "compose" sub-command, not "docker-compose")
```

---

## Installation — Linux / macOS

```bash
git clone https://github.com/kozz36/ctr-rosales-qc.git
cd ctr-rosales-qc
./install.sh
```

`install.sh` checks prerequisites, builds the backend and frontend images from source,
and starts the stack in deterministic mode. The first build takes a few minutes; subsequent
starts are fast.

| Command | Action |
|---|---|
| `./install.sh` | Build + start the app |
| `./install.sh --stop` | Stop the app |
| `./install.sh --logs` | Follow live logs |

---

## Installation — Windows

**Requirement:** Docker Desktop must be installed and running before you begin.

Open **PowerShell** (Windows Terminal or the Start menu) and run:

```powershell
git clone https://github.com/kozz36/ctr-rosales-qc.git
cd ctr-rosales-qc
.\install.ps1
```

If PowerShell reports an execution-policy restriction, you can bypass it for a single run:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Alternatively, double-click `install.bat` in File Explorer — it calls `install.ps1`
with the execution-policy bypass automatically.

| Command | Action |
|---|---|
| `.\install.ps1` | Build + start the app |
| `.\install.ps1 -Stop` | Stop the app |
| `.\install.ps1 -Logs` | Follow live logs |

Both `install.ps1` and `install.bat` use the same `docker-compose.app.yml` as the
Linux/macOS installer, so the stack is byte-identical across platforms.

---

## Opening the app

Once the installer reports that the backend is ready, open your browser at:

- **App (frontend):** http://localhost:5173
- **Backend API / docs:** http://localhost:8000/docs

The installer attempts to open the browser automatically on Linux (X11/Wayland) and Windows.

---

## Stopping the app

```bash
# Linux / macOS
./install.sh --stop
# or equivalently
make app-down

# Windows PowerShell
.\install.ps1 -Stop
```

---

## Default run mode

Out of the box the stack runs in **deterministic vision-off + SUNAT-authoritative** mode:

- `vision.enabled = false` — zero LLM calls; no Ollama or API key required.
- `sunat.enabled = true` — material quantities and guía reception dates come from SUNAT GRE
  data fetched via the QR-decoded `serie-numero`.

**SUNAT reachability:** the first run for each guía fetches from
`e-factura.sunat.gob.pe`. Subsequent runs read from a local cross-run cache stored in a
named Docker volume (`sunat-cache`). Once the cache is warm the tool runs fully offline.

The cache survives `--stop` / restart cycles but is cleared by `make app-clean`.

To enable cloud or local Ollama vision (handwritten guía date reads), set
`vision.enabled = true` and configure the provider via environment variables — see
`.env.example` and `backend/.env.example`.

---

## Troubleshooting

**Port already in use (5173 or 8000)**

Another process is listening on that port. Find and stop it, then run the installer again.

```bash
# Linux / macOS
ss -tlnp | grep '5173\|8000'

# Windows PowerShell
netstat -ano | findstr "5173 8000"
```

**Docker is not running**

The installer will print an error message. On Windows/macOS, open Docker Desktop and wait
for it to report "Engine running" before retrying. On Linux, run:

```bash
sudo systemctl start docker
```

**Backend takes too long to start**

On first run, the backend initializes its environment. The installer waits up to 60 seconds.
If it times out, check logs:

```bash
./install.sh --logs      # Linux / macOS
.\install.ps1 -Logs      # Windows
```

**Build fails on first run**

Ensure Docker Desktop has sufficient resources (Recommended: 4 GB RAM, 2 CPUs). On Windows,
verify that Docker Desktop is using the WSL 2 backend (Settings → General).

**SUNAT is unreachable**

The app starts regardless. The SUNAT fetch will be retried on the next pipeline run. If
your network blocks SUNAT, contact your network administrator or enable an alternative
operating mode (vision-on with a local Ollama instance).

---

## Where to download

The source and tagged releases are at:

- **Repository:** https://github.com/kozz36/ctr-rosales-qc
- **Releases:** https://github.com/kozz36/ctr-rosales-qc/releases

Download the source archive for the tagged version (`v1.0.0` or later) and follow the
installation steps above. There are no pre-built binary installers; the Docker build step
produces the runtime images from source on your machine.

---

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

---

## Development quick start

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

Ollama endpoint and model are configurable via `OLLAMA_BASE_URL` / `OLLAMA_MODEL`
environment variables.

---

## Status

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

1. **`disable_thinking` perf lever** — verify the compose default propagates correctly under
   load with the full 493-page PDF.
2. **Determinate progress bar ETA calibration** — ETA accuracy improves as the pipeline
   accumulates real timing samples; current estimate is linear interpolation from first 5%.
3. **Date-read variance verify** — vision-read year reconstruction under high-load throttling
   can produce year inference edge cases; monitor on multi-page runs.

---

## Privacy

Local-first by design. The input PDF is treated read-only and never leaves the machine.
Page images go only to the configured vision provider (which can be a local Ollama instance
for full air-gap). The SUNAT document-fetch feature and cloud vision are opt-in and off by
default.

---

## License

[Apache License 2.0](LICENSE).
