import { useEffect, useMemo, useState } from "react";
import * as api from "../api.js";
import {
  buildGroups,
  formatDate,
  statusClass,
  useCovers,
  useDashboard
} from "../dashboard_feed.js";
import * as cache from "../session_cache.js";
// ⚠ THE SAME THUMBNAIL MATHS THE LIBRARIES AND THE DASHBOARD USE. `aspectStyle`
// is what stops a 9:16 project being shown as a slice out of its own middle,
// and `matchesFilter` is the same "contains" the storyboard library's filter box
// already runs — a second, slightly different search would be a second answer.
import { aspectStyle, formatBytes, matchesFilter } from "./LibraryList.jsx";
// ⚠ THE SALES COPY IS THE LANDING PAGE'S, NOT A SECOND SET. Every line of "what
// is this workflow for" on this page comes from there, so a workflow that is
// re-pitched is re-pitched once. See the note above `COPY` in Landing.jsx.
import { COPY } from "./Landing.jsx";
// The brand slide has no workflow to draw, so it draws the mark itself —
// the same one the rail and the favicon carry. See Logo.jsx.
import Logo from "./Logo.jsx";
import useBranding from "../useBranding.js";
// The discount that comes to the customer instead of waiting to be found. It
// fetches its own offer and draws nothing at all when there isn't one.
import PromoPopup from "./PromoPopup.jsx";
// ⚠ THE RAIL'S OWN SHORT NAMES, so a workflow is called one thing in the
// narrow sidebar and the same thing on this page's chips and cards. See
// `WORKFLOW_SHORT` in Sidebar.jsx for why they are not `COPY[id].short`.
import { shortLabel } from "./Sidebar.jsx";
import WorkflowIcon from "./WorkflowIcon.jsx";

// Explore — the DISCOVERY page: what you can make, and what you have made.
//
// ⚠ HOW THIS IS DIFFERENT FROM HOME, because two dashboards is one too many
// unless they answer different questions. Home answers "where did I leave off"
// — your name, your plan, two rows per workflow. Explore answers "what can this
// studio do, and what does my work look like" — banners, a tile per workflow,
// and every project this account owns as one picture wall.
//
// ⚠ AND THE WALL IS YOUR OWN WORK, NOT A COMMUNITY FEED. The reference this was
// built from (Kling's Explore) fills its grid with strangers' videos; this app
// has no public gallery, nothing is shared by default, and inventing one would
// mean publishing customers' storyboards. So the grid is the account's own
// library, laid out the way a gallery is laid out. If a public feed is ever
// built, it becomes a fourth tab here — the layout already has room.
//
// ⚠ IT FETCHES NOTHING OF ITS OWN. Same rule as Home: every list is read
// synchronously out of `session_cache`, which was filled at sign-in. See
// `dashboard_feed.js`.

// How long each banner stays up. Six seconds is long enough to read two lines
// and short enough that a visitor sees more than one without waiting.
const SLIDE_MS = 6000;

// How many banners the left carousel carries: the brand slide plus this many
// workflows. ⚠ MORE THAN FOUR DOTS STOPS READING AS "there is more" and starts
// reading as a progress bar for something you did not ask to sit through.
const HERO_WORKFLOWS = 3;

// The banner on the right is a fixed billboard, not part of the rotation. This
// workflow is the one it advertises when the account can see it; otherwise the
// last workflow the account CAN see, so the slot is never empty and never
// points at a room with no door.
const SIDE_PREFERRED = "storyboard-to-animatics";

// What "＋" on the toolbar starts, when this account may see it.
const CREATE_PREFERRED = "script-to-storyboard";

// ---------------------------------------------------------------------------
// THE WALL'S SHAPE. Two numbers, and between them they are the whole fix for
// "the tall board leaves a hole beside it".
//
// ⚠ WHAT WAS ACTUALLY WRONG, MEASURED: with seven projects the five columns
// ended at 483, 483, 323, 323 and 154 pixels — the last one two thirds empty.
// It was NOT that the columns were badly packed; CSS multi-column balances by
// height and shortest-column-first packing gives the same answer. The cause is
// the SPREAD: a 9:16 board is 3.2× the height of a 16:9 one in the same column,
// so two of them tower over everything and nothing can fill the gap they leave.
//
// ⚠ AND FEWER COLUMNS DOES NOT FIX IT, WHICH IS THE TRAP. Fewer columns are
// WIDER columns, and a 9:16 card in a wider column is taller still — at three
// columns the tall card grows from 466px to 843px and the hole gets bigger.
// ---------------------------------------------------------------------------

