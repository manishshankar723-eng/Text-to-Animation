import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import Avatar from "./Avatar.jsx";

// Home — the DASHBOARD: who you are at a glance, plan & credits, recent work.
// Anything you CHANGE (details, storyboard defaults, 3D keys, password, delete
// account) lives on the Profile page; Home links to it.
export default function Home({ email, onOpenJob, onUpgrade, onOpenProfile }) {
  const [profile, setProfile] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [p, j] = await Promise.all([
        api.me(),
        // Character work only: "Recent work" opens the Text-to-Image job detail
        // and offers its asset ZIP, neither of which a storyboard job can serve.
        api.listJobs(api.CHARACTER_JOB_KINDS),
      ]);
      setProfile(p);
      setJobs(Array.isArray(j) ? j : j.jobs || []);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const memberSince = profile?.created_at
    ? new Date(profile.created_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
      })
    : "—";
  // Prefer what the user asked to be called; fall back to their email.
  const displayName = profile?.display_name || profile?.full_name || email;
  const initial = (displayName || "?").trim().charAt(0).toUpperCase();
  const recent = jobs.slice(0, 6);

  return (
    <div className="home">
      <header className="home-head">
        <h1>Welcome back 👋</h1>
        <p className="muted">Manage your profile, plan and recent generations.</p>
      </header>

      {error && <div className="error">{error}</div>}

      <div className="home-grid">
        {/* Profile summary — the details themselves live on the Profile page. */}
        <section className="card home-card profile-card">
          <div className="profile-top">
            <Avatar size={56} initial={initial === "?" ? "" : initial} />
            <div>
              <h2 className="profile-email">{displayName}</h2>
              <p className="muted tiny">{email}</p>
              <p className="muted tiny">Member since {memberSince}</p>
            </div>
          </div>
          {onOpenProfile && (
            <button className="btn small" onClick={onOpenProfile}>
              Edit profile
            </button>
          )}
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

        {/* Account settings live on the Profile page now — 3D API keys, password
            and Delete account were all here, which made Home half dashboard and
            half settings screen. Home shows status; Profile is where you change
            things. */}
        <section className="card home-card account-card">
          <h2>Account</h2>
          <p className="muted tiny">
            Your details, storyboard defaults, 3D API keys and password all live
            on your profile.
          </p>
          <div className="home-account-actions">
            {onOpenProfile && (
              <button className="btn" onClick={onOpenProfile}>
                Open profile
              </button>
            )}
          </div>
          <p className="muted tiny">
            To log out, click your name at the bottom of the sidebar.
          </p>
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
