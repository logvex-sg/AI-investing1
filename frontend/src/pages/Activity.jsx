import { useState } from "react";

import { activity as activityApi } from "../lib/api";
import { usePolling, useLiveEvents } from "../lib/hooks";
import { CATEGORY_COLORS, SEVERITY_COLORS, clock, timeAgo } from "../lib/format";
import { Panel, Stat, Chip, Empty, ErrorBanner, Loading, Tabs } from "../components/ui.jsx";

const CATEGORIES = [
  "",
  "AI",
  "AGENT",
  "RESEARCH",
  "TRADE",
  "RISK",
  "ACCOUNT",
  "SYSTEM",
  "SECURITY",
];

const TABS = [
  { id: "events", label: "Event Stream" },
  { id: "audit", label: "Audit Log" },
  { id: "risk", label: "Risk Events" },
];

export default function Activity() {
  const [tab, setTab] = useState("events");
  const [category, setCategory] = useState("");
  const [severity, setSeverity] = useState("");

  const [live, setLive] = useState([]);
  const connected = useLiveEvents(
    (event) => setLive((prev) => [event, ...prev].slice(0, 200)),
    activityApi.streamUrl()
  );

  const events = usePolling(
    () => activityApi.events({ category, severity, limit: 200 }),
    [category, severity],
    6000
  );
  const audit = usePolling(() => activityApi.audit(200), [], 8000);
  const risk = usePolling(() => activityApi.riskEvents(200), [], 8000);

  const merged = dedupe([...live, ...(events.data?.events || [])]);

  return (
    <>
      <ErrorBanner error={events.error} />

      <div className="grid cols-4">
        <Stat label="Events" value={merged.length} hint="persisted + live" />
        <Stat label="Audit Entries" value={(audit.data?.audit || []).length} hint="append-only" />
        <Stat
          label="Risk Events"
          value={(risk.data?.risk_events || []).length}
          tone={(risk.data?.risk_events || []).length ? "warn" : ""}
        />
        <Stat label="Live Feed" value={connected ? "connected" : "polling"} />
      </div>

      <Tabs tabs={TABS} active={tab} onChange={setTab} />

      {tab === "events" && (
        <Panel
          title="Live Event Stream"
          actions={
            <div className="btn-row">
              <select value={category} onChange={(e) => setCategory(e.target.value)} style={{ width: 150 }}>
                {CATEGORIES.map((c) => (
                  <option key={c || "all"} value={c}>
                    {c || "All categories"}
                  </option>
                ))}
              </select>
              <select value={severity} onChange={(e) => setSeverity(e.target.value)} style={{ width: 140 }}>
                <option value="">All severities</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
                <option value="CRITICAL">CRITICAL</option>
              </select>
            </div>
          }
        >
          {events.loading && !events.data ? (
            <Loading />
          ) : (
            <div className="feed" style={{ maxHeight: 640 }}>
              {merged.map((event) => (
                <div className="feed-item" key={event.id}>
                  <span className="feed-time" title={clock(event.created_at)}>
                    {timeAgo(event.created_at)}
                  </span>
                  <span className="feed-cat" style={{ color: CATEGORY_COLORS[event.category] }}>
                    {event.category}
                  </span>
                  <span>
                    <span style={{ color: SEVERITY_COLORS[event.severity] }}>
                      {event.message}
                    </span>
                    <span className="faint mono" style={{ fontSize: 10, marginLeft: 8 }}>
                      {event.event_type} · {event.source}
                    </span>
                  </span>
                </div>
              ))}
              {merged.length === 0 && <Empty>No events match the filter.</Empty>}
            </div>
          )}
        </Panel>
      )}

      {tab === "audit" && (
        <Panel title="Audit Log">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Resource</th>
                <th>Outcome</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {(audit.data?.audit || []).map((a) => (
                <tr key={a.id}>
                  <td className="faint">{timeAgo(a.created_at)}</td>
                  <td className="mono">
                    {a.actor_type}:{a.actor_id ? String(a.actor_id).slice(0, 8) : "—"}
                  </td>
                  <td className="mono">{a.action}</td>
                  <td className="mono faint">
                    {a.resource_type}:{a.resource_id ? String(a.resource_id).slice(0, 8) : "—"}
                  </td>
                  <td>
                    <Chip tone={a.outcome === "SUCCESS" ? "good" : a.outcome === "DENIED" ? "bad" : "warn"}>
                      {a.outcome}
                    </Chip>
                  </td>
                  <td className="dim" style={{ maxWidth: 280 }}>
                    {a.reason || "—"}
                  </td>
                </tr>
              ))}
              {(audit.data?.audit || []).length === 0 && (
                <tr>
                  <td colSpan={6} className="faint">
                    No audit entries.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Panel>
      )}

      {tab === "risk" && (
        <Panel title="Risk Events">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Decision</th>
                <th>Severity</th>
                <th>Rule</th>
                <th>Observed</th>
                <th>Limit</th>
                <th>Message</th>
              </tr>
            </thead>
            <tbody>
              {(risk.data?.risk_events || []).map((r) => (
                <tr key={r.id}>
                  <td className="faint">{timeAgo(r.created_at)}</td>
                  <td>
                    <Chip tone={r.decision === "ALLOW" ? "good" : "bad"}>{r.decision}</Chip>
                  </td>
                  <td className="mono" style={{ color: SEVERITY_COLORS[r.severity] }}>
                    {r.severity}
                  </td>
                  <td className="mono">{r.rule}</td>
                  <td className="mono">{r.observed_value ?? "—"}</td>
                  <td className="mono">{r.limit_value ?? "—"}</td>
                  <td className="dim">{r.message}</td>
                </tr>
              ))}
              {(risk.data?.risk_events || []).length === 0 && (
                <tr>
                  <td colSpan={7} className="faint">
                    No risk events recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </Panel>
      )}
    </>
  );
}

function dedupe(events) {
  const seen = new Set();
  const out = [];
  for (const event of events) {
    if (event?.id && seen.has(event.id)) continue;
    if (event?.id) seen.add(event.id);
    out.push(event);
  }
  return out;
}