// The tallest and shortest a card may be drawn, as width ÷ height.
//
// ⚠ THIS IS THE ONE PLACE ON THIS SCREEN THAT CROPS, and it is a deliberate
// exception to the rule the libraries keep. `aspectStyle` exists so a 9:16
// project is never "shown as a slice out of its own middle" — but that rule is
// about a THUMBNAIL you identify a project by, in a list, at 86px. This is a
// picture wall, the caption underneath says which project it is, and clicking
// opens the real thing. A 9:16 board lands at 4:5 here, which trims about 30%
// of its height, evenly, top and bottom.
const WALL_AR_MIN = 0.8; // 4:5 — the tallest a card gets
const WALL_AR_MAX = 16 / 9; // the widest

/**
 * A project's own ratio, pulled into the range the wall can lay out.
 *
 * Returns the same `--lib-thumb-ar` custom property `aspectStyle` does, so the
 * card's CSS does not have to know which of the two it was handed.
 */
function wallAspect(aspect) {
  const style = aspectStyle(aspect);
  if (!style) return undefined;
  const [w, h] = String(style["--lib-thumb-ar"]).split("/").map(Number);
  if (!(w > 0) || !(h > 0)) return style;
  const ratio = Math.min(WALL_AR_MAX, Math.max(WALL_AR_MIN, w / h));
  return { "--lib-thumb-ar": `${ratio} / 1` };
}

// How many columns, given how much there is to put in them.
//
// ⚠ THIS IS A CEILING, NOT A COUNT — `columns: N 14rem` means "at most N, each
// at least 14rem", so a narrow window still uses fewer. It exists because five
// columns holding one card each is not a wall, it is a row with gaps: with
// seven projects the fifth column got a single card and ended less than a third
// of the way down. Two per column is the floor at which masonry starts looking
// like masonry.
const WALL_MAX_COLS = 5;

function wallColumns(count) {
  return Math.max(2, Math.min(WALL_MAX_COLS, Math.ceil(count / 2)));
}

// The three ways of looking at the wall. `id` is state, `label` is the tab.
//
// ⚠ "HIGHLIGHTS" IS AN ORDERING, NOT A JUDGEMENT. It puts the projects that
// HAVE a picture first, because a gallery whose first row is six grey
// placeholders is not a gallery — a plan and a character run have nothing to
// show yet and belong further down, not nowhere. Inside each half it is still
// newest-first, so it can never hide today's work behind last month's.
const VIEWS = [
  { id: "highlights", label: "Highlights" },
  { id: "recent", label: "Recent" },
  { id: "active", label: "In progress" }
];

// The OS draws emoji, so a rotating banner would be four different type
// designers' work — see the header of WorkflowIcon.jsx. Nothing here is an
// emoji; every glyph on this page is that component.
function prefersReducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    // Ancient browser / no matchMedia. Motion is the default everywhere else.
    return false;
  }
}

/**
 * The rotating billboard on the left.
 *
 * ⚠ IT STOPS WHEN YOU ARE READING IT. Hover or keyboard focus pauses the timer,
 * because a banner that slides away mid-sentence — or worse, mid-click, moving
 * the button out from under the pointer — is the one thing a carousel must not
 * do. `prefers-reduced-motion` switches the timer off altogether; the arrows and
 * dots still work, so nothing is unreachable, it just never moves on its own.
 */
/**
 * A banner's picture — an administrator's, or the built-in glyph.
 *
 * ⚠ A REAL PHOTOGRAPH COVERS THE WHOLE CARD AND A GLYPH DOES NOT, and that is
 * why they are not the same element. The glyph is decoration bled off one
 * corner at 12% opacity; an uploaded picture is the point of the card, so it
 * fills it and the words move onto a scrim. `.has-photo` on the banner is what
 * flips the text to light in BOTH themes — see explore.css.
 */
