// Thin API client. One place owns the base URL, the bearer token and the
// shape of errors, so pages never touch fetch directly.

const TOKEN_KEY = "ecosystem.token";
const USER_KEY = "ecosystem.user";

// In the desktop app the shell picks a loopback port for the bundled backend,
// so requests are absolute. In the browser dev server this stays empty and the
// Vite proxy handles `/api`.
let API_BASE = "";

export function setApiBase(base) {
  API_BASE = base ? base.replace(/\/$/, "") : "";
}

export function apiBase() {
  return API_BASE;
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || "null");
  } catch {
    return null;
  }
}

export function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, { method = "GET", body, signal } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(`${API_BASE}/api${path}`, {
    method,
    headers,
    signal,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 401) {
    clearSession();
    throw new ApiError("Session expired. Please sign in again.", 401);
  }

  const text = await response.text();
  const data = text ? safeJson(text) : null;

  if (!response.ok) {
    const detail =
      (data && (data.detail || data.message)) ||
      (typeof data === "string" ? data : response.statusText);
    throw new ApiError(
      typeof detail === "string" ? detail : JSON.stringify(detail),
      response.status
    );
  }
  return data;
}

function safeJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export const api = {
  get: (path, opts) => request(path, opts),
  post: (path, body, opts) => request(path, { ...opts, method: "POST", body }),
  del: (path, opts) => request(path, { ...opts, method: "DELETE" }),
};

// ---- Domain helpers -------------------------------------------------------

export const system = {
  health: () => api.get("/system/health"),
  status: () => api.get("/system/status"),
  overview: (interval = "1d") => api.get(`/system/overview?interval=${interval}`),
  marketSymbols: () => api.get("/system/market/symbols"),
  marketSeries: (symbol, limit = 180) =>
    api.get(`/system/market/${symbol}?limit=${limit}`),
  reconciliation: () => api.get("/system/reconciliation"),
  mark: (interval = "1d") => api.post("/system/mark", { interval }),
  emergencyStop: (reason) => api.post("/system/emergency-stop", { reason }),
  releaseEmergencyStop: () => api.post("/system/emergency-stop/release"),
};

export const auth = {
  login: (username, password) => api.post("/auth/login", { username, password }),
  me: () => api.get("/auth/me"),
  bootstrap: (payload) => api.post("/auth/bootstrap", payload),
};

export const agents = {
  list: (params = {}) => {
    const q = new URLSearchParams(params).toString();
    return api.get(`/agents${q ? `?${q}` : ""}`);
  },
  network: (generation) =>
    api.get(`/agents/network${generation ? `?generation_number=${generation}` : ""}`),
  compare: (generation) =>
    api.get(`/agents/compare${generation ? `?generation_number=${generation}` : ""}`),
  detail: (id) => api.get(`/agents/${id}`),
  strategies: (id) => api.get(`/agents/${id}/strategies`),
  experiments: (id) => api.get(`/agents/${id}/experiments`),
  memory: (id, query) =>
    api.get(
      `/agents/${id}/memory?limit=40${query ? `&query=${encodeURIComponent(query)}` : ""}`
    ),
  research: (id, objective) => api.post(`/agents/${id}/research`, { agent_id: id, objective }),
};

export const generations = {
  list: () => api.get("/generations"),
  current: () => api.get("/generations/current"),
  history: () => api.get("/generations/history"),
  lineage: (agentId) => api.get(`/generations/agents/${agentId}/lineage`),
  bootstrap: () => api.post("/generations/bootstrap"),
  evolve: (payload = {}) => api.post("/generations/evolve", payload),
};

export const strategies = {
  list: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v))
    ).toString();
    return api.get(`/strategies${q ? `?${q}` : ""}`);
  },
  detail: (id) => api.get(`/strategies/${id}`),
  versions: (id) => api.get(`/strategies/${id}/versions`),
  lineage: (id) => api.get(`/strategies/${id}/lineage`),
  validate: (id, payload) => api.post(`/strategies/${id}/validate`, payload),
  retire: (id, reason) => api.post(`/strategies/${id}/retire?reason=${encodeURIComponent(reason)}`),
};

export const research = {
  questions: (params = {}) => {
    const q = new URLSearchParams(params).toString();
    return api.get(`/research/questions${q ? `?${q}` : ""}`);
  },
  question: (id) => api.get(`/research/questions/${id}`),
  experiments: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v))
    ).toString();
    return api.get(`/research/experiments${q ? `?${q}` : ""}`);
  },
  experiment: (id) => api.get(`/research/experiments/${id}`),
  observation: () => api.get("/research/observation"),
  cycle: (payload = {}) => api.post("/research/cycle", payload),
  characteristics: () => api.post("/research/characteristics"),
};

export const portfolio = {
  list: (generation) =>
    api.get(`/portfolio${generation ? `?generation_number=${generation}` : ""}`),
  detail: (id) => api.get(`/portfolio/${id}`),
  treasury: () => api.get("/portfolio/treasury"),
  contributions: () => api.get("/portfolio/contributions"),
  reconciliation: () => api.get("/portfolio/reconciliation"),
};

export const approvals = {
  pending: () => api.get("/approvals/pending"),
  detail: (id) => api.get(`/approvals/${id}`),
  proposeTrade: (payload) => api.post("/approvals/proposals/trade", payload),
  proposeTransfer: (payload) => api.post("/approvals/proposals/transfer", payload),
  decide: (id, payload) => api.post(`/approvals/${id}/decide`, payload),
  execute: (proposalId, payload) => api.post(`/approvals/proposals/${proposalId}/execute`, payload),
  executions: () => api.get("/approvals/executions/recent"),
};

export const risk = {
  limits: (agentId) =>
    api.get(`/risk/limits${agentId ? `?agent_id=${agentId}` : ""}`),
};

export const activity = {
  events: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v))
    ).toString();
    return api.get(`/activity${q ? `?${q}` : ""}`);
  },
  audit: (limit = 100) => api.get(`/activity/audit?limit=${limit}`),
  riskEvents: (limit = 100) => api.get(`/activity/risk-events?limit=${limit}`),
  streamUrl: () => {
    const token = getToken();
    return `${API_BASE}/api/activity/stream${token ? `?token=${encodeURIComponent(token)}` : ""}`;
  },
};

export const memory = {
  browse: (params = {}) => {
    const q = new URLSearchParams(
      Object.fromEntries(Object.entries(params).filter(([, v]) => v))
    ).toString();
    return api.get(`/memory${q ? `?${q}` : ""}`);
  },
  search: (payload) => api.post("/memory/search", payload),
};
