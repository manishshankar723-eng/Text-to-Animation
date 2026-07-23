// Script → Storyboard flow (UI only — no backend yet).
//   Step 1 "script": centered composer (script textarea + Upload + send arrow).
//   Step 2 "style":  "Select Your Style" card picker (Back / Next).
// Styled in the app's champagne-gold theme with a shared visual language across
// both steps (same panel treatment, back button, titles, gold accents) — a
// distinct design from the Drawstory reference, same content + buttons.
import { useRef, useState } from "react";

// Style options. Content (name + description) matches the reference; previews
// are gold-tinted gradient tiles with a representative icon (no image assets).
const STYLES = [
  {
    id: "sketch",
    name: "Sketch",
    icon: "✏️",
    desc: "Short-form, punchy beats that highlight product moments and CTA flow.",
  },
  {
    id: "comics",
    name: "Comics",
    icon: "💥",
    desc: "Stylized and colored, dramatic frames with bold lines and exaggerated expressions.",
  },
  {
    id: "realistic",
    name: "Realistic",
    icon: "📷",
    desc: "Life-like visuals with detailed textures, lighting, and depth.",
  },
  {
    id: "3d-animation",
    name: "3D Animation",
    icon: "🎬",
    desc: "Cinematic, high-quality 3D renders with realistic lighting and textures.",
  },
];

// Aspect-ratio options. Content matches the reference; the icon is an outlined
// frame sized to the ratio (w/h in px, scaled to fit a shared ~74px box).
const ASPECTS = [
  { id: "21:9", desc: "Cinematic ultra-wide frame", w: 92, h: 40 },
  { id: "16:9", desc: "Standard HD frame", w: 84, h: 47 },
  { id: "9:16", desc: "Vertical format for mobile", w: 36, h: 64 },
  { id: "2:3", desc: "Traditional comic book page format", w: 44, h: 66 },
  {
    id: "1:1",
    desc: "Square format for social posts and stylized compositions",
    w: 58,
    h: 58,
  },
];

