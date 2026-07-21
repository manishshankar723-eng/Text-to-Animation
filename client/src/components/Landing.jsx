import { useState } from "react";

// Public landing page — the first thing a visitor sees. Explains what the tool
// does in plain language, shows the 3-step workflow, then hands off to the login
// screen via onGetStarted().
export default function Landing({ onGetStarted }) {
  return (
    <div className="landing">
      {/* ---------- Top nav ---------- */}
      <nav className="landing-nav">
        <span className="brand small">🎭 Character Asset Studio</span>
        <div className="landing-nav-links">
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
          <span className="pill">✨ AI character pipeline</span>
          <h1 className="hero-title">
            Turn a <span className="grad">photo or a sentence</span> into
            game-ready character assets.
          </h1>
          <p className="hero-sub">
            Describe a character or upload one photo. Our AI builds clean
            turnaround sheets — front, side, three-quarter and back — for every
            body part and garment, then turns them into 3D models. No modelling,
            no drawing.
          </p>
          <div className="hero-actions">
            <button className="btn primary lg" onClick={onGetStarted}>
              Get started — it's free →
            </button>
            <a className="btn lg ghost-bordered" href="#how">
              See how it works
            </a>
          </div>
          <p className="tiny muted hero-note">
            Sign in with Google or email · Bring your own reference or generate one
          </p>
        </div>

        {/* Hero illustration: a stylized 2×2 turnaround sheet */}
        <div className="hero-art" aria-hidden="true">
          <TurnaroundArt />
        </div>
      </header>

      {/* ---------- How it works ---------- */}
      <section id="how" className="section">
        <h2 className="section-title">From idea to asset in 3 steps</h2>
        <p className="section-sub muted">
          The whole pipeline runs for you — you just start it.
        </p>

        <div className="steps">
          <Step
            n="1"
            icon="📝"
            title="Describe or upload"
            body="Type a character description (“Indian woman in a red saree, age 30”) and let AI generate a clean T-pose reference — or upload your own photo."
          />
          <Step
            n="2"
            icon="🎨"
            title="AI generates turnarounds"
            body="Each part — hair, face, body, garments, shoes, accessories — is rendered as a 4-view turnaround sheet on a pure-white background, split and cleaned automatically."
          />
          <Step
            n="3"
            icon="🧊"
            title="Download or go 3D"
            body="Grab every view as a ready-to-use PNG in one zip, or send selected parts to Meshy to generate 3D models."
          />
        </div>
      </section>

      {/* ---------- Features ---------- */}
      <section id="features" className="section">
        <h2 className="section-title">Everything a character needs</h2>
        <div className="feature-grid">
          <Feature icon="🔄" title="Full turnarounds" body="Front, left, three-quarter and back for consistent, riggable references." />
          <Feature icon="🧩" title="Part by part" body="Hair, face, body, jackets, sarees, pants, shoes, goggles, headphones." />
          <Feature icon="✂️" title="Auto clean-up" body="Pure-white backgrounds, auto-crop and normalized framing — no manual editing." />
          <Feature icon="🎭" title="Style templates" body="Built-in templates (default, saree) with per-part prompt control you can tune." />
          <Feature icon="🧊" title="One-click 3D" body="Send parts straight to Meshy multi-image-to-3D using your own API key." />
          <Feature icon="⚡" title="Regenerate anything" body="Not happy with one part? Edit its prompt and regenerate just that piece." />
        </div>
      </section>

      {/* ---------- Bottom CTA ---------- */}
      <section className="cta-band">
        <h2>Ready to build your character?</h2>
        <p className="muted">Sign in and generate your first asset sheet in minutes.</p>
        <button className="btn primary lg" onClick={onGetStarted}>
          Get started →
        </button>
      </section>

      <footer className="landing-footer">
        <span className="brand small">🎭 Character Asset Studio</span>
        <span className="tiny muted">AI-powered character asset generation pipeline</span>
      </footer>
    </div>
  );
}

function Step({ n, icon, title, body }) {
  return (
    <div className="step-card">
      <div className="step-num">{n}</div>
      <div className="step-icon">{icon}</div>
      <h3>{title}</h3>
      <p className="muted">{body}</p>
    </div>
  );
}

function Feature({ icon, title, body }) {
  return (
    <div className="feature-card">
      <div className="feature-icon">{icon}</div>
      <h3>{title}</h3>
      <p className="muted tiny">{body}</p>
    </div>
  );
}

// Inline SVG "turnaround sheet" — a 2×2 grid of simple character silhouettes in
// T-pose, mirroring what the pipeline actually produces. Pure CSS/SVG, no assets.
function TurnaroundArt() {
  const poses = ["front", "left", "¾", "back"];
  return (
    <div className="art-frame">
      <div className="art-grid">
        {poses.map((label, i) => (
          <div className="art-cell" key={i}>
            <svg viewBox="0 0 100 130" className="art-figure" role="img">
              {/* head */}
              <circle cx="50" cy="24" r="12" />
              {/* T-pose body: torso + horizontal arms */}
              <rect x="44" y="36" width="12" height="34" rx="5" />
              <rect x="14" y="40" width="72" height="9" rx="4.5" />
              {/* legs */}
              <rect x="44" y="68" width="5.5" height="40" rx="2.7" />
              <rect x="50.5" y="68" width="5.5" height="40" rx="2.7" />
            </svg>
            <span className="art-label">{label}</span>
          </div>
        ))}
      </div>
      <div className="art-caption">2×2 turnaround sheet</div>
    </div>
  );
}
