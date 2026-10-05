//! Local model management.
//!
//! One shared inference server (Ollama) serves every logical agent. The shell
//! only lists and pulls models; it never loads eight copies, which is what
//! keeps the ecosystem usable on a 4 GB GPU.

use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct ModelInfo {
    pub name: String,
    pub size_bytes: u64,
    pub size_gb: f64,
    pub parameter_size: String,
    pub quantization: String,
    pub family: String,
    pub modified_at: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ModelReport {
    pub server_available: bool,
    pub base_url: String,
    pub provider: String,
    pub overseer_model: String,
    pub agent_model: String,
    pub coding_model: String,
    pub models: Vec<ModelInfo>,
    pub error: Option<String>,
}

fn human_size(bytes: u64) -> f64 {
    (bytes as f64 / 1024.0 / 1024.0 / 1024.0 * 100.0).round() / 100.0
}

pub async fn list(base_url: &str, provider: &str, overseer: &str, agent: &str, coding: &str) -> ModelReport {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(6))
        .build()
        .unwrap_or_default();

    let mut report = ModelReport {
        server_available: false,
        base_url: base_url.to_string(),
        provider: provider.to_string(),
        overseer_model: overseer.to_string(),
        agent_model: agent.to_string(),
        coding_model: coding.to_string(),
        models: vec![],
        error: None,
    };

    if provider != "ollama" {
        report.error = Some(format!(
            "Provider is '{provider}'. The mock provider is deterministic and needs no model server."
        ));
        return report;
    }

    match client
        .get(format!("{}/api/tags", base_url.trim_end_matches('/')))
        .send()
        .await
    {
        Ok(response) if response.status().is_success() => {
            report.server_available = true;
            let body: serde_json::Value = response.json().await.unwrap_or_default();
            if let Some(items) = body.get("models").and_then(|m| m.as_array()) {
                for item in items {
                    let details = item.get("details").cloned().unwrap_or_default();
                    let size = item.get("size").and_then(|s| s.as_u64()).unwrap_or(0);
                    report.models.push(ModelInfo {
                        name: item
                            .get("name")
                            .and_then(|n| n.as_str())
                            .unwrap_or("unknown")
                            .to_string(),
                        size_bytes: size,
                        size_gb: human_size(size),
                        parameter_size: details
                            .get("parameter_size")
                            .and_then(|v| v.as_str())
                            .unwrap_or("—")
                            .to_string(),
                        quantization: details
                            .get("quantization_level")
                            .and_then(|v| v.as_str())
                            .unwrap_or("—")
                            .to_string(),
                        family: details
                            .get("family")
                            .and_then(|v| v.as_str())
                            .unwrap_or("—")
                            .to_string(),
                        modified_at: item
                            .get("modified_at")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                    });
                }
            }
        }
        Ok(response) => {
            report.error = Some(format!("Ollama responded with HTTP {}", response.status()));
        }
        Err(err) => {
            report.error = Some(format!("Local model server not detected: {err}"));
        }
    }
    report
}

/// Start a model pull. Returns immediately; progress is observable through the
/// server's own state, so the GUI stays responsive.
pub async fn pull(base_url: &str, model: &str) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())?;
    let body = serde_json::json!({ "name": model, "stream": false });
    let response = client
        .post(format!("{}/api/pull", base_url.trim_end_matches('/')))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("could not reach model server: {e}"))?;
    if response.status().is_success() {
        Ok(format!("Pull of {model} requested."))
    } else {
        Err(format!("model server returned HTTP {}", response.status()))
    }
}
