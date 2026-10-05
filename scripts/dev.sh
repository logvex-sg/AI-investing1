#!/usr/bin/env bash
# Run the ECOSYSTEM backend and frontend together for local development.
#
# Usage: scripts/dev.sh [api_port] [web_port]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${1:-8000}"
WEB_PORT="${2:-5173}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if [[ ! -x "${REPO_ROOT}/backend/.venv/bin/uvicorn" ]]; then
  echo "Backend venv missing. Run: cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

if [[ ! -d "${REPO_ROOT}/frontend/node_modules" ]]; then
  echo "Frontend dependencies missing. Run: cd frontend && npm install" >&2
  exit 1
fi

cleanup() {
  say "Shutting down"
  # Kill only the processes this script started.
  [[ -n "${API_PID:-}" ]] && kill "${API_PID}" 2>/dev/null || true
  [[ -n "${WEB_PID:-}" ]] && kill "${WEB_PID}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

say "Starting backend on http://127.0.0.1:${API_PORT}"
(
  cd "${REPO_ROOT}/backend"
  exec .venv/bin/uvicorn ecosystem.app:app --reload --port "${API_PORT}"
) &
API_PID=$!

say "Starting frontend on http://127.0.0.1:${WEB_PORT}"
(
  cd "${REPO_ROOT}/frontend"
  exec npm run dev -- --port "${WEB_PORT}"
) &
WEB_PID=$!

say "Control center: http://localhost:${WEB_PORT}  (API docs: http://localhost:${API_PORT}/docs)"
wait
