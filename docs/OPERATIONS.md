# Operations

How to set up, run, seed, tune, and troubleshoot ECOSYSTEM on a single machine.

## Prerequisites

- PostgreSQL 17 with the `vector` and `pg_trgm` extensions available.
- Python 3.11 or newer (developed on 3.13).
- Node 20 or newer (developed on 24).

## First-time setup

```bash
scripts/bootstrap_db.sh
```

This is idempotent. It creates `.env` from `.env.example` if absent, mints a
database password and a session secret, creates the role and database, installs
the extensions (which needs superuser rights, so it lives here rather than in a
migration), grants the schema, and runs `alembic upgrade head`.

Then install the backend and seed a demo world:

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m ecosystem.scripts.seed_demo
```

`seed_demo` creates the `operator` user (password `ecosystem-demo`), generation
1 with its eight agents and their simulated accounts, loads synthetic market
data for every supported symbol, and runs one full Overseer research cycle. It
is safe to run twice: it will not duplicate data.

## Running

```bash
scripts/dev.sh
```

runs the backend on `:8000` and the frontend on `:5173` together. Or run them
separately:

```bash
cd backend && .venv/bin/uvicorn ecosystem.app:app --reload --port 8000
cd frontend && npm run dev
```

The frontend proxies `/api` to the backend, so open
`http://localhost:5173` and sign in.

## Desktop application (Tauri)

The same backend and frontend also ship as a native desktop app. The Tauri 2
shell owns the lifecycle of every local service, so nothing has to be started by
hand:

- a **private PostgreSQL 17 cluster** (with `pgvector`) initialised under the
  application data directory — it does not touch or require a system cluster;
- the **FastAPI backend** as a bundled sidecar on a random loopback port;
- the **React control center** in a native webview.

All mutable state lives under the XDG data directory:

```
~/.local/share/ecosystem/
├── pgdata/        private PostgreSQL cluster
├── logs/          backend and cluster logs
├── backups/       database dumps
└── config.json    shell preferences
```

Set `ECOSYSTEM_DATA_DIR` to relocate it (useful for testing). The shell never
performs financial actions itself; trades still flow through the backend's
risk → accounting → human-approval pipeline.

Build, run and install:

```bash
scripts/desktop-build.sh          # sidecar + frontend + shell bundles (.deb, .AppImage)
scripts/desktop-dev.sh            # hot-reload development shell
scripts/install-desktop.sh        # install into ~/.local (--system for /usr/local)
```

The first launch shows a setup wizard: hardware report, model-server choice
(`mock` or Ollama), database check, creation of the local administrator, and
bootstrap of generation 1 (agents A1–A8). Every step is idempotent and safe to
re-run.

## Testing

```bash
scripts/test.sh
```

runs the backend suite and a frontend production build. The backend suite
needs a PostgreSQL database named `ecosystem_test`; `conftest.py` swaps the
database name from the configured URL, and each test runs inside a transaction
that is rolled back afterwards.

To run just the backend:

```bash
cd backend && .venv/bin/python -m pytest tests/ -q
```

The suite covers accounting math, risk rules, backtest correctness, evaluation,
the full integration pipeline (agent → Overseer → experiment → backtest →
evaluation → generation; proposal → risk → approval → execution → accounting),
and the HTTP API.

## Resource management on a 16 GB laptop

The system is designed to be comfortable on modest hardware.

- **One shared model.** A single inference server (Ollama) serves all eight
  agents. The agents are logical identities, not eight loaded models.
  `services/llm/scheduler.py` serialises access so memory stays flat.
- **Bounded context.** `ECOSYSTEM_LLM_MAX_CONTEXT_TOKENS` (default 4096) caps
  each prompt. Agents retrieve a relevant slice of memory rather than their
  whole history.
- **Scheduled reasoning.** Agents reason on demand and in cycles, not
  continuously. Nothing spins in a busy loop.
- **Deterministic default.** `ECOSYSTEM_LLM_PROVIDER=mock` runs the entire
  ecosystem with no model loaded at all, which is what the test suite uses.
