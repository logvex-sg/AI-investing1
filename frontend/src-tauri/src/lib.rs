//! ECOSYSTEM desktop shell.
//!
//! The shell is the application: it starts the local services, hosts the React
//! control center in a native window, and exposes a small command surface for
//! service state, hardware telemetry, model management, the first-start wizard
//! and desktop notifications. It never performs financial actions — those stay
//! behind the backend's risk, accounting and human-approval pipeline.

mod hardware;
mod models;
mod platform;
mod services;
mod setup;

use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use tauri::{Emitter, Manager, State};
use tauri_plugin_notification::NotificationExt;

use platform::Config;
use services::{ServiceState, Supervisor};

struct AppState {
    supervisor: Arc<Supervisor>,
}

#[derive(Serialize)]
struct RuntimeInfo {
    backend_url: String,
    data_dir: String,
    logs_dir: String,
    config_path: String,
    setup_complete: bool,
}

#[tauri::command]
async fn runtime_info(state: State<'_, AppState>) -> Result<RuntimeInfo, String> {
    let config = state.supervisor.config.read().await.clone();
    Ok(RuntimeInfo {
        backend_url: state.supervisor.backend_url(),
        data_dir: platform::data_dir().to_string_lossy().to_string(),
        logs_dir: platform::logs_dir().to_string_lossy().to_string(),
        config_path: platform::config_path().to_string_lossy().to_string(),
        setup_complete: config.setup_complete,
    })
}

#[tauri::command]
async fn service_status(state: State<'_, AppState>) -> Result<Vec<ServiceState>, String> {
    Ok(state.supervisor.snapshot().await)
}

#[tauri::command]
async fn refresh_services(state: State<'_, AppState>) -> Result<Vec<ServiceState>, String> {
    state.supervisor.refresh_health().await;
    Ok(state.supervisor.snapshot().await)
}

#[tauri::command]
async fn get_config(state: State<'_, AppState>) -> Result<Config, String> {
    Ok(state.supervisor.config.read().await.clone())
}

#[tauri::command]
async fn update_config(
    state: State<'_, AppState>,
    config: Config,
) -> Result<Config, String> {
    {
        let mut current = state.supervisor.config.write().await;
        *current = config.clone();
    }
    platform::save_config(&config).map_err(|e| e.to_string())?;
    Ok(config)
}

#[tauri::command]
async fn hardware_report() -> Result<hardware::HardwareReport, String> {
    Ok(hardware::detect())
}

#[tauri::command]
async fn resource_snapshot() -> Result<hardware::ResourceSnapshot, String> {
    Ok(hardware::snapshot())
}

#[tauri::command]
async fn list_models(state: State<'_, AppState>) -> Result<models::ModelReport, String> {
    let config = state.supervisor.config.read().await.clone();
    Ok(models::list(
        &config.ollama_base_url,
        &config.llm_provider,
        &config.overseer_model,
        &config.agent_model,
        &config.coding_model,
    )
    .await)
}

#[tauri::command]
async fn pull_model(state: State<'_, AppState>, model: String) -> Result<String, String> {
    let config = state.supervisor.config.read().await.clone();
    models::pull(&config.ollama_base_url, &model).await
}

#[tauri::command]
async fn detect_ollama() -> Result<bool, String> {
    Ok(platform::which("ollama").is_some())
}

#[tauri::command]
async fn setup_check_database(state: State<'_, AppState>) -> Result<setup::StepResult, String> {
    Ok(setup::check_database(&state.supervisor).await)
}

#[tauri::command]
async fn setup_create_admin(
    state: State<'_, AppState>,
    username: String,
    password: String,
    display_name: String,
) -> Result<setup::StepResult, String> {
    Ok(setup::create_admin(&state.supervisor, &username, &password, &display_name).await)
}

#[tauri::command]
async fn setup_bootstrap_generation(
    state: State<'_, AppState>,
    username: String,
    password: String,
) -> Result<setup::StepResult, String> {
    Ok(setup::bootstrap_generation(&state.supervisor, &username, &password).await)
}

#[tauri::command]
async fn complete_setup(state: State<'_, AppState>) -> Result<(), String> {
    let mut config = state.supervisor.config.read().await.clone();
    config.setup_complete = true;
    platform::save_config(&config).map_err(|e| e.to_string())?;
    *state.supervisor.config.write().await = config;
    Ok(())
}

#[tauri::command]
async fn notify(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    title: String,
    body: String,
) -> Result<(), String> {
    let enabled = state.supervisor.config.read().await.notifications_enabled;
    if !enabled {
        return Ok(());
    }
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|e| e.to_string())
}

#[tauri::command]
async fn open_path(app: tauri::AppHandle, path: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener()
        .open_path(path, None::<&str>)
        .map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let config = platform::load_config();
    let supervisor = Supervisor::new(config);

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            // Focus the existing window instead of opening a second ecosystem.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
                let _ = window.unminimize();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(AppState {
            supervisor: supervisor.clone(),
        })
        .invoke_handler(tauri::generate_handler![
            runtime_info,
            service_status,
            refresh_services,
            get_config,
            update_config,
            hardware_report,
            resource_snapshot,
            list_models,
            pull_model,
            detect_ollama,
            setup_check_database,
            setup_create_admin,
            setup_bootstrap_generation,
            complete_setup,
            notify,
            open_path,
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let exe = std::env::current_exe().unwrap_or_default();
            let supervisor = supervisor.clone();

            // Start the local services without blocking the window: the GUI
            // opens immediately and shows each service as it comes online.
            tauri::async_runtime::spawn(async move {
                supervisor.start_all(exe).await;
                let _ = supervisor.wait_for_backend(Duration::from_secs(45)).await;
                if let Some(window) = handle.get_webview_window("main") {
                    let _ = window.emit("services-ready", ());
                }
            });

            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let app = window.app_handle().clone();
                let state = app.state::<AppState>();
                let supervisor = state.supervisor.clone();
                tauri::async_runtime::spawn(async move {
                    supervisor.stop_all().await;
                });
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running ECOSYSTEM desktop shell");
}
