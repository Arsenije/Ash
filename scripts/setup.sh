#!/usr/bin/env bash
#
# Ash one-command setup. Installs the local model runtime (llama.cpp +
# llama-swap), the Python sidecar, and the Electron dependencies, then launches
# the app. Safe to re-run — each step is skipped if it's already done, so on a
# second run this just starts Ash.
#
# Usage:   npm run setup        (or: bash scripts/setup.sh)
# Set ASH_NO_LAUNCH=1 to set everything up without starting the app.

set -euo pipefail
cd "$(dirname "$0")/.."

say()  { printf "\033[1m▸ %s\033[0m\n" "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

# Ask Y/n; default Yes. In a non-interactive shell (CI), decline rather than hang.
confirm() {
  [ -t 0 ] || return 1
  local ans
  read -r -p "$1 [Y/n] " ans
  case "${ans:-Y}" in [Yy]*|"") return 0 ;; *) return 1 ;; esac
}

# Put a freshly-installed Homebrew on PATH for the rest of this run.
ensure_brew_path() {
  if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then eval "$(/usr/local/bin/brew shellenv)"; fi
}

# --- base prerequisites (offer to install what's missing) ------------------
# Homebrew first — Node is installed through it.
if ! have brew; then
  if confirm "Homebrew isn't installed (needed for the model runtime). Install it now?"; then
    say "Installing Homebrew… (it may ask for your password)"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    ensure_brew_path
  else
    echo "Can't continue without Homebrew — install it from https://brew.sh, then re-run."; exit 1
  fi
fi

if ! have node; then
  if confirm "Node isn't installed. Install it with Homebrew now?"; then
    say "Installing Node…"
    brew install node
  else
    echo "Can't continue without Node — install it from https://nodejs.org, then re-run."; exit 1
  fi
fi

if ! have uv; then
  if confirm "uv (Python toolchain) isn't installed. Install it now?"; then
    say "Installing uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "Can't continue without uv — see https://docs.astral.sh/uv, then re-run."; exit 1
  fi
fi

# --- 1. local model runtime ------------------------------------------------
for b in llama.cpp llama-swap; do
  if ! brew list "$b" >/dev/null 2>&1; then
    say "Installing $b…"
    brew install "$b"
  fi
done

# --- 2. python sidecar (khora + FastAPI) -----------------------------------
if [ ! -e sidecar/.venv/bin/python ]; then
  say "Creating the Python 3.13 sidecar environment…"
  uv venv --python 3.13 sidecar/.venv
  uv pip install --python sidecar/.venv/bin/python \
    "khora[embedded]" fastapi "uvicorn[standard]" openai pillow python-multipart
fi

# --- 3. electron dependencies ----------------------------------------------
if [ ! -d node_modules ]; then
  say "Installing Electron dependencies…"
  npm install
fi

say "Setup complete."
if [ "${ASH_NO_LAUNCH:-}" != "1" ]; then
  say "Launching Ash… (next time, just run: npm start)"
  npm start
fi
