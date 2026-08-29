import { useCallback, useEffect, useState } from "react";
import * as api from "./api.js";
import { applyTheme, getTheme } from "./theme.js";
// The same answer this shell reads for the rail, handed to the controls buried
// inside the workflows — ✨ Animate, 🎙 Voiceover, the 3D popup. See
// `entitlements.js`; it is a module store, so nothing is threaded as a prop.
import { clearEntitlements, setEntitlements } from "./entitlements.js";
// The boot reads - profile, entitlements, and every dashboard list - fetched
// ONCE at authentication and kept for the session. See session_cache.js for
// what that replaced and why the fetch starts before this component knows
// anything has happened.
import * as cache from "./session_cache.js";
import Landing from "./components/Landing.jsx";
import Login from "./components/Login.jsx";
import Sidebar from "./components/Sidebar.jsx";
import Home from "./components/Home.jsx";
import Explore from "./components/Explore.jsx";
import Profile from "./components/Profile.jsx";
import PlanAndScript from "./components/PlanAndScript.jsx";
import ScriptToStoryboard from "./components/ScriptToStoryboard.jsx";
import StoryboardToAnimatics from "./components/StoryboardToAnimatics.jsx";
import AnimaticsToVideo from "./components/AnimaticsToVideo.jsx";
import CreateAnimaticImage from "./components/CreateAnimaticImage.jsx";
import PublicStoryboard from "./components/PublicStoryboard.jsx";
import PricingModal from "./components/PricingModal.jsx";
import AdminPanel from "./admin/AdminPanel.jsx";
// The panel gets its own shell, not the second column of this one - see the
// header of AdminShell.jsx for why the workflow rail has no business beside it.
import AdminShell from "./admin/AdminShell.jsx";
import WorkflowSoon from "./components/WorkflowSoon.jsx";
import { WORKFLOWS } from "./components/Sidebar.jsx";
import GenerateForm from "./components/GenerateForm.jsx";
import JobList from "./components/JobList.jsx";
import JobDetail from "./components/JobDetail.jsx";

import WorkflowIcon from "./components/WorkflowIcon.jsx";
// ⚠ THE "SOON" BRANCH IS BACK, AND IT HAD TO BE. The note that used to sit here
// warned that a `status: "soon"` workflow would navigate to a blank page unless
// this branch was restored — and Phase 2 made "soon" something an administrator
// can set from the panel, at any moment, without touching this file. The trap
// the old note described was one click away from being live.
//
// A workflow marked "soon" is drawn in the rail with its badge and lands here
// instead of on its real page. The copy is generic on purpose: whoever flips the
// switch is not editing JSX, so the placeholder has to read sensibly for ANY
// workflow it is pointed at.
function soonScreenFor(workflow) {
  return (
    <WorkflowSoon
      icon={workflow?.icon || "🚧"}
      title={workflow?.label || "Coming soon"}
      description="This workflow isn't open on your account yet. It's on the way — you'll see it here the moment it is."
      steps={[]}
    />
  );
}

// A shared storyboard link is `?s=<token>`. Read it once at boot: the app has
// no router, and this is the only route that must render logged OUT.
function readShareToken() {
  const t = new URLSearchParams(window.location.search).get("s");
  return t && /^[a-f0-9]{32}$/i.test(t) ? t : null;
}

// The admin panel is `?admin`. THE SECOND AND LAST THING IN THE URL, and it is
// here for one reason: the panel is a place somebody is sent to ("open the
// admin panel and look at this account"), and a place you can only reach by
// signing in and hunting through a menu is a place nobody links to.
//
// ⚠ IT IS AN ADDRESS, NOT A PERMISSION. Typing it gets a non-admin the same
// "not available on this account" card the menu already refuses to offer them,
// and every /admin/* request behind it answers 404 regardless. See require_admin.
const ADMIN_PARAM = "admin";

function readAdminRoute() {
  return new URLSearchParams(window.location.search).has(ADMIN_PARAM);
}

// Keep the address honest as `nav` moves. ⚠ `replaceState`, NOT `pushState` -
// the app has no router and therefore nothing that could answer a Back button
// walking through a history of nav states; pushing would build a stack that
// only ever behaves wrongly.
function syncUrlFlag(param, on) {
  const url = new URL(window.location.href);
  if (on === url.searchParams.has(param)) return;
  if (on) url.searchParams.set(param, "1");
  else url.searchParams.delete(param);
  window.history.replaceState({}, "", url.pathname + url.search + url.hash);
}

function syncAdminUrl(onAdmin) {
  syncUrlFlag(ADMIN_PARAM, onAdmin);
}

