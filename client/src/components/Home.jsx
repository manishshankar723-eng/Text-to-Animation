import { useEffect, useReducer, useRef, useState } from "react";
import * as api from "../api.js";
import * as cache from "../session_cache.js";
import Avatar from "./Avatar.jsx";
// ⚠ THE SAME CARD THE LANDING PAGE AND THE PRICING MODAL DRAW. A signed-in
// customer is the one most likely to actually spend a coupon, and before this
// the only way they could learn one existed was to open the Upgrade modal and
// notice it. See OfferCard.jsx.
import { OfferStrip } from "./OfferCard.jsx";
// ⚠ THE SAME THUMBNAIL THE LIBRARIES DRAW, not a second one that resembles
// it. `aspectStyle` is what stops a 9:16 project being shown as a slice out
// of its own middle, and `THUMB_EDGE` is what stops a 72px picture costing a
// 3.5 MB download — both are worth exactly as much here as they are there.
import { aspectStyle, formatBytes, THUMB_EDGE } from "./LibraryList.jsx";
import WorkflowIcon from "./WorkflowIcon.jsx";

// Home — the DASHBOARD: who you are, your plan, and the latest work from EVERY
// workflow. Anything you CHANGE (details, storyboard defaults, 3D keys,
// password, delete account) lives on the Profile page; Home links to it.
//
// "Recent work" used to list character jobs only, which made the other three
// workflows invisible from the front page. It now shows the newest couple of
// items per workflow with a "View all" into that workflow, so the dashboard
// answers "what am I working on?" rather than "what did Text-to-Image do?".
//
// ⚠ THIS SCREEN NO LONGER FETCHES ANYTHING ON MOUNT, and that is the point.
// It used to own a `load()` that fired seven requests from a `useEffect` — so
// nothing was even ASKED FOR until the dashboard had been drawn, and the first
// thing a customer with a library saw was their own work replaced by the word
// "Loading…". Worse, it ran again every time they came back to Home.
//
// The requests now start at sign-in, from `Login`, before this component
// exists — see session_cache.js. Home READS that cache, synchronously, so its
// very first render normally has the real content in it. What is left here is
// the two things a dashboard should do when it genuinely has nothing yet:
//
//   - an account we KNOW is new (the server counted at login) gets its real
//     empty state immediately, with no loader of any kind;
//   - anyone else gets skeletons shaped like the rows that are coming, in the
//     same shimmer the storyboard library already uses.

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

// How many ghost rows a group shows while it waits. Matches PER_WORKFLOW so the
// skeleton is the same height as the thing replacing it and nothing jumps.
const GHOST_ROWS = PER_WORKFLOW;

/**
 * Fetch each row's cover picture once, as an authed object URL.
 *
 * ⚠ THIS IS NOT THE THING THE MODULE HEADER FORBIDS. What that note is about is
 * the seven LIST requests this screen used to fire on mount, which blanked the
 * dashboard until they answered. These are pictures for content that is already
 * on screen: nothing waits for them, a failure leaves the workflow's own icon in
 * place, and they are asked for at `THUMB_EDGE` rather than full size.
 *
 * ⚠ `asked` IS WHAT MAKES IT ONCE. `items` is a fresh array on every render, so
 * the effect re-runs constantly; the set is what stops a re-render becoming a
 * re-fetch.
 *
 * ⚠ AND THE REVOKE IS TIED TO UNMOUNT, NOT TO THE EFFECT RE-RUNNING. Revoking
 * on every cleanup would throw away the URL of a fetch that started one render
 * ago — and because `asked` already holds its key, nothing would ever ask for
 * that picture again and the row would keep the placeholder for ever.
 *
 * ⚠ AND THE UNMOUNT PATH HAS TO UNDO **ALL THREE** PIECES OF STATE, BECAUSE OF
 * `React.StrictMode` (main.jsx). In development it mounts, tears down, and
 * mounts again — with the component's state kept. The first version of this
 * hook only ever set its `live` flag to FALSE on the way out and never back to
 * true, so on the second, real mount:
 *
 *   · `live` was still false, so every picture that arrived was revoked and
 *     thrown away, and
 *   · `asked` still held every key, so nothing was ever requested again.
 *
 * Result: not one cover ever appeared on the dashboard — reported as "see not
 * view image". So the setup RE-ARMS `live`, and the cleanup forgets what was
 * asked for AND clears `urls`: state survives a StrictMode remount, so leaving
 * the map in place would leave `<img src>` pointing at blobs that were just
 * revoked, which is a broken picture rather than a missing one.
 *
 * The cost in dev is that each picture is fetched twice. That is what
 * StrictMode is FOR — it is the same double-fetch it forces on every other
 * effect in the app, and it does not happen in a production build.
 */
