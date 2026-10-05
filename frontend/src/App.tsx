// Application shell.
//
// Owns the top bar, sidebar, page routing, the command palette and the
// desktop integration: when running inside the Tauri shell it connects to the
// bundled backend, shows service health, runs the first-start wizard and
// raises native notifications for events that need a human.

import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useAuth } from "./lib/auth.jsx";
import { system as systemApi, setApiBase } from "./lib/api";
import { desktop, isDesktop, notifyDesktop, type RuntimeInfo, type ServiceState } from "./lib/desktop";
import { Loading } from "./components/ui.jsx";
import CommandPalette from "./components/CommandPalette";

import Login from "./pages/Login.jsx";
import Overview from "./pages/Overview.jsx";
import Network from "./pages/Network.jsx";
import Agents from "./pages/Agents.jsx";
import AgentDetail from "./pages/AgentDetail.jsx";
import Generations from "./pages/Generations.jsx";
import Research from "./pages/Research.jsx";
import Strategies from "./pages/Strategies.jsx";
import Portfolio from "./pages/Portfolio.jsx";
import Approvals from "./pages/Approvals.jsx";
import Activity from "./pages/Activity.jsx";
import Settings from "./pages/Settings.jsx";
import SystemPage from "./pages/SystemPage";
import SetupWizard from "./pages/SetupWizard";

const NAV = [
  { to: "/", label: "Overview", icon: "◈", end: true },
  { to: "/network", label: "Network", icon: "⬡" },
  { to: "/agents", label: "Agents", icon: "◉" },
  { to: "/generations", label: "Generations", icon: "⟳" },
  { to: "/research", label: "Research", icon: "⌬" },
  { to: "/strategies", label: "Strategies", icon: "◫" },
  { to: "/portfolio", label: "Portfolio", icon: "▤" },
  { to: "/approvals", label: "Approvals", icon: "⚖" },
  { to: "/activity", label: "Activity", icon: "≋" },
  { to: "/system", label: "System", icon: "▣" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

const STATUS_TONE: Record<string, string> = {
  ONLINE: "good",
  STARTING: "warn",
  DEGRADED: "warn",
  ERROR: "bad",
};

function ServiceIndicator({ services }: { services: ServiceState[] }) {
  if (!isDesktop()) {
    return (
      <div className="service-indicator">
        <span className="service-dot good" />
        <span className="faint" style={{ fontSize: 10.5 }}>BROWSER MODE</span>
      </div>
    );
  }
  const worst = services.some((s) => s.status === "ERROR")
    ? "ERROR"
    : services.some((s) => s.status === "STARTING")
    ? "STARTING"
    : services.some((s) => s.status === "DEGRADED")
    ? "DEGRADED"
    : "ONLINE";
  return (
    <div className="service-indicator" title={services.map((s) => `${s.label}: ${s.status}`).join("\n")}>
      <span className={`service-dot ${STATUS_TONE[worst] || ""}`} />
      <span className="faint" style={{ fontSize: 10.5 }}>
        {worst === "ONLINE" ? "ALL SYSTEMS ONLINE" : worst}
      </span>
    </div>
  );
}

function Sidebar({ pending, emergency, services }: { pending: number; emergency?: { active?: boolean }; services: ServiceState[] }) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" />
        <div>
          <div className="brand-title">ECOSYSTEM</div>
          <div className="brand-sub">CONTROL CENTER</div>
        </div>
      </div>
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
        >
          <span className="nav-icon">{item.icon}</span>
          <span>{item.label}</span>
          {item.label === "Approvals" && pending > 0 && <span className="nav-badge">{pending}</span>}
        </NavLink>
      ))}
      <div className="spacer" />
      {emergency?.active && (
        <div className="warning-banner pulse" style={{ fontSize: 11 }}>
          EMERGENCY STOP ACTIVE
        </div>
      )}
      <ServiceIndicator services={services} />
      <div className="faint" style={{ fontSize: 10, padding: "6px 12px 10px" }}>
        Local-first · simulation only
      </div>
    </aside>
  );
}

