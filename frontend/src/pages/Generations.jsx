import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { generations as generationsApi, research as researchApi } from "../lib/api";
import { usePolling, useMutation } from "../lib/hooks";
import { money, num, pct } from "../lib/format";
import { Panel, Stat, Chip, Empty, ErrorBanner, Loading } from "../components/ui.jsx";

export default function Generations() {
  const [reason, setReason] = useState("");
  const list = usePolling(() => generationsApi.list(), [], 20000);
  const history = usePolling(() => generationsApi.history(), [], 20000);
  const current = usePolling(() => generationsApi.current(), [], 15000);

  const bootstrap = useMutation(() => generationsApi.bootstrap());
  const evolve = useMutation(() => generationsApi.evolve({ reason, limit: 8 }));
  const characteristics = useMutation(() => researchApi.characteristics());

  const rows = history.data?.history || [];
  const chart = rows.map((r) => ({
    name: r.label,
    best: Number(r.best_score || 0),
    value: Number(r.portfolio_value || 0),
  }));
  const currentPerf = rows.find((r) => r.number === current.data?.number) || {};

  return (
    <>
      <ErrorBanner error={list.error || bootstrap.error || evolve.error} />

      <div className="grid cols-4">
        <Stat
          label="Current Generation"
          value={current.data?.label || "—"}
          hint={current.data?.status || "—"}
        />
        <Stat label="Generations" value={list.data?.generations?.length ?? "—"} hint="never deleted" />
        <Stat
          label="Best Score"
          value={rows.length ? num(Math.max(...rows.map((r) => r.best_score)), 4) : "—"}
        />
        <Stat
          label="Active Agents"
          value={(currentPerf.agents || []).length || "—"}
          hint="maximum eight"
        />
      </div>

      <Panel
        title="Evolution"
        actions={
          <div className="btn-row">
            <button className="btn" disabled={bootstrap.busy} onClick={() => bootstrap.run()}>
              {bootstrap.busy ? "Bootstrapping…" : "Bootstrap gen 1"}
            </button>
            <button
              className="btn ghost"
              disabled={characteristics.busy}
              onClick={() => characteristics.run()}
            >
              Extract characteristics
            </button>
          </div>
        }
      >
        <p className="dim" style={{ fontSize: 12.5, marginTop: 0 }}>
          Evolution selects successful characteristics, folds in failure knowledge,
          then combines and mutates to create the next generation. Failed agents
          are stopped, archived and preserved as knowledge — never erased.
        </p>
        <div className="grid cols-2">
          <div className="field">
            <label>Reason for evolution</label>
            <input
              value={reason}
              placeholder="e.g. Gen 2: reward robustness over raw return"
              onChange={(e) => setReason(e.target.value)}
            />
            <button
              className="btn"
              disabled={evolve.busy || !current.data}
              onClick={() => evolve.run()}
              style={{ marginTop: 10 }}
            >
              {evolve.busy ? "Evolving…" : "Evolve to next generation"}
            </button>
          </div>
          <div>
            {characteristics.result && (
              <pre
                className="mono"
                style={{ fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto" }}
              >
                {JSON.stringify(characteristics.result, null, 2)}
              </pre>
            )}
            {evolve.result && (
              <pre
                className="mono"
                style={{ fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto" }}
              >
                {JSON.stringify(evolve.result, null, 2)}
              </pre>
            )}
          </div>
        </div>
      </Panel>

      <Panel title="Best Score per Generation">
        {chart.length ? (
          <div style={{ height: 260 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart}>
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
                />
                <Line type="monotone" dataKey="best" stroke="#8b5cf6" strokeWidth={2} dot />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <Empty>No generation history yet.</Empty>
        )}
      </Panel>

      <Panel title="Family Tree / History">
        {list.loading && !list.data ? (
          <Loading />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Gen</th>
                <th>Label</th>
                <th>Status</th>
                <th>Agents</th>
                <th>Best Score</th>
                <th>Total Value</th>
                <th>Return</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {(list.data?.generations || []).map((g) => {
                const perf = rows.find((r) => r.number === g.number) || {};
                return (
                  <tr key={g.id}>
                    <td className="mono">{g.number}</td>
                    <td className="mono">{g.label}</td>
                    <td>
                      <Chip tone={g.status === "ACTIVE" ? "good" : ""}>{g.status}</Chip>
                    </td>
                    <td className="mono">{(perf.agents || []).length}</td>
                    <td className="mono">{num(perf.best_score, 4)}</td>
                    <td className="mono">{money(perf.portfolio_value)}</td>
                    <td className="mono">{pct(perf.return)}</td>
                    <td className="dim" style={{ maxWidth: 260 }}>
                      {g.creation_reason || "—"}
                    </td>
                  </tr>
                );
              })}
              {(list.data?.generations || []).length === 0 && (
                <tr>
                  <td colSpan={8} className="faint">
                    No generations yet. Bootstrap generation 1 to begin.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
