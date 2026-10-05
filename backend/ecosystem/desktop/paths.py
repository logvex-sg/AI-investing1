"""Application data locations shared by the desktop launcher and the shell.

The desktop app keeps everything under the XDG data directory so that closing
the application never destroys state. `ECOSYSTEM_DATA_DIR` overrides the root,
which is what the Tauri shell uses to keep a single source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("ECOSYSTEM_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "ecosystem"


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"
