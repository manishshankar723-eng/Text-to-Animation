// Left navigation rail. Home + a Workflows section (the live Text-to-Image
// pipeline plus placeholders for future workflows) + an Upgrade CTA.
import Avatar from "./Avatar.jsx";

// Workflow nav items. `status: "live"` is the working pipeline; "soon" items
// are placeholders for the roadmap the user is building toward.
export const WORKFLOWS = [
  // FIRST in the pipeline: decide what to make before making any of it.
  { id: "plan-and-script", label: "Plan & Script", icon: "🗓️", status: "live" },
  { id: "text-to-image", label: "Text to Image", icon: "🖼️", status: "live" },
  { id: "script-to-storyboard", label: "Script to Storyboard", icon: "📝", status: "live" },
  { id: "storyboard-to-animatics", label: "Storyboard to Animatics", icon: "🎬", status: "live" },
  { id: "animatics-to-video", label: "Animatics to Final Video", icon: "🎞️", status: "soon" },
  { id: "final-video-export", label: "Final Video Export", icon: "🎥", status: "soon" },
];

export default function Sidebar({
  active,
  onNavigate,
  email,
  displayName,
  theme,
  onToggleTheme,
  onUpgrade,
  onProfileClick,
}) {
  const who = displayName || email || "";
  const initial = (who || "?").trim().charAt(0).toUpperCase();
  const workspace = displayName || (email || "My workspace").split("@")[0];

  return (
    <aside className="sidebar">
      {/* Brand + the account avatar. The avatar sits here because the top-left
          is where people look for "me" — clicking it opens the profile. */}
      <div className="sb-brand">
        <span className="sb-logo">🎭</span>
        <span className="sb-brand-name">Character Studio</span>
        <button
          type="button"
          className={`sb-brand-avatar ${active === "profile" ? "active" : ""}`}
          onClick={() => onNavigate("profile")}
          title="Your profile"
          aria-label="Your profile"
        >
          <Avatar size={30} initial={initial === "?" ? "" : initial} />
        </button>
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

      {/* Theme switch + profile chip + Upgrade CTA */}
      <div className="sb-footer">
        {/* Sits above the account button so it's reachable from every screen.
            Flipping it re-skins the whole app (see theme.js). */}
        <button
          type="button"
          className="sb-theme"
          onClick={onToggleTheme}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          <span className="sb-ico">{theme === "dark" ? "☀️" : "🌙"}</span>
          <span className="sb-theme-label">
            {theme === "dark" ? "Light mode" : "Dark mode"}
          </span>
          <span className={`sb-theme-switch ${theme === "light" ? "on" : ""}`} />
        </button>

        {/* Same face as the header, so both read as "you". This one opens the
            account menu (profile / log out) rather than jumping straight in. */}
        <button
          className="sb-workspace"
          onClick={onProfileClick}
          title="Account"
        >
          <Avatar size={30} initial={initial === "?" ? "" : initial} />
          <span className="sb-ws-text">
            <span className="sb-ws-name">{workspace}</span>
            <span className="sb-ws-sub">Account</span>
          </span>
        </button>

        <button className="sb-upgrade" onClick={onUpgrade}>
          <span className="sb-upgrade-ico">⚡</span> Upgrade
        </button>
      </div>
    </aside>
  );
}
