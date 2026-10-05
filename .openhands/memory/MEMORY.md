# ECOSYSTEM — project memory

Local-first AI research & portfolio-simulation app at `/workspace/project/AI-investing1`.
Backend: FastAPI + SQLAlchemy(async) + Alembic + PostgreSQL/pgvector. Frontend: React/Vite.

## Environment
- Backend venv: `backend/.venv` (python `/usr/local/bin/python`, 3.13). Run pytest from `backend/`.
- Tests need PostgreSQL DB `ecosystem_test` (conftest swaps `/ecosystem`→`/ecosystem_test`).
- `scripts/bootstrap_db.sh` creates role/db/extensions and runs `alembic upgrade head`.
- LLM provider default `mock` (deterministic); `ollama` optional. Single shared model — 8 agents are logical identities.
- npm registry reachable; node 24 / npm 11.

## Conventions / invariants (learned the hard way)
- Deterministic services own accounting, risk, permissions, approval. LLM never computes balances or backtest numbers.
- Per-agent starting capital = €500 (treasury_total_capital 4000 / 8). High-impact threshold = 10% of portfolio value; confirmation phrase `CONFIRM HIGH IMPACT`.
- Execution MUST settle into the ledger: `services/execution.execute_approved_trade` calls `_settle_fill` → `accounting.apply_buy/apply_sell/transfer`. Without this, positions never appear.
- `SystemEvent`/`AuditLog`/`ExperimentResult` define their own `created_at` without server_default — they need Python-side `default=utcnow` (from `db.base`) or inserts fail with NotNullViolation.
- Append-only tables (system_events, audit_logs, ...) are enforced by DB triggers in migrations; a deliberate violation poisons the txn (roll back).
- `TimestampMixin` uses Python-side `default=utcnow` + `server_default=func.now()`.
- pgvector memory: `_cosine_similarity` returns similarity (not distance); recall folds similarity+importance+recency.

## API (built)
- `ecosystem/app.py` → `app = create_app()`; routers in `ecosystem/api/routers/` aggregated as `api_router` (prefix `/api`).
- Deps: `api/deps.py` (`get_session` via `session_scope`, `current_user` bearer, `require_operator`, `require_admin`).
- Schemas: `api/schemas.py` (Decimals as strings on the wire).
- Auth: `POST /api/auth/login`, `/api/auth/bootstrap` (only while no users exist).
- Routers: system, auth, agents, generations, strategies, research, portfolio, approvals, risk, activity (incl. SSE `/api/activity/stream`), memory.
- SSE via `services/events.subscribe` in-process bus (advisory; persisted `/api/activity` is source of truth).
- Run backend: `uvicorn ecosystem.app:app --reload --port 8000` (from `backend/`).

## Accounting invariants (regression-hardened)
- `verify_invariants` reconstructs cash as `account.starting_capital + Σ(net_amount of txns with that portfolio_id)`.
  Therefore: (a) `transfer(..., destination_is_opening=True)` funds a NEW account and must NOT record a
  TRANSFER_IN on it (the amount is carried by `starting_capital`); (b) transfers MUST record txns against the
  portfolio that owns the account (`side_portfolios`) or reclaim breaks reconciliation.
- `apply_buy`/`apply_sell` must call `accounting.recompute_aggregates` (updates `total_value` from
  cash+reserved+positions). A trade that only moved `cash` left `total_value` stale by the fee.
- `fund_agents` divides the treasury pool across 8 and gives the rounding remainder to the last agent;
  never compute `pool/n` per agent and let it round up (overdraws treasury → "insufficient funds").
- Evolution archives by reclaiming each agent account's `cash` into TREASURY_TRADING. Archived generation
  value is preserved in `Generation.summary["performance"]`; `performance_history` reads that snapshot
  because the live portfolio rows read 0 after reclaim.
- Risk/accounting use the authoritative market price (`market_data.latest_price`), not the caller's asserted
  price, at proposal time. Execute endpoint auto-fills from latest_price when no price is supplied.

## Desktop app (Tauri 2)
- Shell lives in `frontend/src-tauri/` (`ecosystem-desktop`). It owns a private PostgreSQL 17 cluster
  + FastAPI sidecar + webview; all mutable state under `$ECOSYSTEM_DATA_DIR` (default
  `~/.local/share/ecosystem/`): `pgdata/ logs/ backups/ config.json`. Never touches a system cluster.
- Sidecar = PyInstaller onefile built from `backend/ecosystem-backend.spec`, staged to
  `frontend/src-tauri/binaries/ecosystem-backend-x86_64-unknown-linux-gnu` (~52 MB). Rebuild sidecar AFTER
  any backend code change; `desktop-build.sh` does this unless `--skip-sidecar`.
- Build: `scripts/desktop-build.sh` (needs `source ~/.cargo/env`); produces `.deb` + `.AppImage`.
  `scripts/install-desktop.sh [--user|--system]`. Dev: `scripts/desktop-dev.sh`.
- CORS: backend allowlist MUST include `tauri://localhost` (and the tauri http origin) or the webview
  fetch is rejected. In `backend/ecosystem/app.py`.
- Wizard (`frontend/src/pages/SetupWizard.tsx`): Welcome→Hardware→Model server→Database→Administrator→
  Generation→Finish. Admin step (index 4) Continue MUST call `createAdmin()`; generation step (index 5)
  Continue MUST call `bootstrap()` — the bottom Continue button must not silently advance.
- Backend must degrade gracefully pre-bootstrap: `/api/system/status` + `/api/system/overview` return
  `setup_required: true` (generation status `SETUP_REQUIRED`, number `null`) instead of 500;
  `/api/generations/current` returns 404. `current_generation()` raises `GenerationError` when none.
- Headless E2E: `Xvfb :N -screen 0 1600x1000x24`, `import`/`convert` (ImageMagick) + `tesseract` OCR,
  `xdotool` for clicks/typing. Window offset +80,+50. Run app with `WEBKIT_DISABLE_COMPOSITING_MODE=1`
  `WEBKIT_DISABLE_DMABUF_RENDERER=1`.

## Test status
- `backend`: 116 passed (unit + integration + API). Key regression tests in
  `tests/test_integration_pipeline.py`: capital conservation + reconciliation after evolve, archived history
  snapshot, trade total_value/ledger consistency.
- Live E2E script (temp, not committed): `/tmp/e2e.py` — 19 checks incl. price authority, confirmation gate,
  risk rejection, evolve, treasury buckets, emergency stop. Reset+reseed DB before running.
