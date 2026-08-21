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
const WORKFLOWS = [
  {
    icon: "🗓️",
    title: "Plan & Script",
    body:
      "Talk to a planning agent that asks clickable questions, researches reference videos on YouTube, then hands you a content calendar and a shot-ready script in your language.",
    tags: ["Chat planning", "YouTube research", "Exports"],
  },
  {
    icon: "🖼️",
    title: "Text to Turnaround Image",
    body:
      "Describe a character or upload one photo. Every part — hair, face, body, garments, shoes, props — comes back as a clean 4-view turnaround sheet, split and normalized.",
    tags: ["4 views", "Per-part sheets", "One-click 3D"],
  },
  {
    icon: "📝",
    title: "Script to Storyboard",
    body:
      "A script becomes a shot list, then drawn panels that keep your cast and sets consistent from shot to shot. Edit the board, export a PDF, share a link.",
    tags: ["Shot list", "Continuity", "PDF + share"],
  },
  {
    icon: "🖼️",
    title: "Image to Animatic Image",
    body:
      "Take one approved panel and block its motion out as key poses — about four drawings a second for a 2–10s shot — each anchored on the panel so the character never drifts.",
    tags: ["Key poses", "2–10s shots", "Works on a copy"],
  },
  {
    icon: "🎞️",
    title: "Image to AI Video",
    body:
      "Send shots to Veo image-to-video, see the cost before you spend anything, render shot by shot, then assemble the clips into one final cut.",
    tags: ["Veo render", "Cost estimate", "Final cut"],
  },
  {
    icon: "🎬",
    title: "Video Editor",
    body:
      "Turn a board into a timed MP4 with captions and audio — dialogue, music, the lot. Runs on ffmpeg locally, so it spends no AI credits at all.",
    tags: ["Timed edit", "Captions + audio", "No credits"],
  },
];

// The pipeline, told as four beats rather than six workflows — this is the part
// that answers "where does my idea actually go?".
const STAGES = [
  {
    n: "1",
    icon: "🗓️",
    title: "Plan it",
    body:
      "Decide what you're making before you make any of it — topic, format, language, script.",
  },
  {
    n: "2",
    icon: "🎨",
    title: "Draw it",
    body:
      "Characters as turnaround sheets, scenes as storyboard panels, all style-matched.",
  },
  {
    n: "3",
    icon: "🏃",
    title: "Move it",
    body:
      "Key poses per shot, or a straight jump to AI video — your call, shot by shot.",
  },
  {
    n: "4",
    icon: "🎬",
    title: "Ship it",
    body: "An animatic MP4, a rendered final cut, a PDF board or a ZIP of assets.",
  },
];

const FEATURES = [
  {
    icon: "🔄",
    title: "Full turnarounds",
    body: "Front, left, three-quarter and back for consistent, riggable references.",
  },
  {
    icon: "🧩",
    title: "Part by part",
    body: "Hair, face, body, jackets, sarees, pants, shoes, goggles, headphones.",
  },
  {
    icon: "🎭",
    title: "Cast & set continuity",
    body: "A written bible plus look anchors keep faces and rooms the same across shots.",
  },
  {
    icon: "✂️",
    title: "Auto clean-up",
    body: "Pure-white backgrounds, auto-crop and normalized framing — no manual editing.",
  },
  {
    icon: "💰",
    title: "Nothing spent by surprise",
    body: "Paid video renders are estimated and capped before a single second is billed.",
  },
  {
    icon: "⚡",
    title: "Regenerate anything",
    body: "Not happy with one part, panel or shot? Tune its prompt and redo just that piece.",
  },
];

