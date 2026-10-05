#!/usr/bin/env bash
# Run the ECOSYSTEM desktop application in development.
#
# This launches the Tauri shell against the Vite dev server, with the bundled
# backend sidecar (if staged) or the backend virtualenv. Hot reload works for
# the React frontend; Rust changes trigger a rebuild.
#
# Usage: scripts/desktop-dev.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="${REPO_ROOT}/frontend"
BACKEND="${REPO_ROOT}/backend"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v cargo >/dev/null 2>&1 || {
  # shellcheck disable=SC1091
  [[ -f "$HOME/.cargo/env" ]] && . "$HOME/.cargo/env"
}
command -v cargo >/dev/null 2>&1 || die "cargo not found. Install Rust: https://rustup.rs"
command -v npm >/dev/null 2>&1 || die "npm not found."
[[ -d "${FRONTEND}/node_modules" ]] || die "frontend deps missing: (cd frontend && npm install)"

SIDECAR="${FRONTEND}/src-tauri/binaries/ecosystem-backend-x86_64-unknown-linux-gnu"
if [[ ! -x "${SIDECAR}" ]]; then
  say "Sidecar not staged; the shell will use ECOSYSTEM_BACKEND_BIN if set."
  say "Build it once with: scripts/desktop-build.sh --skip-sidecar (after a sidecar build)"
fi

# In development we point the shell at the source backend so Python changes are
# picked up without rebuilding the sidecar.
export ECOSYSTEM_BACKEND_BIN="${ECOSYSTEM_BACKEND_BIN:-${BACKEND}/.venv/bin/ecosystem-backend}"
if [[ ! -x "${ECOSYSTEM_BACKEND_BIN}" ]]; then
  say "Installing the backend console script into the venv"
  (cd "${BACKEND}" && .venv/bin/pip install -e . >/dev/null)
fi

say "Launching the desktop shell in development"
cd "${FRONTEND}"
npm run tauri dev
