# CTR Rosales QC — Windows Native Installer Build Runbook

Design contract: `docs/WINDOWS-INSTALLER.md`

---

## Prerequisites

Install all of the following on the **Windows build box** before running `build.ps1`.

| Prerequisite | Version | Source |
|---|---|---|
| Python 3.12 | 3.12.x | <https://python.org/downloads/> — check **"Add py.exe to PATH"** and **"Add Python to environment variables"** during install |
| Node.js | LTS (20.x or 22.x) | <https://nodejs.org/> |
| Inno Setup 6 | 6.3.x | <https://jrsoftware.org/isinfo.php> — add `iscc.exe` to `PATH` |
| Visual C++ Redistributable 2022 | x64 | <https://aka.ms/vs/17/release/vc_redist.x64.exe> — required by onnxruntime and OpenCV DLLs at runtime |
| Git | Any recent | <https://git-scm.com/> |

Verify prerequisites with:

```powershell
py -3.12 --version
node --version
npm --version
iscc /?
```

---

## Build

Run from the **repository root** (not from `packaging\windows\`):

```powershell
.\packaging\windows\build.ps1
```

The script stops on any failure (`$ErrorActionPreference='Stop'`). Each step prints a status line.

### Partial rebuild flags

| Flag | Skips |
|---|---|
| `-SkipFrontend` | npm ci + build (use when `frontend/dist/` already up to date) |
| `-SkipVenv` | venv creation + pip install (use when `.venv` already prepared) |
| `-SkipPyInstaller` | PyInstaller bundle step (use when `dist/ctr-rosales-qc/` already built) |

Example — rebuild only the installer `.exe` after a PyInstaller run:

```powershell
.\packaging\windows\build.ps1 -SkipFrontend -SkipVenv -SkipPyInstaller
```

---

## Where the output lands

| Output | Path |
|---|---|
| PyInstaller one-dir bundle | `dist\ctr-rosales-qc\` |
| Launcher executable | `dist\ctr-rosales-qc\ctr-rosales-qc.exe` |
| Setup installer | `dist\CTR-Rosales-QC-Setup-v1.0.0.exe` |

---

## Validation Checklist (Definition of Done)

Unit-green is **not** sufficient — the project hard-won lesson (see `docs/DECISIONS.md §audit`).
Run ALL of the following on a **clean Windows 10/11 machine** (not the build box).

### SA-5 One-Click Runtime Check

- [ ] Run `dist\CTR-Rosales-QC-Setup-v1.0.0.exe` — install completes without admin prompt.
- [ ] Double-click the Desktop shortcut — the default browser opens to `http://127.0.0.1:<port>/`.
- [ ] The homepage loads (Vue SPA renders the upload form).
- [ ] Upload the 493-page `CTR-PLC01-FR001 Recepción de Materiales en Obra.pdf`.
- [ ] The review table renders — run completes without errors.
- [ ] Navigate to `/historial` in the browser — the history page loads (proves SPA history-mode fallback).
- [ ] Close the launcher window — the process exits cleanly (Task Manager shows no zombie `ctr-rosales-qc.exe`).

### Real-Data Acceptance (R8 + R9)

Run a reconciliation with the production PDF and verify:

- [ ] **R8** — Registro N° 232, material `BARRA A615 G60 1/2" 9M`, unit `TN`:
  declared quantity = **4.124**, guía sum = **4.124** → status = **MATCH**.
- [ ] **R9** — At least one guía has `requires_review = true` due to date divergence
  (misfiled guía whose handwritten date differs from the Protocolo reception date by day-month).

### Air-Gap Check

- [ ] Disconnect the network after the first successful run (or use Windows Firewall to block outbound).
- [ ] Run a second reconciliation with the same PDF — completes using the SUNAT cache.
- [ ] Confirm no model download logs in `%LOCALAPPDATA%\ctr-rosales-qc\launcher.log`
  (models load from the bundle `_MEIPASS`, not from the internet).

---

## Troubleshooting (Risk Register §4)

| Risk | Symptom | Fix |
|---|---|---|
| **R1** — `pyzbar` can't find `libzbar` DLL | `OSError: Unable to find zbar shared library` or QR decode returns empty on all guías | Verify `libzbar-64.dll` exists in `dist\ctr-rosales-qc\`. If missing: ensure the `pyzbar` wheel for Windows is installed in the build venv (it ships the DLL); update the spec's `pyzbar_binaries = collect_dynamic_libs("pyzbar")` line. Fallback: `zxingcpp` still provides QR decode — check if guía QR codes read correctly. |
| **R2** — `onnxruntime` DLL/provider not found | `onnxruntime.capi.onnxruntime_pybind11_state.InvalidGraph` or `Failed to load library onnxruntime.dll` | Verify `onnxruntime*.dll` files are in `dist\ctr-rosales-qc\`. Rebuild with latest `onnxruntime` wheel. Check `collect_dynamic_libs("onnxruntime")` output during build. |
| **R3** — RapidOCR models not found frozen | Launcher shows "RapidOCR model/dict missing" error dialog at startup | Verify `dist\ctr-rosales-qc\rapidocr\models\` contains the 5 required files (`ch_PP-OCRv5_det_server.onnx`, `ch_PP-OCRv5_rec_server.onnx`, `ch_ppocr_mobile_v2.0_cls_mobile.onnx`, `ppocr_keys_v1.txt`, `ppocrv5_dict.txt`). If missing: confirm `rapidocr` is installed in the build venv and `collect_data_files("rapidocr", ...)` is not filtering them out. |
| **R4** — PyMuPDF (`fitz`) frozen import fails | `ModuleNotFoundError: No module named 'fitz'` or `ImportError: DLL load failed` during PDF upload | Verify `fitz` and `pymupdf` directories exist in the bundle. Check `collect_all("fitz")` output. Ensure the `pymupdf` wheel version matches the Python 3.12 ABI. |
| **R5** — SPA fallback swallows `/api` 404s | An unknown API endpoint (`/api/v1/xyz`) returns `text/html` instead of `{"detail": "Not Found"}` | This is locked by TDD test #4 in `backend/tests/unit/infrastructure/api/test_spa_serving.py`. If it regresses, check `spa.py` catch-all handler for the `/api/` prefix guard. |
| **R6** — Antivirus flags unsigned `.exe` | Windows SmartScreen "Unknown publisher" warning on first run; some AV tools quarantine the file | Expected for unsigned executables. Instruct the user to click "More info → Run anyway". Long-term fix: code-sign the `.exe` with an EV certificate (deferred). |

---

## Launcher Log Location

All launcher activity (port selection, startup, browser open, errors) is written to:

```
%LOCALAPPDATA%\ctr-rosales-qc\launcher.log
```

In PowerShell:

```powershell
notepad "$env:LOCALAPPDATA\ctr-rosales-qc\launcher.log"
```

---

## Data Directories (not removed on uninstall)

The uninstaller intentionally leaves user data in place:

| Directory | Content |
|---|---|
| `%LOCALAPPDATA%\ctr-rosales-qc\runs\` | Per-run output (reconciliation results, exports) |
| `%LOCALAPPDATA%\ctr-rosales-qc\sunat-cache\` | Cached SUNAT GRE responses (air-gap re-runs) |
| `%LOCALAPPDATA%\ctr-rosales-qc\secrets\` | Optional vision API key (if the operator enables vision) |

To fully remove all data after uninstalling:

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\ctr-rosales-qc"
```