function BannerArt({ slide }) {
  if (slide.image) {
    return <img className="xp-banner-photo" src={slide.image} alt="" />;
  }
  return (
    <span className="xp-banner-art" aria-hidden="true">
      {slide.workflow ? <WorkflowIcon id={slide.workflow} /> : <Logo />}
    </span>
  );
}

function HeroCarousel({ slides }) {
  const [at, setAt] = useState(0);
  const [held, setHeld] = useState(false);
  const still = prefersReducedMotion();

  // ⚠ CLAMPED ON EVERY RENDER RATHER THAN RESET IN AN EFFECT. `slides` is built
  // from the entitlements answer, so it can SHRINK under this component when an
  // administrator hides a workflow mid-session — and an index left pointing past
  // the end would render nothing at all.
  const count = slides.length;
  const idx = count ? Math.min(at, count - 1) : 0;

  useEffect(() => {
    if (still || held || count < 2) return undefined;
    const t = setInterval(() => setAt((n) => (n + 1) % count), SLIDE_MS);
    return () => clearInterval(t);
  }, [still, held, count]);

  if (!count) return null;
  const slide = slides[idx];
  const step = (d) => setAt((n) => (((n + d) % count) + count) % count);

  return (
    <div
      className={`xp-banner xp-hero tone-${slide.tone} ${
        slide.image ? "has-photo" : ""
      }`}
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={() => setHeld(false)}
      role="region"
      aria-label="Highlights"
    >
      <BannerArt slide={slide} />

      <div className="xp-banner-body">
        {/* An administrator may leave either of these empty; a generated slide
            always has both. An empty <span> would still take its line height
            and push the heading down on one card out of four. */}
        {slide.eyebrow && (
          <span className="xp-banner-eyebrow">{slide.eyebrow}</span>
        )}
        <h2 className="xp-banner-title">{slide.title}</h2>
        {slide.sub && <p className="xp-banner-sub">{slide.sub}</p>}
        {slide.go && slide.cta && (
          <button
            type="button"
            className="btn primary xp-banner-cta"
            onClick={slide.go}
            title={slide.hint}
          >
            {slide.cta} →
          </button>
        )}
      </div>

      {count > 1 && (
        <>
          {/* ⚠ ARROWS AS WELL AS DOTS. A dot is a destination, not a direction:
              at four slides the dots are 8px targets, and "the next one" is the
              thing people actually want. Both are keyboard reachable. */}
          <button
            type="button"
            className="xp-hero-arrow prev"
            onClick={() => step(-1)}
            aria-label="Previous highlight"
            title="Previous"
          >
            ‹
          </button>
          <button
            type="button"
            className="xp-hero-arrow next"
            onClick={() => step(1)}
            aria-label="Next highlight"
            title="Next"
          >
            ›
          </button>
          <div className="xp-hero-dots">
            {slides.map((s, i) => (
              <button
                key={s.key}
                type="button"
                className={`xp-hero-dot ${i === idx ? "active" : ""}`}
                onClick={() => setAt(i)}
                aria-label={`Highlight ${i + 1} of ${count}`}
                aria-current={i === idx}
                title={s.title}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * @param {object[]} workflows — the resolved rail: `[{id, label, icon, status,
 *   locked}]`, already filtered to what this account may SEE. Same array the
 *   sidebar draws.
 * @param {boolean} workflowsKnown — false while nobody has told this browser
 *   what the account may see. ⚠ FAILS OPEN, exactly like the rail and the
 *   dashboard do: "not answered yet" must never read as "you have nothing".
 */
export default function Explore({
  workflows = [],
  workflowsKnown = true,
  onNavigate,
  onOpenJob,
  // The pricing modal. The offer card's button is the only thing that uses it —
  // without a handler no button is drawn, which is right: a promotion whose
  // action does nothing is worse than one that only states the code.
  onUpgrade
}) {
  useDashboard();

  // The first hero slide is the PRODUCT rather than a workflow, so it is the one
  // place on this page that prints the app's own name.
  const brand = useBranding();

  // ⚠ THE ADMIN PANEL'S BILLBOARDS, AND AN EMPTY ANSWER IS THE NORMAL ONE. With
  // no banner set for a slot this stays `[]` and the generated card below is
  // what gets drawn — exactly what this page did before the panel could speak.
  // Fails closed like every other decorative fetch here: an unreadable endpoint
  // is the same as an empty one.
  const [madeBanners, setMadeBanners] = useState({ hero: [], side: [] });
  useEffect(() => {
    let cancelled = false;
    api
      .publicBanners()
      .then((r) => {
        if (cancelled) return;
        setMadeBanners({ hero: r?.hero || [], side: r?.side || [] });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const [view, setView] = useState(VIEWS[0].id);
  const [chip, setChip] = useState("all");
  const [query, setQuery] = useState("");

  // ⚠ "NOTHING HAS ARRIVED YET" — NOT "A REQUEST IS RUNNING". Same distinction
  // Home makes: a background top-up behind a wall that is already on screen is
  // not a loading state and must not be drawn as one.
  const waiting = !cache.LIST_KEYS.every((k) => cache.hasLanded(k));
  // A brand-new account never waits — the server counted its work when it
  // handed out the token, so the honest empty wall can be drawn on frame one.
  const showGhosts = waiting && !cache.isNewAccount();

  const groups = buildGroups({ onOpenJob });
  const allowed = workflowsKnown ? workflows.map((w) => w.id) : null;
  const shown = allowed
    ? groups.filter((g) => allowed.includes(g.id))
    : groups;

  // One flat wall out of six groups. Every card remembers which workflow it
  // came from, because that is what the chips filter on and what a click opens.
  //
  // ⚠ `useMemo` IS LOAD-BEARING HERE, not a micro-optimisation. `useCovers`
  // re-runs its effect whenever the array identity changes, and a fresh array
  // every render would re-enter that effect on every keystroke in the search
  // box. It still only FETCHES once (see `asked` in that hook) — but the memo
  // is what keeps the wall from churning while you type.
  //
  // ⚠ AND THE DEPENDENCY IS A SIGNATURE, NOT THE ARRAY. `buildGroups` reads the
  // cache and returns fresh objects on every render, so the array itself is
  // never equal to last render's; what actually changed is WHICH projects are
  // in it, and that is what this string says.
  const wallKey = shown
    .map((g) => `${g.id}:${g.items.map((i) => i.key).join(",")}`)
    .join("|");
  const wall = useMemo(() => {
    const out = [];
    for (const g of shown) {
      for (const it of g.items) {
        out.push({
          ...it,
          group: g.id,
          label: g.label,
          icon: g.icon,
          short: shortLabel(g.id, g.label)
        });
      }
    }
    // Newest first, always — the two views below only ever re-group this.
    out.sort((a, b) => new Date(b.date || 0) - new Date(a.date || 0));
    return out;
  }, [wallKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const filtered = useMemo(() => {
    let list = wall;
    if (chip !== "all") list = list.filter((it) => it.group === chip);
    if (query.trim()) {
      list = list.filter((it) => matchesFilter(query, it.title, it.label));
    }
    if (view === "active") {
      // Anything that is not finished: running, queued, failed. A group whose
      // records carry no status at all (plans) never lands here, which is
      // right — a plan is a document, not a job that can be half-done.
      return list.filter((it) => it.status && it.status !== "succeeded");
    }
    if (view === "highlights") {
      // Stable partition — see the note on VIEWS. `filter` keeps the
      // newest-first order inside each half.
      return [
        ...list.filter((it) => it.loadCover),
        ...list.filter((it) => !it.loadCover)
      ];
    }
    return list;
  }, [wall, chip, query, view]);

  const covers = useCovers(filtered);

  // ---- The banners and the tiles -----------------------------------------
  const live = shown.filter(
    (g) => !workflows.find((w) => w.id === g.id)?.locked
  );
  // ⚠ THE BILLBOARDS WAIT FOR THE ENTITLEMENTS ANSWER; THE GALLERY DOES NOT.
  // They are advertising, and a banner for a workflow an administrator has
  // HIDDEN is the same "hidden feature that reappears on every reload" bug the
  // rail was fixed for (see the note on WORKFLOWS in Sidebar.jsx). The wall
  // below is the customer's OWN work, where failing open is the dashboard's
  // rule — not knowing yet must never read as "you have nothing".
  const promoted = workflowsKnown ? live : [];
  const pitch = (id) => COPY[id]?.body || "";
  /**
   * The one line a banner shows.
   *
   * ⚠ IT IS THE `stage` LINE, NOT THE FIRST SENTENCE OF THE PITCH, and that is
   * a bug fix. The pitches are paragraphs and their first sentences run from 40
   * to 176 characters — Image to Animatic Image is ONE sentence with two em
   * dashes in it — so the banner grew by two lines whenever the carousel
   * reached that slide, and BOTH billboards grew with it because they share a
   * grid row. Reported exactly that way: *"image to animatics image ka panel
   * bara ho jata hai kyun ismai text jayada hai"*.
   *
   * `stage.body` is the same workflow described in one line for the landing
   * page's "How a project moves" strip — written short on purpose, 67 to 115
   * characters across all six. The long pitch is still the tooltip.
   *
   * ⚠ AND THE FALLBACK TRIMS RATHER THAN APPENDS. The old version glued a "."
   * onto whatever it got, which is how the banner ended up reading "never
   * drifts.." on a sentence that already had one. A workflow an administrator
   * launched before anybody wrote its copy gets "" and no body at all, which
   * is better than a lone full stop.
   */
  const blurb = (id) => {
    const line = COPY[id]?.stage?.body || pitch(id).split(". ")[0] || "";
    const text = line.trim();
    return text && !text.endsWith(".") ? `${text}.` : text;
  };

  /**
   * Where a banner's button goes.
   *
   * ⚠ A WORKFLOW ID NAVIGATES; AN ADDRESS OPENS A TAB. Those are the only two
   * shapes the server will store (`_TARGET_RE` in banners.py), and the
   * `noopener` is not optional — a `target="_blank"` without it hands the page
   * it opened a handle on this one.
   */
  const goTo = (target) => {
    if (!target) return null;
    if (/^https?:/i.test(target)) {
      return () => window.open(target, "_blank", "noopener,noreferrer");
    }
    return () => onNavigate?.(target);
  };

  /** One admin banner → the shape the carousel and the side card both draw. */
  const fromAdmin = (b, tone) => ({
    key: b.id,
    tone,
    eyebrow: b.kicker,
    title: b.title,
    sub: b.body,
    cta: b.cta_label,
    hint: b.body,
    // Relative on the wire, because only this side knows `VITE_API_BASE`.
    image: b.image_url ? api.absoluteUrl(b.image_url) : "",
    workflow: null,
    go: goTo(b.cta_target)
  });

  // ⚠ THE PANEL WINS WHERE IT HAS SPOKEN, SLOT BY SLOT. Setting a hero banner
  // must not blank the fixed one beside it, so the two fall back independently.
  const heroSlides = madeBanners.hero.length
    ? madeBanners.hero.map((b) => fromAdmin(b, "work"))
    : [
        {
          key: "brand",
          tone: "brand",
          eyebrow: brand.name,
          title: "Everything from one script",
          sub: "Plan it, board it, block the motion, render the cut — in one place.",
          image: "",
          workflow: null,
          go: null
        },
        ...promoted.slice(0, HERO_WORKFLOWS).map((g) => ({
          key: g.id,
          tone: "work",
          eyebrow: shortLabel(g.id, g.label),
          title: g.label,
          sub: blurb(g.id),
          cta: "Open",
          hint: pitch(g.id),
          image: "",
          workflow: g.id,
          go: () => onNavigate?.(g.id)
        }))
      ];

  const generatedSide =
    promoted.find((g) => g.id === SIDE_PREFERRED) ||
    promoted[promoted.length - 1] ||
    null;
  const side = madeBanners.side.length
    ? fromAdmin(madeBanners.side[0], "side")
    : generatedSide
      ? {
          key: generatedSide.id,
          tone: "side",
          eyebrow: shortLabel(generatedSide.id, generatedSide.label),
          title: generatedSide.label,
          sub: blurb(generatedSide.id),
          cta: "Open",
          hint: pitch(generatedSide.id),
          image: "",
          workflow: generatedSide.id,
          go: () => onNavigate?.(generatedSide.id)
        }
      : null;

  const createId =
    (promoted.find((g) => g.id === CREATE_PREFERRED) || promoted[0] || {}).id ||
    null;
  const createLabel =
    createId === CREATE_PREFERRED ? "New storyboard" : "Start something";

  const chips = [{ id: "all", label: "For you" }].concat(
    promoted.map((g) => ({ id: g.id, label: shortLabel(g.id, g.label) }))
  );

  return (
    <div className="explore">
      {/* The reference page has no title above its banners and neither does
          this — but a screen with no <h1> is a screen a reader lands on with
          nothing to say, so the name is here and only the eyes skip it. */}
      <h1 className="xp-sr-title">Explore</h1>

      {/* ---- Row 1: the billboards ---- */}
      <div className="xp-banners">
        <HeroCarousel slides={heroSlides} />

        {side && (
          <div
            className={`xp-banner tone-side ${side.image ? "has-photo" : ""}`}
          >
            <BannerArt slide={side} />
            <div className="xp-banner-body">
              {side.eyebrow && (
                <span className="xp-banner-eyebrow">{side.eyebrow}</span>
              )}
              <h2 className="xp-banner-title">{side.title}</h2>
              {side.sub && <p className="xp-banner-sub">{side.sub}</p>}
              {side.go && side.cta && (
                <button
                  type="button"
                  className="btn primary xp-banner-cta"
                  onClick={side.go}
                  title={side.hint}
                >
                  {side.cta} →
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ---- Row 2: one tile per workflow ----
          ⚠ ALL ONE COLOUR. The first tile was gold, copied from the reference,
          and it was asked to stop being: this rail's order is the owner's own
          pipeline, so painting whatever happens to be first as the recommended
          one says something nobody meant — and gold means "the action"
          everywhere else on this page. `title` carries the pitch (helper text
          on hover, RULEBOOK E4). */}
      <div className="xp-tiles">
        {!workflowsKnown
          ? Array.from({ length: 6 }, (_, i) => (
              <div className="xp-tile xp-tile-ghost" key={i} aria-hidden="true">
                <span className="xp-ghost-line xp-ghost-tile" />
              </div>
            ))
          : shown.map((g) => {
              const locked = workflows.find((w) => w.id === g.id)?.locked;
              return (
                <button
                  key={g.id}
                  type="button"
                  className="xp-tile"
                  onClick={() => onNavigate?.(g.id)}
                  title={pitch(g.id) || g.label}
                >
                  <span className="xp-tile-ico">
                    <WorkflowIcon id={g.id} fallback={g.icon} />
                  </span>
                  <span className="xp-tile-name">{g.label}</span>
                  {/* ⚠ LOCKED IS NOT HIDDEN — the same rule the rail keeps. A
                      tool nobody can see is a tool nobody upgrades for. */}
                  {locked ? (
                    <span className="xp-tile-lock" title="Included in a higher plan">
                      🔒
                    </span>
                  ) : (
                    <span className="xp-tile-go" aria-hidden="true">
                      →
                    </span>
                  )}
                </button>
              );
            })}
      </div>

      {/* ---- Row 3: how to look at the wall ---- */}
      <div className="xp-toolbar">
        <div className="xp-tabs" role="tablist" aria-label="Gallery view">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              type="button"
              role="tab"
              aria-selected={view === v.id}
              className={`xp-tab ${view === v.id ? "active" : ""}`}
              onClick={() => setView(v.id)}
            >
              {v.label}
            </button>
          ))}
        </div>

        <label className="xp-search">
          <span className="xp-search-ico" aria-hidden="true">
            ⌕
          </span>
          <input
            className="xp-search-input"
            type="search"
            value={query}
            placeholder="Search your work"
            aria-label="Search your work"
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button
              type="button"
              className="xp-search-clear"
              onClick={() => setQuery("")}
              title="Clear"
              aria-label="Clear search"
            >
              ✕
            </button>
          )}
        </label>

        {createId && (
          <button
            className="btn primary xp-create"
            onClick={() => onNavigate?.(createId)}
            title={pitch(createId) || createLabel}
          >
            ＋ {createLabel}
          </button>
        )}
      </div>

      {/* ---- Row 4: the workflow filter ---- */}
      <div className="opt-chips xp-chips">
        {chips.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`opt-chip xp-chip ${chip === c.id ? "active" : ""}`}
            onClick={() => setChip(c.id)}
          >
            {c.label}
          </button>
        ))}
      </div>

      {/* ---- Row 5: the wall ---- */}
      {showGhosts ? (
        <div
          className="xp-gallery xp-ghosts is-loading"
          aria-hidden="true"
          style={{ "--xp-cols": wallColumns(10) }}
        >
          {Array.from({ length: 10 }, (_, i) => (
            <div className="xp-card xp-card-ghost" key={i}>
              {/* The same two shapes the real wall can draw, so the skeleton is
                  the height of the thing replacing it and nothing jumps. */}
              <span
                className="xp-card-pic xp-ghost-cover"
                style={{
                  "--lib-thumb-ar":
                    i % 3 === 1 ? `${WALL_AR_MIN} / 1` : `${WALL_AR_MAX} / 1`
                }}
              />
            </div>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="xp-empty">
          <span className="xp-empty-ico" aria-hidden="true">
            {createId ? <WorkflowIcon id={createId} /> : null}
          </span>
          <p className="xp-empty-text">
            {query || chip !== "all" || view !== VIEWS[0].id
              ? "Nothing matches that yet."
              : "Your gallery fills up as you work. Start with a storyboard."}
          </p>
          {createId && (
            <button
              className="btn xp-empty-btn"
              onClick={() => onNavigate?.(createId)}
            >
              {createLabel} →
            </button>
          )}
        </div>
      ) : (
        <div
          className="xp-gallery"
          style={{ "--xp-cols": wallColumns(filtered.length) }}
        >
          {filtered.map((it) => (
            <button
              key={`${it.group}:${it.key}`}
              type="button"
              className="xp-card"
              onClick={() =>
                it.onOpen ? it.onOpen() : onNavigate?.(it.group)
              }
              title={`Open ${it.title} — ${it.label}`}
            >
              <span className="xp-card-pic" style={wallAspect(it.aspect)}>
                {covers[it.key] ? (
                  <img src={covers[it.key]} alt="" loading="lazy" />
                ) : (
                  <span className="xp-card-glyph" aria-hidden="true">
                    <WorkflowIcon id={it.group} fallback={it.icon} />
                  </span>
                )}
              </span>

              {/* ⚠ THE STATUS CHIP SITS OVER THE PICTURE, not under the name.
                  A running render and a failed one are the two things worth
                  seeing without reading, and a wall is read by glancing. A
                  finished project wears nothing — six green "SUCCEEDED" badges
                  in a row is noise that hides the one red one. */}
              {it.status && it.status !== "succeeded" && (
                <span className={`badge xp-card-badge ${statusClass(it.status)}`}>
                  {it.status}
                </span>
              )}

              <span className="xp-card-veil" aria-hidden="true" />
              <span className="xp-card-meta">
                <span className="xp-card-name">{it.title}</span>
                <span className="xp-card-sub">
                  <span className="xp-card-wf">{it.short}</span>
                  {it.meta && <span>{it.meta}</span>}
                  {it.size > 0 && <span>{formatBytes(it.size)}</span>}
                  {it.date && <span>{formatDate(it.date)}</span>}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}

      {/* ⚠ LAST IN THE MARKUP AND `position: fixed` IN CSS, so it is over the
          page rather than in it — and last in the tab order, so a keyboard
          reaches the page's own content before the advertisement. */}
      <PromoPopup onCta={onUpgrade} />
    </div>
  );
}
