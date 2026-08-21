import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import Avatar from "./Avatar.jsx";

// Home — the DASHBOARD: who you are, your plan, and the latest work from EVERY
// workflow. Anything you CHANGE (details, storyboard defaults, 3D keys,
// password, delete account) lives on the Profile page; Home links to it.
//
// "Recent work" used to list character jobs only, which made the other three
// workflows invisible from the front page. It now shows the newest couple of
// items per workflow with a "View all" into that workflow, so the dashboard
// answers "what am I working on?" rather than "what did Text-to-Image do?".

// How many items each workflow shows here. Two is enough to recognise where you
// left off; more turns the dashboard into four half-libraries.
const PER_WORKFLOW = 2;

function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function statusClass(status) {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "fail";
  if (status === "running") return "running";
  return "queued";
}

export default function Home({
  email,
  onOpenJob,
  onUpgrade,
  onOpenProfile,
  onNavigate
}) {
  const [profile, setProfile] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [boards, setBoards] = useState([]);
  // Image to Animatic Image's own copies — a different set from `boards`.
  const [copiedBoards, setCopiedBoards] = useState([]);
  const [animatics, setAnimatics] = useState([]);
  const [videos, setVideos] = useState([]);
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // One workflow being unreachable must not blank the whole dashboard, so
      // each list settles on its own and falls back to empty.
      // TWO board lists, because the two board workflows own different sets:
      // Script to Storyboard has the originals (untagged), Image to Animatic
      // Image has its independent copies. See list_storyboards' `workflow`.
      const [p, j, b, cb, a, v, pl] = await Promise.all([
        api.me().catch(() => null),
        api.listJobs(api.CHARACTER_JOB_KINDS).catch(() => []),
        api.listStoryboards().catch(() => []),
        api.listStoryboards("animatic-image").catch(() => []),
        api.listAnimatics().catch(() => []),
        api.listFinalVideos().catch(() => []),
        api.listPlans().catch(() => [])
      ]);
      setProfile(p);
      setJobs(Array.isArray(j) ? j : j.jobs || []);
      setBoards(Array.isArray(b) ? b : []);
      setCopiedBoards(Array.isArray(cb) ? cb : []);
      setAnimatics(Array.isArray(a) ? a : []);
      setVideos(Array.isArray(v) ? v : []);
      setPlans(Array.isArray(pl) ? pl : []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const memberSince = profile?.created_at
    ? new Date(profile.created_at).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric"
      })
    : "—";
  const displayName = profile?.display_name || profile?.full_name || email;
  const initial = (displayName || "?").trim().charAt(0).toUpperCase();

  // One shape for every workflow, so the groups render from one component
  // instead of five near-identical blocks. ORDER MATCHES THE SIDEBAR — when a
  // workflow is added, renamed or moved in Sidebar.jsx, it has to be added,
  // renamed or moved here too, or Recent work quietly stops showing it (which
  // is exactly how Image to Video went missing).
  const groups = [
    {
      id: "plan-and-script",
      icon: "🗓️",
      label: "Plan & Script",
      items: plans.map((p) => ({
        key: p.job_id,
        title: p.title || "Untitled plan",
        meta: p.item_count > 0 ? `${p.item_count} uploads` : "no plan yet",
        date: p.updated_at || p.created_at
      }))
    },
    {
      id: "text-to-image",
      icon: "🖼️",
      label: "Text to Turnaround Image",
      items: jobs.map((j) => ({
        key: j.job_id,
        title: j.character_name || "Untitled",
        status: j.status,
        date: j.created_at,
        // Only this workflow can open a job detail and serve an asset ZIP.
        onOpen: () => onOpenJob?.(j.job_id),
        zip:
          j.status === "succeeded"
            ? () =>
                api
                  .downloadZip(
                    j.job_id,
                    `${j.character_name}_assets.zip`,
                    j.result?.zip
                  )
                  .catch((e) => setError(e.message))
            : null
      }))
    },
    {
      id: "script-to-storyboard",
      icon: "📝",
      label: "Script to Storyboard",
      items: boards.map((b) => ({
        key: b.job_id,
        title: b.title || "Storyboard",
        status: b.status,
        meta: b.panel_count ? `${b.panel_count} panels` : "",
        date: b.created_at
      }))
    },
    {
      // Its OWN boards — independent copies made by its "From a Storyboard"
      // tile, not the originals. Drawing in a copy must never change the
      // storyboard it came from, so the two sets are kept apart everywhere.
      id: "create-animatic-image",
      icon: "🖼️",
      label: "Image to Animatic Image",
      items: copiedBoards.map((b) => ({
        key: b.job_id,
        title: b.title || "Storyboard",
        status: b.status,
        meta: b.panel_count ? `${b.panel_count} panels` : "",
        date: b.updated_at || b.created_at
      }))
    },
    {
      id: "animatics-to-video",
      icon: "🎞️",
      label: "Image to AI Video",
      items: videos.map((v) => ({
        key: v.job_id,
        title: v.title || "Final video",
        status: v.status,
        // How much is DONE, not just how much is in it — this is the only
        // workflow where the remainder costs money to finish.
        meta: v.shot_count
          ? `${v.rendered_count}/${v.shot_count} rendered`
          : "",
        date: v.updated_at || v.created_at
      }))
    },
    {
      id: "storyboard-to-animatics",
      icon: "🎬",
      label: "Video Editor",
      items: animatics.map((a) => ({
        key: a.job_id,
        title: a.title || "Animatic",
        status: a.status,
        // Same shape of hint as the others: how much is in it.
        meta: a.frame_count ? `${a.frame_count} frames` : "",
        date: a.updated_at || a.created_at
      }))
    }
  ];

  const totalItems = groups.reduce((n, g) => n + g.items.length, 0);

  return (
    <div className="home">
      <header className="home-head">
        <h1>Welcome back 👋</h1>
        <p className="muted">
          Your profile, your plan, and where you left off.
        </p>
      </header>

      {error && <div className="error">{error}</div>}

      {/* Top row: two equal-height cards. */}
      <div className="home-grid">
        <section className="card home-card profile-card">
          <div className="profile-top">
            <Avatar size={56} initial={initial === "?" ? "" : initial} />
            <div className="profile-who">
              <h2 className="profile-email">{displayName}</h2>
              <p className="muted tiny">{email}</p>
              <p className="muted tiny">Member since {memberSince}</p>
            </div>
          </div>
          <div className="home-card-foot">
            {onOpenProfile && (
              <button className="btn small" onClick={onOpenProfile}>
                Edit profile
              </button>
            )}
          </div>
        </section>

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
              <span className="credit-num">{totalItems}</span>
              <span className="muted tiny">Projects</span>
            </div>
          </div>
          <div className="home-card-foot">
            <button className="btn small upgrade-inline" onClick={onUpgrade}>
              ⚡ Upgrade plan
            </button>
            <p className="muted tiny plan-note">
              Usage-based billing &amp; credits are coming soon.
            </p>
          </div>
        </section>
      </div>

      {/* Recent work, one group per workflow. */}
      <section className="card home-card recent-card">
        <div className="recent-head">
          <h2>Recent work</h2>
          <button className="btn ghost small" onClick={load} disabled={loading}>
            ↻ {loading ? "Loading…" : "Refresh"}
          </button>
        </div>

        <div className="wf-grid">
          {groups.map((g) => (
            <div className="wf-group" key={g.id}>
              <div className="wf-group-head">
                <span className="wf-group-title">
                  <span className="wf-group-ico">{g.icon}</span>
                  {g.label}
                </span>
                <button
                  className="btn ghost small"
                  onClick={() => onNavigate?.(g.id)}
                  title={`Open ${g.label}`}
                >
                  View all{g.items.length ? ` (${g.items.length})` : ""} →
                </button>
              </div>

              {g.items.length === 0 ? (
                <button className="wf-empty" onClick={() => onNavigate?.(g.id)}>
                  {loading ? "Loading…" : `Nothing yet — start your first`}
                </button>
              ) : (
                <ul className="wf-list">
                  {g.items.slice(0, PER_WORKFLOW).map((it) => (
                    <li key={it.key} className="wf-item">
                      <button
                        className="wf-open"
                        onClick={() =>
                          it.onOpen ? it.onOpen() : onNavigate?.(g.id)
                        }
                        title={it.title}
                      >
                        <span className="wf-name">{it.title}</span>
                        <span className="wf-sub">
                          {it.meta && (
                            <span className="muted tiny">{it.meta}</span>
                          )}
                          {it.date && (
                            <span className="muted tiny">
                              {formatDate(it.date)}
                            </span>
                          )}
                        </span>
                      </button>
                      <div className="wf-actions">
                        {it.status && (
                          <span className={`badge ${statusClass(it.status)}`}>
                            {it.status}
                          </span>
                        )}
                        {it.zip && (
                          <button className="btn small" onClick={it.zip}>
                            ⬇ ZIP
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Account settings live on the Profile page — Home shows status, Profile
          is where you change things. */}
      <section className="card home-card account-card">
        <h2>Account</h2>
        <p className="muted tiny">
          Your details, storyboard defaults, 3D API keys and password all live
          on your profile.
        </p>
        <div className="home-card-foot">
          {onOpenProfile && (
            <button className="btn small" onClick={onOpenProfile}>
              Open profile
            </button>
          )}
          <p className="muted tiny">
            To log out, click your name at the bottom of the sidebar.
          </p>
        </div>
      </section>
    </div>
  );
}