// The public wall is `?explore`. THE THIRD AND LAST THING IN THE URL, and it
// exists for the reason the marketing page exists at all: Explore is the SHOP
// WINDOW — the films and stills a stranger can actually watch — and a shop
// window you cannot send anybody a link to is a shop window facing a wall.
// Until this, it was reachable only by landing on `/`, reading the sales page
// and finding "See the work"; the page whose entire job is to be shown to
// people had no way of being shown to anybody directly.
//
// ⚠ IT IS THE LOGGED-OUT VIEW, AND IT STAYS THAT WAY. `LANDING_NAV` above is
// the standing decision that a signed-in customer never sees Explore again
// ("any logged in user must not see explore"), so this parameter is honoured
// for a VISITOR and dropped the moment somebody signs in — a marketing link
// opened by an existing customer lands them in their own app, which is where
// they were going anyway. It is not a second front door for the signed-in app.
//
// ⚠ `?admin` WINS OVER IT when somebody has managed to put both in one URL.
// One is an address a person was SENT to work at; this one is a shop window.
const EXPLORE_PARAM = "explore";

function readExploreRoute() {
  const q = new URLSearchParams(window.location.search);
  return q.has(EXPLORE_PARAM) && !q.has(ADMIN_PARAM);
}

function syncExploreUrl(onExplore) {
  syncUrlFlag(EXPLORE_PARAM, onExplore);
}

// ⚠ WHERE THE APP OPENS, IN ONE PLACE. Five separate paths land somebody in the
// app — a returning session, a fresh sign-in, a sign-out (which sets the state
// the next sign-in starts from), an account switch, and leaving the admin panel
// — and before this constant existed all five spelled the destination out for
// themselves. That is five lines to change to move the front door, and four of
// them are easy to miss: the bug it produces is "it opens on the right page
// UNLESS you switched account", which nobody reports as one bug.
//
// ⚠ IT IS HOME, AND IT USED TO BE EXPLORE. Explore was the front door for
// everybody — *"jab user aaye to explore page khule, home page nhi"* — and it
// has since changed sides entirely: it is the PUBLIC marketing page now, shown
// only to somebody who has not signed in. Asked for directly: *"any logged in
// user must not see explore ... after login we know how our page which is home
// must look"*.
//
// So the two screens finally answer the two different questions they were always
// meant to. Explore is the SHOP WINDOW — what this studio makes, and a wall of
// work a stranger can watch. Home is the DESK — who you are, your plan, where
// you left off. A shop window is what a front door opens onto; it is not what
// you look at once you are inside, and a customer who has already bought does
// not need selling to every time they sign in.
//
// ⚠ THE URL STILL WINS OVER IT. `?admin` is an address somebody was SENT, and a
// link that lands somewhere other than where it points is a broken link.
const LANDING_NAV = "home";

/**
 * A destination from the PUBLIC page, made safe to navigate to.
 *
 * ⚠ WHAT A VISITOR CLICKED IS NOT A NAV ID UNTIL THIS SAYS SO. Explore carries
 * the workflow somebody clicked THROUGH the sign-in, so they land where they
 * were headed rather than on a generic dashboard — and one of those ids comes
 * from a banner's `cta_target`, which an administrator TYPED and the server only
 * checks the SHAPE of (see `_TARGET_RE` in banners.py). "explore", "admin" and a
 * plain typo all pass that check. A nav string nothing matches renders no
 * content at all, and a blank page is a poor first screen after signing up — so
 * anything that is not a real workflow becomes `null`, which means Home.
 */
