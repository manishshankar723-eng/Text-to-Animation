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
import Logo from "./Logo.jsx";
import useBranding from "../useBranding.js";
import WorkflowIcon from "./WorkflowIcon.jsx";
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
// "fix" it.
//
// ⚠ THIS ARRAY IS THE LAST RESORT, NOT THE SOURCE OF TRUTH, AND NO LONGER THE
// "not answered yet" ANSWER EITHER. Since the feature registry (Phase 2) the
// real list comes from `GET /auth/me/entitlements` and is handed down as the
// `workflows` prop — which is what makes hiding, staging and reordering a
// workflow a switch in the admin panel instead of a redeploy.
//
// ⚠ IT USED TO BE DRAWN WHILE THAT CALL WAS IN FLIGHT, and that was a bug worth
// naming: this array is every workflow that EXISTS, so an administrator who had
// HIDDEN two of them saw both flash up on every single reload before the answer
// arrived and removed them. A hidden feature that reappears on every refresh is
// not hidden. "Not answered yet" is now `workflowsKnown === false`, and it draws
// SKELETON rows — nothing that could be wrong.
//
// This array is what is left for the one case that still needs it: the request
// FAILED and this browser has never had an answer to remember. A rail nobody can
// use is a worse outage than a rail that is briefly out of date.
//
// Keep it byte-identical to `_WORKFLOWS` in `server/features.py`, or a database
// hiccup silently reorders somebody's sidebar.
// ⚠ WHERE THE RAIL'S OPEN/CLOSED STATE IS REMEMBERED, and it lives here rather
// than in the shell because there are TWO shells now: the signed-in app and the
// public Explore page, which draws this same component. Two copies of a
// localStorage key is two things to keep in step and one of them to forget.
export const NAV_COLLAPSED_KEY = "cas_nav_collapsed";

export const WORKFLOWS = [
  // FIRST in the pipeline: decide what to make before making any of it.
  { id: "plan-and-script", label: "Plan & Script", icon: "🗓️", status: "live" },
  { id: "text-to-image", label: "Text to Turnaround Image", icon: "🖼️", status: "live" },
  { id: "script-to-storyboard", label: "Script to Storyboard", icon: "📝", status: "live" },
  { id: "create-animatic-image", label: "Image to Animatic Image", icon: "🖼️", status: "live" },
  { id: "animatics-to-video", label: "Image to AI Video", icon: "🎞️", status: "live" },
  { id: "storyboard-to-animatics", label: "Video Editor", icon: "🎬", status: "live" },
];

/**
 * THE SHORT NAME — what a workflow is called where there is no room for what it
 * is called.
 *
 * ⚠ THIS EXISTS BECAUSE THE COLLAPSED RAIL GREW LABELS. It used to be icons
 * only, and an icon-only rail needs no names; now every row carries one under
 * its glyph, and "Image to Animatic Image" does not fit under a 24px picture in
 * an 84px column. Asked for by name, workflow by workflow: *"Script to
 * Storyboard ka only Storyboard dikhao, second Image to Animatic Image ko
 * Animatics, aur Video Editor ka Editor only."*
 *
 * ⚠ AND IT COVERS THE HIDDEN ONES TOO, WHICH IS THE HALF THAT WAS ASKED FOR
 * TWICE: *"jo hide mai kiya hai uska bhi kar hi dena, to mai hide wale ko on
 * karun to ye kaam fir nahi karwana pare."* Three of these six are switched off
 * in the admin panel today. They are one click from coming back, and a workflow
 * that returns with its full name spilling out of the rail is the bug this map
 * is meant to prevent — so all six are named now, not the three on screen.
 *
 * ⚠ KEYED BY ID, WITH A FALLBACK, for the same reason `WorkflowIcon` is: the
 * rail is DATA from `/auth/me/entitlements` and an administrator can add a
 * workflow this build has never heard of. `shortLabel` then takes the last word
 * of whatever they typed — which is right far more often than it is wrong
 * ("Video Editor" → "Editor", "Script to Storyboard" → "Storyboard") and is
 * never a guess at meaning, only at length.
 *
 * ⚠ NOT `COPY[id].short` FROM `Landing.jsx`, and the two are allowed to differ.
 * That one is the word in the landing page's pipeline line — *Plan → Characters
 * → Storyboard → Key poses → AI video → Video* — a sentence describing how a
 * film is made. This one is a NAV LABEL: it has to name the room you are about
 * to walk into, which is why the same workflow is "Key poses" there and
 * "Animatics" here.
 */
