#!/usr/bin/env bash
# Install ECOSYSTEM as a native Linux desktop application.
#
# Usage: scripts/install-desktop.sh [--user|--system]
#
#   --user    (default) install into ~/.local, no root required
#   --system  install into /usr/local (needs sudo), appears for all users
#
# The installer places the shell and its sidecar, installs the icon and the
# application-menu entry, and refreshes the desktop caches. Afterwards the app
# launches like any other desktop program.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND="${REPO_ROOT}/frontend"
SCOPE="user"

for arg in "$@"; do
  case "$arg" in
    --user) SCOPE="user" ;;
    --system) SCOPE="system" ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "${SCOPE}" == "system" ]]; then
  PREFIX="/usr/local"
  SUDO="sudo"
else
  PREFIX="${HOME}/.local"
  SUDO=""
fi

APP_DIR="${PREFIX}/lib/ecosystem"
BIN_DIR="${PREFIX}/bin"
ICON_DIR="${PREFIX}/share/icons/hicolor"
DESKTOP_DIR="${PREFIX}/share/applications"
BUNDLE_DIR="${FRONTEND}/src-tauri/target/release/bundle"
BINARY="${FRONTEND}/src-tauri/target/release/ecosystem-desktop"
SIDECAR="${FRONTEND}/src-tauri/binaries/ecosystem-backend-x86_64-unknown-linux-gnu"

install_icon() {
  local size="$1" src="$2"
  [[ -f "$src" ]] || return 0
  ${SUDO} install -Dm644 "$src" "${ICON_DIR}/${size}/apps/ecosystem.png"
}

# Prefer the .deb produced by the bundler: it already contains the shell, the
# sidecar and a desktop entry.
DEB=""
if [[ -d "${BUNDLE_DIR}/deb" ]]; then
  DEB=$(find "${BUNDLE_DIR}/deb" -maxdepth 1 -name '*.deb' | head -1 || true)
fi

if [[ -n "${DEB}" && "${SCOPE}" == "system" ]]; then
  say "Installing package ${DEB} system-wide"
  sudo apt-get install -y "${DEB}"
elif [[ -n "${DEB}" ]]; then
  say "Extracting ${DEB} into ${PREFIX}"
  TMP="$(mktemp -d)"
  trap 'rm -rf "${TMP}"' EXIT
  dpkg-deb -x "${DEB}" "${TMP}"

  main_bin="$(find "${TMP}/usr/bin" -maxdepth 1 -type f -name 'ecosystem*' | head -1)"
  [[ -n "${main_bin}" ]] || die "no executable found inside ${DEB}"
  ${SUDO} install -Dm755 "${main_bin}" "${BIN_DIR}/ecosystem-desktop"
  ${SUDO} install -d "${APP_DIR}"
  for f in "${TMP}/usr/bin/"*; do
    base="$(basename "$f")"
    [[ "${base}" == "ecosystem-desktop" ]] && continue
    ${SUDO} install -Dm755 "$f" "${APP_DIR}/${base}"
  done

  for size in 32x32 128x128 256x256; do
    install_icon "$size" "${TMP}/usr/share/icons/hicolor/${size}/apps/ECOSYSTEM.png"
  done

  src_desktop="$(find "${TMP}/usr/share/applications" -name '*.desktop' | head -1 || true)"
  if [[ -n "${src_desktop}" ]]; then
    ${SUDO} install -Dm644 "${src_desktop}" "${DESKTOP_DIR}/ecosystem.desktop"
  fi
else
  [[ -x "${BINARY}" ]] || die "no built application found. Run scripts/desktop-build.sh first."
  say "Installing raw release binary into ${PREFIX}"
  ${SUDO} install -Dm755 "${BINARY}" "${BIN_DIR}/ecosystem-desktop"
  ${SUDO} install -d "${APP_DIR}"
  [[ -f "${SIDECAR}" ]] && ${SUDO} install -Dm755 "${SIDECAR}" "${APP_DIR}/ecosystem-backend"
  install_icon "32x32" "${FRONTEND}/src-tauri/icons/32x32.png"
  install_icon "128x128" "${FRONTEND}/src-tauri/icons/128x128.png"
  ${SUDO} install -Dm644 "${REPO_ROOT}/packaging/ecosystem.desktop" \
    "${DESKTOP_DIR}/ecosystem.desktop"
fi

say "Refreshing desktop and icon caches"
if command -v update-desktop-database >/dev/null 2>&1; then
  ${SUDO} update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  ${SUDO} gtk-update-icon-cache -f -t "${PREFIX}/share/icons/hicolor" 2>/dev/null || true
fi

say "Installed. Launch 'ECOSYSTEM' from your application menu, or run: ecosystem-desktop"
