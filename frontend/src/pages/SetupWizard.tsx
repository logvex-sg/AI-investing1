// First-start setup wizard.
//
// Steps: welcome, hardware detection, local model configuration, database
// check, administrator creation, initial generation, finish. Each backend step
// is idempotent, so re-running the wizard is safe.

import { useCallback, useEffect, useState } from "react";

import { desktop, type HardwareReport } from "../lib/desktop";
import { auth, setApiBase } from "../lib/api";
import { setSession } from "../lib/api";
import { Panel } from "../components/ui.jsx";

interface Props {
  onComplete: () => void;
}

const STEPS = [
  "Welcome",
  "Hardware",
  "Model server",
  "Database",
  "Administrator",
  "Generation",
  "Finish",
];

function fmtMb(mb: number): string {
  if (!mb) return "—";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

export default function SetupWizard({ onComplete }: Props) {
  const [step, setStep] = useState(0);
  const [hardware, setHardware] = useState<HardwareReport | null>(null);
  const [ollamaInstalled, setOllamaInstalled] = useState<boolean | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [provider, setProvider] = useState("mock");
  const [dbStatus, setDbStatus] = useState<string | null>(null);
  const [username, setUsername] = useState("operator");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("Local Operator");
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  const note = useCallback((message: string) => {
    setLog((entries) => [...entries, message]);
  }, []);

  useEffect(() => {
    if (step !== 1 || hardware) return;
    desktop.hardwareReport().then((report) => report && setHardware(report));
  }, [step, hardware]);

  useEffect(() => {
    if (step !== 2) return;
    desktop.detectOllama().then((installed) => {
      setOllamaInstalled(Boolean(installed));
      if (installed) {
        desktop.listModels().then((report) => {
          if (report?.models) setModels(report.models.map((m) => m.name));
        });
      }
    });
  }, [step]);

  const checkDatabase = useCallback(async () => {
    setBusy(true);
    const result = await desktop.setupCheckDatabase();
    setDbStatus(result?.message || "Desktop shell unavailable in browser mode.");
    setBusy(false);
  }, []);

  useEffect(() => {
    if (step === 3 && dbStatus === null) checkDatabase();
  }, [step, dbStatus, checkDatabase]);

  const createAdmin = useCallback(async () => {
    setBusy(true);
    const result = await desktop.setupCreateAdmin(username, password, displayName);
    if (result) note(result.message);
    if (result?.ok) {
      // Persist the chosen provider before the backend restarts it.
      const config = await desktop.getConfig();
      if (config) {
        await desktop.updateConfig({ ...config, llm_provider: provider });
      }
      setBusy(false);
      setStep(5);
      return;
    }
    setBusy(false);
  }, [username, password, displayName, provider, note]);

  const bootstrap = useCallback(async () => {
    setBusy(true);
    // Re-assert the administrator first: the step-4 Continue may have been
    // skipped on a resumed wizard, and the call is idempotent server-side.
    const admin = await desktop.setupCreateAdmin(username, password, displayName);
    if (admin && !admin.ok) note(admin.message);
    const result = await desktop.setupBootstrapGeneration(username, password);
    if (result) note(result.message);
    if (result?.ok) setStep(6);
    setBusy(false);
  }, [username, password, displayName, note]);

  const finish = useCallback(async () => {
    setBusy(true);
    await desktop.completeSetup();
    // Log in so the main application opens already authenticated.
    try {
      const runtime = await desktop.runtimeInfo();
      if (runtime?.backend_url) setApiBase(runtime.backend_url);
      const result = await auth.login(username, password);
      setSession(result.token, result.user);
    } catch {
      // Setup still completes; the user can sign in from the login screen.
    }
    onComplete();
    setBusy(false);
  }, [username, password, onComplete]);

  return (
    <div className="setup-shell">
      <div className="setup-card">
        <div className="setup-brand">
          <div className="brand-mark" />
          <div>
            <div className="brand-title">ECOSYSTEM</div>
            <div className="brand-sub">FIRST-START SETUP</div>
          </div>
        </div>

        <ol className="setup-steps">
          {STEPS.map((label, index) => (
            <li
              key={label}
              className={`setup-step ${index === step ? "active" : ""} ${
                index < step ? "done" : ""
              }`}
            >
              <span className="setup-step-index">{index < step ? "✓" : index + 1}</span>
              {label}
            </li>
          ))}
        </ol>

        <div className="setup-body">
          {step === 0 && (
            <Panel title="Welcome to ECOSYSTEM">
              <p className="dim">
                A local-first AI research and portfolio-simulation ecosystem.
                An Overseer coordinates up to eight investor agents through
                research, strategy generation, backtesting, paper trading and
                human-approved execution.
              </p>
              <ul className="setup-list">
                <li>Everything runs on this machine. Nothing is sent anywhere.</li>
                <li>One shared local model serves every agent.</li>
                <li>Financial actions require your explicit approval.</li>
                <li>Closing the application never destroys agent state.</li>
              </ul>
            </Panel>
          )}

          {step === 1 && (
            <Panel title="Hardware detection">
              {hardware ? (
                <table>
                  <tbody>
                    <tr><td className="faint">CPU</td><td className="mono">{hardware.cpu_brand} · {hardware.cpu_cores} cores</td></tr>
                    <tr><td className="faint">RAM</td><td className="mono">{fmtMb(hardware.ram_total_mb)} total · {fmtMb(hardware.ram_used_mb)} in use</td></tr>
                    <tr><td className="faint">GPU</td><td className="mono">{hardware.gpu.available ? hardware.gpu.name : "No NVIDIA GPU detected"}</td></tr>
                    <tr><td className="faint">VRAM</td><td className="mono">{hardware.gpu.available ? `${fmtMb(hardware.gpu.vram_total_mb)}` : "—"}</td></tr>
                    <tr><td className="faint">Disk</td><td className="mono">{hardware.disk_available_gb.toFixed(0)} GB free of {hardware.disk_total_gb.toFixed(0)} GB</td></tr>
                  </tbody>
                </table>
              ) : (
                <p className="dim">Detecting hardware…</p>
              )}
              <p className="faint" style={{ fontSize: 11.5, marginTop: 12 }}>
                The ecosystem is tuned for 16 GB RAM and a 4 GB GPU: one model,
                bounded context, scheduled agent reasoning.
              </p>
            </Panel>
          )}

          {step === 2 && (
            <Panel title="Local model server">
              {ollamaInstalled === null && <p className="dim">Checking for Ollama…</p>}
              {ollamaInstalled === false && (
                <>
                  <div className="warning-banner">
                    Local model server not detected.
                  </div>
                  <p className="dim" style={{ fontSize: 12.5 }}>
                    You can continue with the deterministic mock provider (no
                    model weights needed), or install Ollama and pull a model:
                  </p>
                  <pre className="mono code-block">{`curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b-instruct`}</pre>
                  <div className="field">
                    <label>Provider</label>
                    <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                      <option value="mock">mock — deterministic, no model server</option>
                      <option value="ollama">ollama — local models</option>
                    </select>
                  </div>
                </>
              )}
              {ollamaInstalled === true && (
                <>
                  <div className="good-banner">Ollama detected.</div>
                  <div className="field">
                    <label>Provider</label>
                    <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                      <option value="ollama">ollama — local models</option>
                      <option value="mock">mock — deterministic</option>
                    </select>
                  </div>
                  <p className="faint" style={{ fontSize: 11.5 }}>
                    {models.length
                      ? `Installed models: ${models.join(", ")}`
                      : "No models installed yet; the shell can pull one after setup."}
                  </p>
                </>
              )}
            </Panel>
          )}

          {step === 3 && (
            <Panel
              title="Database"
              actions={
                <button className="btn ghost" onClick={checkDatabase} disabled={busy}>
                  {busy ? "Checking…" : "Re-check"}
                </button>
              }
            >
              <p className="dim" style={{ fontSize: 12.5 }}>
                The shell manages a private PostgreSQL + pgvector instance under
                your application data directory. No administration is required.
              </p>
              {dbStatus && (
                <div className={dbStatus.toLowerCase().includes("online") ? "good-banner" : "warning-banner"}>
                  {dbStatus}
                </div>
              )}
            </Panel>
          )}

          {step === 4 && (
            <Panel title="Create local administrator">
              <p className="dim" style={{ fontSize: 12.5 }}>
                Only a human identity can approve proposals. The AI can never
                impersonate you, and every privileged action is audited.
              </p>
              <div className="field">
                <label>Username</label>
                <input value={username} onChange={(e) => setUsername(e.target.value)} />
              </div>
              <div className="field">
                <label>Display name</label>
                <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
              </div>
              <div className="field">
                <label>Password (min 12 characters)</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </Panel>
          )}

          {step === 5 && (
            <Panel
              title="Initial generation"
              actions={
                <button className="btn" onClick={bootstrap} disabled={busy}>
                  {busy ? "Creating…" : "Create generation 1"}
                </button>
              }
            >
              <p className="dim" style={{ fontSize: 12.5 }}>
                This creates the genesis generation: eight founding agents
                (A1–A8) across momentum, quantitative, value, defensive, macro,
                volatility, experimental and diversified specialisations, each
                funded from the system treasury.
              </p>
            </Panel>
          )}

          {step === 6 && (
            <Panel title="Ready">
              <div className="good-banner">Setup complete.</div>
              <p className="dim" style={{ fontSize: 12.5 }}>
                The command center will open now. Local services continue
                running in the background and are visible on the System page.
              </p>
            </Panel>
          )}

          {log.length > 0 && (
            <pre className="mono code-block" style={{ maxHeight: 140, overflow: "auto" }}>
              {log.join("\n")}
            </pre>
          )}
        </div>

        <div className="setup-actions">
          <button
            className="btn ghost"
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0 || busy}
          >
            Back
          </button>
          <div className="spacer" />
          {step < STEPS.length - 1 ? (
            <button
              className="btn"
              onClick={() => {
                if (step === 4) return createAdmin();
                if (step === 5) return bootstrap();
                return setStep((s) => s + 1);
              }}
              disabled={busy || (step === 4 && password.length < 12)}
            >
              {step === 5 && busy ? "Creating…" : "Continue"}
            </button>
          ) : (
            <button className="btn good" onClick={finish} disabled={busy}>
              {busy ? "Opening…" : "Launch ECOSYSTEM"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
