//! Paths, on-disk configuration and small OS helpers.
//!
//! All mutable application state lives under the XDG data directory
//! (`~/.local/share/ecosystem` by default) so that closing the application
//! never destroys agent state and restarting it restores everything.

use std::fs;
use std::net::{TcpListener, TcpStream, ToSocketAddrs};
use std::path::PathBuf;
use std::time::Duration;

use serde::{Deserialize, Serialize};

/// Everything the desktop shell needs to know to start the ecosystem.
///
/// This is written to `config.json` in the data directory on first run. It
/// never contains third-party credentials: the database password is generated
/// locally for the loopback-only PostgreSQL role.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub setup_complete: bool,
    pub database_url: String,
    pub llm_provider: String,
    pub ollama_base_url: String,
    pub overseer_model: String,
    pub agent_model: String,
    pub coding_model: String,
    pub max_context_tokens: u32,
    pub notifications_enabled: bool,
    pub theme: String,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            setup_complete: false,
            // A dedicated loopback port keeps the private cluster clear of any
            // system PostgreSQL that may already own 5432.
            database_url:
                "postgresql+asyncpg://ecosystem:ecosystem@127.0.0.1:55432/ecosystem".to_string(),
            llm_provider: "mock".to_string(),
            ollama_base_url: "http://127.0.0.1:11434".to_string(),
            overseer_model: "qwen2.5:7b-instruct".to_string(),
            agent_model: "qwen2.5:7b-instruct".to_string(),
            coding_model: "qwen2.5-coder:7b-instruct".to_string(),
            max_context_tokens: 4096,
            notifications_enabled: true,
            theme: "dark".to_string(),
        }
    }
}

/// Root of the writable application state.
pub fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("ECOSYSTEM_DATA_DIR") {
        if !dir.is_empty() {
            return PathBuf::from(dir);
        }
    }
    let base = dirs::data_dir().unwrap_or_else(|| PathBuf::from("."));
    base.join("ecosystem")
}

pub fn config_path() -> PathBuf {
    data_dir().join("config.json")
}

pub fn logs_dir() -> PathBuf {
    data_dir().join("logs")
}

/// Load the configuration, creating a default one on first run.
pub fn load_config() -> Config {
    let path = config_path();
    if let Ok(text) = fs::read_to_string(&path) {
        if let Ok(config) = serde_json::from_str::<Config>(&text) {
            return config;
        }
    }
    let config = Config::default();
    let _ = save_config(&config);
    config
}

pub fn save_config(config: &Config) -> std::io::Result<()> {
    let dir = data_dir();
    fs::create_dir_all(&dir)?;
    fs::create_dir_all(logs_dir())?;
    let text = serde_json::to_string_pretty(config)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    fs::write(dir.join("config.json"), text)?;
    // The config is not a secret store, but it is user data: keep it private.
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(dir.join("config.json"), fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

/// Ask the kernel for a free loopback port.
///
/// The backend binds to loopback only; nothing is exposed to the network.
pub fn free_port() -> u16 {
    TcpListener::bind(("127.0.0.1", 0))
        .ok()
        .and_then(|l| l.local_addr().ok())
        .map(|a| a.port())
        .unwrap_or(8000)
}

/// TCP reachability probe used for the database.
pub fn tcp_reachable(host: &str, port: u16, timeout: Duration) -> bool {
    let addr = format!("{host}:{port}");
    match addr.to_socket_addrs() {
        Ok(mut addrs) => match addrs.next() {
            Some(sock) => TcpStream::connect_timeout(&sock, timeout).is_ok(),
            None => false,
        },
        Err(_) => false,
    }
}

/// Parse `host:port` out of a SQLAlchemy-style database URL.
pub fn parse_db_host_port(url: &str) -> (String, u16) {
    // postgresql+asyncpg://user:pass@host:port/db
    let after_scheme = url.split("://").nth(1).unwrap_or(url);
    let authority = after_scheme.split('/').next().unwrap_or(after_scheme);
    let host_port = authority.rsplit('@').next().unwrap_or(authority);
    let mut parts = host_port.split(':');
    let host = parts.next().unwrap_or("127.0.0.1").to_string();
    let port = parts.next().and_then(|p| p.parse().ok()).unwrap_or(5432);
    (host, port)
}

/// Parse host/port out of an `http://host:port` base URL.
pub fn parse_http_host_port(url: &str) -> (String, u16) {
    let rest = url.split("://").nth(1).unwrap_or(url);
    let authority = rest.split('/').next().unwrap_or(rest);
    let mut parts = authority.split(':');
    let host = parts.next().unwrap_or("127.0.0.1").to_string();
    let port = parts.next().and_then(|p| p.parse().ok()).unwrap_or(80);
    (host, port)
}

/// Locate a bundled sidecar binary.
///
/// Tauri copies `externalBin` entries next to the main executable. Depending on
/// the bundler the target triple may be part of the file name, so both spellings
/// are tried.
pub fn resolve_sidecar(app_exe: &std::path::Path, name: &str) -> Option<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(explicit) = std::env::var("ECOSYSTEM_BACKEND_BIN") {
        if !explicit.is_empty() {
            candidates.push(PathBuf::from(explicit));
        }
    }
    if let Some(dir) = app_exe.parent() {
        candidates.push(dir.join(name));
        candidates.push(dir.join(format!("{name}-x86_64-unknown-linux-gnu")));
        candidates.push(dir.join(format!("{name}-aarch64-unknown-linux-gnu")));
        // A user-level install keeps the sidecar in ~/.local/lib/ecosystem while
        // the launcher lives in ~/.local/bin.
        candidates.push(dir.join("..").join("lib").join("ecosystem").join(name));
    }
    for candidate in candidates {
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

pub fn which(program: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(program);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}