export default function Landing({ onGetStarted }) {
  return (
    <div className="landing">
      {/* ---------- Top nav ---------- */}
      <nav className="landing-nav">
        <span className="brand small">🎭 Character Asset Studio</span>
        <div className="landing-nav-links">
          <a href="#workflows">Workflows</a>
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <button className="btn small primary nav-cta" onClick={onGetStarted}>
            Get started
          </button>
        </div>
      </nav>

      {/* ---------- Hero ---------- */}
      <header className="hero">
        <div className="hero-copy">
          <span className="pill">✨ Six workflows · one AI studio</span>
          <h1 className="hero-title">
            From a <span className="grad">sentence</span> to a{" "}
            <span className="grad">finished animated cut</span>.
          </h1>
          <p className="hero-sub">
            Plan the story, generate the characters, draw the storyboard, block
            the motion and render the video — every stage is its own workflow,
            and they hand off to each other. No modelling, no drawing, no
            editing suite.
          </p>
          <div className="hero-actions">
            <button className="btn primary lg" onClick={onGetStarted}>
              Get started — it's free →
            </button>
            <a className="btn lg ghost-bordered" href="#workflows">
              See the workflows
            </a>
          </div>

          {/* The pipeline as a single readable line, so the promise above is
              concrete before anyone scrolls. */}
          <ul className="lp-flow" aria-label="Pipeline stages">
            {["Plan", "Characters", "Storyboard", "Key poses", "Video"].map(
              (s, i) => (
                <li key={s}>
                  {i > 0 && <span className="lp-flow-arrow">→</span>}
                  <span className="lp-flow-step">{s}</span>
                </li>
              )
            )}
          </ul>

          <p className="tiny muted hero-note">
            Sign in with Google or email · Every workflow below is live today
          </p>
        </div>

        <div className="hero-art" aria-hidden="true">
          <PipelineArt />
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
          {WORKFLOWS.map((w) => (
            <article className="lp-wf-card" key={w.title}>
              <div className="lp-wf-top">
                <span className="lp-wf-ico">{w.icon}</span>
                <span className="lp-wf-live">
                  <span className="lp-wf-dot" /> Live
                </span>
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
          {STAGES.map((s) => (
            <div className="step-card" key={s.n}>
              <div className="step-num">{s.n}</div>
              <div className="step-icon">{s.icon}</div>
              <h3>{s.title}</h3>
              <p className="muted">{s.body}</p>
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
        <span className="brand small">🎭 Character Asset Studio</span>
        <span className="tiny muted">
          AI planning · character assets · storyboards · animatics · video
        </span>
      </footer>
    </div>
  );
}

// Hero illustration — three real outputs stacked: a 2×2 turnaround sheet (Text
// to Turnaround Image), a strip of key poses (Image to Animatic Image) and a
// render chip (Image to AI Video). Pure SVG/CSS so it costs no assets and
// themes itself.
function PipelineArt() {
  const poses = ["front", "left", "¾", "back"];
  return (
    <div className="lp-art-stack">
      <span className="lp-art-chip lp-art-chip-a">🎞️ Veo render · 4s</span>

      <div className="art-frame">
        <div className="art-grid">
          {poses.map((label) => (
            <div className="art-cell" key={label}>
              <Figure />
              <span className="art-label">{label}</span>
            </div>
          ))}
        </div>
        <div className="art-caption">2×2 turnaround sheet</div>
      </div>

      {/* Key-pose strip: same figure, arms stepping down — reads as motion
          without needing four separate drawings. */}
      <div className="lp-art-strip">
        {[0, 1, 2, 3].map((i) => (
          <div className="lp-art-cell" key={i}>
            <Figure armDrop={i * 7} />
          </div>
        ))}
        <span className="lp-art-play">▶</span>
      </div>
    </div>
  );
}

// One stick figure. `armDrop` lowers the arms a few units per frame so the pose
// strip animates the same character instead of repeating it.
function Figure({ armDrop = 0 }) {
  return (
    <svg viewBox="0 0 100 130" className="art-figure" role="img">
      <circle cx="50" cy="24" r="12" />
      <rect x="44" y="36" width="12" height="34" rx="5" />
      <rect x="14" y={40 + armDrop} width="72" height="9" rx="4.5" />
      <rect x="44" y="68" width="5.5" height="40" rx="2.7" />
      <rect x="50.5" y="68" width="5.5" height="40" rx="2.7" />
    </svg>
  );
}
