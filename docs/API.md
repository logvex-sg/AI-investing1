# API

Base path: `/api`. Interactive docs are served at `/docs` (OpenAPI at
`/openapi.json`).

Authentication is a bearer token from `POST /api/auth/login`. Send it as
`Authorization: Bearer <token>`. Endpoints marked **operator** require the
operator role; **admin** requires the admin role; the rest require any
authenticated user. `/api/system/health` is the only unauthenticated endpoint.

Amounts and balances are serialized as decimal strings to avoid floating-point
drift on the wire.

## Auth

| Method | Path                   | Auth     | Purpose                                   |
| ------ | ---------------------- | -------- | ----------------------------------------- |
| POST   | `/auth/login`          | none     | Exchange credentials for a session token  |
| POST   | `/auth/bootstrap`      | none     | Create the first admin (only while no users exist) |
| GET    | `/auth/me`             | user     | Current identity and role                 |
| GET    | `/auth/users`          | admin    | List users                                |
| POST   | `/auth/users`          | admin    | Create a user                             |

## System

| Method | Path                          | Auth | Purpose                                        |
| ------ | ----------------------------- | ---- | ---------------------------------------------- |
| GET    | `/system/health`              | none | Liveness probe; reveals only non-sensitive status |
| GET    | `/system/status`              | user | Environment, generation, agent count, pending approvals, emergency state |
| GET    | `/system/overview`            | user | Dashboard payload: portfolio, counts, recent events |
| POST   | `/system/mark`                | user | Re-value every portfolio at the latest prices  |
| POST   | `/system/emergency-stop`      | op   | Halt new proposals and execution               |
| POST   | `/system/emergency-stop/release` | op | Release the halt                             |
| GET    | `/system/reconciliation`      | user | Ledger-vs-balances reconciliation              |
| GET    | `/system/market/symbols`      | user | Supported symbols with ingested series metadata |
| GET    | `/system/market/{symbol}`     | user | OHLCV bars for a symbol and interval           |

## Agents

| Method | Path                                | Auth | Purpose                                |
| ------ | ----------------------------------- | ---- | -------------------------------------- |
| GET    | `/agents`                           | user | Roster with portfolios and scores      |
| GET    | `/agents/network`                   | user | Overseer + agents graph with edges     |
| GET    | `/agents/compare`                   | user | Side-by-side agent comparison          |
| GET    | `/agents/{agent_id}`                | user | Full agent detail                      |
| GET    | `/agents/{agent_id}/strategies`     | user | The agent's strategies                 |
| GET    | `/agents/{agent_id}/experiments`    | user | The agent's experiments                |
| GET    | `/agents/{agent_id}/memory`         | user | The agent's memories                   |
| GET    | `/agents/{agent_id}/relationships`  | user | Relationships to other agents          |
| POST   | `/agents/{agent_id}/research`       | op   | Assign a research objective            |

## Generations

| Method | Path                                  | Auth | Purpose                                     |
| ------ | ------------------------------------- | ---- | ------------------------------------------- |
| GET    | `/generations`                        | user | All generations                             |
| GET    | `/generations/current`                | user | The active generation                       |
| GET    | `/generations/history`                | user | Per-generation performance history          |
| GET    | `/generations/{generation_number}`    | user | One generation with its agents              |
| GET    | `/generations/agents/{agent_id}/lineage` | user | Family tree for an agent                 |
| POST   | `/generations/bootstrap`              | op   | Create generation 1 (treasury + eight agents) |
| POST   | `/generations/evolve`                 | op   | Archive current, create next by selection and mutation |

## Research

| Method | Path                                 | Auth | Purpose                                   |
| ------ | ------------------------------------ | ---- | ----------------------------------------- |
| GET    | `/research/questions`                | user | Research questions                        |
| GET    | `/research/questions/{question_id}`  | user | Question with hypotheses and experiments  |
| GET    | `/research/experiments`              | user | Experiments with their latest result      |
| GET    | `/research/experiments/{experiment_id}` | user | Experiment detail                      |
| GET    | `/research/observation`              | user | Overseer observation snapshot             |
| POST   | `/research/cycle`                    | op   | Run one full Overseer loop                |
| POST   | `/research/characteristics`          | op   | Extract useful characteristics for a generation |

