import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { system as systemApi, approvals as approvalsApi, activity as activityApi } from "../lib/api";
import { usePolling, useLiveEvents } from "../lib/hooks";
import { money, pct, signed, timeAgo, CATEGORY_COLORS, SEVERITY_COLORS } from "../lib/format";
import { Panel, Stat, Chip, SimBadge, ErrorBanner, Loading, Progress } from "../components/ui.jsx";

export default function Overview() {
  const navigate = useNavigate();
  const { data, error, loading } = usePolling(() => systemApi.overview("1d"), [], 7000);
  const pending = usePolling(() => approvalsApi.pending(), [], 6000);
  const [liveEvents, setLiveEvents] = useState([]);
  const connected = useLiveEvents(
    (event) => setLiveEvents((prev) => [event, ...prev].slice(0, 40)),
    activityApi.streamUrl()
  );

  const portfolio = data?.portfolio;
  const generation = data?.generation;
  const counts = data?.counts || {};

  const equityCurve = (data?.portfolios || [])
    .filter((p) => Number(p.total_value) > 0)
    .map((p) => ({ name: p.name || "unassigned", value: Number(p.total_value) }));

  const feed = [...liveEvents, ...(data?.recent_events || [])].slice(0, 24);

  return (
    <>
      <ErrorBanner error={error} />

      <div className="grid cols-4">
        <Stat
          label="Portfolio Value"
          value={money(portfolio?.total_value)}
          hint={`start ${money(portfolio?.starting_capital)}`}
          tone={
            Number(portfolio?.total_value) >= Number(portfolio?.starting_capital)
              ? "good"
              : "bad"
          }
        />
        <Stat
          label="Realized P/L"
          value={signed(portfolio?.realized_profit)}
          hint="closed positions"
          tone={Number(portfolio?.realized_profit) >= 0 ? "good" : "bad"}
        />
        <Stat
          label="Unrealized P/L"
          value={signed(portfolio?.unrealized_pnl)}
          hint="open positions"
          tone={Number(portfolio?.unrealized_pnl) >= 0 ? "good" : "bad"}
        />
        <Stat
          label="Max Drawdown"
          value={pct(portfolio?.max_drawdown)}
          hint={`exposure ${pct(portfolio?.exposure)}`}
          tone={Number(portfolio?.max_drawdown) > 0.1 ? "warn" : ""}
        />
      </div>

      <div className="grid cols-4">
        <Stat label="Generation" value={generation?.label || "—"} hint={generation?.status || "—"} />
        <Stat label="Active Agents" value={`${counts.active_agents ?? "—"}/8`} hint="maximum eight" />
        <Stat
          label="Experiments"
          value={counts.experiments ?? "—"}
          hint={`${counts.risk_events ?? 0} risk events`}
        />
        <Stat
          label="Approvals Pending"
          value={counts.pending_approvals ?? 0}
          hint="human decisions"
          tone={counts.pending_approvals > 0 ? "warn" : ""}
        />
      </div>

      <Panel
        title="Portfolio"
        actions={
          <div className="btn-row">
            <SimBadge />
            <Chip tone={Number(portfolio?.total_return) >= 0 ? "good" : "bad"}>
              {pct(portfolio?.total_return)} total return
            </Chip>
          </div>
        }
      >
        {loading && !data ? (
          <Loading />
        ) : (
          <div className="grid cols-2">
            <div>
              <div style={{ height: 240 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart
                    data={[
                      { name: "start", value: Number(portfolio?.starting_capital) || 0 },
                      ...equityCurve,
                    ]}
                  >
                    <defs>
                      <linearGradient id="eq" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.5} />
                        <stop offset="100%" stopColor="#38bdf8" stopOpacity={0} />
                      </linearGradient>
                    </defs>
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
                    <Area
                      type="monotone"
                      dataKey="value"
                      stroke="#38bdf8"
                      fill="url(#eq)"
                      strokeWidth={2}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <p className="faint" style={{ fontSize: 11 }}>
                Value per agent portfolio, with the starting capital as the first point.
              </p>
            </div>
            <div>
              <table>
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th>Value</th>
                    <th>Realized</th>
                    <th>Unrealized</th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.portfolios || []).map((p) => (
                    <tr
                      key={p.id}
                      onClick={() => p.agent_id && navigate(`/agents/${p.agent_id}`)}
                      style={{ cursor: p.agent_id ? "pointer" : "default" }}
                    >
                      <td className="mono">{p.name || "unassigned"}</td>
                      <td className="mono">{money(p.total_value)}</td>
                      <td className={`mono ${Number(p.realized_profit) >= 0 ? "good" : "bad"}`}>
                        {signed(p.realized_profit)}
                      </td>
                      <td className={`mono ${Number(p.unrealized_pnl) >= 0 ? "good" : "bad"}`}>
                        {signed(p.unrealized_pnl)}
                      </td>
                    </tr>
                  ))}
                  {(data?.portfolios || []).length === 0 && (
                    <tr>
                      <td colSpan={4} className="faint">
                        No portfolios yet. Bootstrap a generation from Settings.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Panel>

      <div className="grid cols-2">
        <Panel title="Overseer Objective" actions={<Chip>{generation?.label || "—"}</Chip>}>
          <p className="dim" style={{ fontSize: 13, marginTop: 0 }}>
            {data?.objective || "No active objective. Run an Overseer cycle from Research."}
          </p>
          <div className="btn-row">
            <button className="btn" onClick={() => navigate("/research")}>
              Open research
            </button>
            <button className="btn ghost" onClick={() => navigate("/network")}>
              View network
            </button>
          </div>
          <div style={{ marginTop: 14 }}>
            <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>
              Research pipeline progress
            </div>
            <Progress value={Math.min(1, (counts.experiments || 0) / 40)} />
            <div className="faint" style={{ fontSize: 11, marginTop: 6 }}>
              {counts.experiments || 0} experiments recorded
            </div>
          </div>
        </Panel>

        <Panel
          title="Approvals"
          actions={
            <button className="btn ghost" onClick={() => navigate("/approvals")}>
              Open queue
            </button>
          }
        >
          {pending.data?.count ? (
            <div className="btn-row" style={{ flexDirection: "column", alignItems: "stretch" }}>
              {pending.data.pending.slice(0, 4).map((item) => (
                <div
                  key={item.id}
                  className="stat"
                  style={{ padding: "10px 12px", cursor: "pointer" }}
                  onClick={() => navigate("/approvals")}
                >
                  <div className="mono" style={{ fontSize: 12 }}>
                    {item.proposal?.action} {item.proposal?.quantity} {item.proposal?.symbol}
                  </div>
                  <div className="faint" style={{ fontSize: 11 }}>
                    {item.is_high_impact ? "HIGH IMPACT · " : ""}
                    {money(item.proposal?.amount)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="faint" style={{ fontSize: 12.5 }}>
              No proposals awaiting a human decision.
            </p>
          )}
        </Panel>
      </div>

      <Panel title="Activity" actions={<Chip>{connected ? "live" : "polling"}</Chip>}>
        <div className="feed">
          {feed.map((event) => (
            <div className="feed-item" key={event.id}>
              <span className="feed-time">{timeAgo(event.created_at)}</span>
              <span
                className="feed-cat"
                style={{ color: CATEGORY_COLORS[event.category] || "#64748b" }}
              >
                {event.category}
              </span>
              <span style={{ color: SEVERITY_COLORS[event.severity] || undefined }}>
                {event.message}
              </span>
            </div>
          ))}
          {feed.length === 0 && <div className="empty">No events yet.</div>}
        </div>
      </Panel>
    </>
  );
}
