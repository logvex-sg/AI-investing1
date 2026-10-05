import { useNavigate } from "react-router-dom";

import { agents as agentsApi } from "../lib/api";
import { usePolling } from "../lib/hooks";
import { money, num, signed } from "../lib/format";
import { Panel, Chip, ErrorBanner, Loading, Stat } from "../components/ui.jsx";

export default function Agents() {
  const navigate = useNavigate();
  const { data, error, loading } = usePolling(() => agentsApi.list(), [], 8000);
  const agents = data?.agents || [];

  const totalValue = agents.reduce((sum, a) => sum + Number(a.portfolio?.total_value || 0), 0);
  const totalRealized = agents.reduce(
    (sum, a) => sum + Number(a.portfolio?.realized_profit || 0),
    0
  );

  return (
    <>
      <ErrorBanner error={error} />

      <div className="grid cols-4">
        <Stat label="Active Agents" value={`${agents.length}/8`} hint="logical identities" />
        <Stat label="Combined Value" value={money(totalValue)} hint="across portfolios" />
        <Stat
          label="Combined Realized"
          value={signed(totalRealized)}
          tone={totalRealized >= 0 ? "good" : "bad"}
        />
        <Stat
          label="Specializations"
          value={new Set(agents.map((a) => a.specialization)).size}
          hint="distinct roles"
        />
      </div>

      <Panel title="Roster">
        {loading && !data ? (
          <Loading />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Codename</th>
                <th>Specialization</th>
                <th>Gen</th>
                <th>Objective</th>
                <th>Portfolio</th>
                <th>Realized</th>
                <th>Drawdown</th>
              </tr>
            </thead>
            <tbody>
              {agents.map((agent) => (
                <tr
                  key={agent.id}
                  onClick={() => navigate(`/agents/${agent.id}`)}
                  style={{ cursor: "pointer" }}
                >
                  <td className="mono">{agent.codename}</td>
                  <td>
                    <Chip>{agent.specialization}</Chip>
                  </td>
                  <td className="mono">{agent.generation_number}</td>
                  <td className="dim" style={{ maxWidth: 280 }}>
                    {agent.current_objective || "—"}
                  </td>
                  <td className="mono">{money(agent.portfolio?.total_value)}</td>
                  <td
                    className={`mono ${
                      Number(agent.portfolio?.realized_profit) >= 0 ? "good" : "bad"
                    }`}
                  >
                    {signed(agent.portfolio?.realized_profit)}
                  </td>
                  <td className="mono">{num(agent.portfolio?.max_drawdown, 4)}</td>
                </tr>
              ))}
              {agents.length === 0 && (
                <tr>
                  <td colSpan={7} className="faint">
                    No active agents. Bootstrap a generation from Settings.
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
