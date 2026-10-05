import { useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { portfolio as portfolioApi, generations as generationsApi } from "../lib/api";
import { usePolling } from "../lib/hooks";
import { money, num, pct, signed } from "../lib/format";
import { Panel, Stat, Chip, Empty, ErrorBanner, Loading, SimBadge } from "../components/ui.jsx";

const COLORS = ["#38bdf8", "#8b5cf6", "#34d399", "#f59e0b", "#fb7185", "#22d3ee", "#a3e635", "#f472b6"];

export default function Portfolio() {
  const [generation, setGeneration] = useState(null);
  const [selected, setSelected] = useState(null);
  const [scope, setScope] = useState("all");

  const generations = usePolling(() => generationsApi.list(), [], 30000);
  const list = usePolling(() => portfolioApi.list(generation), [generation], 8000);
  const treasury = usePolling(() => portfolioApi.treasury(), [], 10000);
  const contributions = usePolling(() => portfolioApi.contributions(), [], 10000);
  const reconciliation = usePolling(() => portfolioApi.reconciliation(), [], 30000);

  const detail = usePolling(
    () => (selected ? portfolioApi.detail(selected) : Promise.resolve(null)),
    [selected],
    selected ? 7000 : 0
  );

  const portfolios = list.data?.portfolios || [];
  const totals = portfolios.reduce(
    (acc, p) => ({
      value: acc.value + Number(p.total_value || 0),
      starting: acc.starting + Number(p.starting_capital || 0),
      realized: acc.realized + Number(p.realized_profit || 0),
      unrealized: acc.unrealized + Number(p.unrealized_pnl || 0),
      fees: acc.fees + Number(p.total_fees || 0),
      cash: acc.cash + Number(p.cash || 0),
    }),
    { value: 0, starting: 0, realized: 0, unrealized: 0, fees: 0, cash: 0 }
  );

  const drawdown = Number(
    Math.max(...portfolios.map((p) => Number(p.max_drawdown || 0)), 0)
  );

  const equityCurve = (detail.data?.equity_curve || []).map((point, index) => ({
    index,
    value: Number(point.value),
    timestamp: point.timestamp,
  }));

  const allocation = (contributions.data?.contributions || []).map((c) => ({
    name: c.codename,
    value: Number(c.total_value),
  }));

  const strategyContribution = (detail.data?.positions || []).map((p) => ({
    name: p.symbol,
    value: Number(p.market_value || 0),
  }));

  return (
    <>
      <ErrorBanner error={list.error} />

      <div className="grid cols-4">
        <Stat
          label="Portfolio Value"
          value={money(totals.value)}
          hint={`start ${money(totals.starting)}`}
          tone={totals.value >= totals.starting ? "good" : "bad"}
        />
        <Stat label="Cash" value={money(totals.cash)} hint="available" />
        <Stat
          label="Realized P/L"
          value={signed(totals.realized)}
          tone={totals.realized >= 0 ? "good" : "bad"}
        />
        <Stat
          label="Unrealized P/L"
          value={signed(totals.unrealized)}
          tone={totals.unrealized >= 0 ? "good" : "bad"}
        />
      </div>

      <div className="grid cols-4">
        <Stat
          label="Total Return"
          value={pct(totals.starting ? (totals.value - totals.starting) / totals.starting : 0)}
          tone={totals.value >= totals.starting ? "good" : "bad"}
        />
        <Stat label="Max Drawdown" value={pct(drawdown)} tone={drawdown > 0.1 ? "warn" : ""} />
        <Stat label="Fees Paid" value={money(totals.fees)} hint="cumulative" />
        <Stat
          label="Reconciliation"
          value={
            reconciliation.data === undefined
              ? "—"
              : reconciliation.data?.ok
              ? "OK"
              : "CHECK"
          }
          hint="ledger vs balances"
          tone={reconciliation.data?.ok ? "good" : reconciliation.data ? "bad" : ""}
        />
      </div>

      <Panel
        title="Filters"
        actions={
          <div className="btn-row">
            <SimBadge />
            <select
              value={generation ?? ""}
              onChange={(e) => setGeneration(e.target.value ? Number(e.target.value) : null)}
              style={{ width: 170 }}
            >
              <option value="">Current generation</option>
              {(generations.data?.generations || []).map((g) => (
                <option key={g.number} value={g.number}>
                  {g.label}
                </option>
              ))}
            </select>
            <select value={scope} onChange={(e) => setScope(e.target.value)} style={{ width: 150 }}>
              <option value="all">System</option>
              <option value="agents">Agents</option>
              <option value="strategies">Strategies</option>
            </select>
          </div>
        }
      >
        <p className="faint" style={{ fontSize: 11.5, marginTop: 0 }}>
          Scope: {scope}. All figures are simulation/paper data produced by the
          deterministic accounting service.
        </p>
        <table>
          <thead>
            <tr>
              <th>Portfolio</th>
              <th>Gen</th>
              <th>Cash</th>
              <th>Positions</th>
              <th>Value</th>
              <th>Realized</th>
              <th>Unrealized</th>
              <th>Return</th>
              <th>Drawdown</th>
            </tr>
          </thead>
          <tbody>
            {portfolios.map((p) => (
              <tr
                key={p.id}
                onClick={() => setSelected(p.id)}
                style={{ cursor: "pointer" }}
              >
                <td className="mono">{p.name}</td>
                <td className="mono">{p.generation_number}</td>
                <td className="mono">{money(p.cash)}</td>
                <td className="mono">{money(p.positions_value)}</td>
                <td className="mono">{money(p.total_value)}</td>
                <td className={`mono ${Number(p.realized_profit) >= 0 ? "good" : "bad"}`}>
                  {signed(p.realized_profit)}
                </td>
                <td className={`mono ${Number(p.unrealized_pnl) >= 0 ? "good" : "bad"}`}>
                  {signed(p.unrealized_pnl)}
                </td>
                <td className="mono">{pct(p.total_return)}</td>
                <td className="mono">{pct(p.max_drawdown)}</td>
              </tr>
            ))}
            {portfolios.length === 0 && (
              <tr>
                <td colSpan={9} className="faint">
                  No portfolios yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Panel>

      <div className="grid cols-2">
        <Panel title="Equity Curve" actions={selected && <Chip>selected portfolio</Chip>}>
          {selected && equityCurve.length ? (
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={equityCurve}>
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" />
                  <XAxis dataKey="index" stroke="#64748b" fontSize={10} />
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
                  <Line type="monotone" dataKey="value" stroke="#34d399" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty>Select a portfolio to see its equity curve.</Empty>
          )}
        </Panel>

        <Panel title="Agent Contribution">
          {allocation.length ? (
            <div style={{ height: 260 }}>
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={allocation} dataKey="value" nameKey="name" outerRadius={90} label>
                    {allocation.map((entry, index) => (
                      <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <Legend />
                  <Tooltip
                    contentStyle={{
                      background: "#0b1220",
                      border: "1px solid rgba(56,189,248,0.3)",
                      borderRadius: 10,
                      fontSize: 12,
                    }}
                    formatter={(v) => money(v)}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty>No contributions yet.</Empty>
          )}
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel title="Drawdown">
          {selected && equityCurve.length ? (
            <div style={{ height: 240 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={buildDrawdown(equityCurve)}>
                  <defs>
                    <linearGradient id="dd" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#fb7185" stopOpacity={0.5} />
                      <stop offset="100%" stopColor="#fb7185" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" />
                  <XAxis dataKey="index" stroke="#64748b" fontSize={10} />
                  <YAxis stroke="#64748b" fontSize={10} width={70} />
                  <Tooltip
                    contentStyle={{
                      background: "#0b1220",
                      border: "1px solid rgba(56,189,248,0.3)",
                      borderRadius: 10,
                      fontSize: 12,
                    }}
                    formatter={(v) => `${(Number(v) * 100).toFixed(2)}%`}
                  />
                  <Area type="monotone" dataKey="drawdown" stroke="#fb7185" fill="url(#dd)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty>Select a portfolio to see drawdown.</Empty>
          )}
        </Panel>

        <Panel title="Strategy / Position Contribution">
          {strategyContribution.length ? (
            <div style={{ height: 240 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={strategyContribution}>
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
                  <Bar dataKey="value" fill="#8b5cf6" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty>Select a portfolio with open positions.</Empty>
          )}
        </Panel>
      </div>

      <Panel title="System Treasury">
        <table>
          <thead>
            <tr>
              <th>Account</th>
              <th>Kind</th>
              <th>Currency</th>
              <th>Starting</th>
              <th>Cash</th>
              <th>Reserved</th>
              <th>Locked</th>
            </tr>
          </thead>
          <tbody>
            {(treasury.data?.accounts || []).map((a) => (
              <tr key={a.id}>
                <td>{a.name}</td>
                <td className="mono">{a.kind}</td>
                <td className="mono">{a.currency}</td>
                <td className="mono">{money(a.starting_capital, a.currency)}</td>
                <td className="mono">{money(a.cash, a.currency)}</td>
                <td className="mono">{money(a.reserved_capital, a.currency)}</td>
                <td>{a.is_locked ? <Chip tone="warn">locked</Chip> : "—"}</td>
              </tr>
            ))}
            {(treasury.data?.accounts || []).length === 0 && (
              <tr>
                <td colSpan={7} className="faint">
                  Treasury not initialised. Bootstrap a generation.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Panel>

      {reconciliation.data && (
        <Panel title="Accounting Reconciliation">
          <pre className="mono" style={{ fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 240, overflow: "auto" }}>
            {JSON.stringify(reconciliation.data, null, 2)}
          </pre>
        </Panel>
      )}
    </>
  );
}

function buildDrawdown(curve) {
  let peak = 0;
  return curve.map((point) => {
    peak = Math.max(peak, point.value);
    const drawdown = peak > 0 ? (point.value - peak) / peak : 0;
    return { index: point.index, drawdown };
  });
}
