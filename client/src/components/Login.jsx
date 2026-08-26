import { useState } from "react";
import * as api from "../api.js";
import * as cache from "../session_cache.js";
import Logo from "./Logo.jsx";

// Combined login / register screen. On success, stores the token and calls
// onAuthed(email) so the app can switch to the dashboard. onBack returns to the
// public landing page.
export default function Login({ onAuthed, onBack }) {
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    // Hide the password back to dots the moment login is pressed, so it isn't
    // left visible on screen while the request runs.
    setShowPassword(false);
    setError("");
    setInfo("");
    setBusy(true);
    try {
      const fn = mode === "login" ? api.login : api.register;
      const res = await fn(email.trim(), password);
      api.setSession(res.access_token, res.email);
      // ⚠ THE DASHBOARD'S DATA IS ASKED FOR *HERE*, one line after the token
      // exists and one line BEFORE React is told anything has changed. It used
      // to be asked for by Home, which meant nothing was requested until the
      // dashboard had already been drawn empty - so a returning customer's
      // first sight of the app was six "Loading..." labels on their own work.
      // Started now, the answers are usually back before the page finishes
      // mounting and there is nothing to load at all.
      //
      // `res.counts` is the server's `{kind: n}` hint, which is what lets a
      // BRAND-NEW account skip these requests entirely and paint its real
      // empty dashboard instantly. See session_cache.prefetch.
      //
      // Deliberately NOT awaited: sign-in must not wait on the dashboard.
      cache.prefetch({ email: res.email, counts: res.counts });
      onAuthed(res.email);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  // Google OAuth is not wired to the backend yet (backend is email/password +
  // JWT). Show a clear message instead of faking a sign-in.
  function handleGoogle() {
    setError("");
    setInfo("Google sign-in is coming soon — please continue with email below.");
  }

  return (
    <div className="auth-wrap">
      <form className="card auth-card" onSubmit={submit}>
        {onBack && (
          <button
            type="button"
            className="btn ghost small auth-back"
            onClick={onBack}
          >
            ← Back to home
          </button>
        )}

        <h1 className="brand">
          <Logo /> Aniwala AI Studio
        </h1>
        <p className="muted">
          {mode === "login" ? "Sign in to continue" : "Create an account"}
        </p>

        <button type="button" className="btn google-btn" onClick={handleGoogle}>
          <GoogleIcon />
          Continue with Google
        </button>

        {info && <div className="info-msg">{info}</div>}

        <div className="or-divider">
          <span>or use email</span>
        </div>

        <label>Email</label>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@example.com"
          required
        />

        <label>Password</label>
        <div className="password-field">
          <input
            type={showPassword ? "text" : "password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={mode === "register" ? "At least 8 characters" : "••••••••"}
            minLength={mode === "register" ? 8 : undefined}
            required
          />
          <button
            type="button"
            className="password-toggle"
            onClick={() => setShowPassword((v) => !v)}
            aria-label={showPassword ? "Hide password" : "Show password"}
            title={showPassword ? "Hide password" : "Show password"}
          >
            {showPassword ? <EyeOffIcon /> : <EyeIcon />}
          </button>
        </div>

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

// Eye icons for the password show/hide toggle (inline SVG, no network request).
function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}
function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
      <line x1="1" y1="1" x2="23" y2="23" />
    </svg>
  );
}

// Google "G" logo as inline SVG (no external asset / network request).
function GoogleIcon() {
  return (
    <svg className="google-icon" viewBox="0 0 48 48" width="18" height="18" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}
