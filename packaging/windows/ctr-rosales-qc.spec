# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller ONE-DIR spec for CTR Rosales QC
#
# Build (from repo root on Windows):
#     pyinstaller packaging\windows\ctr-rosales-qc.spec
#
# Output: dist\ctr-rosales-qc\   (the one-dir bundle)
#
# IMPORTANT — consumed on a Windows build box.  Do NOT run pyinstaller on Linux.
#
# Design contract: docs/WINDOWS-INSTALLER.md §2.4
#
# Key decisions:
#   - ONE-DIR (not one-file): avoids per-launch temp extraction, faster startup.
#   - windowed=True: no console window (pythonw equivalent).
#   - pathex includes backend/src so the reconciliation package resolves.
#   - RapidOCR models bundled at rapidocr/models/ to match
#     Path(rapidocr.__file__).parent / "models" resolution at runtime.
#   - pyzbar + zxingcpp BOTH included for maximum QR recall (EXT-012).
#   - onnxruntime DLLs collected via collect_dynamic_libs for R2 mitigation.
#   - fitz (PyMuPDF) collected in full to avoid R4 frozen import failures.
#
# Risks mitigated:
#   R1 — pyzbar libzbar DLL: collect_dynamic_libs("pyzbar") bundles it.
#   R2 — onnxruntime provider DLLs: collect_dynamic_libs + collect_all.
#   R3 — RapidOCR models: collect_data_files at package-relative path.
#   R4 — PyMuPDF frozen: collect_all("fitz").

import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_all

# ---------------------------------------------------------------------------
# Repository root (where this spec is located: packaging/windows/
#   → two levels up → repo root)
# ---------------------------------------------------------------------------
SPEC_DIR = Path(SPECPATH)  # SPECPATH is the directory containing this .spec file
REPO_ROOT = SPEC_DIR.parent.parent

# ---------------------------------------------------------------------------
# Collect package data
# ---------------------------------------------------------------------------

# --- RapidOCR: bundle the full data tree so models land at rapidocr/models/
# under _MEIPASS (matching Path(rapidocr.__file__).parent / "models").
# includes pattern covers .onnx (models), .txt (dicts), .yaml (configs). ---
rapidocr_datas = collect_data_files(
    "rapidocr",
    includes=["**/*.onnx", "**/*.txt", "**/*.yaml"],
)

# --- onnxruntime: DLLs and metadata ---
# collect_all returns (datas, binaries, hiddenimports).
# collect_dynamic_libs supplements with provider DLLs that collect_all may miss.
onnxruntime_datas, onnxruntime_binaries_extra, onnxruntime_hidden = collect_all("onnxruntime")
onnxruntime_binaries = collect_dynamic_libs("onnxruntime") + onnxruntime_binaries_extra

# --- fitz (PyMuPDF): collect all to prevent frozen import failures (R4) ---
fitz_datas, fitz_binaries, fitz_hidden = collect_all("fitz")

# --- cv2 (opencv-python-headless): DLLs + data ---
cv2_datas, cv2_binaries, cv2_hidden = collect_all("cv2")

# --- rapidocr hidden imports ---
rapidocr_datas_all, rapidocr_binaries, rapidocr_hidden = collect_all("rapidocr")

# --- polars: data files (native extensions embedded, but metadata needed) ---
polars_datas = collect_data_files("polars")

# --- pyzbar: libzbar DLL (Windows needs the external shared library) ---
# The pyzbar wheel ships the DLL inside the package directory on Windows.
# collect_dynamic_libs will pick up libzbar-64.dll / zbar.dll from the package.
pyzbar_binaries = collect_dynamic_libs("pyzbar")
# Also grab pyzbar's own DLL that ships inside the wheel (libzbar.dll on some builds)
pyzbar_datas = collect_data_files("pyzbar")

# ---------------------------------------------------------------------------
# Frontend SPA (built with VITE_API_BASE_URL="" for relative /api/v1 prefix)
# ---------------------------------------------------------------------------
# The build.ps1 script runs `npm run build` with VITE_API_BASE_URL=""
# BEFORE calling pyinstaller.  The frontend/dist/ directory must exist.
frontend_dist = str(REPO_ROOT / "frontend" / "dist")

# ---------------------------------------------------------------------------
# All collected datas and binaries
# ---------------------------------------------------------------------------
all_datas = (
    rapidocr_datas
    + rapidocr_datas_all
    + onnxruntime_datas
    + fitz_datas
    + cv2_datas
    + polars_datas
    + pyzbar_datas
    # Frontend SPA: bundle at frontend/dist so launcher.py's resource_path("frontend/dist") works.
    + [(frontend_dist, "frontend/dist")]
    # config.yaml: bundled at root of _MEIPASS so resource_path("config.yaml") works.
    + [(str(REPO_ROOT / "packaging" / "windows" / "config.yaml"), ".")]
)