function Topbar({ status, onOpenPalette }: { status: any; onOpenPalette: () => void }) {
  const location = useLocation();
  const title = NAV.find((n) =>
    n.end ? location.pathname === n.to : location.pathname.startsWith(n.to)
  );
  return (
    <header className="topbar">
      <div>
        <h1>{title?.label?.toUpperCase() || "ECOSYSTEM"}</h1>
        <div className="topbar-sub">
          {status
            ? `Generation ${status.generation?.label || "—"} · ${status.active_agents}/${status.max_active_agents} agents · ${status.llm_provider}`
            : "connecting…"}
        </div>
      </div>
      <div className="spacer" />
      <button className="btn ghost" onClick={onOpenPalette} title="Command palette (Ctrl+K)">
        ⌘ K
      </button>
      {status && (
        <>
          <span className={`chip ${status.emergency_stop?.active ? "bad" : "good"}`}>
            {status.emergency_stop?.active ? "HALTED" : "ARMED"}
          </span>
          <span className="chip">{status.execution?.default_adapter}</span>
          <span className="chip">{status.pending_approvals} pending</span>
        </>
      )}
    </header>
  );
}

export default function App() {
  const { user, ready, login } = useAuth();
  const [status, setStatus] = useState<any>(null);
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [services, setServices] = useState<ServiceState[]>([]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [bootstrapped, setBootstrapped] = useState(!isDesktop());
  const lastPending = useRef<number | null>(null);

  // Connect to the bundled backend and decide whether the wizard is needed.
  useEffect(() => {
    let cancelled = false;
    async function boot() {
      const info = await desktop.runtimeInfo();
      if (cancelled) return;
      if (info) {
        setRuntime(info);
        setApiBase(info.backend_url);
      }
      // Wait for services to be reachable before hiding the wizard.
      for (let attempt = 0; attempt < 60 && !cancelled; attempt += 1) {
        const result = await desktop.refreshServices();
        if (result) setServices(result);
        const backendOnline = result?.find((s) => s.id === "backend")?.status === "ONLINE";
        if (backendOnline || !isDesktop()) break;
        await new Promise((r) => setTimeout(r, 1000));
      }
      if (!cancelled) setBootstrapped(true);
    }
    boot();
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll service state and system status.
  useEffect(() => {
    if (!user) return undefined;
    let cancelled = false;
    async function poll() {
      try {
        const data = await systemApi.status();
        if (!cancelled) setStatus(data);
      } catch {
        /* advisory */
      }
      if (isDesktop()) {
        const result = await desktop.serviceStatus();
        if (!cancelled && result) setServices(result);
      }
    }
    poll();
    const timer = setInterval(poll, 6000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [user]);

  // Native notification when new approvals need a human.
  useEffect(() => {
    const pending = status?.pending_approvals ?? 0;
    if (lastPending.current !== null && pending > lastPending.current) {
      notifyDesktop("ECOSYSTEM", `${pending} proposal(s) awaiting your approval.`);
    }
    lastPending.current = pending;
  }, [status?.pending_approvals]);

  // CTRL+K command palette.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Setup finished: reload so the shell re-reads config and the session
  // established by the wizard is picked up by the auth provider.
  const handleSetupComplete = useCallback(() => {
    window.location.reload();
  }, []);

  if (!bootstrapped || !ready) {
    return (
      <div className="center">
        <Loading label={isDesktop() ? "Starting local services" : "Starting control center"} />
      </div>
    );
  }

  // First-start wizard inside the desktop shell.
  if (isDesktop() && runtime && !runtime.setup_complete) {
    return <SetupWizard onComplete={handleSetupComplete} />;
  }

  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  return (
    <div className="shell">
      <Sidebar
        pending={status?.pending_approvals || 0}
        emergency={status?.emergency_stop}
        services={services}
      />
      <div className="main">
        <Topbar status={status} onOpenPalette={() => setPaletteOpen(true)} />
        <div className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/network" element={<Network />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/agents/:agentId" element={<AgentDetail />} />
            <Route path="/generations" element={<Generations />} />
            <Route path="/research" element={<Research />} />
            <Route path="/strategies" element={<Strategies />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/activity" element={<Activity />} />
            <Route path="/system" element={<SystemPage />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
