// Script → Storyboard flow:
//   Step "form"   — single-page input (script → style → aspect), matches the
//                   Text-to-Image workflow's design language.
//   Step "review" — Stage C: the AI shot list, editable before generating panels
//                   (edit description, reorder, delete, add). Panel generation is
//                   the next build step (button shows a "coming soon" notice).
import { Fragment, useState, useRef } from "react";
import * as api from "../api.js";
import StoryboardBoard from "./StoryboardBoard.jsx";
import StoryboardCast from "./StoryboardCast.jsx";
import BreakdownProgress from "./BreakdownProgress.jsx";

const STYLES = [
  { id: "sketch", label: "✏️ Sketch" },
  { id: "comics", label: "💥 Comics" },
  { id: "realistic", label: "📷 Realistic" },
  { id: "3d-animation", label: "🎬 3D Animation" },
  { id: "custom", label: "＋ Custom" },
];

const ASPECTS = [
  { id: "21:9", note: "Ultra-wide" },
  { id: "16:9", note: "Standard HD" },
  { id: "9:16", note: "Mobile" },
  { id: "2:3", note: "Comic page" },
  { id: "1:1", note: "Square" },
];

// Genre shapes the story's tone / pacing in the shot breakdown.
// "default" = no genre bias (let the story decide); "custom" = type your own.
const GENRES = [
  { id: "default", label: "✨ Default" },
  { id: "action", label: "💥 Action" },
  { id: "animation", label: "🎨 Animation" },
  { id: "comedy", label: "😄 Comedy" },
  { id: "commercial", label: "📢 Commercial" },
  { id: "documentary", label: "🎥 Documentary" },
  { id: "drama", label: "🎭 Drama" },
  { id: "educational", label: "📚 Educational" },
  { id: "fantasy", label: "🐉 Fantasy" },
  { id: "horror", label: "👻 Horror" },
  { id: "music-video", label: "🎵 Music Video" },
  { id: "mystery", label: "🔍 Mystery" },
  { id: "romance", label: "💕 Romance" },
  { id: "sci-fi", label: "🚀 Science Fiction" },
  { id: "thriller", label: "⚡ Thriller" },
  { id: "custom", label: "＋ Custom" },
];

// Text-readable script files we can parse in the browser. PDF/DOCX need
// server-side extraction (not built yet) — user pastes those for now.
const TEXT_EXTENSIONS = ["txt", "fountain", "fdx", "md", "text"];

