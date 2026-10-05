import { useState } from "react";

import { useAuth } from "../lib/auth.jsx";
import { auth as authApi } from "../lib/api";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [bootstrapping, setBootstrapping] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
    } catch (err) {
      setError(err.message || "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }

  async function bootstrap() {
    setBusy(true);
    setError(null);
    try {
      await authApi.bootstrap({
        username,
        password,
        display_name: username,
        role: "ADMIN",
      });
      await login(username, password);
    } catch (err) {
      setError(
        (err.message || "Bootstrap failed.") +
          " Bootstrap only works when no user exists yet."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="login-card" onSubmit={submit}>
        <div className="brand" style={{ padding: "0 0 14px" }}>
          <div className="brand-mark" />
          <div>
            <div className="brand-title">ECOSYSTEM</div>
            <div className="brand-sub">CONTROL CENTER</div>
          </div>
        </div>
        <p className="dim" style={{ fontSize: 12.5, marginTop: 0 }}>
          Human approval keeps the ecosystem in your hands. Sign in to observe
          and authorise. Simulation only — no live capital is at risk.
        </p>

        {error && (
          <div className="error-banner" style={{ marginBottom: 12 }}>
            {error}
          </div>
        )}

        <div className="field">
          <label htmlFor="username">Username</label>
          <input
            id="username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        <div className="btn-row" style={{ marginTop: 8 }}>
          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <button
            className="btn ghost"
            type="button"
            disabled={busy}
            onClick={() => {
              setBootstrapping(true);
              bootstrap();
            }}
          >
            {bootstrapping ? "Creating…" : "Create first admin"}
          </button>
        </div>
        <p className="faint" style={{ fontSize: 11, marginTop: 14 }}>
          The first administrator can only be created while the system has no
          users. After that the button is disabled by the backend.
        </p>
      </form>
    </div>
  );
}
