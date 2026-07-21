// Left navigation rail. Home + a Workflows section (the live Text-to-Image
// pipeline plus placeholders for future workflows) + an Upgrade CTA.

// Workflow nav items. `status: "live"` is the working pipeline; "soon" items
// are placeholders for the roadmap the user is building toward.
export const WORKFLOWS = [
  { id: "text-to-image", label: "Text to Image", icon: "🖼️", status: "live" },
  { id: "script-to-storyboard", label: "Script to Storyboard", icon: "📝", status: "soon" },
  { id: "storyboard-to-animatics", label: "Storyboard to Animatics", icon: "🎬", status: "soon" },
  { id: "animatics-to-video", label: "Animatics to Final Video", icon: "🎞️", status: "soon" },
  { id: "final-video-export", label: "Final Video Export", icon: "🎥", status: "soon" },
];

export default function Sidebar({ active, onNavigate, email, onUpgrade }) {
  const initial = (email || "?").trim().charAt(0).toUpperCase();
  const workspace = (email || "My workspace").split("@")[0];

  return (
    <aside className="sidebar">
      {/* Brand */}
      <div className="sb-brand">
        <span className="sb-logo">🎭</span>
        <span className="sb-brand-name">Character Studio</span>
      </div>

      {/* Home */}
      <nav className="sb-nav">
        <button
          className={`sb-item ${active === "home" ? "active" : ""}`}
          onClick={() => onNavigate("home")}
        >
          <span className="sb-ico">🏠</span> Home
        </button>

        {/* Workflows */}
        <div className="sb-section-label">Workflows</div>
        {WORKFLOWS.map((w) => (
          <button
            key={w.id}
            className={`sb-item ${active === w.id ? "active" : ""}`}
            onClick={() => onNavigate(w.id)}
          >
            <span className="sb-ico">{w.icon}</span>
            <span className="sb-item-label">{w.label}</span>
            {w.status === "soon" && <span className="sb-badge-soon">Soon</span>}
            {w.status === "live" && <span className="sb-dot-live" title="Live" />}
          </button>
        ))}
      </nav>

      {/* Profile chip + Upgrade CTA */}
      <div className="sb-footer">
        <button
          className={`sb-workspace ${active === "home" ? "active" : ""}`}
          onClick={() => onNavigate("home")}
          title="Home & account"
        >
          <span className="sb-avatar">{initial}</span>
          <span className="sb-ws-text">
            <span className="sb-ws-name">{workspace}</span>
            <span className="sb-ws-sub">View profile</span>
          </span>
        </button>

        <button className="sb-upgrade" onClick={onUpgrade}>
          <span className="sb-upgrade-ico">⚡</span> Upgrade
        </button>
      </div>
    </aside>
  );
}
