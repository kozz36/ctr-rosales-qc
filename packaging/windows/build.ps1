<#
.SYNOPSIS
    End-to-end Windows build pipeline for CTR Rosales QC native installer.

.DESCRIPTION
    Runs the full build sequence on a Windows build box:
      1. Frontend: npm ci + build with VITE_API_BASE_URL=""
      2. Backend:  Python 3.12 venv + pip install .[identity,ocr]
      3. PyInstaller: bundle into dist\ctr-rosales-qc\
      4. Inno Setup: package into dist\CTR-Rosales-QC-Setup-v1.0.0.exe

    Design contract: docs/WINDOWS-INSTALLER.md §2.6

.PARAMETER SkipFrontend
    Skip the frontend npm build (use when frontend/dist already exists).

.PARAMETER SkipVenv
    Skip venv creation and pip install (use when .venv already prepared).

.PARAMETER SkipPyInstaller
    Skip the PyInstaller step (use when dist\ctr-rosales-qc already built).

.EXAMPLE
    # Full build from scratch (run this on first build or clean):
    .\packaging\windows\build.ps1

    # Rebuild only the installer after a PyInstaller run:
    .\packaging\windows\build.ps1 -SkipFrontend -SkipVenv -SkipPyInstaller

.NOTES
    Prerequisites (see README.md for install instructions):
      - Python 3.12 from python.org (with the py launcher)
      - Node.js LTS (for npm)
      - Inno Setup 6 (iscc.exe must be on PATH)
      - Visual C++ Redistributable 2022 (for onnxruntime / OpenCV DLLs)

    Run from the REPOSITORY ROOT, not from packaging\windows\.
    This script uses $PSScriptRoot to resolve all paths.
#>

[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$SkipVenv,
    [switch]$SkipPyInstaller
)

# ---------------------------------------------------------------------------
# Error handling: stop on first failure.
# ---------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Derived paths (all relative to repo root, resolved from $PSScriptRoot).
# $PSScriptRoot = packaging\windows\ → go up 2 levels → repo root.
# ---------------------------------------------------------------------------
$RepoRoot    = (Resolve-Path (Join-Path $PSScriptRoot ".." "..")).Path
$FrontendDir = Join-Path $RepoRoot "frontend"
$BackendDir  = Join-Path $RepoRoot "backend"
$PackagingDir = Join-Path $RepoRoot "packaging" "windows"
$DistDir     = Join-Path $RepoRoot "dist"
$VenvDir     = Join-Path $BackendDir ".venv"
$SpecFile    = Join-Path $PackagingDir "ctr-rosales-qc.spec"
$IssFile     = Join-Path $PackagingDir "installer.iss"

Write-Host ""
Write-Host "============================================================"
Write-Host " CTR Rosales QC — Windows build pipeline"
Write-Host "============================================================"
Write-Host " Repo root:    $RepoRoot"
Write-Host " Frontend dir: $FrontendDir"
Write-Host " Backend dir:  $BackendDir"
Write-Host " Output dir:   $DistDir"
Write-Host "============================================================"
Write-Host ""

# ---------------------------------------------------------------------------
# Helper: check that a path exists (file or directory)
# ---------------------------------------------------------------------------
function Assert-PathExists {
    param(
        [string]$Path,
        [string]$Description
    )
    if (-not (Test-Path $Path)) {
        Write-Error "MISSING: $Description`n  Expected at: $Path"
        exit 1
    }
    Write-Host "  [OK] $Description"
}

# ---------------------------------------------------------------------------
# Step 0: Verify build prerequisites
# ---------------------------------------------------------------------------
Write-Host "[Step 0] Checking prerequisites..."

# py launcher (Python 3.12)
try {
    $pyVersion = & py -3.12 --version 2>&1
    Write-Host "  [OK] Python: $pyVersion"
} catch {
    Write-Error "Python 3.12 not found. Install from https://python.org (check 'Add py.exe to PATH')."
    exit 1
}

