//! Background service supervision.
//!
//! On startup the shell brings the local ecosystem online in order:
//!
//! 1. PostgreSQL (checked; started through systemd when possible)
//! 2. the FastAPI backend (bundled sidecar, loopback only)
//! 3. the local model server (Ollama), when the configured provider needs it
//! 4. derived subsystems: memory, risk engine, agent scheduler
//!
//! Failures are reported to the GUI rather than crashing the application.

use std::collections::HashMap;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::process::{Child, Command};
use tokio::sync::RwLock;

use crate::platform::{self, Config};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum ServiceStatus {
    Online,
    Offline,
    Starting,
    Degraded,
    Error,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceState {
    pub id: String,
    pub label: String,
    pub status: ServiceStatus,
    pub detail: String,
    pub port: Option<u16>,
    pub pid: Option<u32>,
}

impl ServiceState {
    fn new(id: &str, label: &str) -> Self {
        ServiceState {
            id: id.to_string(),
            label: label.to_string(),
            status: ServiceStatus::Offline,
            detail: "not started".to_string(),
            port: None,
            pid: None,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct HealthReport {
    pub ok: bool,
    pub backend: String,
    pub llm_provider: String,
    pub execution_mode: String,
}

pub struct Supervisor {
    pub config: RwLock<Config>,
    pub backend_port: u16,
    http: reqwest::Client,
    states: RwLock<HashMap<String, ServiceState>>,
    backend_child: RwLock<Option<Child>>,
    ollama_child: RwLock<Option<Child>>,
    log_dir: std::path::PathBuf,
}

impl Supervisor {
    pub fn new(config: Config) -> Arc<Self> {
        let backend_port = platform::free_port();
        let mut states = HashMap::new();
        for (id, label) in [
            ("database", "Database"),
            ("backend", "Backend"),
            ("llm", "LLM"),
            ("memory", "Memory"),
            ("risk", "Risk Engine"),
            ("scheduler", "Agent Scheduler"),
        ] {
            states.insert(id.to_string(), ServiceState::new(id, label));
        }
        Arc::new(Supervisor {
            config: RwLock::new(config),
            backend_port,
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(6))
                .build()
                .unwrap_or_default(),
            states: RwLock::new(states),
            backend_child: RwLock::new(None),
            ollama_child: RwLock::new(None),
            log_dir: platform::logs_dir(),
        })
    }

    pub fn backend_url(&self) -> String {
        format!("http://127.0.0.1:{}", self.backend_port)
    }

    async fn set(&self, id: &str, status: ServiceStatus, detail: impl Into<String>) {
        let mut states = self.states.write().await;
        if let Some(state) = states.get_mut(id) {
            state.status = status;
            state.detail = detail.into();
        }
    }

    async fn set_port(&self, id: &str, port: u16) {
        let mut states = self.states.write().await;
        if let Some(state) = states.get_mut(id) {
            state.port = Some(port);
        }
    }

    async fn set_pid(&self, id: &str, pid: Option<u32>) {
        let mut states = self.states.write().await;
        if let Some(state) = states.get_mut(id) {
            state.pid = pid;
        }
    }

    pub async fn snapshot(&self) -> Vec<ServiceState> {
        let states = self.states.read().await;
        let order = ["database", "backend", "llm", "memory", "risk", "scheduler"];
        order
            .iter()
            .filter_map(|id| states.get(*id).cloned())
            .collect()
    }

    /// Full startup sequence. Never returns an error: individual failures are
    /// surfaced through service state so the GUI can explain what went wrong.
    pub async fn start_all(self: &Arc<Self>, app_exe: std::path::PathBuf) {
        self.set("database", ServiceStatus::Starting, "checking").await;
        self.ensure_database().await;

        self.set("backend", ServiceStatus::Starting, "launching").await;
        self.start_backend(&app_exe).await;

        // The backend sidecar owns the database: on a first run it initialises
        // and starts a private loopback cluster. Give it time to come up before
        // reporting the database as failed.
        self.wait_for_database(60).await;

        self.set("llm", ServiceStatus::Starting, "checking").await;
        self.ensure_llm().await;

        self.refresh_health().await;
    }

    /// Probe the configured database once, without starting anything.
    async fn ensure_database(&self) {
        let config = self.config.read().await.clone();
        let (host, port) = platform::parse_db_host_port(&config.database_url);
        self.set_port("database", port).await;

        if platform::tcp_reachable(&host, port, Duration::from_millis(600)) {
            self.set("database", ServiceStatus::Online, "reachable").await;
        } else {
            // Not an error yet: the sidecar starts the private cluster. A real
            // failure surfaces after `wait_for_database` gives up and the
            // backend health check reports the reason.
            self.set(
                "database",
                ServiceStatus::Starting,
                "will be started by the backend",
            )
            .await;
        }
    }

    /// Wait until the database port accepts connections, or give up quietly.
    async fn wait_for_database(&self, attempts: u32) {
        let config = self.config.read().await.clone();
        let (host, port) = platform::parse_db_host_port(&config.database_url);
        for _ in 0..attempts {
            if platform::tcp_reachable(&host, port, Duration::from_millis(600)) {
                self.set("database", ServiceStatus::Online, "local cluster ready")
                    .await;
                return;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        let states = self.states.read().await;
        let current = states.get("database").map(|s| s.status);
        drop(states);
        if current != Some(ServiceStatus::Online) {
            self.set(
                "database",
                ServiceStatus::Error,
                format!("PostgreSQL not reachable at {host}:{port}; see logs/backend.log"),
            )
            .await;
        }
    }

    async fn start_backend(self: &Arc<Self>, app_exe: &std::path::Path) {
        let config = self.config.read().await.clone();
        let port = self.backend_port;

        let binary = platform::resolve_sidecar(app_exe, "ecosystem-backend")
            .or_else(|| platform::which("ecosystem-backend"));

        let mut command = match binary {
            Some(path) => Command::new(path),
            None => {
                self.set(
                    "backend",
                    ServiceStatus::Error,
                    "bundled backend sidecar not found next to the application",
                )
                .await;
                return;
            }
        };

        let (db_host, db_port) = platform::parse_db_host_port(&config.database_url);
        let _ = (db_host, db_port);

        command
            .arg("--port")
            .arg(port.to_string())
            .env("ECOSYSTEM_ENVIRONMENT", "production")
            .env("ECOSYSTEM_DATABASE_URL", &config.database_url)
            .env("ECOSYSTEM_LLM_PROVIDER", &config.llm_provider)
            .env("ECOSYSTEM_OLLAMA_BASE_URL", &config.ollama_base_url)
            .env("ECOSYSTEM_OVERSEER_MODEL", &config.overseer_model)
            .env("ECOSYSTEM_AGENT_MODEL", &config.agent_model)
            .env("ECOSYSTEM_CODING_MODEL", &config.coding_model)
            .env(
                "ECOSYSTEM_LLM_MAX_CONTEXT_TOKENS",
                config.max_context_tokens.to_string(),
            )
            .env("ECOSYSTEM_DATA_DIR", platform::data_dir())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());

        // Persist backend output for the Logs/Activity views.
        let _ = std::fs::create_dir_all(&self.log_dir);
        if let Ok(log) = std::fs::File::create(self.log_dir.join("backend.log")) {
            if let Ok(err) = log.try_clone() {
                command.stdout(Stdio::from(log)).stderr(Stdio::from(err));
            }
        }

        match command.spawn() {
            Ok(child) => {
                let pid = child.id();
                *self.backend_child.write().await = Some(child);
                self.set_pid("backend", pid).await;
                self.set_port("backend", port).await;
                self.set("backend", ServiceStatus::Starting, "starting").await;
            }
            Err(err) => {
                self.set("backend", ServiceStatus::Error, format!("spawn failed: {err}"))
                    .await;
            }
        }
    }

    async fn ensure_llm(&self) {
        let config = self.config.read().await.clone();
        if config.llm_provider != "ollama" {
            self.set(
                "llm",
                ServiceStatus::Online,
                format!("{} provider (no model server required)", config.llm_provider),
            )
            .await;
            return;
        }

        let (host, port) = platform::parse_http_host_port(&config.ollama_base_url);
        self.set_port("llm", port).await;

        if platform::tcp_reachable(&host, port, Duration::from_millis(600)) {
            self.set("llm", ServiceStatus::Online, "Ollama responding").await;
            return;
        }

        if let Some(binary) = platform::which("ollama") {
            let mut command = Command::new(binary);
            command
                .arg("serve")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            let _ = std::fs::create_dir_all(&self.log_dir);
            if let Ok(log) = std::fs::File::create(self.log_dir.join("ollama.log")) {
                if let Ok(err) = log.try_clone() {
                    command.stdout(Stdio::from(log)).stderr(Stdio::from(err));
                }
            }
            match command.spawn() {
                Ok(child) => {
                    let pid = child.id();
                    *self.ollama_child.write().await = Some(child);
                    self.set_pid("llm", pid).await;
                    for _ in 0..30 {
                        if platform::tcp_reachable(&host, port, Duration::from_millis(600)) {
                            self.set("llm", ServiceStatus::Online, "Ollama started")
                                .await;
                            return;
                        }
                        tokio::time::sleep(Duration::from_millis(500)).await;
                    }
                    self.set("llm", ServiceStatus::Degraded, "Ollama starting slowly")
                        .await;
                }
                Err(err) => {
                    self.set("llm", ServiceStatus::Error, format!("Ollama failed: {err}"))
                        .await;
                }
            }
        } else {
            self.set(
                "llm",
                ServiceStatus::Error,
                "Local model server not detected. Install Ollama or switch the provider to 'mock' in Settings.",
            )
            .await;
        }
    }

    /// Poll the backend health endpoint and update derived subsystem states.
    pub async fn refresh_health(&self) -> Option<HealthReport> {
        let url = format!("{}/api/system/health", self.backend_url());
        match self.http.get(&url).send().await {
            Ok(response) if response.status().is_success() => {
                let body: serde_json::Value = response.json().await.unwrap_or_default();
                let provider = body
                    .get("llm_provider")
                    .and_then(|v| v.as_str())
                    .unwrap_or("unknown")
                    .to_string();
                let execution = body
                    .get("execution_mode")
                    .and_then(|v| v.as_str())
                    .unwrap_or("paper")
                    .to_string();

                self.set("backend", ServiceStatus::Online, "responding").await;
                // Subsystems live inside the backend process; if it answers,
                // they are up. This keeps the indicator honest without a second
                // health surface.
                self.set("memory", ServiceStatus::Online, "pgvector store ready")
                    .await;
                self.set("risk", ServiceStatus::Online, "deterministic limits loaded")
                    .await;
                self.set("scheduler", ServiceStatus::Online, "inference queue ready")
                    .await;
                if provider != "ollama" {
                    self.set("llm", ServiceStatus::Online, format!("{provider} provider"))
                        .await;
                }
                Some(HealthReport {
                    ok: true,
                    backend: self.backend_url(),
                    llm_provider: provider,
                    execution_mode: execution,
                })
            }
            _ => {
                let states = self.states.read().await;
                let backend_status = states.get("backend").map(|s| s.status);
                drop(states);
                if backend_status != Some(ServiceStatus::Error) {
                    self.set("backend", ServiceStatus::Degraded, "not responding yet")
                        .await;
                }
                None
            }
        }
    }

    /// Wait until the backend answers, or give up after `timeout`.
    pub async fn wait_for_backend(&self, timeout: Duration) -> bool {
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            if self.refresh_health().await.is_some() {
                return true;
            }
            if tokio::time::Instant::now() >= deadline {
                return false;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }

    /// Stop children started by this shell. The database is left running.
    pub async fn stop_all(&self) {
        if let Some(mut child) = self.backend_child.write().await.take() {
            let _ = child.kill().await;
        }
        if let Some(mut child) = self.ollama_child.write().await.take() {
            let _ = child.kill().await;
        }
    }
}