function asWorkflow(id) {
  return WORKFLOWS.some((w) => w.id === id) ? id : null;
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
  // "landing" | "explore" | "login" — the three logged-out screens.
  // ⚠ THE URL DECIDES WHICH PUBLIC SCREEN OPENS, exactly as it does for the
  // admin panel one line down. `?explore` lands on the wall; anything else
  // lands on the sales page, which is still the front door for a bare `/`.
  const [authView, setAuthView] = useState(() =>
    readExploreRoute() ? "explore" : "landing"
  );
  // Which of the first two the sign-in card was opened FROM, so its Back button
  // goes where the person came from. Without it, Back from a sign-in reached
  // through Explore drops somebody on the landing page they had already left —
  // which reads as the app losing their place.
  const [authBack, setAuthBack] = useState("landing");
  // ⚠ WHAT THEY CLICKED BEFORE THEY HAD AN ACCOUNT. Every control on the public
  // Explore page is a sign-in gate, and the workflow it was selling is
  // remembered here so `onAuthed` can open it. Clicking "Script to Storyboard",
  // typing a password and arriving on a generic dashboard is how an interested
  // visitor is lost in the two screens between the click and the account.
  const [pendingWorkflow, setPendingWorkflow] = useState(null);
  // Land on `LANDING_NAV` by default — both a fresh login and a returning
  // session — so opening the app shows what this studio can make and what you
  // have made, rather than dropping you mid-workflow. See that constant for
  // which screen it is and why it is not spelled out here.
  // ⚠ THE URL WINS OVER THE DEFAULT, and only for this one destination. A
  // bookmark or a pasted link has to land where it points.
  const [nav, setNav] = useState(() =>
    readAdminRoute() ? "admin" : LANDING_NAV
  );
  // "explore" | "home" | "profile" | "admin" | workflow id
  // Bumped when the user clicks the workflow they are ALREADY in. Every
  // workflow keeps its own screen in local state (library → session → board),
  // so re-selecting it in the sidebar did nothing — you stayed wherever you
  // were. This is fed into the content's `key`, which remounts it and drops it
  // back on its first page. Nothing is lost by that: drafts, plans, boards and
  // jobs all live server-side and are re-read on mount.
  const [navResetKey, setNavResetKey] = useState(0);
  // Bumped whenever the SIGNED-IN ACCOUNT changes without `authed` changing -
  // switching accounts, or adding one. It is the dependency that re-runs the
  // boot effect in exactly the case that used to be covered by listing `nav`
  // there, and in no other case. See that effect's closing comment.
  const [authRun, setAuthRun] = useState(0);
  const [selectedId, setSelectedId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  // Set by the board's "Make animatic" button: the animatic already exists, so
  // the animatics workflow opens straight into its editor instead of the library.
  const [pendingAnimaticId, setPendingAnimaticId] = useState(null);
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  // Every account whose token this browser is holding, for the switcher in the
  // sidebar's menu. Kept in state rather than read straight from `api` on each
  // render because adding, switching and logging out all have to REDRAW it.
  const [accounts, setAccounts] = useState(api.listAccounts);
  // The "Add another account" dialog: the ordinary sign-in form, in a modal,
  // over the app you are already signed in to.
  const [addAccountOpen, setAddAccountOpen] = useState(false);
  // ⚠ NO `accountOpen` ANY MORE. The sidebar's account button used to dim the
  // app and put up a card ("are you sure you want to log out?"); it opens the
  // shared dropdown now and keeps that flag itself — see Sidebar.jsx.
  // main.jsx already applied the stored theme before the first paint; this only
  // has to re-stamp <html> when the user flips the switch.
  const [theme, setTheme] = useState(getTheme);
  const [navCollapsed, setNavCollapsed] = useState(readNavCollapsed);
  // The name the user chose on their profile, so the sidebar shows it instead
  // of the local part of their email. Refreshed whenever they leave the profile
  // page, which is the only place it can change.
  const [displayName, setDisplayName] = useState("");
  // ⚠ WHAT IS DRAWN, NEVER WHAT IS ALLOWED. This decides whether the account
  // menu shows an Admin row; every /admin route is guarded server-side by
  // `require_admin`, which reads the role out of the database and answers 404
  // to anyone else. Editing this in a debugger gets you an empty panel.
  // It rides along on the `me()` call the shell already makes — see below.
  const [isAdmin, setIsAdmin] = useState(false);
  // What this account may SEE and USE, from `/auth/me/entitlements`.
  //
  // ⚠ THE FIRST PAINT STARTS FROM THE LAST ANSWER THIS ACCOUNT GOT, not from the
  // built-in list. This used to be `useState(WORKFLOWS)` — every workflow that
  // EXISTS — under a note explaining that the alternative was a blank rail. What
  // that note missed is the third option, and the bug it was hiding: an
  // administrator who had HIDDEN two workflows watched both of them reappear
  // for about a second on every reload, because the built-in list still
  // contains them. A hidden feature that flashes up on every refresh is not
  // hidden. See `rememberEntitlements` in api.js.
  //
  // Three states now, and the rail draws each one differently:
  //   - a remembered answer → draw it; correct on frame one, nothing flashes.
  //   - nothing remembered  → draw SKELETON rows, so a wrong row is never shown.
  //                           (First sign-in on a new browser, and only then.)
  //   - the request FAILED with nothing remembered → the built-in list, because
  //                           fail-open still beats a rail nobody can use.
  const [workflows, setWorkflows] = useState(
    () => cache.rememberedEntitlements()?.workflows || WORKFLOWS
  );
  // ⚠ "HAS THE SERVER ANSWERED?" IS A DIFFERENT QUESTION FROM "WHAT DID IT SAY?"
  // and both are needed. A workflow missing from `workflows` means *hidden* only
  // if the list came from the server; while it is the built-in fallback, a
  // missing entry means nothing at all — and treating those the same would show
  // "not available" for a second on every cold start.
  //
  // ⚠ A REMEMBERED ANSWER COUNTS AS ANSWERED. It IS what the server said — just
  // not in this second.
  const [entitled, setEntitled] = useState(
    () => Boolean(cache.rememberedEntitlements())
  );
  // ⚠ AND THIS IS WHAT THE RAIL DRAWS SKELETONS FROM. It differs from `entitled`
  // in exactly one case, which is the case that matters: a first sign-in on a
  // browser, nothing remembered, no answer yet. `entitled` is about what a
  // MISSING entry means; this is about whether the list on screen may be shown
  // at all.
  const [railKnown, setRailKnown] = useState(
    () => Boolean(cache.rememberedEntitlements())
  );
  // Which tier this account is on. ⚠ IT COMES FROM THE ENTITLEMENTS CALL, NOT
  // FROM THE PRICE LIST — `/billing/tiers` is public and knows nothing about
  // who is asking, which is exactly what lets a logged-out page show prices.
  const [tier, setTier] = useState(
    () => cache.rememberedEntitlements()?.tier || ""
  );

  useEffect(() => applyTheme(theme), [theme]);

  // ⚠ ONE DIRECTION ONLY: nav writes the URL, the URL is read once at boot.
  // Watching the address as well would make two owners of one piece of state,
  // and the app has no router to arbitrate between them.
  useEffect(() => {
    syncAdminUrl(nav === "admin");
  }, [nav]);

  // The same one-way rule for the public wall. ⚠ `authed` IS IN HERE ON
  // PURPOSE: signing in is what makes `?explore` wrong, and the sign-in does
  // not touch `authView` on its way through — without this the address would
  // still read `?explore` while the customer is looking at their own Home, and
  // a reload of that URL is the one case where the parameter would have to be
  // ignored twice to stay harmless. Written once, at the moment it stops being
  // true, is cheaper than defending against it everywhere else.
  useEffect(() => {
    syncExploreUrl(!authed && authView === "explore");
  }, [authed, authView]);

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

  // ⚠ THE PREFETCH GETS ITS OWN EFFECT, KEYED ON THE ACCOUNT AND NOT ON
  // `nav`. It is the "go and fetch the dashboard" trigger, and the dashboard
  // does not become more out of date because somebody opened the Video Editor.
  // Folded into the effect below - which DOES depend on `nav`, deliberately -
  // it would have re-run every list on a timer for as long as anyone kept
  // clicking, which is the shape of the problem this set out to remove.
  //
  // This is the RELOAD path: the token was already in storage, so `Login` never
  // ran and nothing has been asked for yet. A sign-in has already started the
  // same prefetch a moment earlier and this is idempotent, so that path pays
  // nothing for passing through here.
  useEffect(() => {
    if (!authed) return;
    cache.prefetch({ email: api.getEmail() });
    // ⚠ THE SAME FLASH, ONE LAYER DOWN. `clearEntitlements()` leaves the
    // capability store saying "we don't know", and that is fail-OPEN — so the
    // Animate, Voiceover and 3D controls draw as AVAILABLE until the answer
    // lands, including on an account where an administrator has switched them
    // off. Seeding from the remembered answer closes that window, for exactly
    // the reason the rail's was closed.
    const last = cache.rememberedEntitlements();
    if (last) setEntitlements(last);
  }, [authed, authRun]);

  useEffect(() => {
    if (!authed) {
      setDisplayName("");
      setIsAdmin(false);
      setWorkflows(WORKFLOWS);
      setEntitled(false);
      // ⚠ BACK TO UNKNOWN, so the next account's first paint waits for its own
      // answer instead of inheriting this one's. `WORKFLOWS` above is only what
      // gets drawn if that answer never comes at all.
      setRailKnown(false);
      setTier("");
      // ⚠ CLEARED IS "WE DON'T KNOW", NOT "NOTHING IS ALLOWED" — the module
      // goes back to fail-open, exactly as the rail goes back to WORKFLOWS.
      // Leaving the last account's answer in place would grey out a control for
      // the next person to sign in on this browser.
      clearEntitlements();
      return;
    }
    let cancelled = false;

    cache
      .ensure("me")
      .then((p) => {
        if (cancelled || !p) return;
        const name = p?.display_name || p?.full_name || "";
        setDisplayName(name);
        setIsAdmin(p?.account_role === "admin");
        // Cached against the account so the switcher can name it later, when it
        // is one of the OTHER entries and `me()` is answering for someone else.
        const mine = api.getEmail();
        if (mine) {
          api.rememberAccountName(mine, name);
          setAccounts(api.listAccounts());
        }
      })
      .catch(() => {
        // Cosmetic only - the sidebar falls back to the email.
      });

    // ⚠ A SEPARATE, INDEPENDENTLY-FAILING REQUEST. Chaining it onto `me()` would
    // mean one failure took out both, and these two have very different blast
    // radii: a missing display name is cosmetic, a missing workflow list is the
    // whole navigation. Its own `.catch` KEEPS whatever is already on screen -
    // never replaces it with nothing. (The cache preserves that: a failed
    // refresh keeps the last good answer and records the message beside it.)
    cache
      .ensure("entitlements")
      .then((e) => {
        if (cancelled || !e) return;
        if (e?.workflows?.length) {
          setWorkflows(e.workflows);
          setEntitled(true);
          setRailKnown(true);
        }
        setTier(e?.tier || "");
        // ⚠ OUTSIDE THE `workflows.length` BRANCH ON PURPOSE. The two answers
        // are independent: an account can legitimately have every workflow and
        // a capability switched off, and hanging this on the rail's condition
        // would have made "no workflows" silently mean "every button on".
        setEntitlements(e);
      })
      .catch(() => {
        if (cancelled) return;
        // ⚠ FAIL OPEN, AND SAY SO. Whatever is on screen stays — a remembered
        // rail is still the best answer available. But if nothing was
        // remembered the rail is currently drawing SKELETONS, and leaving it
        // that way would be a permanent shimmer where the navigation should be.
        // Marking it known lets the built-in list through: briefly out of date
        // beats unusable, which is the rule this file has always followed.
        setRailKnown(true);
      });

    return () => {
      cancelled = true;
    };
    // ⚠ `nav` IS STILL A DEPENDENCY, AND IT NO LONGER COSTS ANYTHING. This
    // effect used to run two REQUESTS on every single click in the rail - who
    // are you, what may you use - for two answers that had not changed since
    // sign-in, and that was a large part of "there is a delay whenever I move
    // around". The fix was never to stop re-checking; it was to stop re-asking.
    // `ensure` hands back the cached answer without touching the network until
    // it is a minute old, so what used to be a round trip per click is now at
    // most one per minute, per feed.
    //
    // Keeping `nav` is what preserves the thing that behaviour was BUYING: an
    // administrator turning a workflow on for this account shows up while they
    // are still using the app, rather than waiting for them to reload. It also
    // covers switching account while already on Home - though `authRun` covers
    // that explicitly, because a switch must re-read IMMEDIATELY and not on
    // whatever the staleness window says.
  }, [authed, nav, authRun]);

  // Stable identity: children list this in effect deps, so a fresh function on
  // every render would re-fire those effects (and, for the one that calls back
  // here, loop forever).
  const refreshJobs = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  function onAuthed(mail) {
    setEmail(mail);
    setAuthed(true);
    // ⚠ WHERE THEY WERE HEADED, AND ONLY THEN THE FRONT DOOR. See
    // `pendingWorkflow`. A workflow this account cannot have is not a problem
    // here — the branches below already answer "soon", "locked" and "hidden"
    // properly, which is a better first screen than silently ignoring the click.
    setNav(pendingWorkflow || LANDING_NAV);
    setPendingWorkflow(null);
  }

  /**
   * The sign-in gate. Every control on the public Explore page calls this.
   *
   * @param {string} [workflowId] what they clicked, if they clicked something
   *   specific. Sanitised, because some of these ids were typed into the admin
   *   panel — see `asWorkflow`.
   */
  function startSignIn(workflowId = null) {
    setPendingWorkflow(asWorkflow(workflowId));
    setAuthBack(authView === "explore" ? "explore" : "landing");
    setAuthView("login");
  }

  function logout() {
    api.clearSession();
    // ⚠ THE CACHE GOES WITH THE TOKEN. It holds this person's profile and
    // every list on their dashboard; leaving it in memory for whoever signs in
    // next on this browser is not a staleness bug, it is a leak.
    cache.reset();
    setAccounts(api.listAccounts());
    setAuthed(false);
    setEmail(null);
    setSelectedId(null);
    // Not for the person leaving — for the next one to sign in on this browser.
    setNav(LANDING_NAV);
    setPendingWorkflow(null);
    setAuthView("landing");
  }

  // ⚠ SWITCHING IS A FULL RESET, NOT A RE-LABEL. Every workflow holds the
  // PREVIOUS account's work in local state - a job id, an open board, a
  // half-filled form - and none of it belongs to the person now signed in. The
  // shell's own pointers are cleared and `navResetKey` remounts the page, which
  // is exactly what clicking the current workflow in the rail already does.
  function switchAccount(next) {
    if (!next || next === email) return;
    const now = api.switchAccount(next);
    if (!now) return;
    setEmail(now);
    setDisplayName("");
    // ⚠ CLEARED, NOT LEFT TO THE NEXT `me()`. Switching from an admin account
    // to an ordinary one would otherwise keep the Admin row on screen until
    // that request answers — and clicking it in that window lands on a panel
    // whose every call 404s.
    setIsAdmin(false);
    // ⚠ AND THE SAME FOR THE CAPABILITIES, for a sharper version of that
    // reason: the effect above re-runs on `nav`, and switching account while
    // already on Home does not change `nav` — so the previous account's answer
    // would sit there unrefreshed until they navigated. Cleared, it means "we
    // don't know yet", which is fail-open; kept, it is one customer's locks on
    // another customer's screen.
    clearEntitlements();
    // ⚠ AND THE RAIL FOLLOWS THE ACCOUNT. Two accounts do not have the same
    // workflows, so showing the previous one's rail until the new one answers
    // is the same flash, one customer over.
    const theirs = api.getRememberedEntitlements(now);
    setWorkflows(theirs?.workflows || WORKFLOWS);
    setEntitled(Boolean(theirs));
    setRailKnown(Boolean(theirs));
    // Same reason as logout: none of what is cached belongs to the account now
    // signed in. `prefetch` in the boot effect refills it for the new one, and
    // `authRun` is what makes that effect run - `authed` has not changed.
    cache.reset();
    cache.prefetch({ email: now });
    setAuthRun((k) => k + 1);
    setAccounts(api.listAccounts());
    setSelectedId(null);
    setPendingAnimaticId(null);
    setNav(LANDING_NAV);
    setNavResetKey((k) => k + 1);
  }

  // The sign-in dialog succeeded: `Login` has already written the new session,
  // so this only has to catch the app up. Same reset as a switch, for the same
  // reason - it IS a switch, to an account that was not held a moment ago.
  function accountAdded(mail) {
    setAddAccountOpen(false);
    setEmail(mail);
    setDisplayName("");
    setIsAdmin(false);
    // It IS a switch — same reason as `switchAccount`, same reset.
    //
    // ⚠ AND YET NO `cache.reset()` HERE, unlike the switch above. `Login` has
    // ALREADY started this account's prefetch, hint and all, before calling
    // back — and `prefetch` clears a cache belonging to a different account
    // itself. Resetting again from here would throw away the eight requests
    // that are in the air on this account's behalf and start them over, which
    // is the exact delay this whole change exists to remove.
    clearEntitlements();
    // Same as `switchAccount` above — it IS a switch, so the rail switches too.
    const theirs = api.getRememberedEntitlements(mail);
    setWorkflows(theirs?.workflows || WORKFLOWS);
    setEntitled(Boolean(theirs));
    setRailKnown(Boolean(theirs));
    setAuthRun((k) => k + 1);
    setAccounts(api.listAccounts());
    setSelectedId(null);
    setPendingAnimaticId(null);
    setNav(LANDING_NAV);
    setNavResetKey((k) => k + 1);
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
  //
  // ⚠ THREE OF THEM NOW, AND EXPLORE IS ONE. It is the marketing page — the
  // wall of work a stranger can actually watch — and it is reachable ONLY from
  // here. See the note on `LANDING_NAV` for why a signed-in customer never sees
  // it again.
  if (!authed) {
    if (authView === "login") {
      return (
        <Login
          onAuthed={onAuthed}
          /* Back goes where they came FROM, not always to the landing page.
             See `authBack`. */
          onBack={() => setAuthView(authBack)}
        />
      );
    }
    /* ⚠ THE SAME THEME SWITCH THE SIDEBAR AND THE ADMIN BAR CARRY, handed to
       both public screens: they are the ONLY screens a visitor can reach with
       no rail to flip it from. `applyTheme` stamps <html>, so the choice a
       prospect makes out here is still theirs after they sign in. */
    const toggleTheme = () => setTheme((t) => (t === "dark" ? "light" : "dark"));
    if (authView === "explore") {
      return (
        <Explore
          /* THE ONLY WAY OUT OF THAT PAGE that is not Back — a tile, a banner
             button, the create button, a card's viewer and the footer all call
             this, and every one of them names what it was selling. */
          onSignIn={startSignIn}
          onBack={() => setAuthView("landing")}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
      );
    }
    return (
      <Landing
        onGetStarted={() => startSignIn()}
        onExplore={() => setAuthView("explore")}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
    );
  }

  // ---- Main content by nav ----
  // ⚠ A LEFT-OVER "explore" IS HOME, AND THIS LINE IS FIRST BECAUSE EVERYTHING
  // BELOW READS IT. Nothing in the signed-in shell can reach that nav any more —
  // but `pendingWorkflow` carries a string from the PUBLIC page across the
  // sign-in, and a banner target an administrator typed is only shape-checked by
  // the server. `asWorkflow` already filters that; this is the second net,
  // because the cost of missing one is a blank first screen and the cost of this
  // line is nothing.
  const page = nav === "explore" ? "home" : nav;
  // The rail's own entry for wherever we are, so the branch below can ask
  // whether this workflow is merely a teaser. Looked up in the RESOLVED list,
  // not the fallback: the fallback is always "live", which is exactly the wrong
  // answer for something an administrator has just staged.
  const soonWorkflow = workflows.find((w) => w.id === page && w.status === "soon");
  // Visible but above this account's tier. ⚠ A DIFFERENT ANSWER FROM "soon":
  // "soon" is not for sale at any price, this one is one click from being
  // bought — so it gets the pricing modal, not a placeholder.
  const lockedWorkflow = workflows.find((w) => w.id === page && w.locked);
  // Switched off underneath somebody who was already standing on it — an admin
  // hiding a workflow while a customer has it open. The server refuses the work
  // either way; this stops the page rendering as though it were still there.
  const hiddenWorkflow =
    entitled &&
    WORKFLOWS.some((w) => w.id === page) &&
    !workflows.some((w) => w.id === page);
  let content;
  if (page === "home") {
    content = (
      <Home
        email={email}
        /* Which workflow groups Recent work may draw. ⚠ THE RAIL WAS FILTERED
           AND THE DASHBOARD WAS NOT: an administrator hid two workflows and
           they vanished from the sidebar while keeping their own column on
           Home — with a "View all →" that navigated to a room with no door.
           `null` means "nobody has told this browser yet", and Home fails OPEN
           on it, exactly like the rail does. */
        visibleWorkflows={railKnown ? workflows.map((w) => w.id) : null}
        onOpenJob={openJobInWorkflow}
        onUpgrade={() => setUpgradeOpen(true)}
        onOpenProfile={() => setNav("profile")}
        // "View all" on a workflow group jumps into that workflow.
        onNavigate={setNav}
      />
    );
    // ⚠ THERE IS NO `nav === "explore"` BRANCH ANY MORE, and its absence is
    // the change, not an omission. Explore is the logged-out marketing page and
    // is returned far above this line; a signed-in customer lands on Home and
    // the rail no longer carries a row for it. Anything that could still SET
    // this nav is caught by `page` below, which sends it here instead of
    // rendering nothing.
  } else if (page === "profile") {
    content = (
      <Profile
        email={email}
        onLogout={logout}
        /* "Manage accounts" in the switcher comes here, so the managing has to
           be here: the list, a switch, and a way to drop one. */
        accounts={accounts}
        onSwitchAccount={switchAccount}
        onAddAccount={() => setAddAccountOpen(true)}
        onForgetAccount={(mail) => {
          api.forgetAccount(mail);
          setAccounts(api.listAccounts());
          // Forgetting the LIVE account signs you out of it - `api` drops the
          // session with it, so the shell has to follow or it would sit on a
          // page it no longer has a token for.
          if (mail === email) logout();
        }}
      />
    );
  } else if (page === "admin") {
    /* ⚠ GUARDED TWICE, AND THE SECOND ONE IS THE REAL GUARD. `isAdmin` only
       stops the panel being DRAWN for somebody who reached this branch by
       switching to a non-admin account while sitting on it; the API behind
       every request in there answers 404 to a non-admin regardless. */
    content = isAdmin ? (
      <AdminPanel />
    ) : (
      /* ⚠ AND IT NEEDS ITS OWN WAY BACK NOW. In the app shell the rail was the
         way out of this card; in the admin shell there is no rail, and someone
         who followed a `?admin` link they cannot open would otherwise be sitting
         on a dead end with a top bar. */
      <div className="card placeholder">
        <p className="muted">That page isn't available on this account.</p>
        <button className="btn" onClick={() => setNav(LANDING_NAV)}>
          Go to the app
        </button>
      </div>
    );
  } else if (hiddenWorkflow) {
    content = (
      <div className="card placeholder">
        <p className="muted">
          That workflow isn't available on your account. Pick another from the
          sidebar.
        </p>
      </div>
    );
  } else if (lockedWorkflow) {
    content = (
      <div className="card placeholder upgrade-gate">
        <span className="upgrade-gate-ico">{lockedWorkflow.icon}</span>
        <h1 className="wf-title">{lockedWorkflow.label}</h1>
        <p className="muted">
          This workflow is part of a higher plan. Upgrade to unlock it — your
          existing work stays exactly where it is.
        </p>
        <button className="btn primary" onClick={() => setUpgradeOpen(true)}>
          See plans
        </button>
      </div>
    );
  } else if (soonWorkflow) {
    // ⚠ CHECKED BEFORE EVERY WORKFLOW BRANCH BELOW, so a workflow switched to
    // "soon" shows the placeholder rather than its real (working) page. Putting
    // this after them would make the switch do nothing at all.
    content = soonScreenFor(soonWorkflow);
  } else if (page === "plan-and-script") {
    // The pipeline handoff: a script written in Plan & Script is saved as the
    // user's script draft server-side, and Script to Storyboard loads that
    // draft on mount — so navigating there is the whole of the client's job.
    content = <PlanAndScript onOpenStoryboard={() => setNav("script-to-storyboard")} />;
  } else if (page === "text-to-image") {
    content = (
      <div className="workflow-head-wrap">
        <WorkflowHeader
          workflowId="text-to-image"
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
  } else if (page === "script-to-storyboard") {
    content = (
      <ScriptToStoryboard
        onOpenAnimatic={(id) => {
          setPendingAnimaticId(id);
          setNav("storyboard-to-animatics");
        }}
      />
    );
  } else if (page === "storyboard-to-animatics") {
    content = (
      <StoryboardToAnimatics
        openId={pendingAnimaticId}
        onOpened={() => setPendingAnimaticId(null)}
      />
    );
  } else if (page === "animatics-to-video") {
    content = (
      <AnimaticsToVideo />
    );
  } else if (page === "create-animatic-image") {
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

  // ---- The admin panel, in its own shell -------------------------------
  // ⚠ RETURNED BEFORE THE APP SHELL, NOT INSIDE IT. `content` above already
  // decided WHAT to draw here (the panel, or the refusal card for an account
  // that is not an administrator); this decides what it is drawn IN, and the
  // answer is deliberately not the customer's workflow rail. The modals below
  // do not come with it: "Add another account" and the pricing modal are both
  // about being a customer, and the panel has its own Pricing tab.
  if (page === "admin") {
    return (
      <AdminShell
        email={email}
        displayName={displayName}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        onExit={() => setNav(LANDING_NAV)}
        /* Your account is in the app, so this leaves the panel to get there —
           the same door, not a second copy of the profile page. */
        onOpenAccount={() => setNav("profile")}
        onLogout={logout}
      >
        {content}
      </AdminShell>
    );
  }

  return (
    <div className={`shell ${navCollapsed ? "nav-collapsed" : ""}`}>
      <Sidebar
        active={page}
        onNavigate={navigate}
        workflows={workflows}
        workflowsKnown={railKnown}
        email={email}
        displayName={displayName}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
        /* `onUpgrade` is the Upgrade button AND the menu's "Pricing and plan"
           — one modal, two ways in. */
        onUpgrade={() => setUpgradeOpen(true)}
        onOpenAccount={() => setNav("profile")}
        /* Omitted for everyone else, and the menu hides a row with no handler —
           so an ordinary account has no Admin entry at all rather than a
           greyed-out one advertising that the page exists. */
        onOpenAdmin={isAdmin ? () => setNav("admin") : undefined}
        onLogout={logout}
        accounts={accounts}
        onSwitchAccount={switchAccount}
        onAddAccount={() => setAddAccountOpen(true)}
        collapsed={navCollapsed}
        onToggleCollapse={() => setNavCollapsed((c) => !c)}
      />
      {/* Keyed by nav + reset counter: clicking the current workflow again
          changes the key, React remounts it, and it opens on its first page. */}
      <main className="shell-main" key={`${page}-${navResetKey}`}>
        {content}
      </main>

      {/* ⚠ THE ORDINARY SIGN-IN FORM, IN A MODAL - not a second login screen.
          `Login` already knows how to log in, register, show and hide the
          password and report a bad one; a copy of it here would be a copy of
          all four. Only the frame is different, and that is CSS. */}
      {addAccountOpen && (
        <div className="modal-overlay" onClick={() => setAddAccountOpen(false)}>
          <div className="add-account-modal" onClick={(e) => e.stopPropagation()}>
            <button
              className="modal-close"
              onClick={() => setAddAccountOpen(false)}
              aria-label="Close"
            >
              ✕
            </button>
            {/* ⚠ SIGNING IN HERE DOES NOT SIGN THE OTHER ACCOUNT OUT. Its token
                stays in the store, so the switcher lists both afterwards - which
                is the whole point of "add another". */}
            <p className="muted tiny add-account-note">
              Signing in here keeps {email} — you can switch back from this menu.
            </p>
            <Login onAuthed={accountAdded} />
          </div>
        </div>
      )}

      {upgradeOpen && (
        <PricingModal currentTier={tier} onClose={() => setUpgradeOpen(false)} />
      )}
    </div>
  );
}

// ⚠ `workflowId`, NOT AN EMOJI. Every screen in the app used to print its own
// glyph as text, which meant the same workflow wore a different face in the rail,
// on the landing page and at the top of its own page — and on Windows all three
// were drawn by the OS rather than by us. `WorkflowIcon` takes the nav id and
// draws the one glyph that workflow owns everywhere. `icon` still works for the
// handful of headers that are not a workflow at all (the admin shield).
function WorkflowHeader({ workflowId, icon, title, subtitle }) {
  return (
    <div className="workflow-header">
      <span className="wf-icon">
        {workflowId ? <WorkflowIcon id={workflowId} /> : icon}
      </span>
      <div>
        <h1 className="wf-title">{title}</h1>
        <p className="muted">{subtitle}</p>
      </div>
    </div>
  );
}
