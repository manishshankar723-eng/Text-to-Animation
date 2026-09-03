// Public landing page — the first thing a visitor sees.
//
// It used to pitch ONE workflow (Text to Turnaround Image), which made the
// other five invisible to anyone who hadn't signed in yet — the same problem
// Home had before it grew per-workflow groups. So the page now leads with the
// whole pipeline (plan → storyboard → poses → video) and gives every live
// workflow its own card.
//
// The cards MIRROR Sidebar.jsx's WORKFLOWS — same labels, same icons, same
// order — so what a visitor reads here is what they find in the nav after
// signing in. When a workflow is added, renamed or moved there, move it here
// too. Deliberately NOT numbered: the sidebar order is the owner's choice and
// is not pipeline order (see Sidebar.jsx), so the pipeline story is told
// separately in "How it works" below.
//
// ⚠ THE OFFER STRIP UNDER THE HERO IS THE ONLY PLACE A COUPON REACHES SOMEBODY
// WHO HAS NOT SIGNED UP. See OfferCard.jsx.
import { useEffect, useState } from "react";
import * as api from "../api.js";
import { OfferStrip } from "./OfferCard.jsx";
import Logo from "./Logo.jsx";
import useBranding from "../useBranding.js";
import WorkflowIcon from "./WorkflowIcon.jsx";

// ⚠ THIS IS COPY, NOT A LIST OF WHAT IS LIVE — AND THAT SPLIT IS THE POINT.
// ⚠ WHICH IS WHY THERE ARE SIX OF THEM AND ONLY FOUR ARE ON THE PAGE TODAY.
// Cutting the array down to the four that happen to be launched was the same
// bug as hard-coding six: a workflow the owner turns ON in the admin panel then
// has no paragraph, and a page that cannot describe what it is selling silently
// drops it. Every workflow that EXISTS gets its pitch written here; the server
// decides which pitches get read out.
// It used to be both, and it went stale the moment an administrator flipped a
// switch: a hidden workflow stayed advertised to every stranger who visited, and
// a newly launched one went unmentioned until somebody edited this file. The
// rail never had that problem because it reads `/auth/me/entitlements`.
//
// So `GET /public/workflows` now answers the same question WITHOUT a token
// (see `features.py`), and this array's job shrank to the half a server should
// never own: the sales pitch. The server says WHICH workflows and what they are
// called; this says what each one is worth. An id the server does not return is
// simply not rendered, and an id it returns that is missing here is skipped —
// so a workflow launched in the admin panel appears on the rail immediately and
// on this page as soon as somebody writes its paragraph.
//
// ⚠ `title` IS A FALLBACK. The label printed is the server's, so renaming a
// workflow in the admin panel renames it here too.
//
// `short` is the one-word name for the pipeline line in the hero. `tile` is the
// caption under its picture in the hero art, and is only written out where it
// differs from `short` — a tile has room for three words and the flow line does
// not. `stage` is this workflow's beat in "How a project moves" — it lives here
// rather than in its own array so the two can never drift apart.
const WORKFLOWS = [
  {
    id: "plan-and-script",
    title: "Plan & Script",
    body:
      "Talk to a planning agent that asks clickable questions, researches reference videos on YouTube, then hands you a content calendar and a shot-ready script in your language.",
    tags: ["Chat planning", "YouTube research", "Exports"],
    short: "Plan",
    // The one label the hero tile has always spelled out in full — the flow line
    // above it needs one word, the picture under it can carry three.
    tile: "Plan & script",
    stage: {
      title: "Plan it",
      body:
        "Decide what you're making before you make any of it — topic, format, language, script.",
    },
  },
  {
    id: "text-to-image",
    title: "Text to Turnaround Image",
    body:
      "Describe a character or upload one photo. Every part — hair, face, body, garments, shoes, props — comes back as a clean 4-view turnaround sheet, split and normalized.",
    tags: ["4 views", "Per-part sheets", "One-click 3D"],
    short: "Characters",
    stage: {
      title: "Cast it",
      body:
        "Turn a description or a photo into a 4-view turnaround sheet you can reuse in every shot.",
    },
  },
  {
    id: "script-to-storyboard",
    title: "Script to Storyboard",
    body:
      "A script becomes a shot list, then drawn panels that keep your cast and sets consistent from shot to shot. Edit the board, export a PDF, share a link.",
    tags: ["Shot list", "Continuity", "PDF + share"],
    short: "Storyboard",
    stage: {
      title: "Draw it",
      body:
        "The script becomes a shot list, then drawn panels with your cast and sets holding still across them.",
    },
  },
  {
    id: "create-animatic-image",
    title: "Image to Animatic Image",
    body:
      "Take one approved panel and block its motion out as key poses — about four drawings a second for a 2–10s shot — each anchored on the panel so the character never drifts.",
    tags: ["Key poses", "2–10s shots", "Works on a copy"],
    short: "Key poses",
    stage: {
      title: "Move it",
      body:
        "Block each shot out as key poses, anchored on the panel it came from — motion you can see before you commit to it.",
    },
  },
  {
    id: "animatics-to-video",
    title: "Image to AI Video",
    body:
      "Send shots to Veo image-to-video, see the cost before you spend anything, render shot by shot, then assemble the clips into one final cut.",
    tags: ["Veo render", "Cost estimate", "Final cut"],
    short: "AI video",
    stage: {
      title: "Render it",
      body:
        "Hand a shot to Veo and get real footage back — priced and capped before a second is billed.",
    },
  },
  {
    id: "storyboard-to-animatics",
    title: "Video Editor",
    body:
      "Turn a board into a timed MP4 with captions and audio — dialogue, music, the lot. Runs on ffmpeg locally, so it spends no AI credits at all.",
    tags: ["Timed edit", "Captions + audio", "No credits"],
    short: "Video",
    stage: {
      title: "Ship it",
      body: "A timed MP4 with captions and audio, a PDF board, or a shared link.",
    },
  },
];

