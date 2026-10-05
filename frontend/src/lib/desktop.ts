// Bridge to the Tauri desktop shell.
//
// Every call degrades gracefully when the app runs in a plain browser (the
// development workflow), so the same React code serves both. The shell owns
// process supervision, hardware telemetry, model management and notifications;
// it never performs financial actions.

export type ServiceStatus =
  | "ONLINE"
  | "OFFLINE"
  | "STARTING"
  | "DEGRADED"
  | "ERROR";

export interface ServiceState {
  id: string;
  label: string;
  status: ServiceStatus;
  detail: string;
  port: number | null;
  pid: number | null;
}

export interface RuntimeInfo {
  backend_url: string;
  data_dir: string;
  logs_dir: string;
  config_path: string;
  setup_complete: boolean;
}

export interface DesktopConfig {
  setup_complete: boolean;
  database_url: string;
  llm_provider: string;
  ollama_base_url: string;
  overseer_model: string;
  agent_model: string;
  coding_model: string;
  max_context_tokens: number;
  notifications_enabled: boolean;
  theme: string;
}

export interface GpuInfo {
  name: string;
  vram_total_mb: number;
  vram_used_mb: number;
  utilization_pct: number;
  available: boolean;
}

export interface HardwareReport {
  cpu_brand: string;
  cpu_cores: number;
  ram_total_mb: number;
  ram_used_mb: number;
  swap_total_mb: number;
  swap_used_mb: number;
  disk_total_gb: number;
  disk_available_gb: number;
  gpu: GpuInfo;
}

export interface ResourceSnapshot {
  cpu_usage_pct: number;
  ram_total_mb: number;
  ram_used_mb: number;
  ram_usage_pct: number;
  swap_total_mb: number;
  swap_used_mb: number;
  gpu_utilization_pct: number;
  vram_total_mb: number;
  vram_used_mb: number;
  gpu_available: boolean;
}

export interface ModelInfo {
  name: string;
  size_bytes: number;
  size_gb: number;
  parameter_size: string;
  quantization: string;
  family: string;
  modified_at: string;
}

export interface ModelReport {
  server_available: boolean;
  base_url: string;
  provider: string;
  overseer_model: string;
  agent_model: string;
  coding_model: string;
  models: ModelInfo[];
  error: string | null;
}

export interface StepResult {
  ok: boolean;
  message: string;
}

/** True when running inside the Tauri webview rather than a browser. */
export function isDesktop(): boolean {
  if (typeof window === "undefined") return false;
  const w = window as unknown as Record<string, unknown>;
  return Boolean(w.__TAURI_INTERNALS__ || w.__TAURI__);
}

type InvokeFn = <T>(cmd: string, args?: Record<string, unknown>) => Promise<T>;

let invokePromise: Promise<InvokeFn | null> | null = null;

async function getInvoke(): Promise<InvokeFn | null> {
  if (!isDesktop()) return null;
  if (!invokePromise) {
    invokePromise = import("@tauri-apps/api/core")
      .then((mod) => mod.invoke as InvokeFn)
      .catch(() => null);
  }
  return invokePromise;
}

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> {
  const invoke = await getInvoke();
  if (!invoke) return null;
  try {
    return await invoke<T>(cmd, args);
  } catch (err) {
    // A failed telemetry call must never take the UI down.
    console.warn(`desktop command '${cmd}' failed:`, err);
    return null;
  }
}

export const desktop = {
  isDesktop,
  runtimeInfo: () => call<RuntimeInfo>("runtime_info"),
  serviceStatus: () => call<ServiceState[]>("service_status"),
  refreshServices: () => call<ServiceState[]>("refresh_services"),
  getConfig: () => call<DesktopConfig>("get_config"),
  updateConfig: (config: DesktopConfig) =>
    call<DesktopConfig>("update_config", { config }),
  hardwareReport: () => call<HardwareReport>("hardware_report"),
  resourceSnapshot: () => call<ResourceSnapshot>("resource_snapshot"),
  listModels: () => call<ModelReport>("list_models"),
  pullModel: (model: string) => call<string>("pull_model", { model }),
  detectOllama: () => call<boolean>("detect_ollama"),
  setupCheckDatabase: () => call<StepResult>("setup_check_database"),
  setupCreateAdmin: (username: string, password: string, displayName: string) =>
    call<StepResult>("setup_create_admin", {
      username,
      password,
      displayName,
    }),
  setupBootstrapGeneration: (username: string, password: string) =>
    call<StepResult>("setup_bootstrap_generation", { username, password }),
  completeSetup: () => call<void>("complete_setup"),
  notify: (title: string, body: string) => call<void>("notify", { title, body }),
  openPath: (path: string) => call<void>("open_path", { path }),
};

/** Subscribe to the shell's "services ready" event. Returns an unsubscribe. */
export async function onServicesReady(callback: () => void): Promise<() => void> {
  if (!isDesktop()) return () => {};
  try {
    const mod = await import("@tauri-apps/api/event");
    const unlisten = await mod.listen("services-ready", () => callback());
    return unlisten;
  } catch {
    return () => {};
  }
}

/** Send a desktop notification through the shell (respects user settings). */
export async function notifyDesktop(title: string, body: string): Promise<void> {
  await desktop.notify(title, body);
}