function useCovers(items) {
  const [urls, setUrls] = useState({});
  const made = useRef([]);
  const asked = useRef(new Set());
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      made.current.forEach((u) => URL.revokeObjectURL(u));
      made.current = [];
      asked.current = new Set();
      setUrls({});
    };
  }, []);

  useEffect(() => {
    for (const it of items) {
      if (!it.loadCover || asked.current.has(it.key)) continue;
      asked.current.add(it.key);
      it.loadCover()
        .then((url) => {
          if (!live.current) {
            URL.revokeObjectURL(url);
            return;
          }
          made.current.push(url);
          setUrls((m) => ({ ...m, [it.key]: url }));
        })
        .catch(() => {}); // a missing cover just leaves the workflow's icon
    }
  }, [items]);

  return urls;
}

// `/jobs` answers with a bare array; one older shape wrapped it. Normalised in
// one place rather than at each use.
function asList(value) {
  if (Array.isArray(value)) return value;
  if (value && Array.isArray(value.jobs)) return value.jobs;
  return [];
}

/**
 * The number to print in "View all (N)", or `null` for "we can't say".
 *
 * ⚠ THE DASHBOARD ONLY FETCHES A PAGE NOW (`DASH_LIMIT`), so the length of the
 * list it holds is NOT the size of the library — and quietly printing it would
 * turn "View all (40)" into "View all (8)" for the busiest accounts, which is
 * the sort of wrong number nobody reports and everybody half-notices.
 *
 * Two ways to be sure, and if neither applies we print no number at all:
 *   - the page came back SHORT of the limit, so the page is everything;
 *   - the login hint counts these job kinds exactly (see TokenResponse.counts).
 *
 * `kinds` is empty for the two storyboard groups on purpose: both are made of
 * `storyboard` records and the hint cannot tell an original from a copy, so a
 * full page of either is honestly unknown.
 */
function totalFor(list, kinds) {
  if (list.length < cache.DASH_LIMIT) return list.length;
  const counts = cache.hint();
  if (counts && kinds.length) {
    const n = kinds.reduce((sum, k) => sum + (Number(counts[k]) || 0), 0);
    if (n >= list.length) return n;
  }
  return null;
}

/**
 * Subscribe to the session cache and re-render whenever it changes.
 *
 * ⚠ IT READS SYNCHRONOUSLY AND ONLY THEN ASKS FOR A REFRESH. That order is what
 * removes the loader: by the time this runs the prefetch started at sign-in has
 * usually landed, so the first paint is real content and the refresh is a
 * silent top-up behind it. Nothing here can start a duplicate request — the
 * cache joins whatever is already in flight.
 *
 * ⚠ AND IT IS `refresh`, NOT `ensure` — the lists are re-read on EVERY mount,
 * staleness window ignored. This screen has no router: leaving Home unmounts it
 * and coming back mounts it again, and the commonest reason to come back is
 * that you just made something. A cache that answered "still fresh, I read this
 * forty seconds ago" would show a customer a dashboard with their new project
 * missing from it — which is a worse bug than the slowness this all started as.
 *
 * It costs what it should: five small requests that nobody waits for, because
 * what is already cached stays on screen throughout. That is the difference
 * from the version this replaced, which made the same requests and BLANKED THE
 * PAGE until they answered.
 */
function useDashboard() {
  const [, bump] = useReducer((n) => n + 1, 0);
  useEffect(() => cache.subscribe(bump), []);
  useEffect(() => {
    // `me` / `entitlements` are deliberately not in here — the shell owns those
    // and they do not change while you are signed in.
    cache.refresh(cache.LIST_KEYS);
  }, []);
}