export default function ScriptToStoryboard() {
  const [step, setStep] = useState("script"); // "script" | "style" | "aspect"

  // Step 1 state
  const [script, setScript] = useState("");
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef(null);

  // Step 2 state
  const [selectedStyle, setSelectedStyle] = useState(null);

  // Step 3 state
  const [selectedAspect, setSelectedAspect] = useState(null);

  const [notice, setNotice] = useState("");

  const hasInput = script.trim().length > 0 || Boolean(file);

  function pickFile(f) {
    if (!f) return;
    setFile(f);
    setNotice("");
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    pickFile(e.dataTransfer.files?.[0]);
  }

  // Step 1 → Step 2 (arrow button).
  function goToStyle() {
    if (!hasInput) return;
    setNotice("");
    setStep("style");
  }

  // Step 2 → Step 3 (Next button).
  function goToAspect() {
    if (!selectedStyle) return;
    setNotice("");
    setStep("aspect");
  }

  // Step 3 → done (Next button). No backend yet.
  function handleFinish() {
    if (!selectedAspect) return;
    setNotice(
      "Storyboard generation is coming soon — your script, style and aspect ratio are ready to go."
    );
  }

  // ---------------------------------------------------------------- Step 2
  if (step === "style") {
    return (
      <div className="sts-wrap style-wrap">
        <div className="sts-page">
          <div className="sts-topbar">
            <button
              type="button"
              className="sts-back"
              onClick={() => {
                setNotice("");
                setStep("script");
              }}
            >
              ← Back
            </button>
          </div>

          <h1 className="sts-title sts-title-sm">Select Your Style</h1>
          <p className="sts-sub">Pick a look for your storyboard — you can fine-tune it later.</p>

          <div className="style-grid">
            {STYLES.map((s) => {
              const active = selectedStyle === s.id;
              return (
                <button
                  key={s.id}
                  type="button"
                  className={`style-card ${active ? "selected" : ""}`}
                  onClick={() => setSelectedStyle(s.id)}
                  aria-pressed={active}
                >
                  {active && <span className="style-check">✓</span>}
                  <div className={`style-preview preview-${s.id}`}>
                    <span className="style-icon">{s.icon}</span>
                  </div>
                  <div className="style-name">{s.name}</div>
                  <p className="style-desc">{s.desc}</p>
                </button>
              );
            })}

            {/* Custom card */}
            <button
              type="button"
              className={`style-card style-custom ${
                selectedStyle === "custom" ? "selected" : ""
              }`}
              onClick={() => setSelectedStyle("custom")}
              aria-pressed={selectedStyle === "custom"}
            >
              {selectedStyle === "custom" && <span className="style-check">✓</span>}
              <div className="style-custom-plus">+</div>
              <div className="style-name">+ Custom</div>
              <p className="style-desc">Create your own unique style</p>
            </button>
          </div>

          {notice && <p className="sts-notice">{notice}</p>}

          <div className="sts-next-row">
            <button
              type="button"
              className="btn primary sts-next"
              disabled={!selectedStyle}
              onClick={goToAspect}
            >
              Next
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------- Step 3
  if (step === "aspect") {
    return (
      <div className="sts-wrap style-wrap">
        <div className="sts-page">
          <div className="sts-topbar">
            <button
              type="button"
              className="sts-back"
              onClick={() => {
                setNotice("");
                setStep("style");
              }}
            >
              ← Back
            </button>
          </div>

          <h1 className="sts-title sts-title-sm">Select Your Aspect Ratio</h1>
          <p className="sts-sub">Choose the frame shape for your storyboard panels.</p>

          <div className="style-grid">
            {ASPECTS.map((a) => {
              const active = selectedAspect === a.id;
              return (
                <button
                  key={a.id}
                  type="button"
                  className={`style-card ratio-card ${active ? "selected" : ""}`}
                  onClick={() => setSelectedAspect(a.id)}
                  aria-pressed={active}
                >
                  {active && <span className="style-check">✓</span>}
                  <div className="ratio-frame-box">
                    <span
                      className="ratio-frame"
                      style={{ width: `${a.w}px`, height: `${a.h}px` }}
                    />
                  </div>
                  <div className="ratio-name">{a.id}</div>
                  <p className="ratio-desc">{a.desc}</p>
                </button>
              );
            })}
          </div>

          {notice && <p className="sts-notice">{notice}</p>}

          <div className="sts-next-row">
            <button
              type="button"
              className="btn primary sts-next"
              disabled={!selectedAspect}
              onClick={handleFinish}
            >
              Next
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ---------------------------------------------------------------- Step 1
  return (
    <div className="sts-wrap">
      <div className="sts-hero">
        <h1 className="sts-title">
          Upload your script to
          <br />
          create storyboards
        </h1>
        <p className="sts-sub">
          Create consistent storyboards that are fully customizable
        </p>

        <div
          className={`sts-composer ${dragOver ? "over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          <textarea
            className="sts-textarea"
            placeholder="Create..."
            value={script}
            onChange={(e) => setScript(e.target.value)}
          />

          {file && (
            <div className="sts-file-chip">
              <span className="sts-file-name">📄 {file.name}</span>
              <button
                type="button"
                className="sts-file-x"
                onClick={() => setFile(null)}
                aria-label="Remove file"
              >
                ✕
              </button>
            </div>
          )}

          <div className="sts-composer-bar">
            <button
              type="button"
              className="sts-upload"
              onClick={() => fileInputRef.current?.click()}
            >
              <span className="sts-upload-plus">+</span> Upload
            </button>
            <button
              type="button"
              className="sts-send"
              disabled={!hasInput}
              onClick={goToStyle}
              aria-label="Continue to style selection"
            >
              ↑
            </button>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.txt,.fountain,.fdx,.docx"
            hidden
            onChange={(e) => pickFile(e.target.files?.[0])}
          />
        </div>

        <p className="sts-foot">
          Type or paste your script — or upload a PDF. You'll pick a style next.
        </p>
      </div>
    </div>
  );
}
