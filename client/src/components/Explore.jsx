import { useEffect, useMemo, useState } from "react";
import * as api from "../api.js";
// ⚠ THE SAME THUMBNAIL MATHS THE LIBRARIES AND THE DASHBOARD USE. `aspectStyle`
// turns "16:9" into the custom property every card in this app is laid out by,
// and `matchesFilter` is the same "contains" the storyboard library's filter box
// already runs — a second, slightly different search would be a second answer.
import { aspectStyle, matchesFilter } from "./LibraryList.jsx";
// ⚠ THE SALES COPY AND THE LIVE LIST ARE THE LANDING PAGE'S, NOT A SECOND SET.
// Every line of "what is this workflow for" comes from there, and so does the
// public `GET /public/workflows` read that says which of them are switched on.
// Two sets of words for one tool is how a workflow ends up described one way to
// a prospect on one page and another way on the next. See Landing.jsx.
import { COPY, useLiveWorkflows } from "./Landing.jsx";
// The brand slide has no workflow to draw, so it draws the mark itself —
// the same one the rail and the favicon carry. See Logo.jsx.
import Logo from "./Logo.jsx";
import useBranding from "../useBranding.js";
// The full-screen player. ⚠ THE WHOLE POINT OF THE WALL — asked for directly:
// *"the videos or images should be clickable and be able to use it properly
// play"*. See MediaLightbox.jsx.
import MediaLightbox from "./MediaLightbox.jsx";
// The discount that comes to the customer instead of waiting to be found. It
// fetches its own offer (from the PUBLIC `/billing/tiers`) and draws nothing at
// all when there isn't one.
import PromoPopup from "./PromoPopup.jsx";
// ⚠ THE RAIL'S OWN SHORT NAMES, so a workflow is called one thing in the
// narrow sidebar after somebody signs in and the same thing on this page's
// chips and cards. See `WORKFLOW_SHORT` in Sidebar.jsx.
import { shortLabel } from "./Sidebar.jsx";
import WorkflowIcon from "./WorkflowIcon.jsx";

// Explore — the PUBLIC SHOP WINDOW. What this studio makes, what it has made,
// and one door: sign in.
//
// ⚠ THIS SCREEN CHANGED SIDES, AND THAT IS THE WHOLE STORY OF THIS FILE. It
// used to live INSIDE the signed-in shell and answered "what can this studio do,
// and what does MY work look like" — banners, a tile per workflow, and every
// project the account owned as one picture wall. Asked for directly:
//
//   *"the page we created on explore should be used to market ... any logged in
//   user must not see explore ... the explore page is going to be only used for
//   getting users, nothing more than that"*
//
// So three things moved at once and they only make sense together:
//
//   1. IT IS LOGGED-OUT ONLY. The rail no longer carries an Explore row and the
//      shell no longer has a branch for it — a signed-in customer lands on Home,
//      which is the DESK (who you are, your plan, where you left off). A sales
//      page shown to somebody who has already bought is a wasted screen.
//   2. EVERY CONTROL IS A SIGN-IN GATE. There is nothing to navigate TO from
//      here: a tile, a banner button, the ＋ and the viewer's own CTA all call
//      `onSignIn(workflowId)`, and the workflow they name is where the person
//      lands the moment they are through. Clicking "Script to Storyboard" and
//      being dropped on a generic dashboard is how an interested visitor is lost
//      between the click and the password.
//   3. THE WALL IS CURATED WORK, NOT THE ACCOUNT'S. There is no account. It is
//      `GET /public/showcase` — items an administrator uploaded in the panel.
//
// ⚠ AND THE OLD REASON FOR **NOT** HAVING A PUBLIC FEED STILL STANDS, WORD FOR
// WORD. This app has no community gallery, nothing is shared by default, and
// filling this grid with customers' storyboards would mean publishing customers'
// storyboards. That is why the wall is admin-curated rather than automatic: the
// only work on it is work somebody chose to put there. See `server/showcase.py`.
//
// ⚠ IT FETCHES ITS OWN THREE THINGS AND NEEDS NO TOKEN FOR ANY OF THEM —
// `/public/workflows`, `/public/banners`, `/public/showcase`. That is not a
// convenience; it is the requirement. This is the page you reach BEFORE you have
// a token, so anything it cannot read without one it cannot draw at all.

