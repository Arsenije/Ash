#!/usr/bin/env bash
#
# Ash one-command setup for macOS and Linux. Downloads the local model runtime
# (llama.cpp + llama-swap), creates the Python sidecar, installs the Electron
# dependencies, then launches the app. Safe to re-run — each step is skipped if
# already done. (Windows: use scripts/setup.ps1.)
#
# Usage:   npm run setup        (or: bash scripts/setup.sh)
# Set ASH_NO_LAUNCH=1 to set everything up without starting the app.

set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf "\033[1m▸ %s\033[0m\n" "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }
confirm() {
  [ -t 0 ] || return 1   # non-interactive (CI): decline rather than hang
  local ans
  read -r -p "$1 [Y/n] " ans
  case "${ans:-Y}" in [Yy]*|"") return 0 ;; *) return 1 ;; esac
}

# --- base prerequisites ----------------------------------------------------
have node || { echo "Node 18+ is required — install it from https://nodejs.org, then re-run."; exit 1; }

if ! have uv; then
  if confirm "uv (Python toolchain) isn't installed. Install it now?"; then
    say "Installing uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "Can't continue without uv — see https://docs.astral.sh/uv, then re-run."; exit 1
  fi
fi

# --- 1. local model runtime (downloaded for this OS/arch) ------------------
say "Fetching the model runtime (llama.cpp + llama-swap)…"
node scripts/fetch-binaries.mjs

# --- 2. python sidecar (khora + FastAPI) -----------------------------------
if [ ! -e sidecar/.venv/bin/python ]; then
  say "Creating the Python 3.13 sidecar environment…"
  uv venv --python 3.13 sidecar/.venv
  uv pip install --python sidecar/.venv/bin/python \
    "khora[embedded]" -r sidecar/requirements.txt
fi

# --- 3. electron dependencies ----------------------------------------------
if [ ! -d node_modules ]; then
  say "Installing Electron dependencies…"
  npm install
fi

say "Setup complete."

# --- 4. macOS launcher app (Spotlight / Raycast) ---------------------------
# Drop a tiny Ash.app into ~/Applications so Ash is searchable and clickable.
# Non-fatal: never let a failure here abort setup.
if [ "$(uname)" = "Darwin" ]; then
  if node scripts/make-macos-app.mjs; then
    say "Added Ash to ~/Applications — search \"Ash\" in Spotlight or Raycast."
  fi
fi

if [ "${ASH_NO_LAUNCH:-}" != "1" ]; then
  say "Launching Ash… (next time, just run: npm start)"
  npm start
fi
