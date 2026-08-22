// Left navigation rail. Home + a Workflows section (the live Text-to-Image
// pipeline plus placeholders for future workflows) + an Upgrade CTA.
//
// COLLAPSED MODE: the same markup, narrowed to an icon-only rail (the toggle
// lives in the brand row, Ctrl/Cmd+B does it from the keyboard). Nothing is
// removed when it collapses — the labels are hidden in CSS — so every row keeps
// its `title`, which becomes the only name you can read at 68px wide.
import { useCallback, useState } from "react";
import Avatar from "./Avatar.jsx";
import Icon from "./Icon.jsx";
// ⚠ THE SAME MENU THE EDITOR'S ⚙ OPENS, not a second list that looks like it.
// See AccountMenu.jsx.
import AccountMenu, { useMenuDismiss } from "./AccountMenu.jsx";

// The account dropdown and its trigger, for the outside-press close. The button
// is in it because it TOGGLES — see `useMenuDismiss`.
const MENU_DISMISS = ".sb-account-menu, .sb-workspace";

// Workflow nav items. `status: "live"` is the working pipeline; "soon" items
// are placeholders for the roadmap the user is building toward.
// The `id`s are internal nav keys, NOT labels — renaming a workflow changes its
// `label` only. `animatics-to-video` keeps its historical id (it is now shown as
// "Image to AI Video") so a rename can't strand anyone mid-session or break the
// deep links App.jsx sets when one workflow hands off to another.
//
// ORDER IS THE OWNER'S CHOICE and is deliberately not pipeline order — don't
// "fix" it. Every entry is currently `live`; `status: "soon"` is still honoured
// by the badge below, but adding one again also means restoring the
// `WorkflowSoon` branch in App.jsx, or the item will navigate to a blank page.
export const WORKFLOWS = [
  // FIRST in the pipeline: decide what to make before making any of it.
  { id: "plan-and-script", label: "Plan & Script", icon: "🗓️", status: "live" },
  { id: "text-to-image", label: "Text to Turnaround Image", icon: "🖼️", status: "live" },
  { id: "script-to-storyboard", label: "Script to Storyboard", icon: "📝", status: "live" },
  { id: "create-animatic-image", label: "Image to Animatic Image", icon: "🖼️", status: "live" },
  { id: "animatics-to-video", label: "Image to AI Video", icon: "🎞️", status: "live" },
  { id: "storyboard-to-animatics", label: "Video Editor", icon: "🎬", status: "live" },
];

export default function Sidebar({
  active,
  onNavigate,
  email,
  displayName,
  theme,
  onToggleTheme,
  onUpgrade,
  // The account menu's rows. `onUpgrade` doubles as Pricing and plan — it is
  // the same modal the Upgrade button below opens, and two ways in are fine;
  // two DIFFERENT pricing screens would not be.
  onOpenAccount,
  onLogout,
  // The account switcher inside that menu. Straight through - the rail owns
  // none of this, it is just where the button lives.
  accounts,
  onSwitchAccount,
  onAddAccount,
  collapsed = false,
  onToggleCollapse,
}) {
  const who = displayName || email || "";
  const initial = (who || "?").trim().charAt(0).toUpperCase();
  const workspace = displayName || (email || "My workspace").split("@")[0];

  // ⚠ THE OPEN/CLOSED FLAG LIVES HERE, NOT IN THE SHELL, because this is the
  // only thing that opens it. It used to be `accountOpen` in App.jsx, back when
  // pressing this button dimmed the whole app and put up a card.
  const [menu, setMenu] = useState(false);
  const closeMenu = useCallback(() => setMenu(false), []);
  useMenuDismiss(menu, closeMenu, MENU_DISMISS);

  return (
    <aside className={`sidebar ${collapsed ? "collapsed" : ""}`}>
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
        {/* Stays in the SAME corner in both states, so the button you clicked
            to close the rail is the button that reopens it. */}
        <button
          type="button"
          className="sb-collapse"
          onClick={onToggleCollapse}
          title={
            collapsed ? "Expand sidebar  Ctrl+B" : "Collapse sidebar  Ctrl+B"
          }
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
        >
          <Icon name="sidebar" size="1.15em" />
        </button>
      </div>

      {/* Home */}
      <nav className="sb-nav">
        <button
          className={`sb-item ${active === "home" ? "active" : ""}`}
          onClick={() => onNavigate("home")}
          title="Home"
        >
          <span className="sb-ico">🏠</span>
          <span className="sb-item-label">Home</span>
        </button>

        {/* Workflows. Collapsed there is no room for the heading, so the group
            is marked by the rule the heading would have sat above. */}
        {collapsed ? (
          <div className="sb-divider" />
        ) : (
          <div className="sb-section-label">Workflows</div>
        )}
        {WORKFLOWS.map((w) => (
          <button
            key={w.id}
            className={`sb-item ${active === w.id ? "active" : ""}`}
            onClick={() => onNavigate(w.id)}
            /* Clicking the one you're in takes you back to its first page, so
               say so rather than leaving it to be discovered. */
            title={
              active === w.id
                ? `${w.label} — click again to go back to the start`
                : w.label
            }
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
            account menu rather than jumping straight in.
            ⚠ IT IS A DROPDOWN NOW, NOT A MODAL. It used to dim the app for a
            card that asked "are you sure you want to log out?" — a dialog for
            what is a four-item menu, and the same mistake ＋ Add layer made and
            undid. User-reported: "not need pop up here".
            ⚠ THE WRAPPER IS WHAT THE MENU HANGS OFF. `.sb-footer` is not
            positioned, and the menu opens UPWARD from here — this button sits
            at the bottom of a full-height rail, so a list below it would open
            off the bottom of the screen. */}
        <span className="sb-account-wrap">
          <button
            className={`sb-workspace ${menu ? "active" : ""}`}
            onClick={() => setMenu((open) => !open)}
            title="Account"
            aria-haspopup="menu"
            aria-expanded={menu}
          >
            <Avatar size={30} initial={initial === "?" ? "" : initial} />
            <span className="sb-ws-text">
              <span className="sb-ws-name">{workspace}</span>
              <span className="sb-ws-sub">Account</span>
            </span>
          </button>

          {/* No Delete row: deleting belongs to a PROJECT, and the rail has no
              project open. That is the whole difference from the editor's copy. */}
          {menu && (
            <AccountMenu
              className="sb-account-menu"
              onPick={closeMenu}
              onOpenAccount={onOpenAccount}
              onOpenPricing={onUpgrade}
              onLogout={onLogout}
              helpSubject="Help with Character Studio"
              accounts={accounts}
              activeEmail={email}
              onSwitchAccount={onSwitchAccount}
              onAddAccount={onAddAccount}
            />
          )}
        </span>

        <button className="sb-upgrade" onClick={onUpgrade} title="Upgrade">
          <span className="sb-upgrade-ico">⚡</span>
          <span className="sb-upgrade-label">Upgrade</span>
        </button>
      </div>
    </aside>
  );
}
