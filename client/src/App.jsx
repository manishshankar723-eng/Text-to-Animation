import { useState } from "react";
import * as api from "./api.js";
import Landing from "./components/Landing.jsx";
import Login from "./components/Login.jsx";
import Sidebar from "./components/Sidebar.jsx";
import Home from "./components/Home.jsx";
import WorkflowSoon from "./components/WorkflowSoon.jsx";
import GenerateForm from "./components/GenerateForm.jsx";
import JobList from "./components/JobList.jsx";
import JobDetail from "./components/JobDetail.jsx";

// Descriptions for the roadmap (not-yet-built) workflows.
const SOON = {
  "script-to-storyboard": {
    icon: "📝",
    title: "Script to Storyboard",
    description:
      "Paste a script and generate a shot-by-shot storyboard with framed panels, camera notes and scene beats.",
    steps: ["Parse the script into scenes", "Generate a panel per shot", "Review & re-roll panels"],
  },
  "storyboard-to-animatics": {
    icon: "🎬",
    title: "Storyboard to Animatics",
    description:
      "Turn static storyboard panels into timed animatics with motion, transitions and rough timing.",
    steps: ["Sequence the panels", "Add timing & camera moves", "Preview the animatic"],
  },
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

export default function App() {
  const [email, setEmail] = useState(api.getEmail());
  const [authed, setAuthed] = useState(Boolean(api.getToken()));
  const [authView, setAuthView] = useState("landing");
  // Land on the working page by default (both fresh login and returning session).
  const [nav, setNav] = useState("text-to-image"); // "home" | workflow id
  const [selectedId, setSelectedId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);

  function refreshJobs() {
    setRefreshKey((k) => k + 1);
  }

  function onAuthed(mail) {
    setEmail(mail);
    setAuthed(true);
    setNav("text-to-image");
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
        onLogout={logout}
        onOpenJob={openJobInWorkflow}
        onUpgrade={() => setUpgradeOpen(true)}
      />
    );
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
  } else if (SOON[nav]) {
    content = <WorkflowSoon {...SOON[nav]} />;
  }

  return (
    <div className="shell">
      <Sidebar
        active={nav}
        onNavigate={setNav}
        email={email}
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
            <span className="account-modal-avatar">
              {(email || "?").trim().charAt(0).toUpperCase()}
            </span>
            <h2>{email}</h2>
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

      {upgradeOpen && (
        <div className="modal-overlay" onClick={() => setUpgradeOpen(false)}>
          <div className="card upgrade-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setUpgradeOpen(false)}>
              ✕
            </button>
            <span className="soon-icon">⚡</span>
            <h2>Plans &amp; upgrades coming soon</h2>
            <p className="muted">
              Paid plans with more credits, faster generation and the full
              script-to-video pipeline are on the way. You're on the Free plan for now.
            </p>
            <button className="btn primary" onClick={() => setUpgradeOpen(false)}>
              Got it
            </button>
          </div>
        </div>
      )}
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
