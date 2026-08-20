import { useCallback, useEffect, useState } from "react";
import * as api from "./api.js";
import { applyTheme, getTheme } from "./theme.js";
import Landing from "./components/Landing.jsx";
import Login from "./components/Login.jsx";
import Sidebar from "./components/Sidebar.jsx";
import Home from "./components/Home.jsx";
import Profile from "./components/Profile.jsx";
import Avatar from "./components/Avatar.jsx";
import PlanAndScript from "./components/PlanAndScript.jsx";
import ScriptToStoryboard from "./components/ScriptToStoryboard.jsx";
import StoryboardToAnimatics from "./components/StoryboardToAnimatics.jsx";
import AnimaticsToVideo from "./components/AnimaticsToVideo.jsx";
import CreateAnimaticImage from "./components/CreateAnimaticImage.jsx";
import PublicStoryboard from "./components/PublicStoryboard.jsx";
import PricingModal from "./components/PricingModal.jsx";
import GenerateForm from "./components/GenerateForm.jsx";
import JobList from "./components/JobList.jsx";
import JobDetail from "./components/JobDetail.jsx";

// Every workflow in the sidebar is BUILT, so there is no roadmap placeholder to
// render any more. `WorkflowSoon.jsx` is kept for the next one that needs it:
// re-add a `SOON` map here plus the `else if (SOON[nav])` branch below, or a
// `status: "soon"` item will navigate to a blank page.

// A shared storyboard link is `?s=<token>`. Read it once at boot: the app has
// no router, and this is the only route that must render logged OUT.
function readShareToken() {
  const t = new URLSearchParams(window.location.search).get("s");
  return t && /^[a-f0-9]{32}$/i.test(t) ? t : null;
}

// Whether the nav rail is collapsed to icons. Remembered per browser, like the
// theme is: someone who works in the narrow rail wants it narrow next time too.
// Kept HERE and not in Sidebar because `.shell` is a two-column grid — the rail
// and the page width have to change in the same render or the layout tears.
const NAV_COLLAPSED_KEY = "cas_nav_collapsed";

function readNavCollapsed() {
  try {
    return localStorage.getItem(NAV_COLLAPSED_KEY) === "1";
  } catch {
    // Private mode / storage disabled — start expanded, don't crash the boot.
    return false;
  }
}

