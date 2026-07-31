import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";

// Home / account dashboard: profile, plan & credits, recent work (with
// downloads), and account actions (log out / delete account).
export default function Home({ email, onLogout, onOpenJob, onUpgrade }) {
  const [profile, setProfile] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [apiKeys, setApiKeys] = useState({}); // { meshy:true, tripo:true }
  const [error, setError] = useState("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [p, j, k] = await Promise.all([
        api.me(),
        // Character work only: "Recent work" opens the Text-to-Image job detail
        // and offers its asset ZIP, neither of which a storyboard job can serve.
        api.listJobs(api.CHARACTER_JOB_KINDS),
        api.getApiKeys().catch(() => ({})),
      ]);
      setProfile(p);
      setJobs(Array.isArray(j) ? j : j.jobs || []);
      setApiKeys(k || {});
    } catch (e) {
      setError(e.message);
    }
  }, []);

  async function removeKey(provider) {
    try {
      await api.deleteApiKey(provider);
      setApiKeys((prev) => {
        const next = { ...prev };
        delete next[provider];
        return next;
      });
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    load();
  }, [load]);

  async function handleDelete() {
    setDeleting(true);
    setError("");
    try {
      await api.deleteAccount();
      api.clearSession();
      onLogout();
    } catch (e) {
      setError(e.message);
      setDeleting(false);
    }
  }

  const memberSince = profile?.created_at
    ? new Date(profile.created_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    : "—";
  const initial = (email || "?").trim().charAt(0).toUpperCase();
  const recent = jobs.slice(0, 6);

  return (
    <div className="home">
      <header className="home-head">
        <h1>Welcome back 👋</h1>
        <p className="muted">Manage your profile, plan and recent generations.</p>
      </header>

      {error && <div className="error">{error}</div>}

      <div className="home-grid">
        {/* Profile */}
        <section className="card home-card profile-card">
          <div className="profile-top">
            <span className="profile-avatar">{initial}</span>
            <div>
              <h2 className="profile-email">{email}</h2>
              <p className="muted tiny">Member since {memberSince}</p>
            </div>
          </div>
        </section>

        {/* Plan & credits */}
        <section className="card home-card plan-card">
          <div className="plan-head">
            <span className="plan-badge">Free plan</span>
          </div>
          <div className="credit-row">
            <div className="credit-stat">
              <span className="credit-num">∞</span>
              <span className="muted tiny">Credits (beta)</span>
            </div>
            <div className="credit-stat">
              <span className="credit-num">{jobs.length}</span>
              <span className="muted tiny">Generations</span>
            </div>
          </div>
          <button className="btn upgrade-inline" onClick={onUpgrade}>
            ⚡ Upgrade plan
          </button>
          <p className="muted tiny plan-note">
            Usage-based billing &amp; credits are coming soon.
          </p>
        </section>

        {/* Recent work */}
        <section className="card home-card recent-card">
          <div className="recent-head">
            <h2>Recent work</h2>
            <button className="btn ghost small" onClick={load}>
              ↻ Refresh
            </button>
          </div>
          {recent.length === 0 ? (
            <p className="muted">
              No generations yet. Open <strong>Text to Image</strong> to create your
              first character asset.
            </p>
          ) : (
            <ul className="recent-list">
              {recent.map((j) => (
                <li key={j.job_id} className="recent-item">
                  <button className="recent-open" onClick={() => onOpenJob?.(j.job_id)}>
                    <span className="recent-name">
                      {j.character_name || "Untitled"}
                    </span>
                    <span className={`badge ${statusClass(j.status)}`}>{j.status}</span>
                  </button>
                  {j.status === "succeeded" && (
                    <button
                      className="btn small"
                      onClick={() =>
                        api
                          .downloadZip(j.job_id, `${j.character_name}_assets.zip`, j.result?.zip)
                          .catch((e) => setError(e.message))
                      }
                    >
                      ⬇ Download
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Account actions */}
        <section className="card home-card account-card">
          <h2>Account</h2>
          <p className="muted tiny">
            To log out, click your name at the bottom of the sidebar.
          </p>

          {/* Saved 3D API keys */}
          <div className="api-keys-block">
            <h3 className="api-keys-title">3D API keys</h3>
            {Object.keys(apiKeys).filter((p) => apiKeys[p]).length === 0 ? (
              <p className="muted tiny">
                No keys saved. You'll be asked for one when you generate a 3D model.
              </p>
            ) : (
              <ul className="api-keys-list">
                {Object.keys(apiKeys)
                  .filter((p) => apiKeys[p])
                  .map((p) => (
                    <li key={p} className="api-key-item">
                      <span>
                        <strong style={{ textTransform: "capitalize" }}>{p}</strong>{" "}
                        <span className="badge ok">saved</span>
                      </span>
                      <button className="btn small danger-btn" onClick={() => removeKey(p)}>
                        Remove
                      </button>
                    </li>
                  ))}
              </ul>
            )}
          </div>

          <div className="danger-zone">
            <h3 className="danger-title">Danger zone</h3>
            {!confirmingDelete ? (
              <button
                className="btn danger-btn"
                onClick={() => setConfirmingDelete(true)}
              >
                <Icon name="trash" /> Delete account
              </button>
            ) : (
              <div className="danger-confirm">
                <p className="tiny">
                  This permanently deletes your account. This cannot be undone.
                </p>
                <div className="danger-actions">
                  <button
                    className="btn danger-btn"
                    disabled={deleting}
                    onClick={handleDelete}
                  >
                    {deleting ? "Deleting…" : "Yes, delete permanently"}
                  </button>
                  <button
                    className="btn ghost small"
                    disabled={deleting}
                    onClick={() => setConfirmingDelete(false)}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function statusClass(status) {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "fail";
  if (status === "running") return "running";
  return "queued";
}
