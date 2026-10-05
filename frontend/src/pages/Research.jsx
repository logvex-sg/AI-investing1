import { useState } from "react";

import { research as researchApi } from "../lib/api";
import { usePolling, useMutation } from "../lib/hooks";
import { num, timeAgo, STAGE_COLORS } from "../lib/format";
import { Panel, Stat, Chip, Empty, ErrorBanner, Loading, Progress } from "../components/ui.jsx";

const PIPELINE = [
  "BACKTEST",
  "OUT_OF_SAMPLE",
  "ROBUSTNESS",
  "PAPER_TRADING",
];

export default function Research() {
  const [objective, setObjective] = useState("");
  const questions = usePolling(() => researchApi.questions(), [], 10000);
  const experiments = usePolling(() => researchApi.experiments(), [], 10000);
  const observation = usePolling(() => researchApi.observation(), [], 12000);

  const [selected, setSelected] = useState(null);
  const detail = usePolling(
    () => (selected ? researchApi.question(selected) : Promise.resolve(null)),
    [selected],
    selected ? 8000 : 0
  );

  const cycle = useMutation(() => researchApi.cycle({ objective, run_assignments: true }));

  const obs = observation.data;
  const openQuestions = (questions.data?.questions || []).filter((q) => q.status === "OPEN");
  const roster = obs?.agents || [];
  const leader = roster.length
    ? roster.reduce((best, a) => (Number(a.score) > Number(best.score) ? a : best), roster[0])
    : null;

  return (
    <>
      <ErrorBanner error={questions.error || cycle.error} />

      <div className="grid cols-4">
        <Stat label="Open Questions" value={openQuestions.length} hint="awaiting answers" />
        <Stat
          label="Experiments"
          value={(experiments.data?.experiments || []).length}
          hint="recorded"
        />
        <Stat
          label="Leader"
          value={leader?.codename || "—"}
          hint={leader ? `${leader.specialization} · ${num(leader.score, 3)}` : "highest score"}
        />
        <Stat
          label="Roster"
          value={`${roster.length}/${8}`}
          hint="active agents"
          tone={roster.length < 8 ? "warn" : ""}
        />
      </div>

      <Panel title="Overseer Cycle">
        <p className="dim" style={{ fontSize: 12.5, marginTop: 0 }}>
          OBSERVE → ANALYZE → IDENTIFY PROBLEM → FORM HYPOTHESIS → ASSIGN
          EXPERIMENT → RECEIVE RESULTS → EVALUATE → LEARN → PLAN NEXT.
        </p>
        <div className="field">
          <label>Research objective (optional)</label>
          <input
            value={objective}
            placeholder="e.g. Which regime filter best protects momentum on EURUSD?"
            onChange={(e) => setObjective(e.target.value)}
          />
        </div>
        <button className="btn" disabled={cycle.busy} onClick={() => cycle.run()}>
          {cycle.busy ? "Running cycle…" : "Run Overseer cycle"}
        </button>
        {cycle.result && (
          <pre
            className="mono"
            style={{ marginTop: 12, fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 260, overflow: "auto" }}
          >
            {JSON.stringify(cycle.result, null, 2)}
          </pre>
        )}
        {obs && (
          <div style={{ marginTop: 14 }}>
            <div className="faint" style={{ fontSize: 11, marginBottom: 6 }}>
              Generation progress
            </div>
            <Progress value={Math.min(1, (obs.experiments || 0) / 40)} />
          </div>
        )}
      </Panel>

      <div className="grid cols-2">
        <Panel title="Research Questions">
          {questions.loading && !questions.data ? (
            <Loading />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Question</th>
                  <th>Status</th>
                  <th>Priority</th>
                  <th>Gen</th>
                </tr>
              </thead>
              <tbody>
                {(questions.data?.questions || []).map((q) => (
                  <tr
                    key={q.id}
                    onClick={() => setSelected(q.id)}
                    style={{ cursor: "pointer" }}
                  >
                    <td style={{ maxWidth: 320 }}>{q.question}</td>
                    <td>
                      <Chip tone={q.status === "OPEN" ? "warn" : "good"}>{q.status}</Chip>
                    </td>
                    <td className="mono">{num(q.priority, 2)}</td>
                    <td className="mono">{q.generation_number}</td>
                  </tr>
                ))}
                {(questions.data?.questions || []).length === 0 && (
                  <tr>
                    <td colSpan={4} className="faint">
                      No research questions yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Question Detail">
          {!selected ? (
            <Empty>Select a question to inspect its hypotheses and experiments.</Empty>
          ) : detail.loading && !detail.data ? (
            <Loading />
          ) : (
            <>
              <p className="dim" style={{ fontSize: 13, marginTop: 0 }}>
                {detail.data?.question?.question}
              </p>
              <div className="faint" style={{ fontSize: 11 }}>
                rationale: {detail.data?.question?.rationale || "—"}
              </div>
              <h3 className="panel-title" style={{ marginTop: 16 }}>
                <span className="dot" /> Hypotheses
              </h3>
              {(detail.data?.hypotheses || []).map((h) => (
                <div key={h.id} className="stat" style={{ padding: "10px 12px", marginBottom: 6 }}>
                  <div style={{ fontSize: 12.5 }}>{h.statement}</div>
                  <div className="faint mono" style={{ fontSize: 10.5, marginTop: 4 }}>
                    prior {num(h.prior_confidence, 2)} → posterior{" "}
                    {num(h.posterior_confidence, 2)} · {h.status}
                  </div>
                </div>
              ))}
              {(detail.data?.hypotheses || []).length === 0 && (
                <Empty>No hypotheses yet.</Empty>
              )}
              <h3 className="panel-title" style={{ marginTop: 16 }}>
                <span className="dot" /> Experiments
              </h3>
              {(detail.data?.experiments || []).map((e) => (
                <div key={e.id} className="stat" style={{ padding: "10px 12px", marginBottom: 6 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <Chip tone={e.status === "COMPLETED" ? "good" : "warn"}>{e.status}</Chip>
                    <span style={{ fontSize: 12.5 }}>{e.title}</span>
                  </div>
                </div>
              ))}
              {(detail.data?.experiments || []).length === 0 && (
                <Empty>No experiments yet.</Empty>
              )}
            </>
          )}
        </Panel>
      </div>

      <Panel title="Experiment Pipeline">
        <div className="pipeline">
          {PIPELINE.map((stage) => {
            const count = (experiments.data?.experiments || []).filter(
              (e) => e.stage === stage
            ).length;
            return (
              <div
                key={stage}
                className={`pipeline-step ${count > 0 ? "done" : ""}`}
                style={{ borderColor: count > 0 ? STAGE_COLORS[stage] : undefined }}
              >
                <div className="mono" style={{ fontSize: 10, letterSpacing: "0.08em" }}>
                  {stage}
                </div>
                <div className="mono" style={{ fontSize: 18, marginTop: 4 }}>
                  {count}
                </div>
              </div>
            );
          })}
        </div>
      </Panel>

      <Panel title="Experiments">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Stage</th>
              <th>Status</th>
              <th>Score</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {(experiments.data?.experiments || []).map((e) => (
              <tr key={e.id}>
                <td>{e.title}</td>
                <td className="mono">{e.stage}</td>
                <td>
                  <Chip tone={e.status === "COMPLETED" ? "good" : e.status === "FAILED" ? "bad" : "warn"}>
                    {e.status}
                  </Chip>
                </td>
                <td className={`mono ${Number(e.result?.score) >= 0 ? "good" : "bad"}`}>
                  {e.result?.score === null || e.result?.score === undefined
                    ? "—"
                    : num(e.result.score, 4)}
                </td>
                <td className="faint">
                  {timeAgo(e.completed_at || e.started_at)}
                </td>
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
    </>
  );
}
