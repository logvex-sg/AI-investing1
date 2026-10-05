// Presentation helpers. Money and percentages are always rendered from the
// strings the backend sends, never recomputed, so the UI cannot disagree with
// the ledger.

export function money(value, currency = "EUR") {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat("en-IE", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(n);
}

export function num(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat("en-IE", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(n);
}

export function pct(value, digits = 2) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function signed(value, formatter = money) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${formatter(value)}`;
}

export function timeAgo(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function clock(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString();
}

export const CATEGORY_COLORS = {
  AI: "#8b5cf6",
  AGENT: "#22d3ee",
  RESEARCH: "#38bdf8",
  TRADE: "#34d399",
  RISK: "#f59e0b",
  ACCOUNT: "#a78bfa",
  SYSTEM: "#64748b",
  SECURITY: "#ef4444",
};

export const SEVERITY_COLORS = {
  INFO: "#38bdf8",
  WARNING: "#f59e0b",
  ERROR: "#fb7185",
  CRITICAL: "#ef4444",
};

export const STAGE_COLORS = {
  IDEA: "#64748b",
  HYPOTHESIS: "#8b5cf6",
  IMPLEMENTATION: "#6366f1",
  BACKTEST: "#38bdf8",
  OUT_OF_SAMPLE: "#22d3ee",
  ROBUSTNESS: "#2dd4bf",
  PAPER_TRADING: "#34d399",
  EVALUATION: "#a3e635",
  PRODUCTION: "#facc15",
  RETIRED: "#475569",
};
