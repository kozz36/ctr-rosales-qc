# Windows Native Installer — Design (SDD#5 / direct-build path B)

> Status: **implementation complete on Linux; pending Windows build + validation**
> (branch `feat/windows-native-installer`). Slice 1 (backend SPA serving) + slice 2 (build-kit)
> done; ctr-review APPROVE-WITH-FINDINGS (0 critical), all confirmed findings fixed; backend suite
> green (14 SPA tests + 1634 targeted). **NOT YET DONE** — the §3 gate (SA-5 1-click + real-data
> R8/R9) must pass on the Windows box before this is shippable.
> This is the source-of-truth contract both implementation slices consume.
> Authoring machine is Linux; the `.exe` build + 1-click validation happen on Windows.

## 1. Decision

The Windows delivery is a **native PyInstaller bundle — no Docker**. A non-technical quality
engineer double-clicks one shortcut; a single local process serves the API and the SPA and opens
the default browser. Zero PC dependencies (no Docker Desktop, no WSL2, no admin, no licensing).

This reverses the earlier "Docker path" handoff note. Trade-off accepted: the Docker runtime image
was already `make verify`-proven, so going native **re-opens the real-data validation surface** on
Windows-native. The "done" gate is therefore SA-5 (1-click runtime) **plus** re-running the
real-data acceptance (R8 + R9) through the native bundle — not unit-green.

## 2. Architecture

### 2.1 Same-origin SPA serving (collapse nginx → FastAPI)

In Docker the topology is two processes: nginx serves the SPA and proxies `/api` → `backend:8000`
(CORS-bridged). Native collapses this to **one `uvicorn` process** that serves both the API and the
SPA from the same origin. Consequences:

- **CORS becomes irrelevant** for the operator's calls (same origin).
- The SPA is built with `VITE_API_BASE_URL=""` so the axios client resolves to the **relative**
  prefix `/api/v1` (`frontend/src/api/client.ts:44-54` concatenates `BASE_URL + '/api/v1'`; empty
  string → `/api/v1`; a bare `/` would wrongly yield `//api/v1`). Relative base ⇒ **port-agnostic**.
- Because the base is relative, the launcher can bind a **dynamic free port** and the SPA still
  works — this also eliminates the historical `:8000`/`:8010` host-port collision concern.

### 2.2 Backend serving contract (slice 1 — touches `backend/src`)

**Additive and opt-in.** The static mount activates only when an env var points at a built SPA, so
Docker/dev behavior is unchanged (nginx keeps serving the SPA there; this path stays API-only).

