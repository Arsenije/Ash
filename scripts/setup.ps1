# Ash one-command setup for Windows (PowerShell).
# Downloads the local model runtime, creates the Python sidecar, installs the
# Electron dependencies, then launches the app. Safe to re-run.
#
# Usage:  npm run setup:win   (or: powershell -ExecutionPolicy Bypass -File scripts/setup.ps1)
# Set $env:ASH_NO_LAUNCH=1 to set up without launching.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Say($msg) { Write-Host "> $msg" -ForegroundColor Cyan }

# --- base prerequisites ----------------------------------------------------
if (-not (Have node)) { throw "Node 18+ is required - install it from https://nodejs.org, then re-run." }

if (-not (Have uv)) {
  Say "Installing uv (Python toolchain)..."
  irm https://astral.sh/uv/install.ps1 | iex
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# --- 1. local model runtime (downloaded for this OS/arch) ------------------
Say "Fetching the model runtime (llama.cpp + llama-swap)..."
node scripts/fetch-binaries.mjs

# --- 2. python sidecar (khora + FastAPI) -----------------------------------
if (-not (Test-Path "sidecar\.venv\Scripts\python.exe")) {
  Say "Creating the Python 3.13 sidecar environment..."
  uv venv --python 3.13 sidecar\.venv
  uv pip install --python sidecar\.venv\Scripts\python.exe `
    "khora[embedded]" fastapi "uvicorn[standard]" openai httpx pillow pillow-heif python-multipart
}

# --- 3. electron dependencies ----------------------------------------------
if (-not (Test-Path "node_modules")) {
  Say "Installing Electron dependencies..."
  npm install
}

Say "Setup complete."
if ($env:ASH_NO_LAUNCH -ne "1") {
  Say "Launching Ash... (next time, just run: npm start)"
  npm start
}
