import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { agents as agentsApi, generations as generationsApi } from "../lib/api";
import { usePolling } from "../lib/hooks";
import { num, timeAgo } from "../lib/format";
import NetworkGraph from "../components/NetworkGraph.jsx";
import { Panel, Chip, ErrorBanner, Loading, Stat } from "../components/ui.jsx";

export default function Network() {
  const navigate = useNavigate();
  const [generation, setGeneration] = useState(null);
  const [selected, setSelected] = useState(null);

  const generations = usePolling(() => generationsApi.list(), [], 30000);
  const { data, error, loading } = usePolling(
    () => agentsApi.network(generation),
    [generation],
    9000
  );

  const observation = data?.observation;
  const ranking = data?.ranking || [];

  return (
    <>
      <ErrorBanner error={error} />

      <div className="grid cols-4">
        <Stat label="Generation" value={data?.generation_number ?? "—"} hint="network scope" />
        <Stat label="Nodes" value={data?.nodes?.length ?? "—"} hint="overseer + agents" />
        <Stat label="Edges" value={data?.edges?.length ?? "—"} hint="relationships" />
        <Stat
          label="Leader"
          value={observation?.leader || "—"}
          hint={observation ? `median ${num(observation.median_score, 3)}` : ""}
        />
      </div>

      <Panel
        title="Agent Network"
        actions={
          <div className="btn-row">
            <select
              value={generation ?? ""}
              onChange={(e) => setGeneration(e.target.value ? Number(e.target.value) : null)}
              style={{ width: 170 }}
            >
              <option value="">Current generation</option>
              {(generations.data?.generations || []).map((g) => (
                <option key={g.number} value={g.number}>
                  {g.label} ({g.status})
                </option>
              ))}
            </select>
          </div>
        }
      >
        {loading && !data ? (
          <Loading label="Mapping the network" />
        ) : (
          <NetworkGraph
            nodes={data?.nodes || []}
            edges={data?.edges || []}
            selectedId={selected}
            onSelect={(node) => {
              if (node.kind === "AGENT") {
                setSelected(node.id);
                navigate(`/agents/${node.id}`);
              }
            }}
          />
        )}
        <p className="faint" style={{ fontSize: 11, marginTop: 10 }}>
          Drag to pan, scroll to zoom, click an agent to open its dossier. The
          Overseer coordinates; agents may disagree, and disagreement produces
          experiments rather than forced consensus.
        </p>
      </Panel>

      <div className="grid cols-2">
        <Panel title="Overseer Observation">
          {observation ? (
            <table>
              <tbody>
                <tr>
                  <td className="faint">Generation</td>
                  <td className="mono">{observation.generation_number}</td>
                </tr>
                <tr>
                  <td className="faint">Active agents</td>
                  <td className="mono">{observation.active_agents}</td>
                </tr>
                <tr>
                  <td className="faint">Median score</td>
                  <td className="mono">{num(observation.median_score, 4)}</td>
                </tr>
                <tr>
                  <td className="faint">Leader</td>
                  <td className="mono">{observation.leader || "—"}</td>
                </tr>
                <tr>
                  <td className="faint">Problems</td>
                  <td className="mono">{(observation.problems || []).length}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <Loading />
          )}
          {(observation?.problems || []).length > 0 && (
            <div className="warning-banner" style={{ marginTop: 12 }}>
              {observation.problems.join(" · ")}
            </div>
          )}
        </Panel>

        <Panel title="Ranking">
          <table>
            <thead>
              <tr>
                <th>Agent</th>
                <th>Specialization</th>
                <th>Score</th>
                <th>Experiments</th>
              </tr>
            </thead>
            <tbody>
              {ranking.map((row) => (
                <tr key={row.codename}>
                  <td className="mono">{row.codename}</td>
                  <td className="dim">{row.specialization}</td>
                  <td className={`mono ${row.score >= 0 ? "good" : "bad"}`}>
                    {num(row.score, 4)}
                  </td>
                  <td className="mono">{row.experiments}</td>
                </tr>
              ))}
              {ranking.length === 0 && (
                <tr>
                  <td colSpan={4} className="faint">
                    No graded experiments yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  );
}