- Env var: **`RECONCILIATION_SPA_DIR`** — absolute path to the built SPA (`index.html` + `assets/`).
- If unset OR the directory is missing → **no mount** (today's behavior; API-only). No regression.
- If set and present → mount in `create_app()` **after** the `/api/v1` router, with these rules:
  - Real asset files (`/assets/*`, `/favicon.ico`, etc.) are served from disk.
  - Any other non-API path (`/`, `/historial`, deep SPA routes) returns **`index.html`** (history-mode fallback).
  - Paths under `api/`, `docs`, `redoc`, `openapi.json` are **never** swallowed — an unknown
    `/api/v1/...` must still return a **404 JSON**, not `index.html`.
- Implementation lives in the **infrastructure/api layer only** (e.g. `infrastructure/api/spa.py`
  + wiring in `main.py`). **No domain/application import. No new heavy top-level import.**

**Strict-TDD (required — touches `backend/src`).** Failing tests first:
1. `GET /` → 200, `text/html`, body is `index.html`.
2. `GET /historial` (SPA client route, no file on disk) → 200 `index.html` (fallback).
3. `GET /assets/<real-file>` → 200 with the asset's content-type.
4. `GET /api/v1/<unknown>` → **404 JSON** (fallback must NOT intercept API).
5. `GET /api/v1/runs/` (existing) → still routes to the API unchanged.
6. SPA dir **unset** → `GET /` behaves as today (no fallback mount); existing API tests still green.

Test the mount via a tmp dir fixture writing a fake `index.html` + `assets/app.js`; set
`RECONCILIATION_SPA_DIR` and build the app via the existing `create_app()` factory.

### 2.3 Launcher (slice 2 — `packaging/windows/launcher.py`)

Frozen with `pythonw` (no console window). Behavior on double-click:

1. Resolve writable per-user dirs under `%LOCALAPPDATA%\ctr-rosales-qc\`: `runs/`, `sunat-cache/`,
   `secrets/`. Create if missing.
2. Resolve bundled read-only assets relative to `sys._MEIPASS` (frozen) or repo (dev): the SPA
   dir (`frontend/dist` bundled) and `config.yaml`.
3. Set the **deterministic vision-off profile** env (mirrors `docker-compose.app.yml`):
   - `RECONCILIATION__VISION__ENABLED=false`
   - `RECONCILIATION__OCR__ENABLED=true`, `RECONCILIATION__OCR__ENGINE=rapidocr`
   - `RECONCILIATION__SUNAT__ENABLED=true`, `RECONCILIATION__SUNAT__CACHE=true`
   - `RECONCILIATION__SUNAT__CACHE_DIR=<localappdata>\sunat-cache`
   - `RECONCILIATION__OUTPUT_DIR=<localappdata>\runs`
   - `RECONCILIATION_SECRETS_DIR=<localappdata>\secrets`
   - `RECONCILIATION_SPA_DIR=<meipass>\frontend\dist`
   - `RECONCILIATION_CONFIG=<meipass>\config.yaml`
   - (`AppConfig._validate_date_source` requires vision-off ⇒ sunat-on; satisfied.)
4. Pick a **free ephemeral port** (bind `127.0.0.1:0`, read the assigned port, release).
5. Start uvicorn **in-process** against the imported app object (frozen mode — pass the object,
   not an import string; no `--reload`): `uvicorn.run(app, host="127.0.0.1", port=<port>)` on a
   background thread.
6. Poll `http://127.0.0.1:<port>/api/v1/runs/` until 200 (health), then open the default browser
   at `http://127.0.0.1:<port>/`.
7. Lifecycle: keep the process alive; a minimal tray icon (or a hidden window with a balloon) lets
   the operator quit. Quitting stops uvicorn and exits. (If tray adds bundling risk, fall back to a
   single small always-on-top "CTR Rosales QC — cerrar para detener" window.)

### 2.4 PyInstaller bundle (`packaging/windows/ctr-rosales-qc.spec`)

**One-dir** build (faster startup than one-file; no per-launch temp extraction). Key `datas`:

- `rapidocr/models/*.onnx` + `*.txt` → bundled at `rapidocr/models/` so RapidOCR's
  `Path(rapidocr.__file__).parent / "models"` resolves automatically under `_MEIPASS`
  (`rapidocr/main.py:34,59-60`; adapter passes no explicit `model_root_dir`). Required models:
  `ch_PP-OCRv5_det_server.onnx`, `ch_PP-OCRv5_rec_server.onnx`,
  `ch_ppocr_mobile_v2.0_cls_mobile.onnx`, plus `ppocr_keys_v1.txt` + `ppocrv5_dict.txt`.
- `frontend/dist` (the SPA built with `VITE_API_BASE_URL=""`).
- `config.yaml` (deterministic profile).
- **`libzbar` DLL** for `pyzbar` (Windows needs the external lib; the union pyzbar+zxing is what
  gives QR recall — EXT-012, do NOT drop pyzbar). Use the PyInstaller `pyzbar` hook / collect the
  DLL from the installed wheel.

`hiddenimports` / `collect_all` to verify on Windows: `onnxruntime` (DLLs + providers),
`rapidocr`, `cv2` (opencv-python-headless), `fitz` (pymupdf), `uvicorn` (its `logging`/`loops`/
`protocols` submodules), `polars`, `openpyxl`, `zxingcpp`, `pyzbar`. The reconciliation package is
imported via `--paths backend/src`.

### 2.5 Inno Setup installer (`packaging/windows/installer.iss`)

- **Per-user** install (`PrivilegesRequired=lowest`) → no admin prompt.
- Installs the one-dir bundle under `%LOCALAPPDATA%\Programs\CTR Rosales QC\`.
- Creates a **Desktop** + **Start Menu** shortcut to `launcher.exe` (icon).
- Uninstaller removes program files; leaves `%LOCALAPPDATA%\ctr-rosales-qc\` data unless the user
  opts to purge.
- Version stamped from `v1.0.0`.

### 2.6 Build pipeline (`packaging/windows/build.ps1`)

Runs on the Windows build box, end to end:
1. `cd frontend; npm ci; $env:VITE_API_BASE_URL=""; npm run build` → `frontend/dist`.
2. `cd backend; py -3.12 -m venv .venv; .venv\Scripts\pip install ".[identity,ocr]"` (NO `llm`,
   NO `ml`/paddle).
3. `pyinstaller packaging/windows/ctr-rosales-qc.spec` → `dist/ctr-rosales-qc/`.
4. `iscc packaging/windows/installer.iss` → `dist/CTR-Rosales-QC-Setup-v1.0.0.exe`.

## 3. Validation gate (definition of done)

Unit-green is **not** done (project hard-won lesson). On the Windows box:

- **SA-5 1-click**: install via the `.exe`; double-click the shortcut; the browser opens; upload the
  493-page PDF; the review table renders; the [Historial] route works (proves SPA fallback).
- **Real-data acceptance**: a reconciliation run reproduces the invariants — **R8** registro 232
  `BARRA A615 G60 1/2" 9M` TN MATCH `declared=4.124`; **R9** divergence flags a misfiled guía
  `requires_review`. This proves RapidOCR + PyMuPDF + QR read **identically** on Windows-native.
- **Air-gap-ish**: confirm no model re-download at first run (models load from `_MEIPASS`).

## 4. Risk register (probe order on Windows)

| # | Risk | Trigger | Mitigation |
|---|------|---------|-----------|
| R1 | `pyzbar` can't find `libzbar` DLL | QR decode raises `ImportError`/`OSError` at runtime | Bundle the DLL via PyInstaller `pyzbar` hook; verify `zxingcpp` alone still degrades gracefully |
| R2 | `onnxruntime` DLL/provider not found under `_MEIPASS` | RapidOCR init fails | `collect_dynamic_libs("onnxruntime")` + `collect_all` in spec |
| R3 | RapidOCR models not found frozen | OCR returns empty / downloads | Bundle at `rapidocr/models/`; assert path exists at launcher start |
| R4 | PyMuPDF (`fitz`) frozen import fails | PDF render errors | `collect_all("fitz")`; pin pymupdf wheel |
| R5 | SPA fallback swallows `/api` 404s | API error returns HTML | Slice-1 strict-TDD test #4 locks this |
| R6 | Antivirus flags the unsigned `.exe` | SmartScreen warning | Document; optional code-signing as a later step |

## 5. Layout

```
docs/WINDOWS-INSTALLER.md          # this file
packaging/windows/
  launcher.py                      # frozen entrypoint (slice 2)
  ctr-rosales-qc.spec              # PyInstaller spec (slice 2)
  installer.iss                    # Inno Setup (slice 2)
  build.ps1                        # Windows build pipeline (slice 2)
  config.yaml                      # deterministic vision-off profile (slice 2)
  README.md                        # build + validation runbook (slice 2)
backend/src/reconciliation/infrastructure/api/spa.py   # SPA mount (slice 1)
```