export const WORKFLOW_SHORT = {
  "plan-and-script": "Plan",
  "text-to-image": "Characters",
  "script-to-storyboard": "Storyboard",
  "create-animatic-image": "Animatics",
  "animatics-to-video": "AI Video",
  "storyboard-to-animatics": "Editor",
};

/** The short name for a workflow, or the last word of its label. */
export function shortLabel(id, label = "") {
  if (WORKFLOW_SHORT[id]) return WORKFLOW_SHORT[id];
  const words = String(label).trim().split(/\s+/);
  return words[words.length - 1] || label;
}

export default function Sidebar({
  active,
  onNavigate,
  // The resolved list from `/auth/me/entitlements`: `[{id, label, icon, status}]`,
  // already filtered to what this account may SEE and already in the owner's
  // order. Null/empty → the built-in fallback above. See the note on WORKFLOWS.
  workflows,
  // ⚠ WHETHER `workflows` IS AN ANSWER OR A GUESS, and the whole reason the
  // hidden-workflow flash is gone. False means nobody has told this browser
  // what this account may see — neither the server just now, nor a remembered
  // answer from last time — so the rows below are drawn as skeletons rather
  // than filled in from the built-in list, which would show workflows an
  // administrator has hidden. The shell flips it true on the answer AND on a
  // failure; see App.jsx.
  workflowsKnown = true,
  email,
  displayName,
  theme,
  onToggleTheme,
  onUpgrade,
  // The account menu's rows. `onUpgrade` doubles as Pricing and plan — it is
  // the same modal the Upgrade button below opens, and two ways in are fine;
  // two DIFFERENT pricing screens would not be.
  onOpenAccount,
  // Straight through to the menu. The rail owns none of this — it is only where
  // the button lives — and an ordinary account is handed nothing, so the row
  // never renders. See AccountMenu.jsx.
  onOpenAdmin,
  onLogout,
  // The account switcher inside that menu. Straight through - the rail owns
  // none of this, it is just where the button lives.
  accounts,
  onSwitchAccount,
  onAddAccount,
  collapsed = false,
  onToggleCollapse,
  // ⚠ THE SAME RAIL, DRAWN FOR SOMEBODY WITH NO ACCOUNT. The public Explore
  // page used to carry a hand-built copy of this component — close, and
  // therefore wrong: it had no brand mark, no app name and no collapse toggle,
  // and that was the report. *"mai chahta hun ki ye sab waisa hi dikhe jaise
  // user login kar ke dikhta hai — jaise abhi missing hai collapse bar and logo
  // and AI Studio name."*
  //
  // ⚠ SO THE ANSWER IS THIS FILE, NOT A SECOND ONE. A rail that merely
  // RESEMBLES this one is the mismatch this repo keeps paying for, and it drifts
  // the first time either side is touched. What `publicMode` removes is only
  // what a visitor cannot have — the avatar, the account menu — and the gold
  // button at the foot becomes the sign-in instead of the upgrade. Everything
  // else is the same markup and the same stylesheet, which is the point.
  //
  // ⚠ THE ROWS STILL CALL `onNavigate`. The public page maps that to a sign-in
  // rather than to a page, so nothing in here has to know which side it is on.
  publicMode = false,
  onSignIn,
}) {
  // ⚠ FAIL OPEN, EVERY TIME. An empty array is treated as "we don't know yet",
  // not as "this account has no workflows" — the second reading would blank the
  // rail for everyone the moment the entitlements call had a bad minute.
  const items = workflows?.length ? workflows : WORKFLOWS;
  // How many placeholder rows to draw while we wait. The remembered list is
  // almost always there, so this is a first-run-on-a-new-browser sight; the
  // built-in count is the best guess available for how tall the rail will be,
  // and being one row out costs nothing because the rows are the same height.
  const ghostRows = WORKFLOWS.length;
  const who = displayName || email || "";
  const initial = (who || "?").trim().charAt(0).toUpperCase();
  const workspace = displayName || (email || "My workspace").split("@")[0];

  // ⚠ THE OPEN/CLOSED FLAG LIVES HERE, NOT IN THE SHELL, because this is the
  // only thing that opens it. It used to be `accountOpen` in App.jsx, back when
  // pressing this button dimmed the whole app and put up a card.
  const [menu, setMenu] = useState(false);
  const closeMenu = useCallback(() => setMenu(false), []);
  useMenuDismiss(menu, closeMenu, MENU_DISMISS);

  // What the app is CALLED, from the admin panel. ⚠ NOT A CONSTANT ANY MORE —
  // `.sb-brand-name` ellipsises, so a longer name trims here SILENTLY; the
  // server caps it at `NAME_MAX_CHARS` for exactly that reason and
  // `tests/branding_check.py` measures the row rather than eyeballing it.
  const brand = useBranding();

  return (
    <aside
      className={`sidebar ${collapsed ? "collapsed" : ""}${
        publicMode ? " sidebar-public" : ""
      }`}
    >
      {/* Brand + the account avatar. The avatar sits here because the top-left
          is where people look for "me" — clicking it opens the profile. */}
      <div className="sb-brand">
        <span className="sb-logo">
          <Logo />
        </span>
        <span className="sb-brand-name">{brand.name}</span>
        {/* ⚠ THE ONE THING A VISITOR HAS NO VERSION OF. Everything else in this
            row — the mark, the name, the collapse toggle — is drawn on the
            public page too, because it is the same rail. */}
        {!publicMode && (
          <button
            type="button"
            className={`sb-brand-avatar ${active === "profile" ? "active" : ""}`}
            onClick={() => onNavigate("profile")}
            title="Your profile"
            aria-label="Your profile"
          >
            <Avatar size={30} initial={initial === "?" ? "" : initial} />
          </button>
        )}
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
        {/* ⚠ THE EXPLORE ROW IS GONE FROM THE SIGNED-IN RAIL, and its absence
            there is a decision rather than a deletion. Explore used to sit above
            Home — the SHOP WINDOW over the DESK — and it has since changed sides
            entirely: it is the public marketing page a stranger lands on, and
            nobody who has signed in sees it. Asked for directly: *"any logged in
            user must not see explore"*. See the note on `LANDING_NAV` in App.jsx.

            ⚠ SO HOME IS THE FIRST ROW AND THE FRONT DOOR — INSIDE THE APP. Emoji,
            like the old Explore row was; the workflow rows below are the ones
            that needed drawn glyphs, because two of them shared a picture. */}

        {/* ⚠ AND ON THE PUBLIC SIDE IT IS BACK, IN ITS OLD PLACE. Out here
            Explore IS the page, and without a row for it the rail had no way of
            saying so and no way back to it — pressing Home left for the sales
            page with only a link buried in its nav to return by. Asked for
            exactly that way: *"explore ka button kyun nahi dikh raha hai, ye page
            kahan se khul raha hai? home ke upar explore button daalo, jaise user
            wale mein tha pehle."*

            ⚠ THE TWO WORDS MEAN DIFFERENT THINGS ON THE TWO SIDES, and that is
            why this is not the same row twice. Signed in, Home is the desk.
            Signed out, Home is the sales page and Explore is the shop window —
            which is the pair this rail was originally built around. */}
        {publicMode && (
          <button
            className={`sb-item ${active === "explore" ? "active" : ""}`}
            onClick={() => onNavigate("explore")}
            title="Explore — the work made with this studio"
          >
            <span className="sb-ico">🧭</span>
            <span className="sb-item-label">Explore</span>
            <span className="sb-item-short" aria-hidden="true">Explore</span>
          </button>
        )}

        <button
          className={`sb-item ${active === "home" ? "active" : ""}`}
          onClick={() => onNavigate("home")}
          title="Home"
        >
          <span className="sb-ico">🏠</span>
          <span className="sb-item-label">Home</span>
          <span className="sb-item-short" aria-hidden="true">Home</span>
        </button>

        {/* Workflows. Collapsed there is no room for the heading, so the group
            is marked by the rule the heading would have sat above. */}
        {collapsed ? (
          <div className="sb-divider" />
        ) : (
          <div className="sb-section-label">Workflows</div>
        )}
        {/* ⚠ SKELETONS RATHER THAN THE BUILT-IN LIST. Until somebody has told
            this browser what this account may see, the honest thing to draw is
            "loading", not "everything that exists" — the latter is how a
            workflow an administrator had hidden appeared for a second on every
            reload. Hidden from assistive tech: it is a placeholder, not a
            navigation. See `workflowsKnown`. */}
        {!workflowsKnown &&
          Array.from({ length: ghostRows }, (_, i) => (
            <div className="sb-item sb-ghost" key={`ghost-${i}`} aria-hidden="true">
              <span className="sb-ico sb-ghost-ico" />
              <span className="sb-item-label sb-ghost-line" />
            </div>
          ))}
        {workflowsKnown &&
          items.map((w) => (
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
            <span className="sb-ico">
              <WorkflowIcon id={w.id} fallback={w.icon} />
            </span>
            <span className="sb-item-label">{w.label}</span>
            {/* ⚠ BOTH NAMES ARE ALWAYS IN THE DOM; the stylesheet shows one and
                hides the other. Swapping the TEXT on collapse would mean the
                rail re-rendered its own labels on a layout change, and the
                `title` — which is the full name, in both states — would have to
                be kept in step with whichever one was showing.
                `aria-hidden` because it is the same row said twice: a screen
                reader should hear "Script to Storyboard", never "Script to
                Storyboard Storyboard". */}
            <span className="sb-item-short" aria-hidden="true">
              {shortLabel(w.id, w.label)}
            </span>
            {w.status === "soon" && <span className="sb-badge-soon">Soon</span>}
            {/* ⚠ LOCKED IS NOT HIDDEN, ON PURPOSE. A feature nobody can see is a
                feature nobody upgrades for — the row stays, wearing the reason
                it can't be opened. */}
            {w.locked && (
              <span className="sb-badge-locked" title="Included in a higher plan">
                🔒
              </span>
            )}
            {w.status === "live" && !w.locked && (
              <span className="sb-dot-live" title="Live" />
            )}
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
        {!publicMode && (
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
              onOpenAdmin={onOpenAdmin}
              onLogout={onLogout}
              helpSubject={`Help with ${brand.name}`}
              accounts={accounts}
              activeEmail={email}
              onSwitchAccount={onSwitchAccount}
              onAddAccount={onAddAccount}
            />
          )}
        </span>
        )}

        {/* ⚠ THE SAME GOLD BUTTON IN THE SAME PLACE, SAYING THE OTHER THING.
            Signed in, the bottom of the rail asks for money; signed out it asks
            for an account, which is the same question one step earlier. Keeping
            the slot means the rail does not visibly rearrange itself the moment
            somebody signs in — which is the whole reason the public page uses
            this component rather than one that looks like it. */}
        <button
          className="sb-upgrade"
          onClick={publicMode ? () => onSignIn?.(null) : onUpgrade}
          title={publicMode ? "Sign in" : "Upgrade"}
        >
          <span className="sb-upgrade-ico">{publicMode ? "→" : "⚡"}</span>
          <span className="sb-upgrade-label">
            {publicMode ? "Sign in" : "Upgrade"}
          </span>
          {/* ⚠ THE ONE FOOTER CONTROL THAT GETS A NAME WHEN NARROW. A sun and a
              face read on their own; a lightning bolt in a gold box does not
              say what pressing it costs. */}
          <span className="sb-item-short" aria-hidden="true">
            {publicMode ? "Sign in" : "Upgrade"}
          </span>
        </button>
      </div>
    </aside>
  );
}
