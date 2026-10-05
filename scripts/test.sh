#!/usr/bin/env bash
# Run the full verification pass: backend tests, then a frontend production build.
#
# Usage: scripts/test.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

if [[ ! -x "${REPO_ROOT}/backend/.venv/bin/python" ]]; then
  fail "Backend venv missing. Run: cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
fi

say "Backend tests"
(cd "${REPO_ROOT}/backend" && .venv/bin/python -m pytest tests/ -q)

if [[ ! -d "${REPO_ROOT}/frontend/node_modules" ]]; then
  fail "Frontend dependencies missing. Run: cd frontend && npm install"
fi

say "Frontend production build"
(cd "${REPO_ROOT}/frontend" && npm run build)

say "All checks passed."