export default function Home({
  email,
  // The ids of the workflows this account may SEE, or `null` while nobody has
  // said. See the note where App passes it.
  visibleWorkflows = null,
  onOpenJob,
  onUpgrade,
  onOpenProfile,
  onNavigate,
  // NO `onResumeStoryboard` HERE ANY MORE. The unfinished storyboard used
  // to get its own strip on this page. It now lives on the Storyboards
  // page, directly above "Recent Storyboards" -- asked for that way:
  // *"maine recent kyun banaya hai jab yahan pe mera resume dikh hi nahi
  // raha ... home page se bhi hatao, bas ek jagah."* One home for it, and
  // it is the page that lists the thing it will become. See
  // StoryboardLibrary.jsx.
}) {
  useDashboard();

  // Every one of these is a synchronous read of an answer that, on the ordinary
  // path, arrived while this component was still being mounted.
  const profile = cache.read("me") || null;
  const jobs = asList(cache.read("jobs"));
  const boards = asList(cache.read("boards"));
  // Image to Animatic Image's own copies — a different set from `boards`.
  const copiedBoards = asList(cache.read("copiedBoards"));
  const animatics = asList(cache.read("animatics"));
  const videos = asList(cache.read("videos"));
  const plans = asList(cache.read("plans"));

  // ⚠ "NOTHING HAS ARRIVED YET" — NOT "A REQUEST IS RUNNING". A background
  // refresh of a dashboard that is already on screen is not a loading state and
  // must not be drawn as one; that was the old behaviour and it made every
  // return to Home look like a fresh, empty start.
  const waiting = !cache.LIST_KEYS.every((k) => cache.hasLanded(k));
  // A request is genuinely in the air. Distinct from `waiting`: this is true
  // during the top-up on every mount as well as when the button is pressed, and
  // it drives NOTHING but the button's own label — the content stays put.
  const refreshing = cache.LIST_KEYS.some((k) => cache.isPending(k));

  // ⚠ AND A NEW ACCOUNT NEVER WAITS. The server counted this account's work
  // when it handed out the token; if the answer was "none", there is nothing to
  // wait for and the honest empty dashboard can be drawn on the first frame.
  // `isNewAccount()` is false when we simply have no hint, so the old
  // behaviour — skeletons until the lists land — is what happens by default.
  const showGhosts = waiting && !cache.isNewAccount();

  // Every list keeps its last good value through a failed refresh, so an error
  // here is only worth showing when it left us with nothing to show instead.
  const loadError = waiting
    ? cache.LIST_KEYS.map((k) => cache.errorOf(k)).find(Boolean) || ""
    : "";
  // Something the user PRESSED went wrong - today only the asset ZIP. Kept
  // apart from `loadError` because the two need opposite handling: a failed
  // download is worth saying out loud and then forgetting, while a failed load
  // is a state the screen is currently IN.
  const [actionError, setActionError] = useState("");
  const error = actionError || loadError;

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
      total: totalFor(plans, ["plan"]),
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
      // Both character kinds land in this one list — see CHARACTER_JOB_KINDS.
      total: totalFor(jobs, ["generate", "meshy"]),
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
                  .catch((e) => setActionError(e.message))
            : null
      }))
    },
    {
      id: "script-to-storyboard",
      icon: "📝",
      label: "Script to Storyboard",
      // No kinds: originals and copies are both `storyboard` records, so the
      // hint cannot separate them. See totalFor.
      total: totalFor(boards, []),
      items: boards.map((b) => ({
        key: b.job_id,
        title: b.title || "Storyboard",
        status: b.status,
        meta: b.panel_count ? `${b.panel_count} panels` : "",
        aspect: b.aspect_ratio,
        size: b.size_bytes,
        loadCover:
          b.cover_index === null || b.cover_index === undefined
            ? null
            : () =>
                api.fetchStoryboardPanel(
                  b.job_id,
                  b.cover_index,
                  b.cover_url,
                  THUMB_EDGE
                ),
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
      total: totalFor(copiedBoards, []),
      items: copiedBoards.map((b) => ({
        key: b.job_id,
        title: b.title || "Storyboard",
        status: b.status,
        meta: b.panel_count ? `${b.panel_count} panels` : "",
        aspect: b.aspect_ratio,
        size: b.size_bytes,
        loadCover:
          b.cover_index === null || b.cover_index === undefined
            ? null
            : () =>
                api.fetchStoryboardPanel(
                  b.job_id,
                  b.cover_index,
                  b.cover_url,
                  THUMB_EDGE
                ),
        date: b.updated_at || b.created_at
      }))
    },
    {
      id: "animatics-to-video",
      icon: "🎞️",
      label: "Image to AI Video",
      total: totalFor(videos, ["final_video"]),
      items: videos.map((v) => ({
        key: v.job_id,
        title: v.title || "Final video",
        status: v.status,
        // How much is DONE, not just how much is in it — this is the only
        // workflow where the remainder costs money to finish.
        meta: v.shot_count
          ? `${v.rendered_count}/${v.shot_count} rendered`
          : "",
        aspect: v.aspect_ratio,
        size: v.size_bytes,
        loadCover: v.cover_url
          ? () => api.fetchFinalVideoMedia(v.cover_url, THUMB_EDGE)
          : null,
        date: v.updated_at || v.created_at
      }))
    },
    {
      id: "storyboard-to-animatics",
      icon: "🎬",
      label: "Video Editor",
      total: totalFor(animatics, ["animatic"]),
      items: animatics.map((a) => ({
        key: a.job_id,
        title: a.title || "Project",
        status: a.status,
        // Same shape of hint as the others: how much is in it.
        meta: a.frame_count ? `${a.frame_count} frames` : "",
        aspect: a.aspect_ratio,
        size: a.size_bytes,
        loadCover: a.cover_url
          ? () => api.fetchAnimaticMedia(a.cover_url, THUMB_EDGE)
          : null,
        date: a.updated_at || a.created_at
      }))
    }
  ];

  // ⚠ THE HINT WINS HERE, because it is the only EXACT number available: it is
  // a count of every record this account owns, made by the database at sign-in.
  // Adding up what is on screen counts a PAGE of each workflow, and did so long
  // before this change — the old dashboard capped every list at 100 and called
  // the sum "Projects" too. Falls back to the visible sum when there is no hint.
  const groupTotal = groups.reduce((n, g) => n + g.items.length, 0);

  // ⚠ WHAT IS DRAWN, AND ONLY WHAT IS DRAWN. `groupTotal` above deliberately
  // still counts every group: it is the fallback for "how many projects does
  // this account have", the server's own hint counts every record too, and the
  // two answers disagreeing by a hidden workflow would be worse than either.
  // Hiding is about the SHELF, not about the count.
  //
  // Fails open on `null` — same rule as the rail: not knowing yet must never
  // read as "this account has nothing".
  const shownGroups = visibleWorkflows
    ? groups.filter((g) => visibleWorkflows.includes(g.id))
    : groups;

  // Exactly the rows on screen — a hidden workflow's pictures are not worth
  // fetching, and neither is the third item of a group that shows two.
  const drawnItems = shownGroups.flatMap((g) => g.items.slice(0, PER_WORKFLOW));
  const covers = useCovers(drawnItems);
  const hinted = cache.hintTotal();
  const totalItems = hinted === null ? groupTotal : hinted;

  return (
    <div className="home">
      <header className="home-head">
        <h1>Welcome back 👋</h1>
        <p className="muted">
          Your profile, your plan, and where you left off.
        </p>
      </header>

      {error && <div className="error">{error}</div>}

      {/* A live discount, if there is one — above the fold, because a coupon
          nobody is shown is a coupon nobody spends. ⚠ THE BUTTON OPENS THE
          PRICING MODAL RATHER THAN APPLYING THE CODE HERE: applying it needs the
          plan and the billing period, and both of those are questions this
          screen does not ask. Renders nothing when no offer is running. */}
      <OfferStrip
        className="home-offers"
        ctaLabel="View plans →"
        onCta={onUpgrade}
      />

      {/* Top row: two equal-height cards. */}
      <div className="home-grid">
        {/* ⚠ BOTH CARDS' ACTION SITS IN THE TOP ROW, NOT IN A FOOT. Pinned to
            the bottom of a stretched card, the button left a hand's width of
            empty panel under the content — the shorter card was mostly air.
            Opposite the thing it acts on (the name here, the plan badge next
            door) it reads as "edit THIS", and the pair keeps one silhouette. */}
        <section className="card home-card profile-card">
          <div className="profile-top">
            <Avatar size={56} initial={initial === "?" ? "" : initial} />
            <div className="profile-who">
              <h2 className="profile-email">{displayName}</h2>
              <p className="muted tiny">{email}</p>
              <p className="muted tiny">Member since {memberSince}</p>
            </div>
            {onOpenProfile && (
              <button className="btn small card-top-btn" onClick={onOpenProfile}>
                Edit profile
              </button>
            )}
          </div>
        </section>

        <section className="card home-card plan-card">
          <div className="plan-head">
            <span className="plan-badge">Free plan</span>
            <button
              className="btn small card-top-btn upgrade-inline"
              onClick={onUpgrade}
            >
              ⚡ Upgrade plan
            </button>
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
          <p className="muted tiny plan-note">
            Usage-based billing &amp; credits are coming soon.
          </p>
        </section>
      </div>

      {/* Recent work, one group per workflow. */}
      <section className="card home-card recent-card">
        <div className="recent-head">
          <h2>Recent work</h2>
          <button
            className="btn ghost small"
            onClick={() => cache.refresh(cache.LIST_KEYS)}
            disabled={refreshing}
          >
            ↻ {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>

        <div className="wf-grid">
          {shownGroups.map((g) => (
            <div className="wf-group" key={g.id}>
              <div className="wf-group-head">
                <span className="wf-group-title">
                  <span className="wf-group-ico">
                    <WorkflowIcon id={g.id} fallback={g.icon} />
                  </span>
                  {g.label}
                </span>
                <button
                  className="btn ghost small"
                  onClick={() => onNavigate?.(g.id)}
                  title={`Open ${g.label}`}
                >
                  {/* No number rather than a wrong one — see totalFor. */}
                  View all{g.total ? ` (${g.total})` : ""} →
                </button>
              </div>

              {showGhosts ? (
                /* Shimmering rows the shape of the ones that are coming, in the
                   same `is-loading` idiom the storyboard library uses. Hidden
                   from assistive tech: it is a placeholder, not content. */
                <ul className="wf-list wf-ghosts is-loading" aria-hidden="true">
                  {Array.from({ length: GHOST_ROWS }, (_, i) => (
                    <li className="wf-item wf-ghost" key={i}>
                      <span className="lib-thumb">
                        <span className="lib-thumb-pic lib-ghost-cover" />
                      </span>
                      <span className="wf-open">
                        <span className="wf-ghost-line wf-ghost-name" />
                        <span className="wf-ghost-line wf-ghost-meta" />
                      </span>
                      <span className="wf-ghost-chip" />
                    </li>
                  ))}
                </ul>
              ) : g.items.length === 0 ? (
                <button className="wf-empty" onClick={() => onNavigate?.(g.id)}>
                  Nothing yet — start your first
                </button>
              ) : (
                <ul className="wf-list">
                  {g.items.slice(0, PER_WORKFLOW).map((it) => (
                    <li key={it.key} className="wf-item">
                      {/* The library's thumbnail, class for class: the slot is
                          fixed so the names line up, the picture inside takes
                          the project's own aspect so a 9:16 one isn't cropped.
                          A workflow with no picture to show (a plan, a
                          character run) falls back to its own icon rather than
                          to an empty grey box. */}
                      <button
                        type="button"
                        className="lib-thumb"
                        onClick={() =>
                          it.onOpen ? it.onOpen() : onNavigate?.(g.id)
                        }
                        title={`Open ${it.title}`}
                      >
                        <span
                          className="lib-thumb-pic"
                          style={aspectStyle(it.aspect)}
                        >
                          {covers[it.key] ? (
                            <img src={covers[it.key]} alt="" />
                          ) : (
                            <WorkflowIcon id={g.id} fallback={g.icon} />
                          )}
                        </span>
                      </button>
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
                          {/* Only where there is one — a workflow with no files
                              of its own would otherwise print an em dash here
                              and look like it had lost something. */}
                          {it.size > 0 && (
                            <span className="muted tiny">
                              {formatBytes(it.size)}
                            </span>
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

      {/* ⚠ THE "ACCOUNT" CARD THAT USED TO SIT HERE IS GONE ON PURPOSE, AND IT
          IS NOT LOST — every single thing it offered is already somewhere the
          user looks first. "Open profile" is the button on the profile card at
          the top of this very page; the sidebar's account menu opens the same
          profile and is where Log out actually lives; and the sentence about
          details / storyboard defaults / 3D keys / password was a description
          of the Profile page written on a different page. A whole card whose
          only content is a second copy of a button eight inches above it is a
          card that teaches people to scroll past the bottom of the dashboard.
          ("remove Account panel from home page not need to show here already
          show many place".) `onOpenProfile` is still a prop — the profile
          card's own button is the one that uses it. */}
    </div>
  );
}