all_binaries = (
    onnxruntime_binaries
    + fitz_binaries
    + cv2_binaries
    + rapidocr_binaries
    + pyzbar_binaries
)

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# Uvicorn sub-modules that PyInstaller misses due to dynamic imports:
uvicorn_hidden = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.loops.uvloop",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.off",
    "uvicorn.lifespan.on",
    "uvicorn.middleware.proxy_headers",
]

# FastAPI / starlette sub-modules
fastapi_hidden = [
    "fastapi",
    "fastapi.middleware",
    "fastapi.middleware.cors",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.staticfiles",
    "starlette.responses",
    "starlette.routing",
]

# pydantic / pydantic-settings
pydantic_hidden = [
    "pydantic",
    "pydantic_settings",
    "pydantic_core",
]

# Reconciliation package sub-modules (needed since backend/src is in pathex, not installed)
reconciliation_hidden = [
    "reconciliation",
    "reconciliation.infrastructure",
    "reconciliation.infrastructure.api",
    "reconciliation.infrastructure.api.main",
    "reconciliation.infrastructure.api.routes",
    "reconciliation.infrastructure.api.spa",
    "reconciliation.application",
    "reconciliation.application.config",
    "reconciliation.application.pipeline",
    "reconciliation.domain",
    "reconciliation.adapters",
    "reconciliation.adapters.ocr",
    "reconciliation.adapters.vision",
]

all_hidden = (
    uvicorn_hidden
    + fastapi_hidden
    + pydantic_hidden
    + reconciliation_hidden
    + onnxruntime_hidden
    + fitz_hidden
    + cv2_hidden
    + rapidocr_hidden
    + [
        # QR decoders
        "pyzbar",
        "pyzbar.pyzbar",
        "zxingcpp",
        # Data processing
        "polars",
        "openpyxl",
        "openpyxl.cell._writer",
        "numpy",
        "PIL",
        "PIL.Image",
        # PDF
        "fitz",
        "pymupdf",
        # Config / YAML
        "yaml",
        "pyyaml",
        # Stdlib that PyInstaller sometimes misses
        "multiprocessing",
        "multiprocessing.pool",
        "email.mime",
        "email.mime.multipart",
        "email.mime.text",
        # Logging (used by uvicorn)
        "logging.handlers",
        # tkinter (quit window in launcher.py)
        "tkinter",
        "tkinter.messagebox",
    ]
)

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    # Script to bundle: the launcher entrypoint
    [str(REPO_ROOT / "packaging" / "windows" / "launcher.py")],

    # pathex: tell PyInstaller where to find the reconciliation package.
    # backend/src is the package root (src layout; package = reconciliation).
    pathex=[str(REPO_ROOT / "backend" / "src")],

    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hidden,

    # hookspath: if you have custom hooks, place them here.
    hookspath=[],

    # hooksconfig: optional hook configuration overrides.
    hooksconfig={},

    # runtime_hooks: scripts run before the frozen app starts.
    runtime_hooks=[],

    # excludes: exclude heavy packages that are NOT needed in the bundle.
    # ml/paddle extras are explicitly excluded (not installed by build.ps1).
    excludes=[
        "paddleocr",
        "paddlepaddle",
        "paddle",
        "anthropic",
        "openai",
        "torch",
        "torchvision",
        "tensorflow",
        "matplotlib",
        "scipy",
        "sklearn",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
    ],

    # noarchive=False: collect bytecode into the archive (smaller bundle).
    noarchive=False,

    # optimize=1: mild bytecode optimization (-O flag; removes assert statements).
    optimize=1,
)

# ---------------------------------------------------------------------------
# PYZ archive (Python bytecode)
# ---------------------------------------------------------------------------
pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# EXE (the launcher executable)
# ---------------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # ONE-DIR mode: binaries collected separately in COLLECT

    name="ctr-rosales-qc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,             # do NOT strip on Windows (breaks some DLLs)
    upx=False,               # UPX disabled: can trigger antivirus false positives (risk R6)
    console=False,           # windowed=True: no console window (pythonw equivalent)
    disable_windowed_traceback=False,

    # argv_emulation: Windows only, not relevant here.
    argv_emulation=False,

    # target_arch: None = matches the host build machine (x86_64 on a normal Windows box).
    target_arch=None,

    codesign_identity=None,  # Code signing: deferred (risk R6, optional later step)
    entitlements_file=None,

    # icon: optional .ico file; add when a branded icon is available.
    # icon=str(REPO_ROOT / "packaging" / "windows" / "ctr-rosales-qc.ico"),
)

# ---------------------------------------------------------------------------
# COLLECT — one-dir bundle
# ---------------------------------------------------------------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,

    strip=False,
    upx=False,
    upx_exclude=[],

    # Output directory name under dist/
    name="ctr-rosales-qc",
)