// How long each banner stays up. Six seconds is long enough to read two lines
// and short enough that a visitor sees more than one without waiting.
const SLIDE_MS = 6000;

// How many banners the left carousel carries: the brand slide plus this many
// workflows. ⚠ MORE THAN FOUR DOTS STOPS READING AS "there is more" and starts
// reading as a progress bar for something you did not ask to sit through.
const HERO_WORKFLOWS = 3;

// The banner on the right is a fixed billboard, not part of the rotation. This
// workflow is the one it advertises when it is live; otherwise the last live
// one, so the slot is never empty and never points at a room with no door.
const SIDE_PREFERRED = "storyboard-to-animatics";

// What the toolbar's "＋" offers to start. It is a sign-in, like everything
// else here — the workflow is only where they land afterwards.
const CREATE_PREFERRED = "script-to-storyboard";

// ---------------------------------------------------------------------------
// THE WALL'S SHAPE. Two numbers, and between them they are the whole fix for
// "the tall board leaves a hole beside it".
//
// ⚠ WHAT WAS ACTUALLY WRONG, MEASURED: with seven items the five columns ended
// at 483, 483, 323, 323 and 154 pixels — the last one two thirds empty. It was
// NOT that the columns were badly packed; CSS multi-column balances by height
// and shortest-column-first packing gives the same answer. The cause is the
// SPREAD: a 9:16 card is 3.2× the height of a 16:9 one in the same column, so
// two of them tower over everything and nothing can fill the gap they leave.
//
// ⚠ AND FEWER COLUMNS DOES NOT FIX IT, WHICH IS THE TRAP. Fewer columns are
// WIDER columns, and a 9:16 card in a wider column is taller still — at three
// columns the tall card grows from 466px to 843px and the hole gets bigger.
// ---------------------------------------------------------------------------

// The tallest and shortest a card may be drawn, as width ÷ height.
//
// ⚠ THIS IS THE ONE PLACE ON THIS SCREEN THAT CROPS, and it is deliberate. A
// 9:16 reel lands at 4:5 here, which trims about 30% of its height, evenly, top
// and bottom — and clicking opens the real thing, uncropped and full size, in
// the viewer. On a wall you are meant to glance at, an even trim beats a column
// that is three cards tall while its neighbour is nine.
const WALL_AR_MIN = 0.8; // 4:5 — the tallest a card gets
const WALL_AR_MAX = 16 / 9; // the widest

/**
 * An item's own ratio, pulled into the range the wall can lay out.
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
// seven items the fifth column got a single card and ended less than a third of
// the way down. Two per column is the floor at which masonry starts looking
// like masonry.
const WALL_MAX_COLS = 5;

// ⚠ `floor`, NOT `ceil`, AND THAT ONE WORD IS THE RULE ABOVE ACTUALLY HOLDING.
// `ceil(count / 2)` says "two per column" and does not deliver it: five items
// asked for three columns, which is 1.67 each — so the third column got ONE card
// and ended at 463px beside two that ran to 854px, which is the same hole in a
// smaller wall. Measured, on exactly that fixture.
//
// `floor` gives five items two columns, seven items three, and never asks for a
// column it cannot fill twice over. The floor of 2 below is what keeps a wall of
// two or three from collapsing into a single stack.
function wallColumns(count) {
  return Math.max(2, Math.min(WALL_MAX_COLS, Math.floor(count / 2)));
}

// The three ways of looking at the wall. `id` is state, `label` is the tab.
//
// ⚠ THESE ARE NOT THE OLD TABS. Highlights / Recent / In progress described a
// CUSTOMER'S OWN JOBS — "in progress" is a render that has not finished, which
// is meaningless to a stranger and impossible to answer without an account.
// What a visitor actually wants to sort by on a show-reel is "show me the
// films", so that is what the tabs do now.
const VIEWS = [
  { id: "all", label: "Everything" },
  { id: "video", label: "Films" },
  { id: "image", label: "Stills" }
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

/**
 * The rotating billboard on the left.
 *
 * ⚠ IT STOPS WHEN YOU ARE READING IT. Hover or keyboard focus pauses the timer,
 * because a banner that slides away mid-sentence — or worse, mid-click, moving
 * the button out from under the pointer — is the one thing a carousel must not
 * do. `prefers-reduced-motion` switches the timer off altogether; the arrows and
 * dots still work, so nothing is unreachable, it just never moves on its own.
 */
