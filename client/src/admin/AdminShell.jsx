// AdminShell.jsx — the frame the admin panel lives in, and the reason it is a
// frame at all: THE PANEL IS NOT A WORKFLOW.
//
// It used to render inside the app's own shell, in the second column of the
// `.shell` grid, with the workflow rail beside it — Plan & Script, Text to
// Turnaround Image, Video Editor and the rest. That was wrong twice over:
//
//   1. The rail is a CUSTOMER's list of things to make. An operator opening
//      Users at 2am is not one click from "Image to AI Video", and offering it
//      makes the panel read as one more workflow rather than the room behind
//      the shop. Reported as "i only need admin panel not need my workflow".
//   2. It cost the panel the width. Six tabs, a table of 269 accounts and a
//      seven-tile dashboard were being drawn in `1fr` next to a 280px rail.
//
// So this is a second, much smaller shell: one bar, one column, everything
// below it the panel's own. ⚠ IT IS NOT A SECOND DESIGN LANGUAGE — the bar is
// built from the pieces the sidebar's footer already uses (`Avatar`, the shared
// `AccountMenu`, the same theme switch), because a panel that looked like a
// different product would be a worse answer than the rail it replaced.
//
// ⚠ AND IT IS NOT A SECURITY BOUNDARY. Being outside the app shell hides
// nothing: every `/admin/*` route answers 404 to a non-admin, which is the only
// thing between this page and anybody who types its address. See `require_admin`.
import { useCallback, useState } from "react";
import Avatar from "../components/Avatar.jsx";
import AccountMenu, { useMenuDismiss } from "../components/AccountMenu.jsx";

// The menu and its button, for the outside-press close. The button is in the
// list because it TOGGLES — see `useMenuDismiss`.
const MENU_DISMISS = ".admin-account-menu, .admin-account-btn";

export default function AdminShell({
  email,
  displayName,
  theme,
  onToggleTheme,
  // Back to the app the customers use. ⚠ THE ONE CONTROL THIS BAR EXISTS FOR:
  // taking the rail away means taking away every way back with it, and a page
  // whose only exit is the browser's Back button is a page people stop opening.
  onExit,
  onOpenAccount,
  onLogout,
  children,
}) {
  const [menu, setMenu] = useState(false);
  const closeMenu = useCallback(() => setMenu(false), []);
  useMenuDismiss(menu, closeMenu, MENU_DISMISS);

  const who = displayName || email || "";
  const initial = (who || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="admin-shell">
      <header className="admin-topbar">
        {/* Says where you are, in the corner where the app says what it is —
            so the swap from one shell to the other reads as a change of room
            rather than a page that failed to draw its sidebar. */}
        <span className="admin-brand">
          <span className="admin-brand-ico">🛡️</span>
          <span className="admin-brand-name">Character Studio</span>
          <span className="admin-brand-tag">Admin</span>
        </span>

        <span className="admin-topbar-actions">
          <button
            type="button"
            className="btn small"
            onClick={onExit}
            title="Back to the workflows"
          >
            ← Back to app
          </button>

          {/* Same switch as the rail's, minus its label — this bar has no room
              for a word and the icon is the one everybody already reads. */}
          <button
            type="button"
            className="btn small admin-theme"
            onClick={onToggleTheme}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            {theme === "dark" ? "☀️" : "🌙"}
          </button>

          <span className="admin-account-wrap">
            <button
              type="button"
              className={`admin-account-btn ${menu ? "active" : ""}`}
              onClick={() => setMenu((open) => !open)}
              title={who || "Account"}
              aria-haspopup="menu"
              aria-expanded={menu}
            >
              <Avatar size={28} initial={initial === "?" ? "" : initial} />
              <span className="admin-account-name">{who}</span>
            </button>

            {/* ⚠ NO "Pricing and plan" AND NO ACCOUNT SWITCHER, and the rows are
                LEFT OUT rather than hidden — handing `AccountMenu` no handler is
                what drops a row (see its header). Pricing is Admin → Pricing
                three inches away, and switching account from inside the panel
                lands whoever you switched to on a screen their token cannot
                open. Same reasoning as the editor's ⚙, which drops the account
                rows for being about YOU rather than about the project. */}
            {menu && (
              <AccountMenu
                className="admin-account-menu"
                onPick={closeMenu}
                onOpenAccount={onOpenAccount}
                onLogout={onLogout}
                helpSubject="Help with Character Studio"
              />
            )}
          </span>
        </span>
      </header>

      <main className="admin-main">{children}</main>
    </div>
  );
}
