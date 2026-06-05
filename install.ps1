#Requires -Version 5.1
<#
.SYNOPSIS
    One-command installer for CTR Rosales QC (v1.0.0) — Windows entry point.

.DESCRIPTION
    Checks prerequisites, builds backend + frontend images from source, and starts
    the stack via Docker Compose in deterministic mode (vision-off + SUNAT-authoritative).
    Uses the same docker-compose.app.yml as install.sh — the Docker images are identical
    across platforms because Docker Desktop on Windows runs Linux containers.

.PARAMETER Stop
    Stop the running app (docker compose down).

.PARAMETER Logs
    Follow live logs from all containers.

.EXAMPLE
    .\install.ps1            # Build + start the app
    .\install.ps1 -Stop      # Stop the app
    .\install.ps1 -Logs      # Follow live logs
#>

[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Logs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ComposeFile  = "docker-compose.app.yml"
$FrontendUrl  = "http://localhost:5173"
$BackendUrl   = "http://localhost:8000"
$SunatHost    = "https://e-factura.sunat.gob.pe"
$MaxWaitSec   = 60   # seconds to wait for backend readiness
$PollInterval = 3    # seconds between readiness probes

# ── Colour helpers ────────────────────────────────────────────────────────────
function Write-Green  { param([string]$Msg) Write-Host $Msg -ForegroundColor Green }
function Write-Yellow { param([string]$Msg) Write-Host $Msg -ForegroundColor Yellow }
function Write-Red    { param([string]$Msg) Write-Host $Msg -ForegroundColor Red }
function Write-Bold   { param([string]$Msg) Write-Host $Msg -ForegroundColor White }

# Change to the directory that contains this script so relative paths work
# regardless of where the caller's working directory is.
Set-Location -Path $PSScriptRoot

function Invoke-Compose {
    docker compose -f $ComposeFile @args
}

# ── Sub-commands ──────────────────────────────────────────────────────────────
if ($Stop) {
    Write-Bold "Stopping CTR Rosales QC..."
    Invoke-Compose down
    Write-Green "Stopped."
    exit 0
}

if ($Logs) {
    Invoke-Compose logs -f
    exit 0
}

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Bold "==================================================="
Write-Bold "  CTR Rosales QC -- Installer v1.0.0"
Write-Bold "==================================================="
Write-Host ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
Write-Bold "1) Checking prerequisites..."

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Red "  [x] Docker is not installed or not on PATH."
    Write-Host "      Install Docker Desktop from https://docs.docker.com/desktop/install/windows-install/"
    Write-Host "      and re-run this script."
    exit 1
}

$composeCheck = docker compose version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Red "  [x] Docker Compose v2 is not available ('docker compose' sub-command required)."
    Write-Host "      Update Docker Desktop to a recent version that includes Compose v2."
    exit 1
}

$daemonCheck = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Red "  [x] Docker daemon is not running."
    Write-Host "      Open Docker Desktop, wait for 'Engine running', then retry."
    exit 1
}

Write-Green "  [+] Docker and Docker Compose are available."

# ── 2. Non-blocking environment checks ───────────────────────────────────────
Write-Bold "2) Checking environment..."

foreach ($port in @(5173, 8000)) {
    $inUse = netstat -ano 2>$null | Select-String ":$port\s"
    if ($inUse) {
        Write-Yellow "  [!] Port $port appears to be in use. If the app does not open, free the port and retry."
    }
}

try {
    $resp = Invoke-WebRequest -Uri $SunatHost -Method Head -TimeoutSec 8 -UseBasicParsing -ErrorAction Stop
    Write-Green "  [+] SUNAT is reachable (deterministic mode uses SUNAT for materials and dates)."
} catch {
    Write-Yellow "  [!] Could not reach SUNAT right now. The app will start; SUNAT queries will"
    Write-Yellow "      succeed when connectivity is restored and results will be cached."
}

# ── 3. Fresh build ────────────────────────────────────────────────────────────
Write-Host ""
Write-Bold "3) Building images (fresh)..."
Write-Yellow "   The first build may take several minutes."
Invoke-Compose build
if ($LASTEXITCODE -ne 0) {
    Write-Red "Build failed. Check the output above for errors."
    exit 1
}

# ── 4. Start ──────────────────────────────────────────────────────────────────
Write-Host ""
Write-Bold "4) Starting the application..."
Invoke-Compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Red "Failed to start containers. Run '.\install.ps1 -Logs' to inspect."
    exit 1
}

# ── 5. Readiness wait ─────────────────────────────────────────────────────────
Write-Host ""
Write-Bold "5) Waiting for backend to be ready..."

$elapsed   = 0
$ready     = $false
$probeUrl  = "$BackendUrl/api/v1/runs/"

while ($elapsed -lt $MaxWaitSec) {
    try {
        $probe = Invoke-WebRequest -Uri $probeUrl -TimeoutSec 4 -UseBasicParsing -ErrorAction Stop
        $ready = $true
        break
    } catch {
        Start-Sleep -Seconds $PollInterval
        $elapsed += $PollInterval
    }
}

if ($ready) {
    Write-Green "  [+] Backend is ready."
} else {
    Write-Yellow "  [!] Backend did not respond within $MaxWaitSec seconds."
    Write-Yellow "      Check logs with: .\install.ps1 -Logs"
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Green "==================================================="
Write-Green "  [+] CTR Rosales QC is running."
Write-Green "==================================================="
Write-Host ""
Write-Bold  "  Open the app at:  $FrontendUrl"
Write-Host  "  Backend API:      $BackendUrl"
Write-Host  ""
Write-Host  "  Stop:   .\install.ps1 -Stop"
Write-Host  "  Logs:   .\install.ps1 -Logs"
Write-Host  ""

# Open the browser automatically.
Start-Process $FrontendUrl