function HeroCarousel({ slides }) {
  const [at, setAt] = useState(0);
  const [held, setHeld] = useState(false);
  const still = prefersReducedMotion();

  // ⚠ CLAMPED ON EVERY RENDER RATHER THAN RESET IN AN EFFECT. `slides` is built
  // from the public workflow answer, so it can SHRINK under this component when
  // that answer lands — and an index left pointing past the end would render
  // nothing at all.
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
 * The public marketing page.
 *
 * ⚠ IT TAKES NO `workflows` PROP ANY MORE, and that is not a tidy-up. The old
 * one was handed the RESOLVED rail — what one signed-in account is entitled to
 * see. There is no account here, so the only honest source is the public
 * endpoint, which answers what the product SELLS rather than what somebody has
 * bought. `useLiveWorkflows` is the landing page's own hook, so both public
 * pages advertise the identical set.
 *
 * @param {function} onSignIn — `(workflowId?) => void`. THE ONLY WAY OUT OF
 *   THIS PAGE that isn't Back. Every control on it calls this.
 * @param {function} [onBack] — to the landing page.
 * @param {string}   [theme] / @param {function} [onToggleTheme] — the same
 *   switch the landing nav carries; this page has no rail to flip it from.
 */
export default function Explore({ onSignIn, onBack, theme, onToggleTheme }) {
  // This page prints the app's own name in the nav, in the first hero slide and
  // in the footer, and all three read the one store so they cannot disagree.
  const brand = useBranding();

  // ⚠ THE SAME HOOK THE LANDING PAGE USES, fallback and all: not answered yet →
  // the built-in list, so the page paints instantly and completely; answered →
  // exactly what the server named; the call FAILED → the built-in list again. A
  // marketing page that flashes empty while it asks the server what it sells is
  // worse than one that is a few hundred milliseconds stale.
  const live = useLiveWorkflows();
  // ⚠ "SOON" IS ADVERTISED AND NOT SOLD. It keeps its tile, because a tool
  // nobody can see is a tool nobody waits for — but it is not what a banner
  // points at and not what ＋ starts, because sending somebody through a sign-up
  // to reach a placeholder is the worst version of this page.
  const sellable = live.filter((w) => w.status !== "soon");

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

  // ---- The wall ------------------------------------------------------------
  // ⚠ `null` MEANS "NOBODY HAS ANSWERED YET" AND `[]` MEANS "THERE IS NOTHING",
  // and the difference is the whole loading state. Ghost cards while it is
  // null; no gallery section at all when it is empty. A marketing page that
  // says "no items" is a marketing page apologising to a stranger.
  const [items, setItems] = useState(null);
  useEffect(() => {
    let cancelled = false;
    api
      .publicShowcase()
      .then((r) => {
        if (!cancelled) setItems(Array.isArray(r?.items) ? r.items : []);
      })
      // Deliberately silent, and it lands on `[]` rather than staying null —
      // ghost cards that never resolve are worse than no wall.
      .catch(() => {
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [view, setView] = useState(VIEWS[0].id);
  const [chip, setChip] = useState("all");
  const [query, setQuery] = useState("");
  // Which card the viewer is open on, by index into `filtered`. ⚠ AN INDEX AND
  // NOT AN ITEM, because the arrows step through what is CURRENTLY FILTERED —
  // stepping out of the "Films" tab into a still nobody asked for would be the
  // filter quietly not applying.
  const [openAt, setOpenAt] = useState(-1);

  /** One workflow's label, whatever the server called it. */
  const labelOf = (id) =>
    live.find((w) => w.id === id)?.title || COPY[id]?.title || id;

  const wall = useMemo(() => {
    return (items || []).map((it) => ({
      ...it,
      // Absolute HERE and once, rather than at four draw sites — only this side
      // knows `VITE_API_BASE`, and an `<img src>` would otherwise resolve a
      // relative path against Vite on :5173 instead of the API on :8000.
      media_url: api.absoluteUrl(it.media_url),
      poster_url: api.absoluteUrl(it.poster_url),
      label: it.workflow ? labelOf(it.workflow) : "",
      short: it.workflow ? shortLabel(it.workflow, labelOf(it.workflow)) : ""
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, live]);

  const filtered = useMemo(() => {
    let list = wall;
    if (view !== "all") list = list.filter((it) => it.kind === view);
    if (chip !== "all") list = list.filter((it) => it.workflow === chip);
    if (query.trim()) {
      list = list.filter((it) => matchesFilter(query, it.title, it.label));
    }
    return list;
  }, [wall, view, chip, query]);

  // ⚠ THE OPEN CARD IS CLOSED WHENEVER THE FILTER MOVES UNDER IT. Index 7 in a
  // list that just became three items long is a viewer showing nothing, and the
  // arrows would step through a set the person can no longer see.
  useEffect(() => {
    setOpenAt(-1);
  }, [view, chip, query]);

  // ---- The banners and the tiles ------------------------------------------
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
   * ⚠ AND THE FALLBACK TRIMS RATHER THAN APPENDS. The old version glued a "."
   * onto whatever it got, which is how the banner ended up reading "never
   * drifts.." on a sentence that already had one.
   */
  const blurb = (id) => {
    const line = COPY[id]?.stage?.body || pitch(id).split(". ")[0] || "";
    const text = line.trim();
    return text && !text.endsWith(".") ? `${text}.` : text;
  };

  /**
   * Where a banner's button goes.
   *
   * ⚠ AN ADDRESS OPENS A TAB; A WORKFLOW ID ASKS FOR A SIGN-IN. That second
   * half is the change: on the signed-in page it navigated, and here there is
   * nowhere to navigate TO. The id is not thrown away — it is carried through
   * the sign-in so the visitor lands in the workflow the banner was selling.
   * These are the only two shapes the server will store (`_TARGET_RE` in
   * banners.py), and the `noopener` is not optional: a `target="_blank"`
   * without it hands the page it opened a handle on this one.
   */
  const goTo = (target) => {
    if (!target) return null;
    if (/^https?:/i.test(target)) {
      return () => window.open(target, "_blank", "noopener,noreferrer");
    }
    return () => onSignIn?.(target);
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
          cta: "Start free",
          hint: "Create an account — it takes a minute",
          go: () => onSignIn?.()
        },
        ...sellable.slice(0, HERO_WORKFLOWS).map((w) => ({
          key: w.id,
          tone: "work",
          eyebrow: shortLabel(w.id, w.title),
          title: w.title,
          sub: blurb(w.id),
          // ⚠ "TRY", NOT "OPEN". The old word was honest inside the app and is a
          // small lie outside it: this button opens a sign-in, and a button that
          // says Open and asks for a password is how a visitor decides the page
          // was misleading them.
          cta: "Try it",
          hint: pitch(w.id),
          image: "",
          workflow: w.id,
          go: () => onSignIn?.(w.id)
        }))
      ];

  const generatedSide =
    sellable.find((w) => w.id === SIDE_PREFERRED) ||
    sellable[sellable.length - 1] ||
    null;
  const side = madeBanners.side.length
    ? fromAdmin(madeBanners.side[0], "side")
    : generatedSide
      ? {
          key: generatedSide.id,
          tone: "side",
          eyebrow: shortLabel(generatedSide.id, generatedSide.title),
          title: generatedSide.title,
          sub: blurb(generatedSide.id),
          cta: "Try it",
          hint: pitch(generatedSide.id),
          image: "",
          workflow: generatedSide.id,
          go: () => onSignIn?.(generatedSide.id)
        }
      : null;

  const createId =
    (sellable.find((w) => w.id === CREATE_PREFERRED) || sellable[0] || {}).id ||
    null;

  // Only workflows something on the wall was actually MADE with get a chip —
  // a filter that always answers "nothing matches" is a broken control, and on
  // this page it reads as "they have never made one of those".
  const tagged = new Set(wall.map((it) => it.workflow).filter(Boolean));
  const chips = [{ id: "all", label: "Everything" }].concat(
    live
      .filter((w) => tagged.has(w.id))
      .map((w) => ({ id: w.id, label: shortLabel(w.id, w.title) }))
  );

  const open = openAt >= 0 && openAt < filtered.length ? filtered[openAt] : null;
  const step = (d) =>
    setOpenAt((n) => (((n + d) % filtered.length) + filtered.length) %
      filtered.length);

  return (
    <div className="explore explore-public xp-has-rail">
      {/* ---- The left rail ----
          ⚠ A RAIL ON THE PAGE THAT HAS NO SHELL. Asked for with the Kling AI
          Explore page beside it: *"need the side bar explore page like this but
          with my own services, and when user click on any of this he must get
          sign in page and after sign in open the usual flow."*

          ⚠ IT IS THE SAME LIST AS THE TILES BELOW, AND THAT IS NOT A MISTAKE.
          The reference page carries both, and they answer different questions:
          the tiles are a row you READ on the way down the page, the rail is a
          launcher that is still there when you have scrolled to the bottom of
          the wall. `live` is the one source for both, so a workflow an
          administrator switches off disappears from the pair of them at once.

          ⚠ EVERY ROW IS A SIGN-IN, exactly like a tile — `onSignIn` carries the
          workflow id through the login and `pendingWorkflow` lands them in it
          afterwards, which is the "usual flow" the request asks for. There is
          nowhere else for a click to go: this page is drawn for somebody with
          no account.

          ⚠ IT LOOKS LIKE THE COLLAPSED APP RAIL ON PURPOSE — glyph over a short
          name, same metrics — so the first thing a visitor sees is the thing
          they will still be looking at after they sign in. The classes are its
          own because `.sidebar` is welded to the shell's grid; the measurements
          are copied from `.sidebar.collapsed` in shell.css. */}
      <aside className="xp-rail" aria-label="What this studio makes">
        <button
          type="button"
          className="xp-rail-brand"
          onClick={onBack}
          title={`${brand.name} — back to the front page`}
        >
          <Logo />
        </button>

        <nav className="xp-rail-list">
          {live.map((w) => (
            <button
              key={w.id}
              type="button"
              className="xp-rail-item"
              onClick={() => onSignIn?.(w.status === "soon" ? null : w.id)}
              /* Helper text on hover, visible text short — RULEBOOK E4, and the
                 same split the tiles keep. */
              title={pitch(w.id) || w.title}
            >
              <span className="xp-rail-ico">
                <WorkflowIcon id={w.id} />
              </span>
              {/* ⚠ THE SHORT NAME, from the same map the app rail reads. A rail
                  is read by glancing, and "Text to Turnaround Image" clipped to
                  "Text to Turnar…" is the bug RULEBOOK E17 and E18 were paid
                  for. */}
              <span className="xp-rail-name">{shortLabel(w.id, w.title)}</span>
              {/* Shown, not hidden — a tool nobody can see is a tool nobody
                  waits for. Same rule as the tiles and the app rail. */}
              {w.status === "soon" && (
                <span className="xp-rail-soon">Soon</span>
              )}
            </button>
          ))}
        </nav>

        {/* ⚠ THE BOTTOM OF THE RAIL IS WHERE THE REFERENCE PUTS IT, and it is
            the right place: the rail is the one part of this page that does not
            scroll away, so the ask does not either. */}
        <div className="xp-rail-foot">
          <button
            type="button"
            className="btn small primary xp-rail-cta"
            onClick={() => onSignIn?.(createId)}
          >
            Sign in
          </button>
        </div>
      </aside>

      {/* ---- The public nav ----
          ⚠ IT WEARS THE LANDING PAGE'S OWN CLASSES. This screen used to sit in
          the app shell and had the rail above it; standing alone it needs a
          header, and a SECOND header that merely resembled the landing page's
          is exactly the mismatch this repo keeps paying for. Same markup, same
          stylesheet, one brand. */}
      <nav className="landing-nav xp-nav">
        <button type="button" className="brand small xp-nav-brand" onClick={onBack}>
          <Logo /> {brand.name}
        </button>
        <div className="landing-nav-links">
          {onBack && (
            <a
              href="#back"
              onClick={(e) => {
                e.preventDefault();
                onBack();
              }}
            >
              Home
            </a>
          )}
          <a href="#work">The work</a>
          {/* ⚠ THE ONLY THEME SWITCH A LOGGED-OUT VISITOR CAN REACH, on this
              page as on the landing one. Icon-only: a nav has no room for a
              word, and the sun/moon is the one everybody reads. Rendered only
              when a handler is handed down, so the page still works standalone
              (tests, previews) without one. */}
          {onToggleTheme && (
            <button
              type="button"
              className="lp-theme"
              onClick={onToggleTheme}
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              aria-label={
                theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
              }
            >
              {theme === "dark" ? "☀️" : "🌙"}
            </button>
          )}
          <button
            className="btn small primary nav-cta"
            onClick={() => onSignIn?.(createId)}
          >
            Sign in
          </button>
        </div>
      </nav>

      <h1 className="xp-sr-title">Explore {brand.name}</h1>

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
          and it was asked to stop being: this order is the owner's own pipeline,
          so painting whatever happens to be first as the recommended one says
          something nobody meant — and gold means "the action" everywhere else on
          this page. `title` carries the pitch (helper text on hover, RULEBOOK
          E4). */}
      <div className="xp-tiles">
        {live.map((w) => (
          <button
            key={w.id}
            type="button"
            className="xp-tile"
            onClick={() => onSignIn?.(w.status === "soon" ? null : w.id)}
            title={pitch(w.id) || w.title}
          >
            <span className="xp-tile-ico">
              <WorkflowIcon id={w.id} />
            </span>
            <span className="xp-tile-name">{w.title}</span>
            {/* ⚠ "SOON" IS SHOWN, NOT HIDDEN — the same rule the rail keeps for
                a locked workflow. A tool nobody can see is a tool nobody waits
                for. It still opens the sign-in, just without naming a
                destination that is not there yet. */}
            {w.status === "soon" ? (
              <span className="xp-tile-lock" title="Not open yet">
                Soon
              </span>
            ) : (
              <span className="xp-tile-go" aria-hidden="true">
                →
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ---- Rows 3-5: the wall ----
          ⚠ THE WHOLE SECTION IS CONDITIONAL, INCLUDING ITS TOOLBAR. An
          administrator who has uploaded nothing gets banners and tiles and no
          empty gallery furniture — a search box over nothing, three tabs that
          all answer nothing, and a line apologising to a stranger. */}
      {items === null ? (
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
      ) : wall.length > 0 ? (
        <>
          <div className="xp-work-head" id="work">
            <h2 className="xp-work-title">Made with {brand.name}</h2>
            <p className="xp-work-sub muted">
              Every one of these started as a line of text. Click any of them.
            </p>
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
                placeholder="Search the work"
                aria-label="Search the work"
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

            <button
              className="btn primary xp-create"
              onClick={() => onSignIn?.(createId)}
              title={
                createId
                  ? pitch(createId) || "Create an account and start"
                  : "Create an account and start"
              }
            >
              ＋ Make your own
            </button>
          </div>

          {/* ---- Row 4: the workflow filter ----
              One chip is no filter — with everything made by one workflow the
              row would be "Everything | Storyboard" and both answer the same. */}
          {chips.length > 2 && (
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
          )}

          {/* ---- Row 5: the wall itself ---- */}
          {filtered.length === 0 ? (
            <div className="xp-empty">
              <span className="xp-empty-ico" aria-hidden="true">
                {createId ? <WorkflowIcon id={createId} /> : null}
              </span>
              <p className="xp-empty-text">Nothing matches that.</p>
              <button
                className="btn xp-empty-btn"
                onClick={() => {
                  setQuery("");
                  setChip("all");
                  setView(VIEWS[0].id);
                }}
              >
                Show everything →
              </button>
            </div>
          ) : (
            <div
              className="xp-gallery"
              style={{ "--xp-cols": wallColumns(filtered.length) }}
            >
              {filtered.map((it, i) => (
                <button
                  key={it.id}
                  type="button"
                  className="xp-card"
                  /* ⚠ THIS OPENS THE THING, IT DOES NOT NAVIGATE. On the old
                     page a card opened the customer's own project; here it
                     plays the film. Asked for directly: *"the videos or images
                     should be clickable and be able to use it properly play"*. */
                  onClick={() => setOpenAt(i)}
                  title={
                    it.kind === "video"
                      ? `Play ${it.title}`
                      : `View ${it.title}`
                  }
                >
                  <span className="xp-card-pic" style={wallAspect(it.aspect)}>
                    {/* ⚠ THE POSTER, NEVER THE CLIP. Twenty-four `<video>`
                        elements on the page everybody lands on is twenty-four
                        downloads before a visitor has clicked anything — on a
                        phone that is the page not loading. The film is fetched
                        when it is asked for, in the viewer. */}
                    {it.poster_url || (it.kind === "image" && it.media_url) ? (
                      <img
                        src={it.poster_url || it.media_url}
                        alt=""
                        loading="lazy"
                      />
                    ) : (
                      <span className="xp-card-glyph" aria-hidden="true">
                        <WorkflowIcon id={it.workflow || createId} />
                      </span>
                    )}
                  </span>

                  {/* ⚠ THE PLAY BADGE SITS OVER THE PICTURE, where the status
                      chip used to. On a wall of stills, "this one moves" is the
                      one thing worth seeing without reading — and a wall is
                      read by glancing. A still wears nothing. */}
                  {it.kind === "video" && (
                    <span className="xp-card-play" aria-hidden="true">
                      ▶
                    </span>
                  )}

                  <span className="xp-card-veil" aria-hidden="true" />
                  <span className="xp-card-meta">
                    <span className="xp-card-name">{it.title}</span>
                    <span className="xp-card-sub">
                      {it.short && <span className="xp-card-wf">{it.short}</span>}
                      {it.blurb && <span>{it.blurb}</span>}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </>
      ) : null}

      {/* ---- The last word ----
          ⚠ A SECOND CTA AT THE BOTTOM, AND IT IS THE ONLY REPEAT ON THE PAGE.
          Somebody who has scrolled a wall of films is the most convinced they
          will ever be, and the nav's Sign in is one whole screen behind them. */}
      <div className="xp-foot">
        <h2 className="xp-foot-title">Your turn.</h2>
        <p className="xp-foot-sub muted">
          Sign in with Google or an email address. The first project is free.
        </p>
        <button
          className="btn primary lg"
          onClick={() => onSignIn?.(createId)}
        >
          Get started — it's free →
        </button>
      </div>

      {/* ⚠ THE VIEWER'S BUTTON IS THE SIGN-IN GATE, and it names the workflow
          the piece was made with — the strongest moment on the page to ask, and
          the one place where "make one like this" is literally true. */}
      <MediaLightbox
        item={open}
        onClose={() => setOpenAt(-1)}
        onStep={filtered.length > 1 ? step : undefined}
        count={open ? `${openAt + 1} / ${filtered.length}` : ""}
        onUse={(it) => onSignIn?.(it.workflow || createId)}
        useLabel={
          open?.label ? `Make one with ${open.label}` : "Make one like this"
        }
      />

      {/* ⚠ LAST IN THE MARKUP AND `position: fixed` IN CSS, so it is over the
          page rather than in it — and last in the tab order, so a keyboard
          reaches the page's own content before the advertisement.
          ⚠ ITS BUTTON IS A SIGN-IN, NOT THE PRICING MODAL. `POST /billing/coupon`
          is signed-in only and would 401 in front of a prospect; the code is
          readable and copyable, which is all a visitor needs to carry it
          through sign-up. Same reasoning as `OfferStrip` in the hero. */}
      <PromoPopup onCta={() => onSignIn?.(createId)} />
    </div>
  );
}
