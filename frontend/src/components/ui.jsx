// Small presentational building blocks shared across screens.

import { useState } from "react";

export function Panel({ title, children, actions, style }) {
  return (
    <section className="panel" style={style}>
      {(title || actions) && (
        <header
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: title ? 12 : 0,
          }}
        >
          {title && (
            <h2 className="panel-title" style={{ margin: 0 }}>
              <span className="dot" />
              {title}
            </h2>
          )}
          <div className="spacer" />
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone || ""}`}>{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function Chip({ children, tone }) {
  return <span className={`chip ${tone || ""}`}>{children}</span>;
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="tabs">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={`tab ${active === tab.id ? "active" : ""}`}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export function Empty({ children }) {
  return <div className="empty">{children || "Nothing here yet."}</div>;
}

export function ErrorBanner({ error }) {
  if (!error) return null;
  return <div className="error-banner">{error.message || String(error)}</div>;
}

export function Spinner() {
  return <div className="spinner" />;
}

export function Loading({ label = "Loading" }) {
  return (
    <div className="empty" style={{ display: "flex", gap: 12, justifyContent: "center" }}>
      <Spinner />
      <span>{label}…</span>
    </div>
  );
}

export function Progress({ value }) {
  const pct = Math.max(0, Math.min(1, Number(value) || 0));
  return (
    <div className="progress">
      <span style={{ width: `${pct * 100}%` }} />
    </div>
  );
}

export function SimBadge() {
  return <span className="sim-banner">Simulation / paper data</span>;
}

export function Modal({ title, children, onClose }) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(2, 6, 23, 0.72)",
        display: "grid",
        placeItems: "center",
        zIndex: 100,
        padding: 20,
      }}
      onClick={onClose}
    >
      <div
        className="panel"
        style={{ maxWidth: 520, width: "100%", maxHeight: "86vh", overflowY: "auto" }}
        onClick={(event) => event.stopPropagation()}
      >
        <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
          <h2 className="panel-title" style={{ margin: 0 }}>
            <span className="dot" />
            {title}
          </h2>
          <div className="spacer" />
          <button className="btn ghost" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function useConfirmPhrase() {
  const [value, setValue] = useState("");
  return { value, setValue };
}

export function KeyValue({ rows }) {
  return (
    <table>
      <tbody>
        {rows.map(([key, value]) => (
          <tr key={key}>
            <td className="faint" style={{ width: "42%" }}>
              {key}
            </td>
            <td className="mono">{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
