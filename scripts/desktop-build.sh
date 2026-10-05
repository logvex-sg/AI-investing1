#!/usr/bin/env bash
# Build the ECOSYSTEM desktop application: backend sidecar + Tauri bundle.
#
# Usage: scripts/desktop-build.sh [--skip-sidecar] [--debug]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="${REPO_ROOT}/backend"
FRONTEND="${REPO_ROOT}/frontend"
TARGET_TRIPLE="x86_64-unknown-linux-gnu"

SKIP_SIDECAR=0
PROFILE="release"
for arg in "$@"; do
  case "$arg" in
    --skip-sidecar) SKIP_SIDECAR=1 ;;
    --debug) PROFILE="debug" ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v cargo >/dev/null 2>&1 || {
  # rustup installs into ~/.cargo/bin which may not be on a non-login PATH.
  # shellcheck disable=SC1091
  [[ -f "$HOME/.cargo/env" ]] && . "$HOME/.cargo/env"
}
command -v cargo >/dev/null 2>&1 || die "cargo not found. Install Rust: https://rustup.rs"
command -v npm >/dev/null 2>&1 || die "npm not found."

[[ -d "${FRONTEND}/node_modules" ]] || die "frontend deps missing: (cd frontend && npm install)"

if [[ "${SKIP_SIDECAR}" -eq 0 ]]; then
  [[ -x "${BACKEND}/.venv/bin/python" ]] || die "backend venv missing: (cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[desktop]')"
  say "Building backend sidecar (PyInstaller)"
  (
    cd "${BACKEND}"
    .venv/bin/pyinstaller --clean --noconfirm ecosystem-backend.spec
  )
  say "Staging sidecar for Tauri"
  mkdir -p "${FRONTEND}/src-tauri/binaries"
  cp "${BACKEND}/dist/ecosystem-backend" \
     "${FRONTEND}/src-tauri/binaries/ecosystem-backend-${TARGET_TRIPLE}"
  chmod +x "${FRONTEND}/src-tauri/binaries/ecosystem-backend-${TARGET_TRIPLE}"
else
  say "Skipping sidecar build (--skip-sidecar)"
  [[ -x "${FRONTEND}/src-tauri/binaries/ecosystem-backend-${TARGET_TRIPLE}" ]] \
    || die "sidecar not staged; run without --skip-sidecar first"
fi

say "Building frontend bundle"
(cd "${FRONTEND}" && npm run build)

say "Building desktop shell (${PROFILE})"
if [[ "${PROFILE}" == "debug" ]]; then
  (cd "${FRONTEND}" && npm run tauri build -- --debug)
else
  (cd "${FRONTEND}" && npm run tauri build)
fi

say "Artifacts:"
ls -1 "${FRONTEND}/src-tauri/target/${PROFILE}/bundle/deb/"*.deb 2>/dev/null || true
ls -1 "${FRONTEND}/src-tauri/target/${PROFILE}/bundle/appimage/"*.AppImage 2>/dev/null || true
