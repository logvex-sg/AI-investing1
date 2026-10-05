"""Self-managed PostgreSQL for the desktop application.

The desktop build must not assume the user knows how to administer PostgreSQL.
This module keeps a private cluster under the application data directory
(`~/.local/share/ecosystem/pgdata`) owned by the current user, so the app can
start its database without root and without touching a system cluster.

If a server is already reachable at the configured host/port (for example a
system PostgreSQL that the user set up, or one we started previously) it is
reused and nothing is started.
"""

from __future__ import annotations

import getpass
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from ecosystem.desktop.paths import data_dir, logs_dir

PG_BIN_CANDIDATES = [
    "/usr/lib/postgresql/17/bin",
    "/usr/lib/postgresql/16/bin",
    "/usr/lib/postgresql/15/bin",
    "/usr/lib/postgresql/14/bin",
    "/usr/local/pgsql/bin",
]


def _find_bin(name: str) -> str | None:
    for directory in PG_BIN_CANDIDATES:
        candidate = Path(directory) / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def parse_host_port(url: str) -> tuple[str, int, str, str]:
    """Return (host, port, user, password) from a SQLAlchemy async URL."""
    from urllib.parse import urlsplit

    # asyncpg URLs are not understood by urlsplit's default schemes, so strip
    # the driver suffix first.
    normalised = url.replace("postgresql+asyncpg://", "postgresql://")
    parts = urlsplit(normalised)
    return (
        parts.hostname or "127.0.0.1",
        parts.port or 5432,
        parts.username or "ecosystem",
        parts.password or "ecosystem",
    )


class PgError(RuntimeError):
    pass


def _run(cmd: list[str], *, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, check=False
    )


def cluster_ready(pgdata: Path) -> bool:
    return (pgdata / "PG_VERSION").is_file()


def _run_as_postgres_user(argv: list[str], env: dict) -> subprocess.CompletedProcess:
    """Run a PostgreSQL binary under a non-root identity.

    initdb/postgres refuse to run as root. On a normal desktop install the app
    is not root and this is a no-op. When it *is* root (containers, CI), we drop
    to the `postgres` system user, which is the only identity that can own a
    cluster there.
    """
    if os.geteuid() != 0:
        return _run(argv, env=env)
    postgres_user = shutil.which("runuser") or shutil.which("su")
    if postgres_user is None:
        raise PgError(
            "running as root and neither runuser nor su is available to drop privileges"
        )
    if postgres_user.endswith("runuser"):
        return _run(["runuser", "-u", "postgres", "--", *argv], env=env)
    return _run(["su", "postgres", "-c", " ".join(argv)], env=env)


def init_cluster(pgdata: Path, password: str) -> None:
    initdb = _find_bin("initdb")
    if initdb is None:
        raise PgError(
            "PostgreSQL server binaries not found. Install the 'postgresql' package."
        )
    pgdata.mkdir(parents=True, exist_ok=True)
    pwfile = pgdata.parent / ".pwfile"
    pwfile.write_text(password)
    try:
        # A non-root user owns the directory directly; root drops to postgres.
        if os.geteuid() == 0:
            shutil.chown(pgdata, user="postgres")
            shutil.chown(pgdata.parent, user="postgres")
            shutil.chown(pwfile, user="postgres")
        result = _run_as_postgres_user(
            [
                initdb,
                "-D",
                str(pgdata),
                "-U",
                "ecosystem",
                "--auth-local=trust",
                "--auth-host=trust",
                f"--pwfile={pwfile}",
                "-E",
                "UTF8",
            ],
            env=os.environ.copy(),
        )
    finally:
        pwfile.unlink(missing_ok=True)
    if result.returncode != 0:
        raise PgError(f"initdb failed: {result.stderr.strip()[:400]}")


def _pg_ctl_start(pgdata: Path, logfile: Path, port: int) -> None:
    pg_ctl = _find_bin("pg_ctl")
    if pg_ctl is None:
        raise PgError("pg_ctl not found; cannot start the local database.")
    if os.geteuid() == 0:
        shutil.chown(pgdata, user="postgres")
        shutil.chown(logfile.parent, user="postgres")
    options = f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={pgdata.parent}"
    result = _run_as_postgres_user(
        [
            pg_ctl,
            "-D",
            str(pgdata),
            "-l",
            str(logfile),
            "-o",
            options,
            "-w",
            "-t",
            "30",
            "start",
        ],
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        # Already running is fine; anything else is a real failure.
        combined = (result.stdout + result.stderr).lower()
        if "already running" not in combined:
            raise PgError(f"pg_ctl start failed: {result.stderr.strip()[:400]}")


def ensure_extensions_and_database(host: str, port: int, user: str, dbname: str) -> None:
    psql = _find_bin("psql")
    if psql is None:
        raise PgError("psql not found; cannot prepare the database.")
    env = os.environ.copy()
    env["PGPASSWORD"] = os.environ.get("ECOSYSTEM_DB_PASSWORD", "")

    def query(sql: str, database: str = "postgres") -> subprocess.CompletedProcess:
        return _run(
            [psql, "-h", host, "-p", str(port), "-U", user, "-d", database, "-tAc", sql],
            env=env,
        )

    if query(f"SELECT 1 FROM pg_database WHERE datname='{dbname}'").stdout.strip() != "1":
        _run(
            [psql, "-h", host, "-p", str(port), "-U", user, "-d", "postgres", "-c",
             f'CREATE DATABASE "{dbname}"'],
            env=env,
        )

    # pgvector may need superuser rights; the app role is a superuser only in
    # the private-cluster case, where this succeeds. Otherwise the migration
    # itself reports the problem clearly.
    for extension in ("vector", "pg_trgm"):
        query(f"CREATE EXTENSION IF NOT EXISTS {extension}", database=dbname)


def ensure_database(url: str, *, dbname: str = "ecosystem") -> str:
    """Make sure a database is reachable; start a private one if needed.

    Returns a short human-readable note about what happened, suitable for the
    service-status detail line.
    """
    host, port, user, password = parse_host_port(url)
    if port_open(host, port):
        ensure_extensions_and_database(host, port, user, dbname)
        return "reused an existing PostgreSQL server"

    pgdata = data_dir() / "pgdata"
    logfile = logs_dir() / "postgres.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    os.environ["ECOSYSTEM_DB_PASSWORD"] = password

    if not cluster_ready(pgdata):
        init_cluster(pgdata, password)

    _pg_ctl_start(pgdata, logfile, port)

    for _ in range(40):
        if port_open(host, port):
            break
        time.sleep(0.5)
    else:
        raise PgError(
            f"local database did not start on {host}:{port}; see {logfile}"
        )

    ensure_extensions_and_database(host, port, user, dbname)
    return "started a private PostgreSQL cluster"


def stop_cluster() -> None:
    pgdata = data_dir() / "pgdata"
    if not cluster_ready(pgdata):
        return
    pg_ctl = _find_bin("pg_ctl")
    if pg_ctl is None:
        return
    _run_as_postgres_user(
        [pg_ctl, "-D", str(pgdata), "-m", "fast", "-w", "-t", "20", "stop"],
        env=os.environ.copy(),
    )


def current_user_name() -> str:
    return os.environ.get("USER") or getpass.getuser()


if __name__ == "__main__":  # pragma: no cover - manual helper
    print(ensure_database(sys.argv[1]))
