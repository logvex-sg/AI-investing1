//! Hardware detection and live resource monitoring.
//!
//! The target machine is a 16 GB laptop with a 4 GB RTX 3050, so the GUI needs
//! to show RAM/VRAM pressure honestly. GPU readings come from `nvidia-smi`
//! when it is present and degrade to "unavailable" otherwise.

use serde::Serialize;
use sysinfo::System;

#[derive(Debug, Clone, Serialize)]
pub struct GpuInfo {
    pub name: String,
    pub vram_total_mb: u64,
    pub vram_used_mb: u64,
    pub utilization_pct: u64,
    pub available: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct HardwareReport {
    pub cpu_brand: String,
    pub cpu_cores: usize,
    pub ram_total_mb: u64,
    pub ram_used_mb: u64,
    pub swap_total_mb: u64,
    pub swap_used_mb: u64,
    pub disk_total_gb: f64,
    pub disk_available_gb: f64,
    pub gpu: GpuInfo,
}

#[derive(Debug, Clone, Serialize)]
pub struct ResourceSnapshot {
    pub cpu_usage_pct: f32,
    pub ram_total_mb: u64,
    pub ram_used_mb: u64,
    pub ram_usage_pct: f32,
    pub swap_total_mb: u64,
    pub swap_used_mb: u64,
    pub gpu_utilization_pct: u64,
    pub vram_total_mb: u64,
    pub vram_used_mb: u64,
    pub gpu_available: bool,
}

fn nvidia_smi() -> Option<GpuInfo> {
    let output = std::process::Command::new("nvidia-smi")
        .args([
            "--query-gpu=name,memory.total,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let line = text.lines().next()?;
    let parts: Vec<&str> = line.split(',').map(|p| p.trim()).collect();
    if parts.len() < 4 {
        return None;
    }
    Some(GpuInfo {
        name: parts[0].to_string(),
        vram_total_mb: parts[1].parse().unwrap_or(0),
        vram_used_mb: parts[2].parse().unwrap_or(0),
        utilization_pct: parts[3].parse().unwrap_or(0),
        available: true,
    })
}

fn no_gpu() -> GpuInfo {
    GpuInfo {
        name: "No NVIDIA GPU detected".to_string(),
        vram_total_mb: 0,
        vram_used_mb: 0,
        utilization_pct: 0,
        available: false,
    }
}

pub fn detect() -> HardwareReport {
    let mut sys = System::new_all();
    sys.refresh_all();

    let cpu_brand = sys
        .cpus()
        .first()
        .map(|c| c.brand().trim().to_string())
        .unwrap_or_else(|| "Unknown CPU".to_string());

    let disks = sysinfo::Disks::new_with_refreshed_list();
    let (disk_total, disk_available) = disks
        .iter()
        .map(|d| (d.total_space(), d.available_space()))
        .fold((0u64, 0u64), |acc, (t, a)| (acc.0 + t, acc.1 + a));

    HardwareReport {
        cpu_brand,
        cpu_cores: sys.cpus().len(),
        ram_total_mb: sys.total_memory() / 1024 / 1024,
        ram_used_mb: sys.used_memory() / 1024 / 1024,
        swap_total_mb: sys.total_swap() / 1024 / 1024,
        swap_used_mb: sys.used_swap() / 1024 / 1024,
        disk_total_gb: disk_total as f64 / 1024.0 / 1024.0 / 1024.0,
        disk_available_gb: disk_available as f64 / 1024.0 / 1024.0 / 1024.0,
        gpu: nvidia_smi().unwrap_or_else(no_gpu),
    }
}

pub fn snapshot() -> ResourceSnapshot {
    let mut sys = System::new();
    sys.refresh_memory();
    sys.refresh_cpu_usage();
    // CPU usage needs two samples separated in time to be meaningful.
    std::thread::sleep(std::time::Duration::from_millis(120));
    sys.refresh_cpu_usage();

    let cpu_usage: f32 = {
        let cpus = sys.cpus();
        if cpus.is_empty() {
            0.0
        } else {
            cpus.iter().map(|c| c.cpu_usage()).sum::<f32>() / cpus.len() as f32
        }
    };

    let ram_total = sys.total_memory() / 1024 / 1024;
    let ram_used = sys.used_memory() / 1024 / 1024;
    let ram_pct = if ram_total > 0 {
        ram_used as f32 / ram_total as f32 * 100.0
    } else {
        0.0
    };

    let gpu = nvidia_smi().unwrap_or_else(no_gpu);

    ResourceSnapshot {
        cpu_usage_pct: (cpu_usage * 10.0).round() / 10.0,
        ram_total_mb: ram_total,
        ram_used_mb: ram_used,
        ram_usage_pct: (ram_pct * 10.0).round() / 10.0,
        swap_total_mb: sys.total_swap() / 1024 / 1024,
        swap_used_mb: sys.used_swap() / 1024 / 1024,
        gpu_utilization_pct: gpu.utilization_pct,
        vram_total_mb: gpu.vram_total_mb,
        vram_used_mb: gpu.vram_used_mb,
        gpu_available: gpu.available,
    }
}
