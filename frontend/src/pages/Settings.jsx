import { useState } from "react";

import { system as systemApi, risk as riskApi, generations as generationsApi } from "../lib/api";
import { usePolling, useMutation } from "../lib/hooks";
import { useAuth } from "../lib/auth.jsx";
import { Panel, Stat, Chip, Empty, ErrorBanner, Loading } from "../components/ui.jsx";

export default function Settings() {
  const { user, logout } = useAuth();
  const health = usePolling(() => systemApi.health(), [], 15000);
  const limits = usePolling(() => riskApi.limits(), [], 20000);
  const symbols = usePolling(() => systemApi.marketSymbols(), [], 30000);
  const generations = usePolling(() => generationsApi.current(), [], 15000);

  const [stopReason, setStopReason] = useState("");
  const stop = useMutation(() => systemApi.emergencyStop(stopReason || "Manual halt"));
  const release = useMutation(() => systemApi.releaseEmergencyStop());
  const mark = useMutation(() => systemApi.mark());
  const bootstrap = useMutation(() => generationsApi.bootstrap());

  const effective = limits.data?.effective;
  const emergency = limits.data?.emergency_stop;

  return (
    <>
      <ErrorBanner error={health.error || stop.error || release.error || mark.error} />

      <div className="grid cols-4">
        <Stat label="Environment" value={health.data?.environment || "—"} />
        <Stat label="LLM Provider" value={health.data?.llm_provider || "—"} hint="single shared model" />
        <Stat label="Execution" value={health.data?.execution_mode || "—"} hint="paper" />
        <Stat
          label="Testnet"
          value={health.data?.testnet_enabled ? "enabled" : "disabled"}
          tone={health.data?.testnet_enabled ? "warn" : ""}
        />
      </div>

      <Panel
        title="Emergency Stop"
        actions={
          <Chip tone={emergency?.active ? "bad" : "good"}>
            {emergency?.active ? "ACTIVE" : "ARMED"}
          </Chip>
        }
      >
        <p className="dim" style={{ fontSize: 12.5, marginTop: 0 }}>
          The emergency stop blocks new execution proposals and halts execution.
          It preserves all state and records a security event. Nothing is deleted.
        </p>
        {emergency?.active ? (
          <>
            <div className="warning-banner">Reason: {emergency.reason || "—"}</div>
            <button className="btn good" disabled={release.busy} onClick={() => release.run()}>
              {release.busy ? "Releasing…" : "Release emergency stop"}
            </button>
          </>
        ) : (
          <>
            <div className="field">
              <label>Reason</label>
              <input
                value={stopReason}
                onChange={(e) => setStopReason(e.target.value)}
                placeholder="Why are you halting the system?"
              />
            </div>
            <button className="btn danger" disabled={stop.busy} onClick={() => stop.run()}>
              {stop.busy ? "Engaging…" : "Engage emergency stop"}
            </button>
          </>
        )}
      </Panel>

      <div className="grid cols-2">
        <Panel title="Risk Limits" actions={<Chip>deterministic</Chip>}>
          {limits.loading && !limits.data ? (
            <Loading />
          ) : effective ? (
            <table>
              <tbody>
                <tr>
                  <td className="faint">Max position</td>
                  <td className="mono">{effective.max_position_pct}</td>
                </tr>
                <tr>
                  <td className="faint">Max exposure</td>
                  <td className="mono">{effective.max_exposure_pct}</td>
                </tr>
                <tr>
                  <td className="faint">Max drawdown</td>
                  <td className="mono">{effective.max_drawdown_pct}</td>
                </tr>
                <tr>
                  <td className="faint">Max daily loss</td>
                  <td className="mono">{effective.max_daily_loss_pct}</td>
                </tr>
                <tr>
                  <td className="faint">Max concentration</td>
                  <td className="mono">{effective.max_concentration_pct}</td>
                </tr>
                <tr>
                  <td className="faint">Max trades / day</td>
                  <td className="mono">{effective.max_trades_per_day}</td>
                </tr>
                <tr>
                  <td className="faint">Asset allowlist</td>
                  <td className="mono">{(effective.asset_allowlist || []).join(", ")}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <Empty>Risk limits unavailable.</Empty>
          )}
          <p className="faint" style={{ fontSize: 11, marginTop: 10 }}>
            These limits are read-only through the API. The AI has no write path
            to them; changing them requires editing configuration and restarting.
          </p>
        </Panel>

        <Panel title="Market Data" actions={<button className="btn ghost" onClick={() => mark.run()}>{mark.busy ? "Marking…" : "Mark portfolios"}</button>}>
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Class</th>
                <th>Bars</th>
                <th>Volatility</th>
              </tr>
            </thead>
            <tbody>
              {(symbols.data?.symbols || []).map((s) => (
                <tr key={s.symbol}>
                  <td className="mono">{s.symbol}</td>
                  <td className="mono">{s.asset_class}</td>
                  <td className="mono">{s.bars}</td>
                  <td className="mono">{s.volatility}</td>
                </tr>
              ))}
              {(symbols.data?.symbols || []).length === 0 && (
                <tr>
                  <td colSpan={4} className="faint">
                    No market data ingested. Seed it from the backend script.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          {mark.result && (
            <pre className="mono" style={{ marginTop: 10, fontSize: 11, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(mark.result, null, 2)}
            </pre>
          )}
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel title="Generation">
          <table>
            <tbody>
              <tr>
                <td className="faint">Current</td>
                <td className="mono">{generations.data?.label || "—"}</td>
              </tr>
              <tr>
                <td className="faint">Status</td>
                <td className="mono">{generations.data?.status || "—"}</td>
              </tr>
            </tbody>
          </table>
          <button className="btn ghost" style={{ marginTop: 10 }} disabled={bootstrap.busy} onClick={() => bootstrap.run()}>
            {bootstrap.busy ? "Bootstrapping…" : "Bootstrap generation 1"}
          </button>
          {bootstrap.result && (
            <pre className="mono" style={{ marginTop: 10, fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto" }}>
              {JSON.stringify(bootstrap.result, null, 2)}
            </pre>
          )}
        </Panel>

        <Panel title="Session">
          <table>
            <tbody>
              <tr>
                <td className="faint">User</td>
                <td className="mono">{user?.username}</td>
              </tr>
              <tr>
                <td className="faint">Role</td>
                <td>
                  <Chip>{user?.role}</Chip>
                </td>
              </tr>
              <tr>
                <td className="faint">Display name</td>
                <td>{user?.display_name || "—"}</td>
              </tr>
            </tbody>
          </table>
          <p className="faint" style={{ fontSize: 11.5, marginTop: 12 }}>
            Only a human identity can approve proposals. The AI cannot
            impersonate the human, and every privileged operation is audited.
          </p>
          <button className="btn bad" onClick={logout}>
            Sign out
          </button>
        </Panel>
      </div>
    </>
  );
}