export default function App() {
  const [shareToken, setShareToken] = useState(readShareToken);
  const [email, setEmail] = useState(api.getEmail());
  const [authed, setAuthed] = useState(Boolean(api.getToken()));
  const [authView, setAuthView] = useState("landing");
  // Land on HOME by default — both a fresh login and a returning session. Home
  // is the dashboard (profile, plan, recent work), so opening the app shows
  // where things stand rather than dropping you mid-workflow.
  const [nav, setNav] = useState("home"); // "home" | "profile" | workflow id
  // Bumped when the user clicks the workflow they are ALREADY in. Every
  // workflow keeps its own screen in local state (library → session → board),
  // so re-selecting it in the sidebar did nothing — you stayed wherever you
  // were. This is fed into the content's `key`, which remounts it and drops it
  // back on its first page. Nothing is lost by that: drafts, plans, boards and
  // jobs all live server-side and are re-read on mount.
  const [navResetKey, setNavResetKey] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  // Set by the board's "Make animatic" button: the animatic already exists, so
  // the animatics workflow opens straight into its editor instead of the library.
  const [pendingAnimaticId, setPendingAnimaticId] = useState(null);
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  // main.jsx already applied the stored theme before the first paint; this only
  // has to re-stamp <html> when the user flips the switch.
  const [theme, setTheme] = useState(getTheme);
  const [navCollapsed, setNavCollapsed] = useState(readNavCollapsed);
  // The name the user chose on their profile, so the sidebar shows it instead
  // of the local part of their email. Refreshed whenever they leave the profile
  // page, which is the only place it can change.
  const [displayName, setDisplayName] = useState("");

  useEffect(() => applyTheme(theme), [theme]);

  useEffect(() => {
    try {
      localStorage.setItem(NAV_COLLAPSED_KEY, navCollapsed ? "1" : "0");
    } catch {
      // Nothing to do — the rail still works, it just won't be remembered.
    }
  }, [navCollapsed]);

  // Ctrl/Cmd+B, the shortcut every editor with a side panel uses. Skipped while
  // a field has focus so it can't fight a text control's own bold binding.
  useEffect(() => {
    function onKeyDown(e) {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
      if (e.key !== "b" && e.key !== "B") return;
      const el = document.activeElement;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) {
        return;
      }
      e.preventDefault();
      setNavCollapsed((c) => !c);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (!authed) {
      setDisplayName("");
      return;
    }
    let cancelled = false;
    api
      .me()
      .then((p) => {
        if (!cancelled) setDisplayName(p?.display_name || p?.full_name || "");
      })
      .catch(() => {
        // Cosmetic only — the sidebar falls back to the email.
      });
    return () => {
      cancelled = true;
    };
  }, [authed, nav]);

  // Stable identity: children list this in effect deps, so a fresh function on
  // every render would re-fire those effects (and, for the one that calls back
  // here, loop forever).
  const refreshJobs = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  function onAuthed(mail) {
    setEmail(mail);
    setAuthed(true);
    setNav("home");
  }

  function logout() {
    api.clearSession();
    setAuthed(false);
    setEmail(null);
    setSelectedId(null);
    setNav("home");
    setAuthView("landing");
    setAccountOpen(false);
  }

  // Sidebar clicks. Selecting a DIFFERENT entry navigates; selecting the one
  // you're already in sends you back to that workflow's first page, so the
  // sidebar name doubles as "start over here" without hunting for a Back
  // button deep in the flow.
  function navigate(id) {
    if (id === nav) {
      // Anything the SHELL holds for a workflow has to be cleared too — a
      // remount alone wouldn't drop these, and Text-to-Image would reopen on
      // the job you were just looking at instead of its first page.
      setSelectedId(null);
      setPendingAnimaticId(null);
      setNavResetKey((k) => k + 1);
      return;
    }
    setNav(id);
  }

  function onJobCreated(jobId) {
    setSelectedId(jobId);
    refreshJobs();
  }

  function openJobInWorkflow(jobId) {
    setNav("text-to-image");
    setSelectedId(jobId);
  }

  // ---- Shared storyboard (public, works with or without a session) ----
  if (shareToken) {
    return (
      <PublicStoryboard
        token={shareToken}
        onExit={() => {
          // Drop ?s= from the URL so a refresh lands on the normal app.
          window.history.replaceState({}, "", window.location.pathname);
          setShareToken(null);
        }}
      />
    );
  }

  // ---- Logged-out screens ----
  if (!authed) {
    if (authView === "login") {
      return <Login onAuthed={onAuthed} onBack={() => setAuthView("landing")} />;
    }
    return <Landing onGetStarted={() => setAuthView("login")} />;
  }

  // ---- Main content by nav ----
  let content;
  if (nav === "home") {
    content = (
      <Home
        email={email}
        onOpenJob={openJobInWorkflow}
        onUpgrade={() => setUpgradeOpen(true)}
        onOpenProfile={() => setNav("profile")}
        // "View all" on a workflow group jumps into that workflow.
        onNavigate={setNav}
      />
    );
  } else if (nav === "profile") {
    content = <Profile email={email} onLogout={logout} />;
  } else if (nav === "plan-and-script") {
    content = <PlanAndScript />;
  } else if (nav === "text-to-image") {
    content = (
      <div className="workflow-head-wrap">
        <WorkflowHeader
          icon="🖼️"
          title="Text to Turnaround Image"
          subtitle="Generate turnaround character asset sheets from a prompt or photo."
        />
        <div className="layout">
          <section className="col-left">
            <GenerateForm onJobCreated={onJobCreated} />
            <JobList
              selectedId={selectedId}
              onSelect={setSelectedId}
              refreshKey={refreshKey}
            />
          </section>
          <section className="col-right">
            {selectedId ? (
              <JobDetail jobId={selectedId} onChanged={refreshJobs} />
            ) : (
              <div className="card placeholder">
                <p className="muted">Select a job, or start a new generation.</p>
              </div>
            )}
          </section>
        </div>
      </div>
    );
  } else if (nav === "script-to-storyboard") {
    content = (
      <ScriptToStoryboard
        onOpenAnimatic={(id) => {
          setPendingAnimaticId(id);
          setNav("storyboard-to-animatics");
        }}
      />
    );
  } else if (nav === "storyboard-to-animatics") {
    content = (
      <StoryboardToAnimatics
        openId={pendingAnimaticId}
        onOpened={() => setPendingAnimaticId(null)}
      />
    );
  } else if (nav === "animatics-to-video") {
    content = (
      <AnimaticsToVideo />
    );
  } else if (nav === "create-animatic-image") {
    content = (
      <CreateAnimaticImage
        /* This workflow mounts the real board page, so its "Make animatic"
           button needs the same hand-off Script to Storyboard gives it —
           without the callback that button hides itself. */
        onOpenAnimatic={(id) => {
          setPendingAnimaticId(id);
          setNav("storyboard-to-animatics");
        }}
      />
    );
  }

  return (
    <div className={`shell ${navCollapsed ? "nav-collapsed" : ""}`}>
      <Sidebar
        active={nav}
        onNavigate={navigate}
        email={email}
        displayName={displayName}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        onUpgrade={() => setUpgradeOpen(true)}
        onProfileClick={() => setAccountOpen(true)}
        collapsed={navCollapsed}
        onToggleCollapse={() => setNavCollapsed((c) => !c)}
      />
      {/* Keyed by nav + reset counter: clicking the current workflow again
          changes the key, React remounts it, and it opens on its first page. */}
      <main className="shell-main" key={`${nav}-${navResetKey}`}>
        {content}
      </main>

      {accountOpen && (
        <div className="modal-overlay" onClick={() => setAccountOpen(false)}>
          <div className="card account-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setAccountOpen(false)}>
              ✕
            </button>
            <Avatar
              size={64}
              initial={(displayName || email || "").trim().charAt(0).toUpperCase()}
            />
            <h2>{displayName || email}</h2>
            {displayName && <p className="muted tiny">{email}</p>}
            {/* The sidebar avatar is where people look for account settings, so
                offer the profile here rather than only from Home. */}
            <button
              className="btn"
              onClick={() => {
                setAccountOpen(false);
                setNav("profile");
              }}
            >
              👤 Your profile
            </button>
            <p className="muted">Are you sure you want to log out of your account?</p>
            <button className="btn primary" onClick={logout}>
              ⎋ Log out
            </button>
            <button
              className="btn ghost small account-modal-cancel"
              onClick={() => setAccountOpen(false)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {upgradeOpen && <PricingModal onClose={() => setUpgradeOpen(false)} />}
    </div>
  );
}

function WorkflowHeader({ icon, title, subtitle }) {
  return (
    <div className="workflow-header">
      <span className="wf-icon">{icon}</span>
      <div>
        <h1 className="wf-title">{title}</h1>
        <p className="muted">{subtitle}</p>
      </div>
    </div>
  );
}