// The copy, by id — what the answer from the server gets joined against.
//
// ⚠ EXPORTED, because the Explore page pitches the same six workflows to a
// customer who is already signed in — its banners and its tile tooltips read
// from here. Two sets of words for one tool is how a workflow ends up
// described one way to a prospect and another way to the person paying for it.
export const COPY = Object.fromEntries(WORKFLOWS.map((w) => [w.id, w]));

// ⚠ WHAT TO DRAW WHEN THE SERVER DOES NOT ANSWER, and the ONE hand-maintained
// list left on this page. It is deliberately the four that are live today
// rather than all six: this is the outage path, and on it the page has to guess.
// Guessing SMALL is a page that is briefly out of date; guessing BIG is a page
// advertising a workflow the visitor will not find after signing up, which is
// the exact fault the public endpoint was added to fix.
//
// ⚠ IT ONLY MATTERS WHILE THE API IS DOWN. Nothing needs updating here when a
// workflow launches — the endpoint covers that within one page load. Update it
// when the live set changes for good, and if it is ever wrong the cost is one
// stale paragraph during an outage.
const FALLBACK_IDS = [
  "plan-and-script",
  "script-to-storyboard",
  "create-animatic-image",
  "storyboard-to-animatics",
];
const FALLBACK = FALLBACK_IDS.map((id) => COPY[id]).filter(Boolean);

// Small words for small numbers. "4 workflows" in a headline reads like a
// spreadsheet; past six this stops being a marketing page's problem.
const COUNT_WORD = ["no", "one", "two", "three", "four", "five", "six"];
const countWord = (n) => COUNT_WORD[n] || String(n);

/**
 * What this visitor should be shown, joined to the copy above.
 *
 * ⚠ THREE STATES, AND THE MIDDLE ONE IS WHY THIS IS A HOOK AND NOT A FETCH.
 *   - not answered yet → the built-in list, so the page paints instantly and
 *     completely. A marketing page that flashes empty while it asks the server
 *     what it sells is worse than one that is a few hundred milliseconds stale.
 *   - answered         → exactly what the server named, in the server's order,
 *     under the server's labels.
 *   - the call FAILED  → the built-in list again. ⚠ FAIL OPEN, the same rule the
 *     rail follows: one bad request must not blank the page every prospect
 *     lands on.
 */
// ⚠ EXPORTED, because the PUBLIC Explore page sells the same set to the same
// stranger. Two public pages asking two different questions about what this
// studio offers is how one of them ends up advertising a workflow the other
// says is not there. Same rule as `COPY` above, one level up: that shares the
// WORDS, this shares the LIST.
// Where the last public answer is kept.
//
// ⚠ **THIS IS WHY A HIDDEN WORKFLOW USED TO FLASH UP ON EVERY RELOAD.** The
// hook started at `null`, drew `FALLBACK` — all six built-in workflows — and
// then swapped to the server's list a moment later, so every visitor saw the
// workflows an administrator had HIDDEN for exactly as long as the request
// took: *"jab refresh kiye to one sec ke liye dikha fir nhi"*. That is the
// precise fault `/public/workflows` was added to fix, arriving through the
// front door instead of the back one.
//
// ⚠ REMEMBERED, NOT SKELETONED, AND FOR A MARKETING PAGE THAT IS THE RIGHT
// TRADE. The sidebar solved the same problem with skeleton rows because a rail
// is chrome; a hero that empties itself for half a second on every visit is a
// worse first impression than the flash was. Same fix, same key shape and the
// same reasoning as the remembered brand in `branding.js`: a returning visitor
// is correct in the first paint, with no request having answered yet.
//
// ⚠ AND `known` COVERS THE ONE VISIT MEMORY CANNOT — the first ever. There is
// nothing to remember, so the page draws no workflow list AT ALL until the
// answer lands, rather than advertising six and settling on three.
const REMEMBER_KEY = "cas_public_workflows";

function rememberedWorkflows() {
  try {
    const raw = JSON.parse(localStorage.getItem(REMEMBER_KEY) || "null");
    return Array.isArray(raw) && raw.length ? raw : null;
  } catch {
    // Private mode, or somebody edited it by hand. No memory is not an error.
    return null;
  }
}

function rememberWorkflows(list) {
  try {
    localStorage.setItem(REMEMBER_KEY, JSON.stringify(list));
  } catch {
    // Storage full or disabled — the page just falls back next load.
  }
}

/**
 * `{workflows, known}` — the list, and whether it is an ANSWER or a guess.
 *
 * `known === false` means nobody has told this browser what is live: not the
 * server just now, and not a remembered answer from last time. A page that
 * makes a CLAIM about the list ("Three workflows", a grid of cards, the hero
 * tiles) must wait for it; anything else may use `workflows` straight away.
 */
