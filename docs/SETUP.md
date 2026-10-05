# ECOSYSTEM — Setup Guide

How to install and first-run ECOSYSTEM as a native desktop application on Linux.

Two paths:

- **A. Install a prebuilt bundle** — the fastest route. Use this if you already
  have `ECOSYSTEM_*.deb` or `ECOSYSTEM_*.AppImage`.
- **B. Build from source** — use this to produce the bundles yourself.

Either way, the first launch runs a setup wizard and everything else is
automatic. Nothing here trades real capital: the default execution adapter is
paper trading.

---

## 1. Prerequisites

| Requirement | Why | Install |
| --- | --- | --- |
| Linux x86_64 (Ubuntu/Debian tested) | Target platform | — |
| PostgreSQL 17 server + client | The app starts its own private cluster; the binaries must be present | `sudo apt install postgresql postgresql-17-pgvector` |
| WebKitGTK 4.1 + GTK 3 | Native webview runtime | `sudo apt install libwebkit2gtk-4.1-0 libgtk-3-0` |
| FUSE (AppImage only) | To mount the AppImage | `sudo apt install libfuse2t64` |
| Ollama *(optional)* | Local LLM inference; skip to use the deterministic `mock` provider | https://ollama.com |

The `.deb` declares PostgreSQL, pgvector, WebKitGTK and GTK as dependencies, so
`apt` pulls them in automatically.

For **building from source** you additionally need:

| Requirement | Version | Install |
| --- | --- | --- |
| Rust toolchain | 1.77+ | `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \| sh` |
| Node.js | 20+ | https://nodejs.org or `nvm` |
| Python | 3.11+ | usually already present |
| Build tools | — | `sudo apt install build-essential libwebkit2gtk-4.1-dev libgtk-3-dev` |

---

## 2A. Install a prebuilt bundle

### Debian/Ubuntu package (recommended)

```bash
sudo apt install ./ECOSYSTEM_0.1.0_amd64.deb
```

This installs the shell, the bundled backend sidecar, the icon and the
application-menu entry. Launch **ECOSYSTEM** from your application menu, or:

```bash
ecosystem-desktop
```

### AppImage (no install, no root)

```bash
chmod +x ECOSYSTEM_0.1.0_amd64.AppImage
./ECOSYSTEM_0.1.0_amd64.AppImage
```

If FUSE is unavailable, extract and run instead:

```bash
./ECOSYSTEM_0.1.0_amd64.AppImage --appimage-extract-and-run
```

### Per-user install from a built repo

If you built from source (path B) and prefer `~/.local` over `sudo`:

```bash
scripts/install-desktop.sh          # → ~/.local
scripts/install-desktop.sh --system # → /usr/local (uses sudo)
```

---

## 2B. Build from source

Run everything from the repository root.

```bash
# 1. Rust (once)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# 2. System build dependencies (once)
sudo apt install build-essential libwebkit2gtk-4.1-dev libgtk-3-dev \
                 postgresql postgresql-17-pgvector libfuse2t64

# 3. Backend venv with the desktop extra (PyInstaller)
cd backend
python3 -m venv .venv
.venv/bin/pip install -e '.[desktop]'
cd ..

# 4. Frontend dependencies
cd frontend && npm install && cd ..

# 5. Build the sidecar + frontend + desktop bundles
scripts/desktop-build.sh
```

Output:

```
frontend/src-tauri/target/release/bundle/deb/ECOSYSTEM_0.1.0_amd64.deb
frontend/src-tauri/target/release/bundle/appimage/ECOSYSTEM_0.1.0_amd64.AppImage
```

Notes:

- The first build downloads the Tauri toolchain and compiles Rust, so it takes
  several minutes. Later builds are much faster.
- `--skip-sidecar` reuses an already-staged sidecar (fast frontend/shell-only
  rebuild). The sidecar is
  `frontend/src-tauri/binaries/ecosystem-backend-x86_64-unknown-linux-gnu`.
- Any backend code change requires rebuilding the sidecar (i.e. run **without**
  `--skip-sidecar`).

### Development mode (hot reload)

```bash
scripts/desktop-dev.sh
```

This launches the Tauri shell against the Vite dev server and points it at the
backend virtualenv, so Python and React changes are picked up without rebuilding
the sidecar. Rust changes trigger an automatic rebuild.

---

## 3. First run — the setup wizard

The first launch shows a seven-step wizard. Every step is idempotent and safe to
re-run.

1. **Welcome** — overview of what the app does.
2. **Hardware** — reports CPU/RAM/GPU and recommends `mock` or Ollama.
3. **Model server** — choose the inference provider:
   - `mock` — deterministic, no model server required (good for a first look);
   - `ollama` — local models. If Ollama is missing the step shows the install
     command:
     ```bash
     curl -fsSL https://ollama.com/install.sh | sh
     ollama pull qwen2.5:7b-instruct
     ```
