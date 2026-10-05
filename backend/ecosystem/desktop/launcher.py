"""Entrypoint for the bundled backend sidecar.

Responsibilities, in order:

1. Resolve the data directory and load `config.json` written by the shell.
2. Ensure a PostgreSQL server is reachable (starting a private cluster if not).
3. Apply Alembic migrations so the schema always matches the code.
4. Serve the FastAPI app on loopback.

The launcher never prints secrets. It exits non-zero with a concise message if
it cannot bring the database up, and the shell surfaces that in the GUI.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _bootstrap_environment(port: int) -> dict:
    """Populate ECOSYSTEM_* environment variables from the desktop config."""
    from ecosystem.desktop.paths import config_path

    env: dict[str, str] = {}
    path = config_path()
    config: dict = {}
    if path.is_file():
        try:
            config = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            config = {}

    mapping = {
        "database_url": "ECOSYSTEM_DATABASE_URL",
        "llm_provider": "ECOSYSTEM_LLM_PROVIDER",
        "ollama_base_url": "ECOSYSTEM_OLLAMA_BASE_URL",
        "overseer_model": "ECOSYSTEM_OVERSEER_MODEL",
        "agent_model": "ECOSYSTEM_AGENT_MODEL",
        "coding_model": "ECOSYSTEM_CODING_MODEL",
        "max_context_tokens": "ECOSYSTEM_LLM_MAX_CONTEXT_TOKENS",
    }
    for key, variable in mapping.items():
        value = config.get(key)
        if value is not None and not os.environ.get(variable):
            env[variable] = str(value)

    env.setdefault("ECOSYSTEM_ENVIRONMENT", "production")
    env["ECOSYSTEM_API_PORT"] = str(port)
    # The desktop build is deny-by-default: no live execution adapters.
    env.setdefault("ECOSYSTEM_ENABLE_TESTNET_ADAPTER", "false")
    env.setdefault("ECOSYSTEM_ENABLE_PRODUCTION_ADAPTER", "false")
    return env


def _resource_root() -> Path:
    """Directory that holds `alembic.ini` and the `alembic/` migrations.

    In a frozen (PyInstaller) build everything is unpacked under `sys._MEIPASS`;
    in a source checkout it is the backend root.
    """
    override = os.environ.get("ECOSYSTEM_RESOURCE_DIR")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[3]


def _run_migrations() -> None:
    """Apply Alembic migrations in-process (no external alembic binary needed)."""
    from alembic import command
    from alembic.config import Config

    from ecosystem.desktop.paths import data_dir

    root = _resource_root()
    ini = root / "alembic.ini"
    if not ini.is_file():
        raise RuntimeError(f"alembic.ini not found under {root}")

    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("prepend_sys_path", ".")
    log_dir = data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    cfg.set_main_option("sqlalchemy.url", os.environ.get("ECOSYSTEM_DATABASE_URL", ""))
    command.upgrade(cfg, "head")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ecosystem-backend")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--skip-database-management",
        action="store_true",
        help="assume PostgreSQL is already running (managed externally)",
    )
    args = parser.parse_args(argv)

    for key, value in _bootstrap_environment(args.port).items():
        os.environ.setdefault(key, value)

    if not args.skip_database_management:
        from ecosystem.desktop import postgres

        try:
            note = postgres.ensure_database(
                os.environ.get(
                    "ECOSYSTEM_DATABASE_URL",
                    "postgresql+asyncpg://ecosystem:ecosystem@127.0.0.1:5432/ecosystem",
                )
            )
            print(f"[ecosystem] database: {note}", flush=True)
        except postgres.PgError as exc:
            print(f"[ecosystem] database unavailable: {exc}", file=sys.stderr, flush=True)
            return 2

    try:
        _run_migrations()
        print("[ecosystem] migrations applied", flush=True)
    except Exception as exc:  # noqa: BLE001 - surface any migration failure
        print(f"[ecosystem] migration failed: {exc}", file=sys.stderr, flush=True)
        return 3

    import uvicorn

    from ecosystem.app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
