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
import WorkflowSoon from "./components/WorkflowSoon.jsx";
import ScriptToStoryboard from "./components/ScriptToStoryboard.jsx";
import StoryboardToAnimatics from "./components/StoryboardToAnimatics.jsx";
import PublicStoryboard from "./components/PublicStoryboard.jsx";
import PricingModal from "./components/PricingModal.jsx";
import GenerateForm from "./components/GenerateForm.jsx";
import JobList from "./components/JobList.jsx";
import JobDetail from "./components/JobDetail.jsx";

// Descriptions for the roadmap (not-yet-built) workflows.
const SOON = {
  "animatics-to-video": {
    icon: "🎞️",
    title: "Animatics to Final Video",
    description:
      "Render animatics into a polished final video with your characters, backgrounds and effects.",
    steps: ["Apply final art & characters", "Render shots", "Assemble the sequence"],
  },
  "final-video-export": {
    icon: "🎥",
    title: "Final Video Export",
    description:
      "Add voiceover, music and captions, then export your finished video in multiple formats.",
    steps: ["Add audio & captions", "Choose export presets", "Download the final cut"],
  },
};

// A shared storyboard link is `?s=<token>`. Read it once at boot: the app has
// no router, and this is the only route that must render logged OUT.
function readShareToken() {
  const t = new URLSearchParams(window.location.search).get("s");
  return t && /^[a-f0-9]{32}$/i.test(t) ? t : null;
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
  // The name the user chose on their profile, so the sidebar shows it instead
  // of the local part of their email. Refreshed whenever they leave the profile
  // page, which is the only place it can change.
  const [displayName, setDisplayName] = useState("");

  useEffect(() => applyTheme(theme), [theme]);

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
          title="Text to Image"
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
  } else if (SOON[nav]) {
    content = <WorkflowSoon {...SOON[nav]} />;
  }

  return (
    <div className="shell">
      <Sidebar
        active={nav}
        onNavigate={setNav}
        email={email}
        displayName={displayName}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        onUpgrade={() => setUpgradeOpen(true)}
        onProfileClick={() => setAccountOpen(true)}
      />
      <main className="shell-main">{content}</main>

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