# node / npm
try {
    $nodeVersion = & node --version 2>&1
    $npmVersion  = & npm  --version 2>&1
    Write-Host "  [OK] Node: $nodeVersion  npm: $npmVersion"
} catch {
    Write-Error "Node.js not found. Install LTS from https://nodejs.org."
    exit 1
}

# iscc (Inno Setup compiler)
try {
    $isccVersion = & iscc /? 2>&1 | Select-Object -First 1
    Write-Host "  [OK] Inno Setup: $isccVersion"
} catch {
    Write-Error "iscc not found on PATH. Install Inno Setup 6 from https://jrsoftware.org/isinfo.php and add to PATH."
    exit 1
}

Write-Host ""

# ---------------------------------------------------------------------------
# Step 1: Frontend build
# ---------------------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Host "[Step 1] Building frontend SPA (VITE_API_BASE_URL='')..."

    Push-Location $FrontendDir
    try {
        Write-Host "  Running: npm ci"
        & npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed (exit code $LASTEXITCODE)" }

        Write-Host "  Running: npm run build (VITE_API_BASE_URL='')"
        # Empty string → relative /api/v1 prefix (NOT bare / which yields //api/v1).
        # See docs/WINDOWS-INSTALLER.md §2.1 for the same-origin SPA rationale.
        $env:VITE_API_BASE_URL = ""
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "npm run build failed (exit code $LASTEXITCODE)" }
    } finally {
        Pop-Location
        Remove-Item Env:\VITE_API_BASE_URL -ErrorAction SilentlyContinue
    }

    # Verify output exists
    Assert-PathExists (Join-Path $FrontendDir "dist" "index.html") "frontend/dist/index.html"
    Assert-PathExists (Join-Path $FrontendDir "dist" "assets")     "frontend/dist/assets/"
    Write-Host "  Frontend build complete."
    Write-Host ""
} else {
    Write-Host "[Step 1] SKIPPED (frontend build)"
    Assert-PathExists (Join-Path $FrontendDir "dist" "index.html") "frontend/dist/index.html (pre-existing)"
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Step 2: Backend venv + pip install .[identity,ocr]
# ---------------------------------------------------------------------------
if (-not $SkipVenv) {
    Write-Host "[Step 2] Creating backend venv and installing dependencies..."

    Push-Location $BackendDir
    try {
        # Create a fresh venv using Python 3.12
        if (Test-Path $VenvDir) {
            Write-Host "  Removing existing venv at $VenvDir"
            Remove-Item -Recurse -Force $VenvDir
        }

        Write-Host "  Running: py -3.12 -m venv .venv"
        & py -3.12 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed (exit code $LASTEXITCODE)" }

        $PipExe = Join-Path $VenvDir "Scripts" "pip.exe"
        $PythonExe = Join-Path $VenvDir "Scripts" "python.exe"

        # Upgrade pip first to avoid stale resolver issues
        Write-Host "  Upgrading pip..."
        & $PipExe install --upgrade pip
        if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit code $LASTEXITCODE)" }

        # Install extras: identity (pyzbar, zxingcpp, Pillow, numpy)
        #                 ocr (rapidocr, onnxruntime, opencv-headless, Pillow, numpy)
        # DO NOT install: llm (anthropic/openai) or ml (paddle) — vision-off profile.
        Write-Host "  Running: pip install '.[identity,ocr]'"
        & $PipExe install ".[identity,ocr]"
        if ($LASTEXITCODE -ne 0) { throw "pip install failed (exit code $LASTEXITCODE)" }

        # Also install pyinstaller inside the venv so the spec runs in the right environment
        Write-Host "  Installing PyInstaller..."
        & $PipExe install pyinstaller
        if ($LASTEXITCODE -ne 0) { throw "pyinstaller install failed (exit code $LASTEXITCODE)" }

    } finally {
        Pop-Location
    }

    Assert-PathExists $VenvDir              "backend/.venv"
    Assert-PathExists (Join-Path $VenvDir "Scripts" "pip.exe") "backend/.venv/Scripts/pip.exe"
    Write-Host "  Backend venv ready."
    Write-Host ""
} else {
    Write-Host "[Step 2] SKIPPED (venv setup)"
    Assert-PathExists $VenvDir "backend/.venv (pre-existing)"
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Step 3: PyInstaller one-dir bundle
# ---------------------------------------------------------------------------
if (-not $SkipPyInstaller) {
    Write-Host "[Step 3] Running PyInstaller..."

    $PyInstallerExe = Join-Path $VenvDir "Scripts" "pyinstaller.exe"
    Assert-PathExists $PyInstallerExe "backend/.venv/Scripts/pyinstaller.exe"

    # Run from repo root so pathex=['backend/src'] resolves correctly.
    Push-Location $RepoRoot
    try {
        Write-Host "  Running: pyinstaller $SpecFile"
        & $PyInstallerExe $SpecFile --noconfirm
        if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed (exit code $LASTEXITCODE)" }
    } finally {
        Pop-Location
    }

    $BundleDir = Join-Path $DistDir "ctr-rosales-qc"
    Assert-PathExists $BundleDir                                        "dist/ctr-rosales-qc/ (bundle dir)"
    Assert-PathExists (Join-Path $BundleDir "ctr-rosales-qc.exe")       "dist/ctr-rosales-qc/ctr-rosales-qc.exe"
    Assert-PathExists (Join-Path $BundleDir "frontend" "dist" "index.html") "dist/ctr-rosales-qc/frontend/dist/index.html"
    Assert-PathExists (Join-Path $BundleDir "config.yaml")              "dist/ctr-rosales-qc/config.yaml"
    Write-Host "  PyInstaller bundle complete: $BundleDir"
    Write-Host ""
} else {
    Write-Host "[Step 3] SKIPPED (PyInstaller)"
    $BundleDir = Join-Path $DistDir "ctr-rosales-qc"
    Assert-PathExists $BundleDir "dist/ctr-rosales-qc/ (pre-existing)"
    Write-Host ""
}

# ---------------------------------------------------------------------------
# Step 4: Inno Setup — package into .exe installer
# ---------------------------------------------------------------------------
Write-Host "[Step 4] Running Inno Setup compiler..."

Assert-PathExists $IssFile "packaging/windows/installer.iss"

# iscc resolves {#MyBundleDir} relative to the .iss file location.
# The .iss file uses: ..\..\dist\ctr-rosales-qc  (from packaging\windows\)
# which resolves to:  <repo>\dist\ctr-rosales-qc — correct.
Write-Host "  Running: iscc $IssFile"
& iscc $IssFile
if ($LASTEXITCODE -ne 0) { throw "iscc (Inno Setup) failed (exit code $LASTEXITCODE)" }

$SetupExe = Join-Path $DistDir "CTR-Rosales-QC-Setup-v1.0.0.exe"
Assert-PathExists $SetupExe "dist/CTR-Rosales-QC-Setup-v1.0.0.exe"

Write-Host ""
Write-Host "============================================================"
Write-Host " BUILD COMPLETE"
Write-Host "============================================================"
Write-Host " Installer: $SetupExe"
Write-Host ""
Write-Host " Next steps (from docs/WINDOWS-INSTALLER.md §3 validation):"
Write-Host "   1. Run the .exe on a clean Windows 10/11 machine"
Write-Host "   2. Double-click the Desktop shortcut"
Write-Host "   3. Upload the 493-page PDF — review table must render"
Write-Host "   4. Navigate to /historial — SPA fallback must work"
Write-Host "   5. Run a reconciliation: R8 232=4.124 TN MATCH, R9 divergence flagged"
Write-Host "   6. Confirm no model download at first run (air-gap)"
Write-Host "============================================================"
