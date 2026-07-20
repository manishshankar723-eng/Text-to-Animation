import { useState } from "react";
import * as api from "../api.js";

// Combined login / register screen. On success, stores the token and calls
// onAuthed(email) so the app can switch to the dashboard.
export default function Login({ onAuthed }) {
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const fn = mode === "login" ? api.login : api.register;
      const res = await fn(email.trim(), password);
      api.setSession(res.access_token, res.email);
      onAuthed(res.email);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <form className="card auth-card" onSubmit={submit}>
        <h1 className="brand">🎭 Character Asset Studio</h1>
        <p className="muted">
          {mode === "login" ? "Sign in to continue" : "Create an account"}
        </p>

        <label>Email</label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          required
        />

        <label>Password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={mode === "register" ? "At least 8 characters" : "••••••••"}
          minLength={mode === "register" ? 8 : undefined}
          required
        />

        {error && <div className="error">{error}</div>}

        <button className="btn primary" disabled={busy} type="submit">
          {busy ? "Please wait…" : mode === "login" ? "Log in" : "Register"}
        </button>

        <p className="muted switch">
          {mode === "login" ? "No account?" : "Already have an account?"}{" "}
          <a
            href="#"
            onClick={(e) => {
              e.preventDefault();
              setError("");
              setMode(mode === "login" ? "register" : "login");
            }}
          >
            {mode === "login" ? "Register" : "Log in"}
          </a>
        </p>
      </form>
    </div>
  );
}
