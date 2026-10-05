# ECOSYSTEM

A local-first AI research and portfolio-simulation ecosystem: one Overseer, up
to eight investor agents, persistent memory, research, strategy evolution,
deterministic backtesting, paper trading, accounting, an independent risk
engine, human approval, and a futuristic control center.

Everything runs on one machine. Nothing trades real capital. The AI proposes;
deterministic services decide what is allowed; a human approves.

```
MARKET DATA → OVERSEER → INVESTOR AGENTS → RESEARCH → HYPOTHESES →
STRATEGIES → BACKTESTING → OUT-OF-SAMPLE → ROBUSTNESS → EVALUATION →
EVOLUTION → PAPER TRADING → PROPOSAL → RISK ENGINE → HUMAN APPROVAL →
EXECUTION ADAPTER → ACCOUNTING → MEMORY → NEXT GENERATION
```

## The one rule that shapes the design

**The AI is not the source of truth for accounting, risk, permissions, or
approval.** Every one of those is owned by a deterministic service that the
language model cannot write to. The model interprets; the services calculate
and enforce. See `docs/ARCHITECTURE.md` for the full boundary map.

## Stack

| Layer     | Technology                                                        |
| --------- | ----------------------------------------------------------------- |
| Backend   | Python 3.11+, FastAPI, SQLAlchemy 2 (async), Alembic, asyncpg      |
| Database  | PostgreSQL 17 + `pgvector` (vector memory) + `pg_trgm` (search)    |
| Inference | One shared local model via Ollama (or a deterministic `mock`)      |
| Frontend  | React 18, Vite 5, react-router, Recharts, hand-rolled SVG network  |
| Desktop   | Tauri 2 (Rust shell + native webview), private PostgreSQL cluster  |

One shared inference server serves all eight agents. The agents are logical
identities, not eight loaded models — the scheduler decides who gets model time.
This is what keeps the system usable on a 16 GB laptop with a 4 GB GPU.

## Desktop app

ECOSYSTEM also ships as a native desktop application. The Tauri shell owns the
lifecycle of a private PostgreSQL cluster, the bundled FastAPI sidecar and the
webview, keeping all mutable state under `~/.local/share/ecosystem`. The shell
never performs financial actions — those stay behind the backend's risk,
accounting and human-approval pipeline.

```bash
scripts/desktop-build.sh     # build the sidecar + frontend + .deb/.AppImage
scripts/install-desktop.sh   # install for the current user (~/.local)
```

The first launch walks through a setup wizard (hardware, model server, database,
administrator, generation 1). See `docs/OPERATIONS.md` for details.

## Quickstart

Prerequisites: PostgreSQL with the `vector` extension available, Python 3.11+,
Node 20+.

```bash
# 1. Database: role, database, extensions, migrations (idempotent)
scripts/bootstrap_db.sh

# 2. Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# 3. Seed a demo world: operator user, generation 1, market data, one research cycle
.venv/bin/python -m ecosystem.scripts.seed_demo
#    → creates user "operator" with password "ecosystem-demo"

# 4. Serve the API
.venv/bin/uvicorn ecosystem.app:app --reload --port 8000
```

```bash
# 5. Frontend (separate shell)
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies /api → :8000
```

Sign in at `http://localhost:5173` with `operator` / `ecosystem-demo`.

### One-shot helpers

```bash
scripts/dev.sh         # run backend and frontend together
scripts/test.sh        # backend pytest + frontend production build
```

## Repository layout

```
backend/
  ecosystem/
    api/            FastAPI routers + request schemas + auth dependencies
    db/models/      SQLAlchemy models (agents, portfolio, research, governance…)
    domain/         Pure, dependency-free math: money, backtest, evaluation,
                    risk rules, strategy signals, accounting math
    services/       Orchestration and persistence: accounting, risk, approval,
                    execution, generation, portfolio, experiments, memory, llm
      agents/       Overseer and investor-agent reasoning loops
    scripts/        Operational scripts (seed_demo)
  alembic/versions/ Migrations (append-only tables enforced by triggers)
  tests/            Unit + integration + API tests
frontend/
  src/pages/        Overview, Network, Agents, Generations, Research,
                    Strategies, Portfolio, Approvals, Activity, Settings
  src/components/   Shared UI primitives + the SVG network graph
  src/lib/          API client, polling/SSE hooks, formatting, auth context
docs/               ARCHITECTURE, API, OPERATIONS, SECURITY, SETUP
scripts/            bootstrap_db.sh, dev.sh, test.sh, desktop-*.sh
```

## Safety by construction

- **Simulation only.** The default and only enabled adapters are
  `SimulatedExecutionAdapter` and `PaperTradingExecutionAdapter`. Testnet and
  production adapters are gated behind explicit configuration and are off.
- **Deny by default.** A proposal is rejected unless the risk engine allows it
  and the accounting check passes. Execution refuses any proposal without a
  recorded human approval.
- **High-impact confirmation.** Actions at or above 10% of portfolio value —
  and every transfer — require the human to type `CONFIRM HIGH IMPACT`.
- **Append-only history.** Events, audit logs and experiment results are
  protected by database triggers. Failed agents are archived and kept as
  knowledge, never deleted.
- **Emergency stop.** Halts new proposals and execution, preserves all state,
  records a security event, and deletes nothing.

See `docs/SECURITY.md` for the permission model and `docs/OPERATIONS.md` for
the emergency-stop procedure.

## Documentation

| Document               | Contents                                                        |
| ---------------------- | --------------------------------------------------------------- |
| `docs/SETUP.md`        | Desktop install, build-from-source, first-run wizard, uninstall |
| `docs/ARCHITECTURE.md` | Layers, boundaries, data flow, invariants, extension points     |
| `docs/API.md`          | Every endpoint, grouped, with auth requirements                 |
| `docs/OPERATIONS.md`   | Running, seeding, resource tuning, troubleshooting              |
| `docs/SECURITY.md`     | Least-privilege model, approval flow, emergency stop, auditing  |

## Status

Backend test suite: **116 passing** (unit, integration, and API) against a real
PostgreSQL database. The frontend builds clean with Vite, and the desktop shell
packages to `.deb` and `.AppImage`. The full proposal → risk → accounting →
approval → execution → accounting pipeline is exercised end to end by the
integration tests, and the first-run wizard (administrator → generation 1 →
command center) is verified against the packaged app.
