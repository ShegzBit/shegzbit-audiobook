#Requires -Version 5.1
<#
.SYNOPSIS
  Set up Web Novel TTS on Windows for source installs (edge-tts backend).
.DESCRIPTION
  1. Checks Python >= 3.11 and ffmpeg/ffprobe on PATH.
  2. Creates .venv, installs the project + Playwright Chromium.
  3. Prints how to run with TTS_BACKEND=edge (no local 5001 server needed).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
#>

$ErrorActionPreference = "Stop"

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Info($msg) { Write-Host $msg -ForegroundColor Cyan }

# --- 1. Must run from the project root (main.py lives here) ---
if (-not (Test-Path ".\main.py")) {
  Fail "Run this script from the project root (the folder containing main.py)."
}

# --- 2. Python >= 3.11 ---
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $pyCmd = "py -3" } else { $pyCmd = "python" }
try {
  $verOut = Invoke-Expression "$pyCmd --version" 2>&1
} catch { Fail "Python not found. Install Python 3.11+ via: winget install Python.Python.3.12" }
if ($verOut -notmatch "Python 3\.(\d+)") { Fail "Could not parse Python version from: $verOut" }
if ([int]$Matches[1] -lt 11) { Fail "Python 3.11+ required (found $verOut)." }
Info "Found $verOut"

# --- 3. ffmpeg + ffprobe (required by synthesizer.py / pydub) ---
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg -or -not $ffprobe) {
  Write-Host "ffmpeg/ffprobe not on PATH. Install with:" -ForegroundColor Yellow
  Write-Host '  winget install Gyan.FFmpeg' -ForegroundColor Yellow
  Write-Host "Then open a NEW terminal (PATH refresh) and re-run this script."
  Fail "Missing ffmpeg/ffprobe."
}
Info "Found ffmpeg + ffprobe."

# --- 4. venv ---
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
  Info "Creating .venv ..."
  Invoke-Expression "$pyCmd -m venv .venv"
}
$VenvPy = ".\.venv\Scripts\python.exe"

# --- 5. Install deps (plain uvicorn on Windows; [standard] extras are skipped via marker) ---
Info "Installing project dependencies ..."
& $VenvPy -m pip install --upgrade pip
& $VenvPy -m pip install -e .
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }

# --- 6. Playwright Chromium (used by browser.py with headless flags that are safe on Windows) ---
Info "Installing Playwright Chromium ..."
& $VenvPy -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Fail "playwright install chromium failed." }

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Run the app (edge-tts backend, no local 5001 server needed):"
Write-Host '  $env:TTS_BACKEND="edge"; .\.venv\Scripts\python.exe main.py' -ForegroundColor Green
Write-Host "Then open http://localhost:5000 (allow the firewall prompt)."
Write-Host "Optional: `$env:SHARED_ACCESS_PASSWORD='your-password'` to enable login."
