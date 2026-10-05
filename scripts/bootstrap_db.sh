#!/usr/bin/env bash
# Bootstrap the ECOSYSTEM PostgreSQL database.
#
# Creates the application role, the database, and the pgvector extension, then
# runs Alembic migrations. Extension creation needs superuser rights, which is
# why it happens here rather than inside a migration.
#
# Usage: scripts/bootstrap_db.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

DB_NAME="${ECOSYSTEM_DB_NAME:-ecosystem}"
DB_USER="${ECOSYSTEM_DB_USER:-ecosystem}"
DB_HOST="${ECOSYSTEM_DB_HOST:-127.0.0.1}"
DB_PORT="${ECOSYSTEM_DB_PORT:-5432}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

if [[ ! -f "${ENV_FILE}" ]]; then
  say "No .env found. Creating one from .env.example."
  cp "${REPO_ROOT}/.env.example" "${ENV_FILE}"
fi

# Reuse the password already in .env if present, otherwise mint one.
if grep -q 'CHANGE_ME' "${ENV_FILE}" 2>/dev/null; then
  DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  python3 - "$ENV_FILE" "$DB_PASSWORD" "$SECRET_KEY" <<'PY'
import re, sys
path, pw, secret = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(path).read()
text = re.sub(r'(ECOSYSTEM_DATABASE_URL=postgresql\+asyncpg://[^:]+:)[^@]+(@)',
              lambda m: m.group(1) + pw + m.group(2), text)
text = re.sub(r'ECOSYSTEM_SECRET_KEY=.*', 'ECOSYSTEM_SECRET_KEY=' + secret, text)
open(path, 'w').write(text)
PY
  chmod 600 "${ENV_FILE}"
fi

DB_PASSWORD="$(python3 - "${ENV_FILE}" <<'PY'
import re, sys
m = re.search(r'postgresql\+asyncpg://[^:]+:([^@]+)@', open(sys.argv[1]).read())
print(m.group(1) if m else "")
PY
)"

say "Ensuring PostgreSQL is running"
if command -v pg_ctlcluster >/dev/null 2>&1; then
  sudo pg_ctlcluster 17 main start 2>/dev/null || true
fi
for _ in $(seq 1 20); do
  pg_isready -h "${DB_HOST}" -p "${DB_PORT}" >/dev/null 2>&1 && break
  sleep 0.5
done

say "Creating role and database"
sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
  "DO \$\$ BEGIN
     IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='${DB_USER}') THEN
       CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';
     ELSE
       ALTER ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';
     END IF;
   END \$\$;"

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  sudo -u postgres psql -v ON_ERROR_STOP=1 -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
fi

say "Installing extensions (requires superuser)"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -c \
  "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm;"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -c \
  "GRANT ALL ON SCHEMA public TO ${DB_USER};"

say "Running migrations"
cd "${REPO_ROOT}/backend"
.venv/bin/alembic upgrade head

say "Database ready: ${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