- **Efficient queries.** Indexed foreign keys and timestamps; dashboards poll on
  intervals (5–30 s) and the live feed is a single SSE connection.
- **Lazy loading.** The frontend loads charts and detail panels on demand.

Suggested settings for a 4 GB GPU: keep `ECOSYSTEM_LLM_MAX_CONCURRENCY=1`, use a
7B-class quantised model, and leave the provider on `mock` when you are only
exercising the pipeline.

## Configuration

All settings live in `.env` and are read by `ecosystem/config.py`. Highlights:

| Variable                                | Default   | Meaning                                   |
| --------------------------------------- | --------- | ----------------------------------------- |
| `ECOSYSTEM_DATABASE_URL`                | —         | `postgresql+asyncpg://…`                  |
| `ECOSYSTEM_SECRET_KEY`                  | —         | Session signing secret                    |
| `ECOSYSTEM_LLM_PROVIDER`                | `mock`    | `mock` or `ollama`                        |
| `ECOSYSTEM_OVERSEER_MODEL`              | qwen2.5:7b-instruct | Reasoning model                |
| `ECOSYSTEM_AGENT_MODEL`                 | qwen2.5:7b-instruct | Shared agent model             |
| `ECOSYSTEM_LLM_MAX_CONTEXT_TOKENS`      | 4096      | Bounded context window                    |
| `ECOSYSTEM_ENABLE_TESTNET_ADAPTER`      | false     | Testnet execution (gated)                 |
| `ECOSYSTEM_ENABLE_PRODUCTION_ADAPTER`   | false     | Production execution (gated, isolated)    |
| `ECOSYSTEM_RISK_MAX_POSITION_PCT`       | 0.25      | Deterministic risk limit                  |
| `ECOSYSTEM_TREASURY_TOTAL_CAPITAL`      | 10000.0   | Split across the four treasury accounts   |

Changing risk limits requires editing configuration and restarting. There is no
API write path, by design.

## Migrations

```bash
cd backend
.venv/bin/alembic revision --autogenerate -m "describe change"
.venv/bin/alembic upgrade head
```

Append-only tables (`system_events`, `audit_logs`, `experiment_results`) are
protected by triggers created in the migrations. Do not add `UPDATE`/`DELETE`
paths for them.

## Backup and restore

The database is the system of record.

```bash
pg_dump -Fc ecosystem > ecosystem-$(date +%F).dump     # backup
pg_restore -d ecosystem --clean ecosystem-2026-10-03.dump   # restore
```

The `.env` file holds the session secret and database password; back it up
separately and keep it out of version control.

## Troubleshooting

**`alembic upgrade head` fails on the `vector` extension.**
Extension creation needs superuser. Run `scripts/bootstrap_db.sh`, which
installs `vector` and `pg_trgm` as the `postgres` user before migrating.

**Tests fail with a database error.**
The suite expects a database named `ecosystem_test`. Create it, or point
`ECOSYSTEM_DATABASE_URL` at a server where it exists.

**"Not null violation" on `created_at` when inserting an event.**
`SystemEvent`, `AuditLog`, and `ExperimentResult` define their own `created_at`
without a server default and rely on a Python-side `default=utcnow`. Insert them
through the service layer (`services.events`, `services.accounting`) rather than
constructing rows directly.

**An append-only trigger aborted my transaction.**
That is the trigger working. A deliberate `UPDATE`/`DELETE` on an append-only
table poisons the surrounding transaction; roll back and retry without it.

**Execution reports FILLED but the portfolio has no position.**
Execution must settle into the ledger. `services.execution` calls the
accounting `apply_buy`/`apply_sell`/`transfer` paths on fill; if you add an
adapter, keep that settlement step.

**The dashboard shows a stale "CHECK" reconciliation.**
Call `POST /api/system/mark` to re-value portfolios at the latest prices, or
open the Portfolio page, which marks on load.

**Emergency stop is engaged and proposals are refused.**
Release it from Settings → Emergency Stop or
`POST /api/system/emergency-stop/release`, once you understand why it was
engaged. Both the engage and the release are audited.
