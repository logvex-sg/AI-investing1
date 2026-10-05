import { useState } from "react";

import { approvals as approvalsApi, agents as agentsApi } from "../lib/api";
import { usePolling, useMutation } from "../lib/hooks";
import { money, timeAgo } from "../lib/format";
import { Panel, Stat, Chip, Empty, ErrorBanner, Loading, Modal } from "../components/ui.jsx";

const CONFIRM_PHRASE = "CONFIRM HIGH IMPACT";

export default function Approvals() {
  const pending = usePolling(() => approvalsApi.pending(), [], 5000);
  const executions = usePolling(() => approvalsApi.executions(), [], 8000);
  const agents = usePolling(() => agentsApi.list(), [], 30000);

  const [decision, setDecision] = useState(null);
  const [reason, setReason] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [adapter, setAdapter] = useState("PAPER_TRADING");
  const [marketPrice, setMarketPrice] = useState("");
  const [lastResult, setLastResult] = useState(null);

  const decide = useMutation((id, approve) =>
    approvalsApi.decide(id, {
      approve,
      reason,
      confirmation: approve ? confirmation : undefined,
    })
  );
  const execute = useMutation((proposalId) =>
    approvalsApi.execute(proposalId, {
      adapter_kind: adapter,
      market_price: marketPrice || undefined,
    })
  );

  const items = pending.data?.pending || [];

  function open(item) {
    setDecision(item);
    setReason("");
    setConfirmation("");
    setMarketPrice(item.proposal?.price || "");
    setLastResult(null);
  }

  async function submitDecision(approve) {
    try {
      await decide.run(decision.id, approve);
      setLastResult(
        approve
          ? "Approved. You can now execute the proposal below."
          : "Rejected. The proposal will not execute."
      );
    } catch {
      /* error is surfaced through decide.error */
    }
  }

  return (
    <>
      <ErrorBanner error={pending.error || decide.error || execute.error} />

      <div className="grid cols-4">
        <Stat
          label="Pending"
          value={items.length}
          hint="awaiting a human"
          tone={items.length ? "warn" : ""}
        />
        <Stat
          label="High Impact"
          value={items.filter((i) => i.is_high_impact).length}
          hint="needs confirmation phrase"
        />
        <Stat
          label="Recent Executions"
          value={(executions.data?.executions || []).length}
        />
        <Stat
          label="Adapters"
          value="paper / simulated"
          hint="production gated"
        />
      </div>

      <div className="warning-banner">
        The AI cannot approve its own actions. Only a signed-in human identity can
        approve, and every decision is audited.
      </div>

      <Panel title="Pending Proposals">
        {pending.loading && !pending.data ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty>No proposals awaiting a decision.</Empty>
        ) : (
          <div className="grid cols-2">
            {items.map((item) => (
              <div key={item.id} className="panel" style={{ margin: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Chip tone={item.is_high_impact ? "bad" : ""}>
                    {item.is_high_impact ? "HIGH IMPACT" : item.proposal_kind}
                  </Chip>
                  <div className="spacer" />
                  <span className="faint mono" style={{ fontSize: 10.5 }}>
                    {timeAgo(item.created_at)}
                  </span>
                </div>
                <div className="mono" style={{ fontSize: 14, margin: "10px 0 4px" }}>
                  {item.proposal?.action} {item.proposal?.quantity} {item.proposal?.symbol}
                </div>
                <div className="dim" style={{ fontSize: 12 }}>
                  {money(item.proposal?.amount)} ·{" "}
                  {item.proposal?.risk_decision || "—"} risk ·{" "}
                  {item.proposal?.accounting_ok ? "accounting OK" : "accounting pending"}
                </div>
                <p className="faint" style={{ fontSize: 11.5 }}>
                  {item.proposal?.reason || "No reason supplied."}
                </p>
                <div className="btn-row">
                  <button className="btn good" onClick={() => open(item)}>
                    Review & approve
                  </button>
                  <button
                    className="btn bad"
                    onClick={() => {
                      setDecision(item);
                      setReason("");
                      decide.run(item.id, false);
                    }}
                  >
                    Quick reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Recent Executions">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Symbol</th>
              <th>Adapter</th>
              <th>Status</th>
              <th>Filled</th>
              <th>Price</th>
              <th>Fee</th>
              <th>Simulated</th>
            </tr>
          </thead>
          <tbody>
            {(executions.data?.executions || []).map((e) => (
              <tr key={e.id}>
                <td className="faint">{timeAgo(e.created_at)}</td>
                <td className="mono">{e.symbol}</td>
                <td className="mono">{e.adapter}</td>
                <td>
                  <Chip tone={e.status === "FILLED" ? "good" : e.status === "REJECTED" ? "bad" : "warn"}>
                    {e.status}
                  </Chip>
                </td>
                <td className="mono">{e.filled_quantity}</td>
                <td className="mono">{e.fill_price}</td>
                <td className="mono">{e.fee}</td>
                <td>{e.is_simulated ? "yes" : "no"}</td>
              </tr>
            ))}
            {(executions.data?.executions || []).length === 0 && (
              <tr>
                <td colSpan={8} className="faint">
                  No executions recorded.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Panel>

      {decision && (
        <Modal title="Human Decision" onClose={() => setDecision(null)}>
          <table>
            <tbody>
              <tr>
                <td className="faint">Proposal ID</td>
                <td className="mono">{decision.proposal?.id}</td>
              </tr>
              <tr>
                <td className="faint">Action</td>
                <td className="mono">
                  {decision.proposal?.action} {decision.proposal?.quantity}{" "}
                  {decision.proposal?.symbol}
                </td>
              </tr>
              <tr>
                <td className="faint">Amount</td>
                <td className="mono">{money(decision.proposal?.amount)}</td>
              </tr>
              <tr>
                <td className="faint">Risk</td>
                <td>
                  <Chip tone={decision.proposal?.risk_decision === "ALLOW" ? "good" : "warn"}>
                    {decision.proposal?.risk_decision || "—"}
                  </Chip>
                </td>
              </tr>
              <tr>
                <td className="faint">Accounting</td>
                <td>{decision.proposal?.accounting_ok ? "OK" : "pending"}</td>
              </tr>
              <tr>
                <td className="faint">Reason</td>
                <td className="dim">{decision.proposal?.reason}</td>
              </tr>
            </tbody>
          </table>

          {decision.is_high_impact && (
            <div className="warning-banner" style={{ marginTop: 12 }}>
              High-impact action. Type <strong className="mono">{CONFIRM_PHRASE}</strong> to
              approve.
            </div>
          )}

          <div className="field" style={{ marginTop: 12 }}>
            <label>Decision reason</label>
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>

          {decision.is_high_impact && (
            <div className="field">
              <label>Confirmation phrase</label>
              <input
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                placeholder={CONFIRM_PHRASE}
              />
            </div>
          )}

          <div className="btn-row">
            <button
              className="btn good"
              disabled={
                decide.busy || (decision.is_high_impact && confirmation !== CONFIRM_PHRASE)
              }
              onClick={() => submitDecision(true)}
            >
              {decide.busy ? "Recording…" : "Approve"}
            </button>
            <button className="btn bad" disabled={decide.busy} onClick={() => submitDecision(false)}>
              Reject
            </button>
          </div>

          {lastResult && <div className="warning-banner" style={{ marginTop: 12 }}>{lastResult}</div>}

          <h3 className="panel-title" style={{ marginTop: 18 }}>
            <span className="dot" /> Execute approved proposal
          </h3>
          <div className="grid cols-3">
            <div className="field">
              <label>Adapter</label>
              <select value={adapter} onChange={(e) => setAdapter(e.target.value)}>
                <option value="SIMULATED">SIMULATED</option>
                <option value="PAPER_TRADING">PAPER_TRADING</option>
                <option value="TESTNET">TESTNET (if enabled)</option>
              </select>
            </div>
            <div className="field">
              <label>Market price (optional)</label>
              <input value={marketPrice} onChange={(e) => setMarketPrice(e.target.value)} />
            </div>
          </div>
          <button className="btn" disabled={execute.busy} onClick={() => execute.run(decision.proposal.id)}>
            {execute.busy ? "Executing…" : "Execute"}
          </button>
          {execute.result && (
            <pre className="mono" style={{ marginTop: 12, fontSize: 11, whiteSpace: "pre-wrap" }}>
              {JSON.stringify(execute.result, null, 2)}
            </pre>
          )}
        </Modal>
      )}
    </>
  );
}
