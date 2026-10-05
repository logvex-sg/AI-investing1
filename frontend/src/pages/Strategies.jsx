import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { strategies as strategiesApi } from "../lib/api";
import { usePolling, useMutation } from "../lib/hooks";
import { num, timeAgo, STAGE_COLORS } from "../lib/format";
import { Panel, Stat, Chip, Empty, ErrorBanner, Loading, Modal } from "../components/ui.jsx";

const STAGES = [
  "IDEA",
  "HYPOTHESIS",
  "IMPLEMENTATION",
  "BACKTEST",
  "OUT_OF_SAMPLE",
  "ROBUSTNESS",
  "PAPER_TRADING",
  "EVALUATION",
  "RETIRED",
  "REJECTED",
];

export default function Strategies() {
  const [params, setParams] = useSearchParams();
  const [stage, setStage] = useState("");
  const [selected, setSelected] = useState(params.get("strategy"));

  const list = usePolling(() => strategiesApi.list({ stage }), [stage], 12000);
  const detail = usePolling(
    () => (selected ? strategiesApi.detail(selected) : Promise.resolve(null)),
    [selected],
    selected ? 8000 : 0
  );

  const [validateForm, setValidateForm] = useState({ symbol: "EURUSD", interval: "1d", limit: 500 });
  const validate = useMutation(() =>
    strategiesApi.validate(selected, {
      symbol: validateForm.symbol,
      interval: validateForm.interval,
      limit: Number(validateForm.limit),
    })
  );
  const [retireReason, setRetireReason] = useState("");
  const retire = useMutation(() => strategiesApi.retire(selected, retireReason));

  useEffect(() => {
    if (selected) setParams({ strategy: selected });
    else setParams({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const rows = list.data?.strategies || [];

  return (
    <>
      <ErrorBanner error={list.error || validate.error || retire.error} />

      <div className="grid cols-4">
        <Stat label="Strategies" value={rows.length} hint={stage || "all stages"} />
        <Stat
          label="In Validation"
          value={rows.filter((s) => ["BACKTEST", "OUT_OF_SAMPLE", "ROBUSTNESS"].includes(s.stage)).length}
          hint="engine-computed"
        />
        <Stat
          label="Paper Trading"
          value={rows.filter((s) => s.stage === "PAPER_TRADING").length}
        />
        <Stat label="Retired" value={rows.filter((s) => s.stage === "RETIRED").length} hint="kept as knowledge" />
      </div>

      <Panel
        title="Strategy Registry"
        actions={
          <select value={stage} onChange={(e) => setStage(e.target.value)} style={{ width: 180 }}>
            <option value="">All stages</option>
            {STAGES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        }
      >
        {list.loading && !list.data ? (
          <Loading />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Stage</th>
                <th>Agent</th>
                <th>Gen</th>
                <th>Ver</th>
                <th>Universe</th>
                <th>Thesis</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} onClick={() => setSelected(s.id)} style={{ cursor: "pointer" }}>
                  <td className="mono">{s.name}</td>
                  <td>
                    <Chip>{s.stage}</Chip>
                  </td>
                  <td className="mono faint">{s.agent_id ? s.agent_id.slice(0, 8) : "—"}</td>
                  <td className="mono">{s.generation_number}</td>
                  <td className="mono">{s.current_version}</td>
                  <td className="mono">{(s.asset_universe || []).join(", ")}</td>
                  <td className="dim" style={{ maxWidth: 260 }}>
                    {s.thesis || "—"}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="faint">
                    No strategies recorded yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="Lifecycle">
        <div className="pipeline">
          {STAGES.map((s) => {
            const count = rows.filter((row) => row.stage === s).length;
            return (
              <div
                key={s}
                className={`pipeline-step ${count > 0 ? "done" : ""}`}
                style={{ borderColor: count > 0 ? STAGE_COLORS[s] : undefined }}
              >
                <div className="mono" style={{ fontSize: 10 }}>
                  {s}
                </div>
                <div className="mono" style={{ fontSize: 16, marginTop: 4 }}>
                  {count}
                </div>
              </div>
            );
          })}
        </div>
      </Panel>

      {selected && (
        <Modal title="Strategy Detail" onClose={() => setSelected(null)}>
          {detail.loading && !detail.data ? (
            <Loading />
          ) : !detail.data ? (
            <Empty>Strategy not found.</Empty>
          ) : (
            <>
              <table>
                <tbody>
                  <tr>
                    <td className="faint">Name</td>
                    <td className="mono">{detail.data.strategy.name}</td>
                  </tr>
                  <tr>
                    <td className="faint">Stage</td>
                    <td>
                      <Chip>{detail.data.strategy.stage}</Chip>
                    </td>
                  </tr>
                  <tr>
                    <td className="faint">Version</td>
                    <td className="mono">{detail.data.strategy.current_version}</td>
                  </tr>
                  <tr>
                    <td className="faint">Time horizon</td>
                    <td>{detail.data.strategy.time_horizon || "—"}</td>
                  </tr>
                  <tr>
                    <td className="faint">Creation reason</td>
                    <td className="dim">{detail.data.strategy.creation_reason || "—"}</td>
                  </tr>
                  {detail.data.best_result && (
                    <tr>
                      <td className="faint">Best score</td>
                      <td className="mono">
                        {num(detail.data.best_result.score, 4)} ·{" "}
                        {detail.data.best_result.passed ? "passed" : "failed"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>

              <h3 className="panel-title" style={{ marginTop: 18 }}>
                <span className="dot" /> Active version
              </h3>
              {detail.data.active_version ? (
                <pre className="mono" style={{ fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto" }}>
                  {JSON.stringify(
                    {
                      parameters: detail.data.active_version.parameters,
                      entry_rules: detail.data.active_version.entry_rules,
                      exit_rules: detail.data.active_version.exit_rules,
                      risk_rules: detail.data.active_version.risk_rules,
                      mutation_reason: detail.data.active_version.mutation_reason,
                    },
                    null,
                    2
                  )}
                </pre>
              ) : (
                <Empty>No active version.</Empty>
              )}

              <h3 className="panel-title" style={{ marginTop: 18 }}>
                <span className="dot" /> Run validation
              </h3>
              <p className="faint" style={{ fontSize: 11.5, marginTop: 0 }}>
                Backtest → out-of-sample → robustness. The engine computes every
                number; the AI only interprets them.
              </p>
              <div className="grid cols-3">
                <div className="field">
                  <label>Symbol</label>
                  <select
                    value={validateForm.symbol}
                    onChange={(e) => setValidateForm({ ...validateForm, symbol: e.target.value })}
                  >
                    <option>EURUSD</option>
                    <option>USDJPY</option>
                    <option>GBPUSD</option>
                    <option>BTCUSD</option>
                  </select>
                </div>
                <div className="field">
                  <label>Interval</label>
                  <select
                    value={validateForm.interval}
                    onChange={(e) => setValidateForm({ ...validateForm, interval: e.target.value })}
                  >
                    <option value="1d">1d</option>
                    <option value="4h">4h</option>
                    <option value="1h">1h</option>
                  </select>
                </div>
                <div className="field">
                  <label>Bars</label>
                  <input
                    value={validateForm.limit}
                    onChange={(e) => setValidateForm({ ...validateForm, limit: e.target.value })}
                  />
                </div>
              </div>
              <button className="btn" disabled={validate.busy} onClick={() => validate.run()}>
                {validate.busy ? "Validating…" : "Validate"}
              </button>
              {validate.result && (
                <pre className="mono" style={{ marginTop: 12, fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}>
                  {JSON.stringify(validate.result, null, 2)}
                </pre>
              )}

              <h3 className="panel-title" style={{ marginTop: 18 }}>
                <span className="dot" /> Retire
              </h3>
              <div className="field">
                <label>Reason</label>
                <input
                  value={retireReason}
                  onChange={(e) => setRetireReason(e.target.value)}
                  placeholder="Why is this strategy being retired?"
                />
              </div>
              <button
                className="btn bad"
                disabled={!retireReason || retire.busy}
                onClick={() => retire.run()}
              >
                {retire.busy ? "Retiring…" : "Retire strategy"}
              </button>
            </>
          )}
        </Modal>
      )}
    </>
  );
}
