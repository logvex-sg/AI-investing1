import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { agents as agentsApi, generations as generationsApi } from "../lib/api";
import { usePolling, useMutation } from "../lib/hooks";
import { money, num, pct, signed, timeAgo } from "../lib/format";
import {
  Panel,
  Stat,
  Chip,
  Tabs,
  Empty,
  ErrorBanner,
  Loading,
  Progress,
} from "../components/ui.jsx";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "strategy", label: "Strategy" },
  { id: "memory", label: "Memory" },
  { id: "experiments", label: "Experiments" },
  { id: "performance", label: "Performance" },
  { id: "relationships", label: "Relationships" },
];

export default function AgentDetail() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState("overview");
  const [objective, setObjective] = useState("");

  const detail = usePolling(() => agentsApi.detail(agentId), [agentId], 8000);
  const experiments = usePolling(() => agentsApi.experiments(agentId), [agentId], 12000);
  const strategies = usePolling(() => agentsApi.strategies(agentId), [agentId], 20000);
  const memory = usePolling(() => agentsApi.memory(agentId), [agentId], 20000);
  const lineage = usePolling(() => generationsApi.lineage(agentId), [agentId], 30000);

  const research = useMutation(() => agentsApi.research(agentId, objective));

  const agent = detail.data;

  if (detail.loading && !agent) return <Loading label="Loading agent" />;
  if (detail.error) return <ErrorBanner error={detail.error} />;
  if (!agent) return <Empty>Agent not found.</Empty>;

  const performance = agent.performance || {};
  const portfolio = agent.portfolio || {};

  return (
    <>
      <Panel
        title={`${agent.codename} · ${agent.specialization}`}
        actions={
          <div className="btn-row">
            <Chip>{agent.status}</Chip>
            <Chip>GEN {agent.generation_number}</Chip>
            <button className="btn ghost" onClick={() => navigate("/network")}>
              Back to network
            </button>
          </div>
        }
      >
        <div className="grid cols-4">
          <Stat label="Portfolio Value" value={money(portfolio.total_value)} />
          <Stat
            label="Realized P/L"
            value={signed(portfolio.realized_profit)}
            tone={Number(portfolio.realized_profit) >= 0 ? "good" : "bad"}
          />
          <Stat
            label="Unrealized P/L"
            value={signed(portfolio.unrealized_pnl)}
            tone={Number(portfolio.unrealized_pnl) >= 0 ? "good" : "bad"}
          />
          <Stat label="Drawdown" value={pct(portfolio.max_drawdown)} />
        </div>
      </Panel>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "overview" && (
        <div className="grid cols-2">
          <Panel title="Identity">
            <table>
              <tbody>
                <tr>
                  <td className="faint">Codename</td>
                  <td className="mono">{agent.codename}</td>
                </tr>
                <tr>
                  <td className="faint">Display name</td>
                  <td>{agent.display_name}</td>
                </tr>
                <tr>
                  <td className="faint">Specialization</td>
                  <td>{agent.specialization}</td>
                </tr>
                <tr>
                  <td className="faint">Time horizon</td>
                  <td>{agent.time_horizon || "—"}</td>
                </tr>
                <tr>
                  <td className="faint">Assets</td>
                  <td className="mono">{(agent.asset_preferences || []).join(", ") || "—"}</td>
                </tr>
                <tr>
                  <td className="faint">Risk profile</td>
                  <td className="mono">{JSON.stringify(agent.risk_profile || {})}</td>
                </tr>
                <tr>
                  <td className="faint">Created</td>
                  <td className="dim">{agent.creation_reason || "—"}</td>
                </tr>
              </tbody>
            </table>
          </Panel>

          <Panel title="Current Objective">
            <p className="dim" style={{ fontSize: 13, marginTop: 0 }}>
              {agent.current_objective || "No objective assigned."}
            </p>
            <div className="field">
              <label>Assign a research objective</label>
              <input
                value={objective}
                placeholder="e.g. Test momentum persistence on BTC at 4h horizon"
                onChange={(e) => setObjective(e.target.value)}
              />
            </div>
            <button
              className="btn"
              disabled={!objective || research.busy}
              onClick={() => research.run().then(() => setObjective(""))}
            >
              {research.busy ? "Running cycle…" : "Run research cycle"}
            </button>
            {research.error && <ErrorBanner error={research.error} />}
            {research.result && (
              <pre
                className="mono"
                style={{
                  marginTop: 12,
                  fontSize: 11,
                  whiteSpace: "pre-wrap",
                  maxHeight: 220,
                  overflow: "auto",
                }}
              >
                {JSON.stringify(research.result, null, 2)}
              </pre>
            )}
            <div style={{ marginTop: 16 }}>
              <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>
                Memory utilisation
              </div>
              <Progress
                value={Math.min(
                  1,
                  Object.values(agent.memory_counts || {}).reduce((a, b) => a + b, 0) / 200
                )}
              />
            </div>
          </Panel>
        </div>
      )}

      {tab === "strategy" && (
        <Panel title="Strategy">
          {agent.strategy ? (
            <>
              <table>
                <tbody>
                  <tr>
                    <td className="faint">Name</td>
                    <td className="mono">{agent.strategy.name}</td>
                  </tr>
                  <tr>
                    <td className="faint">Stage</td>
                    <td>
                      <Chip>{agent.strategy.stage}</Chip>
                    </td>
                  </tr>
                  <tr>
                    <td className="faint">Version</td>
                    <td className="mono">{agent.strategy.current_version}</td>
                  </tr>
                  <tr>
                    <td className="faint">Thesis</td>
                    <td className="dim">{agent.strategy.thesis || "—"}</td>
                  </tr>
                </tbody>
              </table>
              <div className="btn-row" style={{ marginTop: 12 }}>
                <button
                  className="btn ghost"
                  onClick={() => navigate(`/strategies?strategy=${agent.strategy.id}`)}
                >
                  Open in strategies
                </button>
              </div>
            </>
          ) : (
            <Empty>No strategy assigned yet.</Empty>
          )}

          <h3 className="panel-title" style={{ marginTop: 20 }}>
            <span className="dot" /> All strategies
          </h3>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Stage</th>
                <th>Version</th>
                <th>Universe</th>
              </tr>
            </thead>
            <tbody>
              {(strategies.data?.strategies || []).map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.name}</td>
                  <td>
                    <Chip>{s.stage}</Chip>
                  </td>
                  <td className="mono">{s.current_version}</td>
                  <td className="mono">{(s.asset_universe || []).join(", ")}</td>
                </tr>
              ))}
              {(strategies.data?.strategies || []).length === 0 && (
                <tr>
                  <td colSpan={4} className="faint">
                    No strategies recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Panel>
      )}

      {tab === "memory" && (
        <Panel
          title="Persistent Memory"
          actions={
            <div className="btn-row">
              {Object.entries(memory.data?.counts || {}).map(([kind, count]) => (
                <Chip key={kind}>
                  {kind} {count}
                </Chip>
              ))}
            </div>
          }
        >
          <div className="feed" style={{ maxHeight: 560 }}>
            {(memory.data?.memories || []).map((m) => (
              <div
                key={m.id}
                className="stat"
                style={{ padding: "10px 12px", marginBottom: 4 }}
              >
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <Chip>{m.kind}</Chip>
                  <strong style={{ fontSize: 12.5 }}>{m.title}</strong>
                  <div className="spacer" />
                  <span className="faint mono" style={{ fontSize: 10.5 }}>
                    importance {num(m.importance, 2)} · {timeAgo(m.created_at)}
                  </span>
                </div>
                <p className="dim" style={{ fontSize: 12, margin: "6px 0 0" }}>
                  {m.content}
                </p>
              </div>
            ))}
            {(memory.data?.memories || []).length === 0 && (
              <Empty>No memories yet. Run a research cycle to build history.</Empty>
            )}
          </div>
        </Panel>
      )}

      {tab === "experiments" && (
        <Panel title="Experiments">
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Stage</th>
                <th>Score</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {(experiments.data?.experiments || []).map((e) => (
                <tr key={e.id}>
                  <td>{e.title}</td>
                  <td>
                    <Chip
                      tone={
                        e.status === "COMPLETED"
                          ? "good"
                          : e.status === "FAILED"
                            ? "bad"
                            : "warn"
                      }
                    >
                      {e.status}
                    </Chip>
                  </td>
                  <td className="mono">{e.stage}</td>
                  <td className={`mono ${Number(e.score) >= 0 ? "good" : "bad"}`}>
                    {e.score === null || e.score === undefined ? "—" : num(e.score, 4)}
                  </td>
                  <td className="faint">{timeAgo(e.created_at)}</td>
                </tr>
              ))}
              {(experiments.data?.experiments || []).length === 0 && (
                <tr>
                  <td colSpan={5} className="faint">
                    No experiments yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Panel>
      )}

      {tab === "performance" && (
        <div className="grid cols-2">
          <Panel title="Metrics">
            <table>
              <tbody>
                {Object.entries(performance).map(([key, value]) => (
                  <tr key={key}>
                    <td className="faint">{key}</td>
                    <td className="mono">
                      {typeof value === "number" ? num(value, 4) : String(value)}
                    </td>
                  </tr>
                ))}
                {Object.keys(performance).length === 0 && (
                  <tr>
                    <td className="faint">No graded performance yet.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </Panel>
          <Panel title="Equity Components">
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={[
                    { name: "cash", value: Number(portfolio.cash) || 0 },
                    { name: "realized", value: Number(portfolio.realized_profit) || 0 },
                    { name: "unrealized", value: Number(portfolio.unrealized_pnl) || 0 },
                    { name: "value", value: Number(portfolio.total_value) || 0 },
                  ]}
                >
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" />
                  <XAxis dataKey="name" stroke="#64748b" fontSize={10} />
                  <YAxis stroke="#64748b" fontSize={10} width={70} />
                  <Tooltip
                    contentStyle={{
                      background: "#0b1220",
                      border: "1px solid rgba(56,189,248,0.3)",
                      borderRadius: 10,
                      fontSize: 12,
                    }}
                    formatter={(v) => money(v)}
                  />
                  <Bar dataKey="value" fill="#38bdf8" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Panel>
        </div>
      )}

      {tab === "relationships" && (
        <div className="grid cols-2">
          <Panel title="Parents">
            {(lineage.data?.parents || agent.lineage?.parents || []).length ? (
              <table>
                <thead>
                  <tr>
                    <th>Codename</th>
                    <th>Specialization</th>
                    <th>Gen</th>
                  </tr>
                </thead>
                <tbody>
                  {(lineage.data?.parents || agent.lineage?.parents).map((p) => (
                    <tr
                      key={p.id}
                      onClick={() => navigate(`/agents/${p.id}`)}
                      style={{ cursor: "pointer" }}
                    >
                      <td className="mono">{p.codename}</td>
                      <td className="dim">{p.specialization}</td>
                      <td className="mono">{p.generation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>Founder agent — no parents.</Empty>
            )}
          </Panel>
          <Panel title="Children">
            {(lineage.data?.children || agent.lineage?.children || []).length ? (
              <table>
                <thead>
                  <tr>
                    <th>Codename</th>
                    <th>Specialization</th>
                    <th>Gen</th>
                  </tr>
                </thead>
                <tbody>
                  {(lineage.data?.children || agent.lineage?.children).map((c) => (
                    <tr
                      key={c.id}
                      onClick={() => navigate(`/agents/${c.id}`)}
                      style={{ cursor: "pointer" }}
                    >
                      <td className="mono">{c.codename}</td>
                      <td className="dim">{c.specialization}</td>
                      <td className="mono">{c.generation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>No offspring yet.</Empty>
            )}
          </Panel>
          <Panel title="Inherited Traits & Mutations">
            <div className="btn-row">
              {Object.entries(agent.inherited_traits || {}).map(([k, v]) => (
                <Chip key={k}>
                  {k}: {typeof v === "number" ? num(v, 2) : String(v)}
                </Chip>
              ))}
            </div>
            <h3 className="panel-title" style={{ marginTop: 16 }}>
              <span className="dot" /> Mutations
            </h3>
            <ul className="dim" style={{ fontSize: 12 }}>
              {(agent.mutations || []).map((m, i) => (
                <li key={i}>{typeof m === "string" ? m : JSON.stringify(m)}</li>
              ))}
              {(agent.mutations || []).length === 0 && <li className="faint">None recorded.</li>}
            </ul>
          </Panel>
          <Panel title="Research Priorities">
            <div className="btn-row">
              {(agent.research_priorities || []).map((p, i) => (
                <Chip key={i}>{typeof p === "string" ? p : JSON.stringify(p)}</Chip>
              ))}
              {(agent.research_priorities || []).length === 0 && (
                <span className="faint">None set.</span>
              )}
            </div>
          </Panel>
        </div>
      )}
    </>
  );
}