## Strategies

| Method | Path                                | Auth | Purpose                                       |
| ------ | ----------------------------------- | ---- | --------------------------------------------- |
| GET    | `/strategies`                       | user | Strategy registry (filter by stage)           |
| GET    | `/strategies/{strategy_id}`         | user | Detail, active version, lineage, best result  |
| GET    | `/strategies/{strategy_id}/versions` | user | Version history                              |
| GET    | `/strategies/{strategy_id}/lineage` | user | Family tree                                   |
| POST   | `/strategies/{strategy_id}/validate` | op  | Backtest → out-of-sample → robustness         |
| POST   | `/strategies/{strategy_id}/retire`  | op   | Retire with a recorded reason                 |

Validation never fabricates results. The engine computes every number; the
response is the engine's output.

## Portfolio

| Method | Path                         | Auth | Purpose                                     |
| ------ | ---------------------------- | ---- | ------------------------------------------- |
| GET    | `/portfolio`                 | user | Portfolio summaries (filter by generation)  |
| GET    | `/portfolio/{portfolio_id}`  | user | Positions, equity curve, transactions, profits |
| GET    | `/portfolio/treasury`        | user | System Treasury accounts                    |
| GET    | `/portfolio/contributions`   | user | Each agent's share of total value           |
| GET    | `/portfolio/reconciliation`  | user | Ledger-vs-balances reconciliation           |

## Approvals

| Method | Path                                      | Auth | Purpose                                  |
| ------ | ----------------------------------------- | ---- | ---------------------------------------- |
| GET    | `/approvals/pending`                      | user | Proposals awaiting a human decision      |
| GET    | `/approvals/{approval_id}`                | user | One approval with its proposal snapshot  |
| POST   | `/approvals/proposals/trade`              | op   | Create a trade proposal (runs risk + accounting) |
| POST   | `/approvals/proposals/transfer`           | op   | Create a transfer proposal (always high-impact) |
| POST   | `/approvals/{approval_id}/decide`         | user | Approve or reject; high-impact needs `CONFIRM HIGH IMPACT` |
| POST   | `/approvals/proposals/{proposal_id}/execute` | op | Execute an approved proposal            |
| GET    | `/approvals/executions/recent`            | user | Recent execution records                 |

A trade proposal response reports the risk decision and accounting status. A
proposal rejected by risk or accounting never reaches the human queue.

## Risk

| Method | Path          | Auth | Purpose                                                    |
| ------ | ------------- | ---- | ---------------------------------------------------------- |
| GET    | `/risk/limits` | user | Effective limits and emergency-stop state (read-only)      |

There is no write endpoint. Limits come from configuration and require a
restart to change. The AI has no path to them.

## Activity

| Method | Path                      | Auth | Purpose                                |
| ------ | ------------------------- | ---- | -------------------------------------- |
| GET    | `/activity`               | user | Persisted events (filter by category, severity) |
| GET    | `/activity/audit`         | user | Audit log                              |
| GET    | `/activity/risk-events`   | user | Risk engine decisions                  |
| GET    | `/activity/stream`        | token | Server-Sent Events live stream        |

The SSE stream accepts the token as a query parameter because `EventSource`
cannot set headers. The persisted `/activity` feed is the source of truth; the
stream is an advisory live overlay.

## Memory

| Method | Path              | Auth | Purpose                                    |
| ------ | ----------------- | ---- | ------------------------------------------ |
| GET    | `/memory`         | user | Recent memories (filter by agent, kind)    |
| POST   | `/memory/search`  | user | Semantic search over an agent's memories   |

## Error responses

Errors use standard FastAPI shape: `{"detail": "..."}` with an appropriate
status code. `409` indicates a state conflict (for example, a strategy with no
active version, or an execute call on an unapproved proposal). `403` indicates
a permission failure. Authentication failures do not distinguish a missing user
from a wrong password.