export function useLiveWorkflowsState() {
  const [live, setLive] = useState(rememberedWorkflows);
  const [known, setKnown] = useState(() => rememberedWorkflows() !== null);

  useEffect(() => {
    let alive = true;
    api
      .publicWorkflows()
      .then((r) => {
        if (!alive) return;
        if (Array.isArray(r?.workflows)) {
          setLive(r.workflows);
          rememberWorkflows(r.workflows);
        }
        setKnown(true);
      })
      // Deliberately silent — there is nothing a visitor could do about it. But
      // `known` still flips: a failed call is an answered question as far as
      // this page is concerned, and the fallback below is what to draw.
      .catch(() => {
        if (alive) setKnown(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  return { workflows: joinCopy(live), known };
}

/** Just the list, for callers with nothing to hide. */
export function useLiveWorkflows() {
  return useLiveWorkflowsState().workflows;
}

function joinCopy(live) {
  if (!live?.length) return FALLBACK;
  // ⚠ `icon` IS CARRIED THROUGH, and it is not decoration. It is the emoji an
  // administrator typed in the admin panel, and it is the LAST fallback in the
  // hero art: `WorkflowIcon` draws nothing at all for an id this build has no
  // glyph for, so a workflow added after this was written would get an empty
  // tile without it. See `TileArt` below.
  const joined = live
    .filter((w) => COPY[w.id])
    .map((w) => ({
      ...COPY[w.id],
      title: w.label || COPY[w.id].title,
      status: w.status,
      icon: w.icon || "",
    }));
  // An answer that names nothing this page has copy for is not an answer worth
  // drawing — same fail-open rule one more time.
  return joined.length ? joined : FALLBACK;
}

/**
 * The picture an administrator uploaded for each workflow's hero tile, by id.
 *
 * ⚠ IT IS ALLOWED TO BE EMPTY, AND ON A FRESH INSTALL IT IS. The hero's four
 * tiles are hand-drawn SVG and stay that way until somebody uploads something;
 * this hook only ever REPLACES a drawing, so a page that never gets an answer
 * looks exactly as it did before the panel existed. Same fail-open rule as
 * `useLiveWorkflows` above, one degree softer — there is not even a fallback to
 * reach for, because the fallback is the drawing itself.
 *
 * ⚠ AND IT IS ALREADY FILTERED. `/public/landing/art` asks `features.py` which
 * workflows a stranger may see and drops the rest, so a workflow hidden in the
 * admin panel has no picture in this map even if one is stored — which is what
 * *"jo hide hai uska nhi dikhe"* asked for. The tile list below is filtered by
 * `useLiveWorkflows` as well; two filters, because the one in the browser
 * decides the LAYOUT and the one on the server decides what is PUBLISHED.
 */
function useLandingArt() {
  const [art, setArt] = useState({});

  useEffect(() => {
    let alive = true;
    api
      .publicLandingArt()
      .then((r) => {
        if (alive && r?.art && typeof r.art === "object") setArt(r.art);
      })
      // Deliberately silent, like the workflow list: there is nothing a visitor
      // could do about it, and the drawn tiles are already the right thing.
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  return art;
}

// ⚠ WHAT THE LIVE WORKFLOWS ACTUALLY DO. Three of these used to describe the
// character sheets — "Full turnarounds", "Part by part", "Auto clean-up" — and a
// features list selling a switched-off workflow is worse than a shorter one.
// These stay emoji on purpose: they are generic ideas (a voice, money, a pair of
// scissors), not the app's own workflows, and `WorkflowIcon` has no glyph for an
// idea. The workflow cards above and the stages below are the drawn ones.
const FEATURES = [
  {
    icon: "🗣️",
    title: "Your language, all the way",
    body: "Plan and script in the language you publish in, not only in English.",
  },
  {
    icon: "🎭",
    title: "Cast & set continuity",
    body: "A written bible plus look anchors keep faces and rooms the same across shots.",
  },
  {
    icon: "🏃",
    title: "See the motion first",
    body: "Key poses block a shot out cheaply, before anything expensive is rendered.",
  },
  {
    icon: "💰",
    title: "Nothing spent by surprise",
    body: "Paid video renders are estimated and capped before a single second is billed.",
  },
  {
    icon: "✂️",
    title: "No editing suite needed",
    body: "Captions, dialogue and music on a real timeline, cut to MP4 by ffmpeg — no credits.",
  },
  {
    icon: "⚡",
    title: "Regenerate anything",
    body: "Not happy with one part, panel or shot? Tune its prompt and redo just that piece.",
  },
];

export default function Landing({ onGetStarted, onExplore, theme, onToggleTheme }) {
  // The shop window, seen by somebody with no account — which is the whole
  // reason `/public/branding` needs no token. The name appears TWICE on this
  // page (nav and footer) and both read the one store, so they cannot disagree.
  const brand = useBranding();
  // ⚠ `known` IS WHY A HIDDEN WORKFLOW NO LONGER FLASHES UP HERE. See the hook.
  // It is false only on a browser that has never had an answer; every reload
  // after the first starts out correct.
  const { workflows: shown, known } = useLiveWorkflowsState();
  // The hero tiles' pictures, uploaded from the admin panel's Landing tab. An
  // empty map is normal and means "draw them" — see `useLandingArt`.
  const art = useLandingArt();
  const allLive = shown.every((w) => !w.status || w.status === "live");

  return (
    <div className="landing">
      {/* ---------- Top nav ---------- */}
      <nav className="landing-nav">
        <span className="brand small">
          <Logo /> {brand.name}
        </span>
        <div className="landing-nav-links">
          {/* ⚠ THE SHOP WINDOW, AND IT IS FIRST. Explore is the page that shows
              the WORK — films and stills somebody can actually watch — and this
              page is the one that explains it. A prospect who wants to see
              before they read should not have to scroll to find the door.
              Rendered only when a handler is handed down, so the page still
              stands alone in tests and previews. */}
          {onExplore && (
            <a
              href="#explore"
              onClick={(e) => {
                e.preventDefault();
                onExplore();
              }}
            >
              See the work
            </a>
          )}
          <a href="#workflows">Workflows</a>
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          {/* ---------- Light / dark, before the CTA ----------
              ⚠ THE ONLY THEME SWITCH A LOGGED-OUT VISITOR CAN REACH. Every
              other copy lives behind the sign-in (the sidebar's) or behind an
              admin role (the top bar's), so somebody reading this page on a
              white desk had no way to turn the lights on at all.
              Icon-only, for the same reason the admin bar's is: a nav has no
              room for a word, and the sun/moon is the one everybody reads.
              Rendered only when App hands down a handler — the page still
              works standalone (tests, storyboard previews) without one. */}
          {onToggleTheme && (
            <button
              type="button"
              className="lp-theme"
              onClick={onToggleTheme}
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
              aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            >
              {theme === "dark" ? "☀️" : "🌙"}
            </button>
          )}
          <button className="btn small primary nav-cta" onClick={onGetStarted}>
            Get started
          </button>
        </div>
      </nav>

      {/* ---------- Hero ---------- */}
      <header className="hero">
        <div className="hero-copy">
          {/* ⚠ NO COUNT UNTIL THE COUNT IS KNOWN. "Three workflows" that turns
              into "Six workflows" and back is the flash at its most readable —
              and on a first visit the number would be the BUILT-IN one, which
              includes whatever the administrator has hidden. */}
          <span className="pill">
            ✨ {known
              ? shown.length === 1
                ? "One workflow"
                : `${countWord(shown.length).replace(/^./, (c) => c.toUpperCase())} workflows`
              : "Every workflow"}{" "}
            · one AI studio
          </span>
          <h1 className="hero-title">
            From a <span className="grad">sentence</span> to a{" "}
            <span className="grad">finished animated cut</span>.
          </h1>
          <p className="hero-sub">
            Plan the story, draw the storyboard, block the motion and cut the
            video — every stage is its own workflow, and they hand off to each
            other. No modelling, no drawing, no editing suite.
          </p>
          <div className="hero-actions">
            <button className="btn primary lg" onClick={onGetStarted}>
              Get started — it's free →
            </button>
            {/* ⚠ THE SECOND HERO BUTTON NOW LEAVES THE PAGE, and that is the
                point: it used to jump to a grid of PARAGRAPHS further down this
                same page. Explore carries the films. Somebody who did not
                believe the headline is not going to be convinced by more
                writing about it. Falls back to the anchor when this page is
                rendered without a handler. */}
            {onExplore ? (
              <button className="btn lg ghost-bordered" onClick={onExplore}>
                See the work →
              </button>
            ) : (
              <a className="btn lg ghost-bordered" href="#workflows">
                See the workflows
              </a>
            )}
          </div>

          {/* ---------- A live discount, if there is one ----------
              ⚠ THE ONLY PLACE A COUPON REACHES SOMEBODY WHO HAS NOT SIGNED UP.
              `GET /billing/tiers` is public, so this page can carry the same
              card the pricing modal does; a code that only exists behind the
              sign-in is a discount aimed at people who already converted.

              ⚠ IN THE HERO, NOT IN A BAND BELOW IT. The hero is
              `min-height: 100vh`, so anything after it starts one whole screen
              down — a promotion under the fold is a promotion nobody scrolled
              to, which is the bug this card was added to fix, moved rather than
              solved.

              ⚠ AND IT CARRIES NO BUTTON HERE. "Get started — it's free" is two
              centimetres above it; a second CTA saying the same thing is noise.
              An Apply would be worse still — `POST /billing/coupon` is signed-in
              only and would 401 in front of a prospect. The code is readable and
              copyable, which is all a visitor needs to carry it through sign-up.

              ⚠ ONE CARD, NOT A LIST. A hero can hold a promotion; a column of
              them is a coupon site. Renders nothing when no offer is running. */}
          <OfferStrip className="hero-offer" limit={1} />

          {/* The pipeline as a single readable line, so the promise above is
              concrete before anyone scrolls. */}
          <ul className="lp-flow" aria-label="Pipeline stages">
            {shown.map((w, i) => (
              <li key={w.id}>
                {i > 0 && <span className="lp-flow-arrow">→</span>}
                <span className="lp-flow-step">{w.short || w.title}</span>
              </li>
            ))}
          </ul>

          {/* ⚠ THE SECOND HALF IS CONDITIONAL, and it has to be: "all four are
              live today" is a promise, and the moment an administrator stages
              one as "soon" it becomes a false one. */}
          <p className="tiny muted hero-note">
            Sign in with Google or email
            {known && allLive && ` · All ${countWord(shown.length)} workflows are live today`}
          </p>
        </div>

        <div className="hero-art" aria-hidden="true">
          <PipelineArt workflows={known ? shown : []} art={art} />
        </div>
      </header>

      {/* ---------- Workflows: the whole point of the rewrite ---------- */}
      <section id="workflows" className="section">
        <h2 className="section-title">Every workflow in the studio</h2>
        <p className="section-sub muted">
          Start anywhere. Each one stands on its own, and each one feeds the
          next.
        </p>

        {/* ⚠ EMPTY UNTIL THE ANSWER LANDS, ON A FIRST VISIT ONLY. Drawing the
            built-in six here and removing three of them a moment later is
            advertising a workflow that has been switched off — the exact thing
            `/public/workflows` was added to stop. A returning visitor never
            sees this branch: the remembered answer makes `known` true before
            the first paint. */}
        <div className="lp-wf-grid">
          {(known ? shown : []).map((w) => (
            <article className="lp-wf-card" key={w.id}>
              <div className="lp-wf-top">
                <span className="lp-wf-ico">
                  <WorkflowIcon id={w.id} />
                </span>
                {/* ⚠ THE BADGE READS THE STATUS NOW. Every card saying "Live"
                    while the rail shows one of them greyed out with a "Soon"
                    pill is the same lie this page was fetching the list to
                    stop. */}
                {w.status === "soon" ? (
                  <span className="lp-wf-live lp-wf-soon">Soon</span>
                ) : (
                  <span className="lp-wf-live">
                    <span className="lp-wf-dot" /> Live
                  </span>
                )}
              </div>
              <h3>{w.title}</h3>
              <p className="muted">{w.body}</p>
              <div className="lp-wf-tags">
                {w.tags.map((t) => (
                  <span className="lp-tag" key={t}>
                    {t}
                  </span>
                ))}
              </div>
            </article>
          ))}
        </div>

        <div className="lp-wf-foot">
          <button className="btn primary lg" onClick={onGetStarted}>
            Open the studio →
          </button>
        </div>
      </section>

      {/* ---------- How it works ---------- */}
      <section id="how" className="section lp-section-alt">
        <h2 className="section-title">How a project moves</h2>
        <p className="section-sub muted">
          The pipeline runs for you — you just approve each stage.
        </p>

        <div className="steps">
          {shown.map((w, i) => (
            <div className="step-card" key={w.id}>
              <div className="step-num">{i + 1}</div>
              <div className="step-icon">
                <WorkflowIcon id={w.id} />
              </div>
              <h3>{w.stage?.title || w.title}</h3>
              <p className="muted">{w.stage?.body || w.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------- Features ---------- */}
      <section id="features" className="section">
        <h2 className="section-title">Built for people who ship</h2>
        <p className="section-sub muted">
          The details that decide whether the output is usable.
        </p>
        <div className="feature-grid">
          {FEATURES.map((f) => (
            <div className="feature-card" key={f.title}>
              <div className="feature-icon">{f.icon}</div>
              <h3>{f.title}</h3>
              <p className="muted tiny">{f.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------- Bottom CTA ---------- */}
      <section className="cta-band">
        <h2>Ready to build your first scene?</h2>
        <p className="muted">
          Sign in and run any workflow — planning, characters, boards, poses or
          video.
        </p>
        <button className="btn primary lg" onClick={onGetStarted}>
          Get started →
        </button>
      </section>

      <footer className="landing-footer">
        <span className="brand small">
          <Logo /> {brand.name}
        </span>
        <span className="tiny muted">
          AI planning · character assets · storyboards · projects · video
        </span>
      </footer>
    </div>
  );
}

// Hero illustration — THE FOUR LIVE WORKFLOWS, ONE TILE EACH, plus the pose
// strip overhanging the frame.
//
// ⚠ IT USED TO BE A CHARACTER TURNAROUND SHEET, and that was the most
// misleading thing on the page: the biggest picture in the hero was the output
// of the one workflow that is switched OFF. A visitor read "this makes
// characters" before reading a word of the copy.
//
// So it is four tiles now, in pipeline order, each drawing what that workflow
// HANDS YOU. Pure SVG/CSS: no image files, nothing to download.
//
// ⚠ EACH TILE HAS TO LOOK LIKE THE THING, NOT LIKE A DIAGRAM OF IT. First
// attempt was four boxes of grey bars and it was rejected on sight — a page of
// identical lines is not a script, four identical panels are not a board. What
// separates them now is INTERNAL VARIETY: the script tile is indented like a
// screenplay, the four board panels are four DIFFERENT shots, the poses raise
// their arms across the strip, the timeline has tracks its clips sit inside.
//
// ⚠ THE TILES ARE WHITE IN BOTH THEMES, DELIBERATELY. They are pictures of
// OUTPUT — paper and screens — not panels of the app, so their ink comes from
// the fixed `--lp-sheet*` constants in landing.css and not from the palette. A
// palette token here turned the drawings invisible the moment the lights went on.
//
// ⚠ THE TILES USED TO BE FOUR FIXED, HAND-WRITTEN LINES AND THEY ARE NOT ANY
// MORE. The note here read *"THE FOUR TILES ARE FIXED; THE STRIP IS NOT"*, on
// the grounds that a 2×2 with three things in it is a hole rather than a grid —
// and it was right about the geometry and wrong about the price. What it bought
// was a hero whose four biggest pictures could not follow the workflow list at
// all: hide one in the admin panel and its tile stayed on the front page.
//
// So the tiles are the LIVE workflows now, same list as the flow line and the
// cards below, and the hole is handled by capping rather than by hard-coding:
// `HERO_TILES` of them, and WHICH four is the `order` an administrator sets in
// the Features tab. Asked for outright: *"jo live hai uska dikhe image yaha pe
// aur jo hide hai uska nhi dikhe magar mai jab hode se unhode karun to yeha pe
// image aa jana chaiye aur aage ami aur v workflow banau to o v same fuctiuon
// mai chale."*
//
// ⚠ AND EACH TILE IS A PICTURE FIRST AND A DRAWING SECOND. `art[id]` is what the
// admin panel's Landing tab uploaded; with nothing uploaded the tile falls back
// to the SVG it always was (`ART_BY_ID`), and a workflow that never had one —
// anything added after this was written — falls back to its own glyph. Three
// steps, so the page is never empty and never needs a code change to grow.
//
// ⚠ FOUR IS A LAYOUT NUMBER, NOT A LIMIT ON WHAT MAY HOLD A PICTURE. It matches
// `landing.HERO_TILES` on the server, which is what the admin panel reads to
// tell an operator that their fifth workflow has a picture and no tile.
const HERO_TILES = 4;

function PipelineArt({ workflows = [], art = {} }) {
  // ⚠ THE SAME SLICE THE STRIP TAKES, and taken once so the two can never show
  // different workflows in the same corner of one picture.
  const tiles = (workflows.length ? workflows : FALLBACK).slice(0, HERO_TILES);
  return (
    <div className="lp-art-stack">
      {/* ⚠ NO EMOJI IN THIS CHIP. It was "🎬 Final cut · MP4" and the clapper
          drew as a grey tofu box on Windows — the one place on the page where
          the OS got to pick the artwork. It is the brand's own sparkle now, the
          same shape as the one in `Logo.jsx`. */}
      <span className="lp-art-chip lp-art-chip-a">
        <svg viewBox="-1 -1 2 2" className="lp-chip-spark" aria-hidden="true">
          <path d="M0 -1 C 0.13 -0.38, 0.38 -0.13, 1 0 C 0.38 0.13, 0.13 0.38, 0 1 C -0.13 0.38, -0.38 0.13, -1 0 C -0.38 -0.13, -0.13 -0.38, 0 -1 Z" />
        </svg>
        Made with AI
      </span>

      <div className="art-frame">
        <div className="lp-art-steps">
          {tiles.map((w, i) => (
            <ArtStep
              key={w.id}
              n={String(i + 1)}
              label={w.tile || w.short || w.title}
              photo={art[w.id] || ""}
            >
              <TileArt id={w.id} icon={w.icon} />
            </ArtStep>
          ))}
        </div>
        {/* ⚠ NO NUMBER IN THIS LINE. It said "Four workflows" and would have
            been wrong the first time a fifth one launched — and unlike the copy
            above it, a caption inside the artwork is the last place anybody
            thinks to check. */}
        <div className="art-caption">Every stage, one hand-off</div>
      </div>

      {/* ⚠ THIS STRIP CAME BACK AFTER BEING DELETED, and the reason it was
          deleted was wrong. It looked redundant next to the key-poses tile —
          the same stick figure four times over — but its job was never to
          explain a workflow. It is the only element that BREAKS THE FRAME: it
          hangs off the bottom edge, at its own angle, which is what stops the
          hero art reading as one flat rectangle pasted on the page.

          ⚠ AND ITS FOUR CELLS ARE THE FOUR WORKFLOWS NOW, NOT FOUR POSES.
          Asked for directly — "ye four ka v icon live workflow se match karna".
          The figures were left over from the strip's old life as a pose reel,
          which is the same mistake the turnaround sheet was: art on the hero
          that belongs to a workflow rather than to the product. Mapped straight
          off `WORKFLOWS` so the strip, the cards below and the rail can never
          disagree about what is live.

          ⚠ SLICED TO FOUR. The strip is a fixed-width floating element with a
          play button on the end; a fifth workflow added to the array above must
          grow the CARDS, not push this off the frame.

          The play button is the point of the whole thing: these four steps, and
          the cut you press at the end of them. */}
      <div className="lp-art-strip">
        {/* ⚠ THE SAME `tiles` THE GRID DRAWS, not its own slice of the list.
            Two slices of one array is how the strip and the tiles end up
            disagreeing about the fourth workflow. And these stay GLYPHS even
            when a tile above has a photograph: the cell is 44px, and a
            photograph in it is a smudge — the strip's job is the composition,
            not the content. */}
        {tiles.map((w) => (
          <div className="lp-art-cell" key={w.id}>
            {/* The emoji fallback for the same reason `TileArt` needs it: an id
                this build has no glyph for would otherwise draw an empty cell. */}
            <WorkflowIcon id={w.id} fallback={w.icon} />
          </div>
        ))}
        <span className="lp-art-play" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <polygon points="8 5 19 12 8 19" />
          </svg>
        </span>
      </div>
    </div>
  );
}

// One tile: the step number, the picture, the name of what you get.
//
// ⚠ `photo` WINS OVER `children`, and the children are still passed in every
// time. That is the fallback in one line: the drawing is always built and is
// simply not mounted when there is an uploaded picture for this workflow, so a
// picture that is removed in the admin panel puts the drawing back with no
// second code path to keep in step.
//
// ⚠ AND THE SHEET LOSES ITS PADDING FOR A PHOTOGRAPH. `.lp-art-sheet` insets its
// contents by 0.3rem, which is right for a drawing sitting on paper and reads as
// a mistake around a photograph — the picture has to go to the tile's own edges.
function ArtStep({ n, label, photo = "", children }) {
  return (
    <div className="lp-art-step">
      <span className="lp-art-step-n">{n}</span>
      <div className={`lp-art-sheet${photo ? " has-photo" : ""}`}>
        {photo ? (
          // `alt=""` on purpose: the whole hero art is `aria-hidden` and this is
          // decoration. `loading="eager"` is the default and is what we want —
          // it is above the fold on the page every visitor lands on.
          <img className="lp-art-photo" src={api.absoluteUrl(photo)} alt="" />
        ) : (
          children
        )}
      </div>
      <span className="lp-art-step-label">{label}</span>
    </div>
  );
}

// The drawing for one workflow — the tile as it looks with no picture uploaded.
//
// ⚠ IT IS A LOOKUP, NOT A SWITCH, AND IT HAS TWO DEFAULTS. `ART_BY_ID` at the
// foot of this file holds the four drawings that have always been here; a
// workflow with no drawing of its own — the two that were never in the hero, and
// anything added after this was written — gets its rail glyph instead. That is
// the reason a new workflow draws SOMETHING on day one, with an uploaded picture
// replacing it.
//
// ⚠ AND `icon` IS THE THIRD RUNG, WHICH IS NOT OPTIONAL. `WorkflowIcon` renders
// NOTHING for an id it has no glyph for — that is deliberate over there ("the
// wrong picture is worse than the emoji") — so without the server's emoji passed
// in as the fallback, a workflow launched after this build would get a blank
// white tile in the hero. Three rungs: picture, drawing, glyph-or-emoji.
function TileArt({ id, icon = "" }) {
  const Drawn = ART_BY_ID[id];
  if (Drawn) return <Drawn />;
  return (
    <span className="lp-art-glyph" aria-hidden="true">
      <WorkflowIcon id={id} fallback={icon} />
    </span>
  );
}

// 1 — Plan & Script. ⚠ LAID OUT LIKE A SCREENPLAY, which is the whole trick:
// a scene heading hard left, the character name INDENTED to the middle, the
// dialogue indented under it. That shape is recognisable from across a room,
// and a stack of equal-length grey bars — what this was first — is not.
function ScriptArt() {
  return (
    <svg viewBox="0 0 100 74" className="lp-art-svg" aria-hidden="true">
      {/* Scene heading. Gold, because it is the one line a writer scans for. */}
      <rect x="12" y="9" width="36" height="5" rx="2.5" className="lp-fill-gold" />
      <rect x="12" y="20" width="72" height="3.4" rx="1.7" className="lp-fill-soft" />
      <rect x="12" y="27" width="58" height="3.4" rx="1.7" className="lp-fill-soft" />
      {/* Character name, indented — the centre column of a script page. */}
      <rect x="38" y="37" width="22" height="4.2" rx="2.1" className="lp-fill-ink" />
      <rect x="26" y="46" width="48" height="3.4" rx="1.7" className="lp-fill-soft" />
      <rect x="26" y="53" width="40" height="3.4" rx="1.7" className="lp-fill-soft" />
      <rect x="12" y="63" width="26" height="5" rx="2.5" className="lp-fill-gold" />
    </svg>
  );
}

// A stick figure drawn straight into a tile's own coordinate space. `s` scales
// it, `drop` lowers the arms. Used by the board and the pose strip so a person
// is the same person in both tiles.
function TileFigure({ cx, cy, s = 1, drop = 0 }) {
  return (
    <g className="lp-fill-ink">
      <circle cx={cx} cy={cy} r={3.4 * s} />
      <rect x={cx - 1.6 * s} y={cy + 3.6 * s} width={3.2 * s} height={13 * s} rx={1.6 * s} />
      <rect
        x={cx - 7.5 * s}
        y={cy + (6.4 + drop) * s}
        width={15 * s}
        height={2.2 * s}
        rx={1.1 * s}
      />
      <rect x={cx - 1.9 * s} y={cy + 16 * s} width={1.5 * s} height={11 * s} rx={0.75 * s} />
      <rect x={cx + 0.4 * s} y={cy + 16 * s} width={1.5 * s} height={11 * s} rx={0.75 * s} />
    </g>
  );
}

// 2 — Script to Storyboard. ⚠ FOUR DIFFERENT SHOTS, NOT ONE SHOT FOUR TIMES.
// A wide, a close-up, a two-hander and a shot with a camera move on it — that
// difference IS what a storyboard is for, and drawing the same figure in all
// four panels said the opposite.
function BoardArt() {
  const panel = { width: 37, height: 25, rx: 3 };
  return (
    <svg viewBox="0 0 100 74" className="lp-art-svg" aria-hidden="true">
      {/* Wide: horizon low, figure small and off-centre. */}
      <g>
        <rect x="10" y="8" {...panel} className="lp-stroke-soft" />
        <path d="M14 26h29" className="lp-stroke-soft" />
        <TileFigure cx={22} cy={14} s={0.42} />
      </g>
      {/* Close-up: the head fills the frame and the panel crops it. */}
      <g>
        <clipPath id="lp-cu">
          <rect x="53" y="8" {...panel} />
        </clipPath>
        <rect x="53" y="8" {...panel} className="lp-stroke-soft" />
        <g clipPath="url(#lp-cu)" className="lp-fill-ink">
          <circle cx="71.5" cy="19" r="7" />
          <path d="M62 33.5a9.5 9.5 0 0 1 19 0Z" />
        </g>
      </g>
      {/* Two-hander: a conversation, which is most of any board. */}
      <g>
        <rect x="10" y="41" {...panel} className="lp-stroke-soft" />
        <path d="M14 59h29" className="lp-stroke-soft" />
        <TileFigure cx={21} cy={46} s={0.42} />
        <TileFigure cx={35} cy={46} s={0.42} drop={2} />
      </g>
      {/* And one with a camera move marked on it. */}
      <g>
        <rect x="53" y="41" {...panel} className="lp-stroke-soft" />
        <path d="M57 59h29" className="lp-stroke-soft" />
        <TileFigure cx={64} cy={46} s={0.42} />
        <path d="M72 52h11m-2.5-2.5 2.5 2.5-2.5 2.5" className="lp-stroke-move" />
      </g>
    </svg>
  );
}

// 3 — Image to Animatic Image. A filmstrip with real sprocket holes down both
// rails, and the same figure RAISING ITS ARMS across the four frames.
//
// ⚠ THE MOTION IS THE POINT AND IT HAS TO BE BIG. The first version stepped the
// arms 7 units on a figure drawn at a third of this size, which at tile scale is
// under a pixel of travel — four identical drawings, which is exactly what this
// workflow is not.
function PosesArt() {
  const holes = [5, 16, 27, 38, 49, 60, 71, 82];
  return (
    <svg viewBox="0 0 100 74" className="lp-art-svg" aria-hidden="true">
      {holes.map((x) => (
        <rect key={`t${x}`} x={x} y="4" width="7" height="4.5" rx="1.6" className="lp-fill-hole" />
      ))}
      {holes.map((x) => (
        <rect key={`b${x}`} x={x} y="65.5" width="7" height="4.5" rx="1.6" className="lp-fill-hole" />
      ))}
      {[26, 48, 70].map((x) => (
        <path key={x} d={`M${x} 13v48`} className="lp-stroke-frame" />
      ))}
      {[0, 1, 2, 3].map((i) => (
        <TileFigure key={i} cx={15 + i * 22} cy={26} s={0.62} drop={(3 - i) * 3.6} />
      ))}
    </svg>
  );
}

// 4 — Video Editor. ⚠ THE CLIPS SIT INSIDE TRACKS NOW. Three coloured bars
// floating on white read as a bar chart; the same three bars inside three empty
// lanes read as a timeline, and the lane is most of what makes it one.
//
// ⚠ THE COLOURS ARE THE EDITOR'S OWN THREE — orange footage, pink stills, mint
// captions, the same rule the real timeline follows (see the palette notes in
// theme.css). Fixed values, not the `--clip-*` tokens: those are translucent
// washes tuned for the app's dark track and would vanish on a white tile.
function TimelineArt() {
  const tracks = [
    { y: 20, x: 8, w: 44, fill: "#f0a06a" },
    { y: 34, x: 30, w: 52, fill: "#ef8fb4" },
    { y: 48, x: 8, w: 30, fill: "#8fce9f" },
  ];
  return (
    <svg viewBox="0 0 100 74" className="lp-art-svg" aria-hidden="true">
      <path d="M6 13h88" className="lp-stroke-soft" />
      {[6, 28, 50, 72, 94].map((x) => (
        <path key={x} d={`M${x} 8.5v4.5`} className="lp-stroke-soft" />
      ))}
      {tracks.map((t) => (
        <g key={t.y}>
          <rect x="6" y={t.y} width="88" height="11" rx="3" className="lp-fill-track" />
          <rect x={t.x} y={t.y + 1.6} width={t.w} height="7.8" rx="2.4" fill={t.fill} />
        </g>
      ))}
      {/* The playhead, with the grab handle the real one has. */}
      <path d="M64 10v52" className="lp-stroke-head" />
      <path d="M60 6h8v5l-4 3.5L60 11Z" className="lp-fill-gold" />
    </svg>
  );
}

// The four drawings, BY WORKFLOW ID — which is the only thing that made the
// old hero honest and was the one thing the old hero did not write down.
//
// ⚠ IT WAS FOUR HARD-CODED LINES OF JSX IN PIPELINE ORDER, and that is exactly
// how the tiles came loose from the workflow list: nothing in the file said
// which drawing belonged to which workflow, so nothing could follow one being
// hidden. Named here, they can.
//
// ⚠ TWO WORKFLOWS DELIBERATELY HAVE NO ENTRY. `text-to-image` and
// `animatics-to-video` were never in the hero art and there is no drawing to
// give them; `TileArt` falls back to their rail glyph, which is what any
// workflow added later gets too. A missing entry here is a fallback, not a bug —
// the only thing that must never happen is an id in this map that no workflow
// has, and that is a dead drawing rather than a broken tile.
const ART_BY_ID = {
  "plan-and-script": ScriptArt,
  "script-to-storyboard": BoardArt,
  "create-animatic-image": PosesArt,
  "storyboard-to-animatics": TimelineArt,
};
