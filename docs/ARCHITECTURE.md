# Architecture

ECOSYSTEM is a pipeline of stages connected by a persistent database. Each
stage has one owner: either a deterministic service or a language model. The
split is deliberate and is the single most important thing to understand before
changing code.

## The boundary map

| Concern                | Owner                                    | The AI's role            |
| ---------------------- | ---------------------------------------- | ------------------------ |
| Market prices / bars   | `services.market_data`, `domain.market`  | none                     |
| Backtest numbers       | `domain.backtest`                        | interprets only          |
| Out-of-sample results  | `domain.backtest` + `services.experiments` | interprets only        |
| Robustness scoring     | `domain.evaluation`                      | interprets only          |
| Accounting balances    | `services.accounting`                    | none                     |
| Risk limits            | `domain.risk_rules` + `services.risk`    | none (cannot modify)     |
| Permissions            | `api.deps` + `services.auth`             | none                     |
| Human approval         | `services.approval` + `api.routers.approvals` | none                 |
| Execution              | `services.execution`                     | none                     |
| Strategy ideas         | `services.agents.investor` + LLM         | proposes                 |
| Research questions     | `services.agents.overseer` + LLM         | proposes                 |
| Evolution / selection  | `services.generation` (deterministic)    | proposes mutations       |

If you are adding a feature and are unsure which side it belongs to, ask: *can
a wrong answer here lose money or break an invariant?* If yes, it is
deterministic.

## Layer diagram

```
                    ┌──────────────────────────────┐
   HTTP / SSE  ───► │  api/routers  (FastAPI)       │
                    │  api/deps   (auth, session)   │
                    │  api/schemas (wire types)     │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  services/  (orchestration)   │
                    │  accounting  risk   approval  │
                    │  execution   portfolio        │
                    │  experiments generation       │
                    │  strategies  market_data      │
                    │  memory      events           │
                    │  agents/{overseer,investor}   │
                    │  llm/{base,mock,ollama,sched} │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  domain/  (pure functions)    │
                    │  money backtest evaluation    │
                    │  risk_rules signals           │
                    │  accounting_math market       │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼───────────────┐
                    │  db/models  (SQLAlchemy)      │
                    │  db/session (async engine)    │
                    │  PostgreSQL + pgvector        │
                    └──────────────────────────────┘
```

`domain/` has no I/O and no framework imports. It is pure functions over
numbers and dataclasses, which is why it is cheap to unit-test exhaustively.
`services/` owns transactions and side effects. `api/` owns authentication and
wire formats only.

## Data flow, stage by stage

1. **Market data.** `market_data.get_or_load_series` produces a deterministic
   synthetic OHLCV series (`SyntheticMarketSource`, fixed seed) or loads from a
   configured provider. Bars are persisted to `market_data_series` /
   `market_bars`. BTC is flagged volatile; EUR and USD are treated as cash-like
   and are never used as a "stable value" proxy for BTC.
2. **Overseer.** `agents.overseer.observe` builds a bounded picture (roster,
   scores, open questions, recent events). `plan` forms a question and an
   assignment plan. `run_cycle` executes OBSERVE → ANALYZE → IDENTIFY PROBLEM →
   HYPOTHESIS → ASSIGN → EVALUATE → LEARN and returns a report.
3. **Investor agents.** Each agent is a logical identity with a persistent row,
   a specialization, a persona, and its own strategy. `investor` runs the
   reasoning loop: recall relevant memories, read the objective and market
   context, propose a strategy (or mutation), and record hypotheses.
4. **Research → experiments.** A question spawns hypotheses; hypotheses spawn
   experiments. `experiments.run_full_validation` runs backtest → out-of-sample
   → robustness and persists an `ExperimentResult` per stage.
5. **Backtesting.** `domain.backtest` is the only thing that computes returns.
   It models price, volume, spread, fees, slippage, position sizing, available
   capital, and turnover, and refuses to read future bars (look-ahead guard).
   Results are stored; the LLM only ever reads them.
