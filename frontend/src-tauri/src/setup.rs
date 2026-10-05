//! First-start setup: verify the database, create the local administrator and
//! bootstrap the initial generation.
//!
//! Every step is idempotent so the wizard can be re-run safely. Financial
//! actions remain impossible until a human administrator exists.

use serde::Serialize;

use crate::services::Supervisor;

#[derive(Debug, Clone, Serialize)]
pub struct StepResult {
    pub ok: bool,
    pub message: String,
}

impl StepResult {
    fn ok(message: impl Into<String>) -> Self {
        StepResult {
            ok: true,
            message: message.into(),
        }
    }
    fn err(message: impl Into<String>) -> Self {
        StepResult {
            ok: false,
            message: message.into(),
        }
    }
}

/// Ask the backend whether the database and schema are ready.
pub async fn check_database(supervisor: &Supervisor) -> StepResult {
    let url = format!("{}/api/system/health", supervisor.backend_url());
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(6))
        .build()
        .unwrap_or_default();
    match client.get(&url).send().await {
        Ok(response) if response.status().is_success() => {
            StepResult::ok("Database and backend are online.")
        }
        Ok(response) => StepResult::err(format!(
            "Backend answered with HTTP {}. The schema may still be migrating.",
            response.status()
        )),
        Err(err) => StepResult::err(format!("Backend not reachable: {err}")),
    }
}

/// Create the first administrator through the bootstrap endpoint. The endpoint
/// refuses once any user exists, so this is safe to call more than once.
pub async fn create_admin(
    supervisor: &Supervisor,
    username: &str,
    password: &str,
    display_name: &str,
) -> StepResult {
    if username.trim().len() < 3 || password.len() < 12 {
        return StepResult::err(
            "Choose a username of at least 3 characters and a password of at least 12 characters.",
        );
    }
    let url = format!("{}/api/auth/bootstrap", supervisor.backend_url());
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .unwrap_or_default();
    let body = serde_json::json!({
        "username": username,
        "password": password,
        "display_name": display_name,
    });
    match client.post(&url).json(&body).send().await {
        Ok(response) if response.status().is_success() => {
            StepResult::ok(format!("Administrator '{username}' created."))
        }
        Ok(response) => {
            let status = response.status();
            let text = response.text().await.unwrap_or_default();
            if status.as_u16() == 409 || text.contains("already") {
                StepResult::ok("An administrator already exists; keeping it.")
            } else {
                StepResult::err(format!("Could not create administrator: {text}"))
            }
        }
        Err(err) => StepResult::err(format!("Backend not reachable: {err}")),
    }
}

/// Log in and bootstrap the genesis generation (idempotent on the backend).
pub async fn bootstrap_generation(supervisor: &Supervisor, username: &str, password: &str) -> StepResult {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .unwrap_or_default();
    let base = supervisor.backend_url();

    let login = client
        .post(format!("{base}/api/auth/login"))
        .json(&serde_json::json!({ "username": username, "password": password }))
        .send()
        .await;
    let token = match login {
        Ok(response) if response.status().is_success() => {
            let body: serde_json::Value = response.json().await.unwrap_or_default();
            body.get("token").and_then(|t| t.as_str()).map(str::to_string)
        }
        _ => None,
    };
    let Some(token) = token else {
        return StepResult::err("Could not authenticate to bootstrap the generation.");
    };

    let response = client
        .post(format!("{base}/api/generations/bootstrap"))
        .bearer_auth(&token)
        .send()
        .await;
    match response {
        Ok(r) if r.status().is_success() => {
            StepResult::ok("Initial generation is ready (agents A1–A8).")
        }
        Ok(r) => {
            let text = r.text().await.unwrap_or_default();
            if text.contains("exists") || text.contains("already") {
                StepResult::ok("A generation already exists; keeping it.")
            } else {
                StepResult::err(format!("Could not bootstrap generation: {text}"))
            }
        }
        Err(err) => StepResult::err(format!("Backend not reachable: {err}")),
    }
}
