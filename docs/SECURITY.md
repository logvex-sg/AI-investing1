# Security

ECOSYSTEM holds a simulated portfolio, not real money, but it is built as if it
did. The reason is simple: the habits and interfaces are the same, and the
difference between paper and live should be one configuration flag — not a
rewrite of the trust model.

## Principle of least privilege

Permissions are separated by capability, and each capability has one owner.

| Capability            | Who holds it                                        |
| --------------------- | --------------------------------------------------- |
| Read research/state   | Any authenticated user                              |
| Create proposals      | Operator role (`require_operator`)                  |
| Run research cycles   | Operator role                                       |
| Evolve generations    | Operator role                                       |
| Approve / reject      | A human identity, recorded with user id + timestamp |
| Execute a proposal    | Requires a prior human approval row                 |
| Manage users          | Admin role (`require_admin`)                        |
| Read/write risk rules | Nobody through the API — configuration only         |
| Read/write balances   | Nobody through the API — `services.accounting` only |

The AI runs behind the same API surface as any client. It has no elevated
credential. It cannot call the approve endpoint on its own behalf, and it
cannot reach the database directly.

## What an LLM is never given

- Passwords or password hashes.
- Private keys, seed phrases, or withdrawal credentials of any kind.
- Database credentials.
- The session signing secret.
- Any write path to accounting, risk limits, permissions, or approval state.

Secrets live in environment variables loaded from `.env` (mode `600`, created
by `scripts/bootstrap_db.sh`, never committed). `.env.example` contains only
placeholders. `.gitignore` excludes `.env`.

## The approval flow

```
proposal  ──►  risk engine  ──►  accounting check  ──►  human approval  ──►  execution  ──►  accounting  ──►  audit
```

- **Risk engine.** Independent and deterministic. Enforces maximum position,
  exposure, loss, drawdown, trade frequency, and concentration, plus an asset
  allowlist and per-account limits. A violation rejects the proposal before a
  human ever sees it.
- **Accounting check.** Confirms the account actually has the available capital
  the proposal needs. An LLM cannot assert a balance into existence.
- **Human approval.** Recorded with a unique proposal id, timestamp, user
  identity, action, amount, asset, source, destination, reason, risk status,
  accounting status, approval status, and execution status.
- **High-impact confirmation.** Any action at or above 10% of portfolio value —
  and every transfer, regardless of size — requires the human to type the exact
  phrase `CONFIRM HIGH IMPACT`. A missing or wrong phrase is refused.
- **Execution.** Refuses any proposal without a recorded approval. Default
  adapters are `SimulatedExecutionAdapter` and `PaperTradingExecutionAdapter`.

## Emergency stop

The emergency stop is the human's hard halt.

- It stops new execution proposals and halts execution.
- It preserves all state and deletes nothing.
- It records a security event (`SECURITY` category) and shows a system warning
  in the control center.

Engage it from **Settings → Emergency Stop** or:

```bash
curl -X POST http://localhost:8000/api/system/emergency-stop \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Investigating anomalous drawdown"}'
```

Release it deliberately, once the cause is understood. The state change itself
is audited in both directions.

## Append-only history

`system_events`, `audit_logs`, and `experiment_results` reject `UPDATE` and
`DELETE` at the database level via triggers created in the migrations. History
is evidence; it is not editable by application code, by an operator, or by the
AI. A deliberate violation aborts the surrounding transaction.

## Auditing

Every privileged operation writes an audit row capturing actor type, actor id,
action, resource type, resource id, outcome (`SUCCESS` / `DENIED` / …), and a
reason. Failed agents are archived with their failure history rather than
removed, so the evolutionary record stays intact.

## Error handling

- The unauthenticated liveness probe (`/api/system/health`) deliberately
  reveals only non-sensitive status: environment, provider name, and adapter
  enablement. It never returns credentials or connection strings.
- Authentication failures return a generic message; they do not distinguish a
  missing user from a wrong password.
- Errors never include secrets. Database connection strings are never echoed in
  responses.

## Execution adapter isolation

| Adapter                     | Default | Requires                                     |
| --------------------------- | ------- | -------------------------------------------- |
| `SimulatedExecutionAdapter` | on      | nothing                                      |
| `PaperTradingExecutionAdapter` | on   | nothing                                      |
| `TestnetExecutionAdapter`   | off     | `ECOSYSTEM_ENABLE_TESTNET_ADAPTER=true`      |
| Production adapter          | off     | `ECOSYSTEM_ENABLE_PRODUCTION_ADAPTER=true`   |

The production path is isolated and explicitly disabled. Enabling it is a
deliberate act by a human operator who understands the consequences, and it
should be done only after the rest of the pipeline has been verified against
paper trading.

## Reporting

This is a local-first system with no external attack surface by default
(CORS is restricted to local origins). If you extend it toward a network
deployment, revisit this document first: bind authentication to HTTPS, add
rate limiting, and reconsider the CORS origins in `ecosystem/app.py`.