export default function ScriptToStoryboard() {
  const [step, setStep] = useState("form"); // "form" | "review"

  // Form state
  const [tab, setTab] = useState("paste"); // "paste" | "upload"
  const [script, setScript] = useState("");
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [genre, setGenre] = useState("default"); // "default" = no bias
  const [customGenre, setCustomGenre] = useState("");
  const [style, setStyle] = useState("");
  const [aspect, setAspect] = useState("");
  const fileInputRef = useRef(null);

  // Review state
  const [shots, setShots] = useState([]);
  const [characters, setCharacters] = useState([]);

  // Board state
  const [jobId, setJobId] = useState(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const hasScript = script.trim().length > 0 || Boolean(file);
  const canGenerate = hasScript && Boolean(style) && Boolean(aspect);

  const styleLabel = STYLES.find((s) => s.id === style)?.label || style;

  function pickFile(f) {
    if (!f) return;
    setFile(f);
    setError("");
  }

  // The genre string sent to the breakdown: "" for Default, the typed text for
  // Custom, otherwise the readable genre name (label minus its emoji).
  function effectiveGenre() {
    if (genre === "custom") return customGenre.trim();
    if (!genre || genre === "default") return "";
    const g = GENRES.find((x) => x.id === genre);
    return g ? g.label.replace(/^\S+\s+/, "") : genre;
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    pickFile(e.dataTransfer.files?.[0]);
  }

  // Resolve the script text from the paste box or an uploaded text file.
  async function resolveScriptText() {
    const pasted = script.trim();
    if (pasted) return pasted;
    if (file) {
      const ext = (file.name.split(".").pop() || "").toLowerCase();
      if (TEXT_EXTENSIONS.includes(ext)) {
        return (await file.text()).trim();
      }
      throw new Error(
        "PDF/DOCX text extraction is coming soon — please paste your script text for now."
      );
    }
    return "";
  }

  async function handleGenerate() {
    if (!canGenerate || busy) return;
    setError("");
    setNotice("");
    setBusy(true);
    try {
      const text = await resolveScriptText();
      if (text.length < 20) {
        throw new Error("Please provide at least a few sentences of script.");
      }
      const res = await api.breakdownScript(text, {
        style,
        aspectRatio: aspect,
        genre: effectiveGenre(),
      });
      setShots(res.shots || []);
      setCharacters(res.characters || []);
      setStep("review");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // ---- Review handlers ----
  function updateShot(i, patch) {
    setShots((s) => s.map((sh, idx) => (idx === i ? { ...sh, ...patch } : sh)));
  }
  function deleteShot(i) {
    setShots((s) => s.filter((_, idx) => idx !== i));
  }
  function moveShot(i, dir) {
    setShots((s) => {
      const j = i + dir;
      if (j < 0 || j >= s.length) return s;
      const copy = [...s];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
  }
  function blankShot(sceneNumber) {
    return {
      scene_number: sceneNumber || 1,
      shot_number: 0,
      description: "",
      characters: [],
      location: "",
      camera: "",
    };
  }
  function addShot() {
    setShots((s) => [...s, blankShot(s.at(-1)?.scene_number || 1)]);
  }
  // Insert a blank shot right AFTER position `index` (between two shots).
  function insertShot(index) {
    setShots((s) => {
      const copy = [...s];
      copy.splice(index + 1, 0, blankShot(s[index]?.scene_number || 1));
      return copy;
    });
  }
  // The ACTIVE cast = only characters that appear in the current (edited) shots,
  // deduped, enriched with descriptions from the breakdown when available. This
  // shrinks as the user deletes shots, so the cast count stays honest.
  function computeCast() {
    const descByName = new Map(
      characters.map((c) => [c.name.trim().toLowerCase(), c])
    );
    const seen = new Set();
    const out = [];
    for (const sh of shots) {
      for (const raw of sh.characters || []) {
        const name = (raw || "").trim();
        const key = name.toLowerCase();
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.push(descByName.get(key) || { name, description: "" });
      }
    }
    return out;
  }

  // Review → cast (or straight to generation if the script has no named cast).
  function handleReviewNext() {
    if (shots.length === 0 || busy) return;
    setError("");
    setNotice("");
    if (computeCast().length > 0) {
      setStep("cast");
    } else {
      startStoryboard({});
    }
  }

  // Kick off panel generation (optionally with character references).
  async function startStoryboard(characterRefs) {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const res = await api.createStoryboard({
        shots,
        style,
        aspectRatio: aspect,
        characterRefs,
      });
      setJobId(res.job_id);
      setStep("board");
    } catch (e) {
      setError(e.message);
      setStep("review"); // surface the error where the user can act on it
    } finally {
      setBusy(false);
    }
  }

  // ============================================================== Cast step
  if (step === "cast") {
    return (
      <StoryboardCast
        characters={computeCast()}
        busy={busy}
        onBack={() => setStep("review")}
        onGenerate={(refs) => startStoryboard(refs)}
      />
    );
  }

  // ============================================================= Board step
  if (step === "board" && jobId) {
    return (
      <StoryboardBoard
        jobId={jobId}
        styleLabel={styleLabel}
        aspect={aspect}
        onBack={() => setStep("review")}
        onRestart={() => {
          setJobId(null);
          setShots([]);
          setCharacters([]);
          setGenre("default");
          setCustomGenre("");
          setStep("form");
        }}
      />
    );
  }

  // ============================================================ Review step
  if (step === "review") {
    const activeCast = computeCast();
    return (
      <div className="workflow-head-wrap sb-review">
        <div className="workflow-header">
          <span className="wf-icon">🎞️</span>
          <div>
            <h1 className="wf-title">Review your shots</h1>
            <p className="muted">
              Edit, reorder or delete panels before generating — this is your
              chance to fix the AI before it draws.
            </p>
          </div>
        </div>

        <div className="review-summary">
          <span className="chip">{styleLabel}</span>
          <span className="chip">{aspect}</span>
          <span className="chip">
            {shots.length} shot{shots.length === 1 ? "" : "s"}
          </span>
        </div>

        {error && <div className="error">{error}</div>}
        {notice && <div className="info-msg">{notice}</div>}

        <div className="shot-list">
          {shots.map((sh, i) => (
            <Fragment key={i}>
            <div className="card shot-card">
              <div className="shot-head">
                <span className="shot-index">
                  Shot {i + 1}
                  <span className="shot-scene">Scene {sh.scene_number}</span>
                </span>
                <div className="shot-actions">
                  <button
                    type="button"
                    className="shot-btn"
                    onClick={() => moveShot(i, -1)}
                    disabled={i === 0}
                    title="Move up"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="shot-btn"
                    onClick={() => moveShot(i, 1)}
                    disabled={i === shots.length - 1}
                    title="Move down"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="shot-btn danger"
                    onClick={() => deleteShot(i)}
                    title="Delete shot"
                  >
                    ✕
                  </button>
                </div>
              </div>

              <textarea
                className="prompt-textarea shot-desc"
                value={sh.description}
                placeholder="Describe what we see in this panel…"
                onChange={(e) => updateShot(i, { description: e.target.value })}
              />

              <div className="grid2 shot-meta">
                <div>
                  <label>Camera</label>
                  <input
                    value={sh.camera}
                    placeholder="e.g. wide, close-up"
                    onChange={(e) => updateShot(i, { camera: e.target.value })}
                  />
                </div>
                <div>
                  <label>Location</label>
                  <input
                    value={sh.location}
                    placeholder="e.g. city street, night"
                    onChange={(e) => updateShot(i, { location: e.target.value })}
                  />
                </div>
              </div>

              {sh.characters?.length > 0 && (
                <div className="shot-chars">
                  {sh.characters.map((c, ci) => (
                    <span className="chip" key={ci}>
                      {c}
                    </span>
                  ))}
                </div>
              )}
            </div>

            {i < shots.length - 1 && (
              <div className="shot-insert-row">
                <button
                  type="button"
                  className="shot-insert-btn"
                  onClick={() => insertShot(i)}
                  title="Add a shot here"
                >
                  ＋ Add a shot
                </button>
              </div>
            )}
            </Fragment>
          ))}

          <button type="button" className="btn ghost add-shot-btn" onClick={addShot}>
            ＋ Add a shot
          </button>
        </div>

        <div className="review-actions">
          <button
            type="button"
            className="btn"
            onClick={() => {
              setNotice("");
              setError("");
              setStep("form");
            }}
          >
            ← Back
          </button>
          <button
            type="button"
            className="btn primary"
            disabled={shots.length === 0 || busy}
            onClick={handleReviewNext}
          >
            {busy ? (
              <>
                <span className="spinner-inline" /> Starting…
              </>
            ) : activeCast.length > 0 ? (
              `🎭 Next: cast (${activeCast.length})`
            ) : (
              `🎬 Generate panels (${shots.length})`
            )}
          </button>
        </div>
      </div>
    );
  }

  // ============================================================== Form step
  return (
    <div className="workflow-head-wrap sb-form">
      <div className="workflow-header">
        <span className="wf-icon">📝</span>
        <div>
          <h1 className="wf-title">Script to Storyboard</h1>
          <p className="muted">
            Turn a script into a shot-by-shot storyboard — pick a style and frame,
            then generate.
          </p>
        </div>
      </div>

      <div className="sts-hero-grid">
      <div className="sts-form-wrap">
        <div className="card">
          {/* --- Script --- */}
          <label>Your script</label>
          <div className="tab-bar">
            <button
              type="button"
              className={`tab-btn ${tab === "paste" ? "active" : ""}`}
              onClick={() => setTab("paste")}
            >
              ✍️ Paste script
            </button>
            <button
              type="button"
              className={`tab-btn ${tab === "upload" ? "active" : ""}`}
              onClick={() => setTab("upload")}
            >
              📁 Upload file
            </button>
          </div>

          {tab === "paste" ? (
            <textarea
              className="prompt-textarea sts-script-area"
              placeholder="Paste or type your script here…"
              value={script}
              onChange={(e) => setScript(e.target.value)}
            />
          ) : (
            <div
              className={`dropzone ${dragOver ? "over" : ""}`}
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleDrop}
            >
              <span className="dropzone-icon">📄</span>
              {file ? (
                <>
                  <span className="dropzone-text">{file.name}</span>
                  <span
                    className="dropzone-sub sts-clear-file"
                    onClick={(e) => {
                      e.stopPropagation();
                      setFile(null);
                    }}
                  >
                    Remove
                  </span>
                </>
              ) : (
                <>
                  <span className="dropzone-text">Click or drop your script file</span>
                  <span className="dropzone-sub">TXT / Fountain / FDX (PDF, DOCX soon)</span>
                </>
              )}
            </div>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.txt,.fountain,.fdx,.docx,.md"
            hidden
            onChange={(e) => pickFile(e.target.files?.[0])}
          />

          {/* --- Genre --- */}
          <label>Genre <span className="label-optional">· shapes the tone</span></label>
          <div className="opt-chips">
            {GENRES.map((g) => (
              <button
                key={g.id}
                type="button"
                className={`opt-chip ${genre === g.id ? "active" : ""}`}
                onClick={() => setGenre(g.id)}
              >
                {g.label}
              </button>
            ))}
          </div>
          {genre === "custom" && (
            <input
              className="custom-genre-input"
              value={customGenre}
              placeholder="Type your genre, e.g. Neo-noir, Coming-of-age…"
              onChange={(e) => setCustomGenre(e.target.value)}
            />
          )}

          {/* --- Style --- */}
          <label>Visual style</label>
          <div className="opt-chips">
            {STYLES.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`opt-chip ${style === s.id ? "active" : ""}`}
                onClick={() => setStyle(s.id)}
              >
                {s.label}
              </button>
            ))}
          </div>

          {/* --- Aspect ratio --- */}
          <label>Aspect ratio</label>
          <div className="opt-chips">
            {ASPECTS.map((a) => (
              <button
                key={a.id}
                type="button"
                className={`opt-chip ${aspect === a.id ? "active" : ""}`}
                onClick={() => setAspect(a.id)}
                title={a.note}
              >
                {a.id}
                <span className="opt-chip-note">{a.note}</span>
              </button>
            ))}
          </div>

          {error && <div className="error">{error}</div>}

          <button
            type="button"
            className="btn primary sts-generate"
            disabled={!canGenerate || busy}
            onClick={handleGenerate}
          >
            {busy ? (
              <span className="btn-loading">
                <span className="btn-ring" />
                Breaking down your script…
              </span>
            ) : (
              "🎬 Generate storyboard"
            )}
          </button>
        </div>
      </div>

      <aside className="sts-hero-aside">
        <div className="card sts-guide">
          <h3 className="sts-guide-title">How it works</h3>
          <ol className="sts-guide-steps">
            <li>
              <span className="sts-guide-num">1</span>
              Paste or upload your script
            </li>
            <li>
              <span className="sts-guide-num">2</span>
              Pick a visual style &amp; frame
            </li>
            <li>
              <span className="sts-guide-num">3</span>
              Review &amp; edit the shots
            </li>
            <li>
              <span className="sts-guide-num">4</span>
              Set up your cast (optional)
            </li>
            <li>
              <span className="sts-guide-num">5</span>
              Generate panels &amp; download a PDF
            </li>
          </ol>
          <p className="tiny muted sts-guide-tip">
            Tip: keep each scene short and visual — one clear moment per shot gives
            the best panels.
          </p>
        </div>
      </aside>
      </div>
    </div>
  );
}