4. **Database** — verifies the backend and the private PostgreSQL cluster are
   online.
5. **Administrator** — create the local human operator:
   - username (default `operator`),
   - display name (default `Local Operator`),
   - password (**minimum 12 characters**).
   Press **Continue** to create the account.
6. **Generation** — press **Create generation 1** (or **Continue**) to bootstrap
   the genesis generation: the system treasury plus the eight founding agents
   **A1–A8** (momentum, quantitative, value, defensive, macro, volatility,
   experimental, diversified).
7. **Finish** — **Launch ECOSYSTEM** signs in and opens the command center.

The human is the only actor that can approve capital movement. The AI cannot
create its own permissions, disable risk limits, or bypass approval.

---

## 4. Where data lives

All mutable state is under the XDG data directory:

```
~/.local/share/ecosystem/
├── pgdata/        private PostgreSQL 17 cluster (owned by your user)
├── logs/          postgres.log, backend.log
├── backups/       database dumps
└── config.json    shell preferences (provider, database URL, models)
```

- The cluster listens on loopback port **55432** by default and does **not**
  touch any system PostgreSQL cluster.
- Set `ECOSYSTEM_DATA_DIR=/some/path` to relocate everything (handy for testing
  or multiple profiles).
- The backend sidecar runs on a random free loopback port chosen at launch.

---

## 5. Configuration

The desktop app reads and writes `~/.local/share/ecosystem/config.json`
(provider, database URL, model names). Environment variables override it:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ECOSYSTEM_DATA_DIR` | `~/.local/share/ecosystem` | Data root |
| `ECOSYSTEM_DATABASE_URL` | `postgresql+asyncpg://ecosystem:ecosystem@127.0.0.1:55432/ecosystem` | Database |
| `ECOSYSTEM_LLM_PROVIDER` | `mock` | `mock` or `ollama` |
| `ECOSYSTEM_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `ECOSYSTEM_OVERSEER_MODEL` / `ECOSYSTEM_AGENT_MODEL` / `ECOSYSTEM_CODING_MODEL` | `qwen2.5:7b-instruct` / `qwen2.5:7b-instruct` / `qwen2.5-coder:7b-instruct` | Model names |
| `ECOSYSTEM_BACKEND_BIN` | *(bundled sidecar)* | Override the backend binary (dev) |
| `ECOSYSTEM_ENABLE_TESTNET_ADAPTER` | `false` | Enable testnet execution |
| `ECOSYSTEM_ENABLE_PRODUCTION_ADAPTER` | `false` | Enable production execution |

For the server/development workflow (system PostgreSQL, `scripts/dev.sh`), copy
`.env.example` to `.env` and fill it in — see `docs/OPERATIONS.md`.

---

## 6. Troubleshooting

**Wizard says the backend/database is unreachable.**
Check the logs:

```bash
tail -n 100 ~/.local/share/ecosystem/logs/backend.log
tail -n 100 ~/.local/share/ecosystem/logs/postgres.log
```

Common causes: PostgreSQL server binaries missing (`sudo apt install
postgresql`), or pgvector unavailable for the cluster's major version
(`postgresql-17-pgvector`).

**Port 55432 already in use.**
Something else is listening there. Either stop it or point the app elsewhere via
`ECOSYSTEM_DATABASE_URL` (any free loopback port works).

**AppImage won't start: "FUSE" error.**
Install `libfuse2t64`, or run with `--appimage-extract-and-run`.

**Ollama selected but inference fails.**
Confirm the server is up and the model is pulled:

```bash
ollama list
curl http://127.0.0.1:11434/api/tags
```

If you just want to try the app, switch the provider to `mock` in the wizard or
`config.json`.

**Stale test cluster after changing code.**
Remove the private cluster and let it be recreated:

```bash
rm -rf ~/.local/share/ecosystem/pgdata
```

**Reset the whole app.**
`rm -rf ~/.local/share/ecosystem` — this deletes all state (accounts, agents,
generations, audit logs). There is no other copy.

---

## 7. Uninstall

```bash
# .deb install
sudo apt remove ecosystem

# per-user install
rm -rf ~/.local/lib/ecosystem ~/.local/bin/ecosystem-desktop \
       ~/.local/share/applications/ecosystem.desktop

# application data (optional — removes all state)
rm -rf ~/.local/share/ecosystem
```

---

## 8. Verifying the install

The backend suite runs against a real PostgreSQL database and covers accounting,
risk, backtests, the full pipeline and the HTTP API:

```bash
cd backend
.venv/bin/python -m pytest tests/ -q
```

The suite needs a database named `ecosystem_test`; see `docs/OPERATIONS.md`
for the one-time setup.
