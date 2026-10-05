// System page: local services, live resource telemetry and model management.
//
// The target machine has 16 GB RAM and a 4 GB GPU, so this page is where the
// user watches the ecosystem stay inside its budget.

import { useEffect, useState } from "react";

import { desktop, type ModelReport, type RuntimeInfo } from "../lib/desktop";
import { useResources, useServices } from "../lib/desktopHooks";
import { Panel, Stat, Chip, Empty } from "../components/ui.jsx";

const STATUS_TONE: Record<string, string> = {
  ONLINE: "good",
  STARTING: "warn",
  DEGRADED: "warn",
  OFFLINE: "",
  ERROR: "bad",
};

function Bar({ value, tone }: { value: number; tone?: string }) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="progress">
      <span
        style={{
          width: `${pct}%`,
          background:
            tone === "bad"
              ? "var(--danger)"
              : tone === "warn"
              ? "var(--warn)"
              : "linear-gradient(90deg, var(--accent), var(--accent-2))",
        }}
      />
    </div>
  );
}

export default function SystemPage() {
  const { services, refresh } = useServices(4000);
  const resources = useResources(3000);
  const [models, setModels] = useState<ModelReport | null>(null);
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [pulling, setPulling] = useState<string | null>(null);
  const [modelName, setModelName] = useState("qwen2.5:7b-instruct");

  useEffect(() => {
    desktop.listModels().then((report) => report && setModels(report));
    desktop.runtimeInfo().then((info) => info && setRuntime(info));
  }, []);

  async function pull() {
    setPulling(modelName);
    await desktop.pullModel(modelName);
    const report = await desktop.listModels();
    if (report) setModels(report);
    setPulling(null);
  }

  const ramTone = resources && resources.ram_usage_pct > 88 ? "bad" : resources && resources.ram_usage_pct > 70 ? "warn" : "";
  const vramPct =
    resources && resources.vram_total_mb > 0
      ? (resources.vram_used_mb / resources.vram_total_mb) * 100
      : 0;

  return (
    <>
      <div className="grid cols-4">
        <Stat label="CPU" value={resources ? `${resources.cpu_usage_pct.toFixed(1)}%` : "—"} />
        <Stat
          label="RAM"
          value={resources ? `${resources.ram_usage_pct.toFixed(1)}%` : "—"}
          hint={resources ? `${(resources.ram_used_mb / 1024).toFixed(1)} / ${(resources.ram_total_mb / 1024).toFixed(1)} GB` : undefined}
          tone={ramTone}
        />
        <Stat
          label="VRAM"
          value={resources?.gpu_available ? `${vramPct.toFixed(0)}%` : "n/a"}
          hint={resources?.gpu_available ? `${(resources.vram_used_mb / 1024).toFixed(1)} / ${(resources.vram_total_mb / 1024).toFixed(1)} GB` : "no NVIDIA GPU"}
        />
        <Stat label="GPU util" value={resources?.gpu_available ? `${resources.gpu_utilization_pct}%` : "n/a"} />
      </div>

      <div className="grid cols-2">
        <Panel
          title="Local services"
          actions={<button className="btn ghost" onClick={refresh}>Refresh</button>}
        >
          <table>
            <thead>
              <tr><th>Service</th><th>Status</th><th>Detail</th><th>Port</th></tr>
            </thead>
            <tbody>
              {services.map((service) => (
                <tr key={service.id}>
                  <td>{service.label}</td>
                  <td><Chip tone={STATUS_TONE[service.status]}>{service.status}</Chip></td>
                  <td className="faint" style={{ fontSize: 11.5 }}>{service.detail}</td>
                  <td className="mono">{service.port ?? "—"}</td>
                </tr>
              ))}
              {services.length === 0 && (
                <tr><td colSpan={4} className="faint">Browser mode — the desktop shell owns local services.</td></tr>
              )}
            </tbody>
          </table>
        </Panel>

        <Panel title="Resource budget">
          <div className="field">
            <label>CPU</label>
            <Bar value={resources?.cpu_usage_pct ?? 0} />
          </div>
          <div className="field">
            <label>RAM {resources ? `${resources.ram_usage_pct.toFixed(1)}%` : ""}</label>
            <Bar value={resources?.ram_usage_pct ?? 0} tone={ramTone} />
          </div>
          <div className="field">
            <label>VRAM {resources?.gpu_available ? `${vramPct.toFixed(1)}%` : "(unavailable)"}</label>
            <Bar value={vramPct} />
          </div>
          {resources && resources.swap_total_mb > 0 && (
            <p className="faint" style={{ fontSize: 11.5 }}>
              Swap: {(resources.swap_used_mb / 1024).toFixed(1)} / {(resources.swap_total_mb / 1024).toFixed(1)} GB
            </p>
          )}
          <p className="faint" style={{ fontSize: 11.5 }}>
            The ecosystem uses one shared model, bounded context windows and
            scheduled agent reasoning so it stays within this budget.
          </p>
        </Panel>
      </div>

      <Panel
        title="Models"
        actions={
          <Chip tone={models?.server_available ? "good" : ""}>
            {models?.server_available ? "server online" : "server unavailable"}
          </Chip>
        }
      >
        {models?.error && <div className="warning-banner">{models.error}</div>}
        <table>
          <thead>
            <tr><th>Model</th><th>Size</th><th>Parameters</th><th>Quantisation</th><th>Family</th></tr>
          </thead>
          <tbody>
            {(models?.models || []).map((model) => (
              <tr key={model.name}>
                <td className="mono">{model.name}</td>
                <td className="mono">{model.size_gb} GB</td>
                <td className="mono">{model.parameter_size}</td>
                <td className="mono">{model.quantization}</td>
                <td className="mono">{model.family}</td>
              </tr>
            ))}
            {(models?.models || []).length === 0 && (
              <tr><td colSpan={5} className="faint">No local models listed.</td></tr>
            )}
          </tbody>
        </table>

        <div style={{ display: "flex", gap: 8, alignItems: "flex-end", marginTop: 12 }}>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label>Pull a model into the shared server</label>
            <input value={modelName} onChange={(e) => setModelName(e.target.value)} />
          </div>
          <button className="btn" onClick={pull} disabled={pulling !== null}>
            {pulling ? "Requesting…" : "Pull"}
          </button>
        </div>
        <p className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>
          One model is shared by the Overseer, research and every agent. The
          shell never loads eight copies.
        </p>
      </Panel>

      <div className="grid cols-2">
        <Panel title="Assigned models">
          <table>
            <tbody>
              <tr><td className="faint">Overseer</td><td className="mono">{models?.overseer_model || "—"}</td></tr>
              <tr><td className="faint">Agents</td><td className="mono">{models?.agent_model || "—"}</td></tr>
              <tr><td className="faint">Coding</td><td className="mono">{models?.coding_model || "—"}</td></tr>
              <tr><td className="faint">Provider</td><td className="mono">{models?.provider || "—"}</td></tr>
            </tbody>
          </table>
        </Panel>

        <Panel title="Local data">
          {runtime ? (
            <table>
              <tbody>
                <tr><td className="faint">Data directory</td><td className="mono" style={{ fontSize: 11 }}>{runtime.data_dir}</td></tr>
                <tr><td className="faint">Logs</td><td className="mono" style={{ fontSize: 11 }}>{runtime.logs_dir}</td></tr>
                <tr><td className="faint">Config</td><td className="mono" style={{ fontSize: 11 }}>{runtime.config_path}</td></tr>
                <tr><td className="faint">Backend</td><td className="mono" style={{ fontSize: 11 }}>{runtime.backend_url}</td></tr>
              </tbody>
            </table>
          ) : (
            <Empty>Desktop shell unavailable in browser mode.</Empty>
          )}
          {runtime && (
            <button className="btn ghost" style={{ marginTop: 10 }} onClick={() => desktop.openPath(runtime.logs_dir)}>
              Open logs folder
            </button>
          )}
        </Panel>
      </div>
    </>
  );
}