6. **Evaluation.** `domain.evaluation` grades results into a score and a
   pass/fail against criteria that live in configuration — outside any agent's
   authority.
7. **Evolution.** `generation.evolve` ranks the roster, extracts useful
   characteristics, combines and mutates them, and creates the next generation.
   Failed agents are `STOPPED`, archived, analysed, and preserved as knowledge.
   Nothing is deleted.
8. **Portfolio simulation.** `portfolio` maintains a System Treasury
   (Protected Capital / Trading Allocation / Operating Reserve / Profit
   Reserve) and one simulated account per agent. Default mode is paper trading.
9. **Accounting.** `accounting` is the ledger. It distinguishes starting
   capital, available capital, reserved capital, realized profit, unrealized
   P/L, fees, losses, portfolio value, and profit reserve. Every balance change
   comes from an immutable transaction row.
10. **Proposal → risk → accounting → approval → execution.**
    `approval.create_trade_proposal` runs the risk engine and the accounting
    check, then queues a human approval request. `execution` refuses to run
    without a recorded approval, applies the configured adapter, and settles the
    fill back into the ledger.
11. **Memory.** `memory` stores observations, research, hypotheses, decisions,
    results, strategy versions, discoveries, failures, lessons, market
    observations, and relationships, with embeddings in pgvector. Agents
    retrieve a bounded, relevant slice — never their whole history.
12. **Audit.** `events` and `audit` record every privileged operation.
    `system_events`, `audit_logs`, and `experiment_results` are append-only,
    enforced by database triggers in the migrations.

## The proposal pipeline (the safety spine)

```
AI proposal
   │
   ├─ risk engine        → ALLOW | WARN | REJECT   (services.risk)
   ├─ accounting check   → enough available capital? (services.accounting)
   ├─ human approval     → APPROVED | REJECTED     (services.approval)
   ├─ execution adapter  → SIMULATED | PAPER_TRADING | TESTNET(gated)
   ├─ accounting settle  → apply_buy / apply_sell / transfer
   └─ audit              → audit_logs + system_events
```

Any stage can refuse. A refusal at the risk or accounting stage never reaches
the human queue. A proposal without an approval never executes.

## Memory model

`agent_memory` rows carry a `kind`, a `content`, a JSONB payload, an importance
score, an optional embedding (`vector`), and a timestamp. Recall combines
cosine similarity, importance, and recency so that a large history stays
navigable inside a bounded context window. See `services/memory.py`.

## Inference model

One shared inference server (Ollama by default, or the deterministic `mock`
provider used in tests and CI). `services/llm/scheduler.py` serialises model
access so that eight logical agents never require eight loaded models. The
provider interface is `services/llm/base.py`; `mock.py` is a fully working
implementation, not a stub, so the whole ecosystem runs without a GPU.

## Extension points

- **A new execution venue:** implement the adapter interface in
  `services/execution.py`, register it behind a config flag, and leave the
  production adapter disabled by default.
- **A new risk rule:** add a pure function in `domain/risk_rules.py` plus a
  limit in `config.py`, then wire it in `services/risk.py`. Never let an agent
  path write limits.
- **A new evaluation metric:** add it to `domain/evaluation.py` and persist it
  in `performance_metrics`. Criteria stay in configuration.
- **A new market provider:** implement the `MarketSource` protocol in
  `services/market_data.py`.
- **A new agent specialization:** add it to the founding roster in
  `services/generation.py`; keep the active roster at or below eight.

## Invariants worth protecting

1. No LLM call ever computes or writes a balance, a risk limit, or an approval.
2. `domain/` stays pure and dependency-free.
3. Backtests never read bars after the decision timestamp.
4. Execution requires a persisted human approval.
5. Failed agents and their history are never deleted.
6. Every privileged operation writes an audit row.
