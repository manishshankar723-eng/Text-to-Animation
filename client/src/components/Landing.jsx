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
// `short` is the one-word name for the pipeline line in the hero. `stage` is
// this workflow's beat in "How a project moves" — it lives here rather than in
// its own array so the two can never drift apart.
const WORKFLOWS = [
  {
    id: "plan-and-script",
    title: "Plan & Script",
    body:
      "Talk to a planning agent that asks clickable questions, researches reference videos on YouTube, then hands you a content calendar and a shot-ready script in your language.",
    tags: ["Chat planning", "YouTube research", "Exports"],
    short: "Plan",
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
const COPY = Object.fromEntries(WORKFLOWS.map((w) => [w.id, w]));

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
function useLiveWorkflows() {
  const [live, setLive] = useState(null);

  useEffect(() => {
    let alive = true;
    api
      .publicWorkflows()
      .then((r) => {
        if (alive && Array.isArray(r?.workflows)) setLive(r.workflows);
      })
      // Deliberately silent. There is nothing a visitor could do about it, and
      // the fallback below is already the right thing to draw.
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  if (!live?.length) return FALLBACK;
  const joined = live
    .filter((w) => COPY[w.id])
    .map((w) => ({ ...COPY[w.id], title: w.label || COPY[w.id].title, status: w.status }));
  // An answer that names nothing this page has copy for is not an answer worth
  // drawing — same fail-open rule one more time.
  return joined.length ? joined : FALLBACK;
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

export default function Landing({ onGetStarted, theme, onToggleTheme }) {
  const shown = useLiveWorkflows();
  const allLive = shown.every((w) => !w.status || w.status === "live");

  return (
    <div className="landing">
      {/* ---------- Top nav ---------- */}
      <nav className="landing-nav">
        <span className="brand small">
          <Logo /> Aniwala AI Studio
        </span>
        <div className="landing-nav-links">
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
          <span className="pill">
            ✨ {shown.length === 1
              ? "One workflow"
              : `${countWord(shown.length).replace(/^./, (c) => c.toUpperCase())} workflows`}{" "}
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
            <a className="btn lg ghost-bordered" href="#workflows">
              See the workflows
            </a>
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
            {allLive && ` · All ${countWord(shown.length)} workflows are live today`}
          </p>
        </div>

        <div className="hero-art" aria-hidden="true">
          <PipelineArt workflows={shown} />
        </div>
      </header>

      {/* ---------- Workflows: the whole point of the rewrite ---------- */}
      <section id="workflows" className="section">
        <h2 className="section-title">Every workflow in the studio</h2>
        <p className="section-sub muted">
          Start anywhere. Each one stands on its own, and each one feeds the
          next.
        </p>

        <div className="lp-wf-grid">
          {shown.map((w) => (
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
          <Logo /> Aniwala AI Studio
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
// ⚠ THE FOUR TILES ARE FIXED; THE STRIP IS NOT. Each tile is a hand-drawn
// picture of one workflow's output, laid out as a 2×2 — and a 2×2 with three
// things in it is a hole, not a grid. So the tiles stay as the picture of the
// pipeline, and the STRIP, which is a flex row of icons and degrades to any
// count, is the part that tracks what is actually live.
function PipelineArt({ workflows = [] }) {
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
          <ArtStep n="1" label="Plan & script">
            <ScriptArt />
          </ArtStep>
          <ArtStep n="2" label="Storyboard">
            <BoardArt />
          </ArtStep>
          <ArtStep n="3" label="Key poses">
            <PosesArt />
          </ArtStep>
          <ArtStep n="4" label="Video">
            <TimelineArt />
          </ArtStep>
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
        {(workflows.length ? workflows : FALLBACK).slice(0, 4).map((w) => (
          <div className="lp-art-cell" key={w.id}>
            <WorkflowIcon id={w.id} />
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

// One tile: the step number, the drawing, the name of what you get.
function ArtStep({ n, label, children }) {
  return (
    <div className="lp-art-step">
      <span className="lp-art-step-n">{n}</span>
      <div className="lp-art-sheet">{children}</div>
      <span className="lp-art-step-label">{label}</span>
    </div>
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
