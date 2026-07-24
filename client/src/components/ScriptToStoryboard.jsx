// Script → Storyboard flow:
//   Step "library"— "Your Storyboards": every board this user has generated,
//                   plus the New Storyboard card. This is where the flow opens,
//                   so returning users land on their saved work.
//   Step "form"   — single-page input (script → style → aspect), matches the
//                   Text-to-Image workflow's design language.
//   Step "review" — Stage C: the AI shot list, editable before generating panels
//                   (edit description, reorder, delete, add). Panel generation is
//                   the next build step (button shows a "coming soon" notice).
import { useState, useRef, useEffect } from "react";
import * as api from "../api.js";
import StoryboardBoard from "./StoryboardBoard.jsx";
import StoryboardCast from "./StoryboardCast.jsx";
import StoryboardAssets from "./StoryboardAssets.jsx";
import StoryboardLibrary from "./StoryboardLibrary.jsx";
import BreakdownProgress from "./BreakdownProgress.jsx";

// Visual styles. The first 7 show as chips; the rest live behind "＋ More".
// "custom" ("Add Your Own Style") reveals a free-text box.
const STYLES = [
  { id: "sketch", label: "✏️ Sketch" },
  { id: "comic", label: "💥 Comic" },
  { id: "cinematic", label: "🎬 Cinematic" },
  { id: "animation-3d", label: "🧸 Animation 3D" },
  { id: "watercolor", label: "🎨 Watercolor Paint" },
];
const MORE_STYLES = [
  { id: "photo-commercial", label: "📷 Photo / Commercial" },
  { id: "charcoal", label: "🖤 Charcoal Sketch" },
  { id: "dark-anime", label: "🌃 Dark Anime" },
  { id: "flat-vector", label: "🔷 Flat / Vector" },
  { id: "noir", label: "🎞️ Noir" },
  { id: "stick-figure", label: "🏃 Stick Figure" },
  { id: "graphic-novel", label: "📖 Graphic Novel" },
  { id: "custom", label: "＋ Custom" },
];
const ALL_STYLES = [...STYLES, ...MORE_STYLES];
const DEFAULT_STYLE = "sketch"; // pre-selected default (highlighted)

const ASPECTS = [
  { id: "21:9", note: "Ultra-wide" },
  { id: "16:9", note: "Standard HD" },
  { id: "9:16", note: "Mobile" },
  { id: "2:3", note: "Comic page" },
  { id: "1:1", note: "Square" },
];
const DEFAULT_ASPECT = "16:9"; // pre-selected standard frame

// Genre shapes the story's tone / pacing in the shot breakdown. The first 7 show
// as chips; the rest live behind "＋ More". "default" = no genre bias (let the
// story decide); "custom" = type your own.
const GENRES = [
  { id: "default", label: "✨ Default" },
  { id: "animation", label: "🎨 Animation" },
  { id: "commercial", label: "📢 Commercial" },
  { id: "documentary", label: "🎥 Documentary" },
  { id: "educational", label: "📚 Educational" },
  { id: "mythology", label: "🏛️ Mythology" },
];
const MORE_GENRES = [
  { id: "action", label: "💥 Action" },
  { id: "comedy", label: "😄 Comedy" },
  { id: "drama", label: "🎭 Drama" },
  { id: "fantasy", label: "🐉 Fantasy" },
  { id: "horror", label: "👻 Horror" },
  { id: "music-video", label: "🎵 Music Video" },
  { id: "mystery", label: "🔍 Mystery" },
  { id: "romance", label: "💕 Romance" },
  { id: "sci-fi", label: "🚀 Science Fiction" },
  { id: "thriller", label: "⚡ Thriller" },
  { id: "custom", label: "＋ Custom" },
];
const ALL_GENRES = [...GENRES, ...MORE_GENRES];

// Text-readable script files we can parse in the browser. PDF/DOCX need
// server-side extraction (not built yet) — user pastes those for now.
const TEXT_EXTENSIONS = ["txt", "fountain", "fdx", "md", "text"];

export default function ScriptToStoryboard() {
  // Open on the library so a returning user sees their saved storyboards first.
  const [step, setStep] = useState("library");

  // Form state
  const [tab, setTab] = useState("paste"); // "paste" | "upload"
  const [script, setScript] = useState("");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [genre, setGenre] = useState("default"); // "default" = no bias
  const [customGenre, setCustomGenre] = useState("");
  const [style, setStyle] = useState(DEFAULT_STYLE);
  const [customStyle, setCustomStyle] = useState("");
  const [aspect, setAspect] = useState(DEFAULT_ASPECT);
  const [customAspect, setCustomAspect] = useState("");
  // "＋ More" popups for the overflow genres / styles.
  const [genreMoreOpen, setGenreMoreOpen] = useState(false);
  const [styleMoreOpen, setStyleMoreOpen] = useState(false);
  const fileInputRef = useRef(null);

  // Review state
  const [shots, setShots] = useState([]);
  const [characters, setCharacters] = useState([]);
  const [assets, setAssets] = useState([]);
  // Character refs chosen on the cast step, carried into the assets step so both
  // sets of references reach panel generation together.
  const [characterRefs, setCharacterRefs] = useState({});

  // Everything the user sets up on the cast / props steps — the generated or
  // uploaded reference AND the edited description — lives HERE rather than in
  // those step components, because they unmount whenever the user steps away.
  // Keyed by lowercased name, so a step can re-seed itself when it mounts again
  // and the work survives Back → forward for the whole storyboard session.
  const [savedCastRefs, setSavedCastRefs] = useState({});
  const [savedAssetRefs, setSavedAssetRefs] = useState({});
  // Blob preview URLs are owned here too: the step components used to revoke
  // theirs on unmount, which is exactly what blanked the thumbnails on Back.
  const previewUrls = useRef([]);

  useEffect(
    () => () => previewUrls.current.forEach((u) => URL.revokeObjectURL(u)),
    []
  );

  function refKey(name) {
    return (name || "").trim().toLowerCase();
  }

  // Called by the cast / props steps whenever a durable field changes.
  function saveRefFields(setter, name, fields) {
    const key = refKey(name);
    if (!key) return;
    if (fields.previewUrl) previewUrls.current.push(fields.previewUrl);
    setter((m) => ({ ...m, [key]: { ...m[key], ...fields } }));
  }

  function clearSavedRefs() {
    previewUrls.current.forEach((u) => URL.revokeObjectURL(u));
    previewUrls.current = [];
    setSavedCastRefs({});
    setSavedAssetRefs({});
  }

  // Board state
  const [jobId, setJobId] = useState(null);
  // Where the board was opened from, so ← Back goes somewhere that still has
  // content: the review step for a board we just generated, the library for a
  // saved board re-opened from a card (whose shots aren't loaded).
  const [boardOrigin, setBoardOrigin] = useState("review");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const hasScript = script.trim().length > 0 || Boolean(file);
  // Style + aspect are pre-selected, so only the script gates generation.
  const canGenerate = hasScript && Boolean(style) && Boolean(aspect);

  const styleLabel =
    style === "custom" && customStyle.trim()
      ? customStyle.trim()
      : ALL_STYLES.find((s) => s.id === style)?.label || style;

  // The style value sent to the backend: for "Add Your Own Style" send the typed
  // text (used directly as the panel art direction); otherwise the style id.
  function effectiveStyle() {
    if (style === "custom") return customStyle.trim() || "custom";
    return style;
  }

  // The aspect ratio sent to the backend: the typed W:H for Custom, else the id.
  function effectiveAspect() {
    if (aspect === "custom") return customAspect.trim() || DEFAULT_ASPECT;
    return aspect;
  }

  function pickFile(f) {
    if (!f) return;
    setFile(f);
    setError("");
  }

  // The name this storyboard is saved under. Falls back to the script's first
  // non-empty line so a saved board is never just called "Storyboard".
  function effectiveTitle() {
    const typed = title.trim();
    if (typed) return typed;
    const firstLine = script
      .split("\n")
      .map((l) => l.trim())
      .find((l) => l.length > 0);
    if (firstLine) return firstLine.slice(0, 80);
    if (file?.name) return file.name.replace(/\.[^.]+$/, "");
    return "Untitled storyboard";
  }

  // Re-select the chips for a saved board's settings. Anything that isn't one
  // of our known ids came from a "custom" field, so restore it as custom text.
  function applySavedSettings({ style: s, aspect_ratio: a, genre: g }) {
    if (s) {
      const known = ALL_STYLES.some((x) => x.id === s && x.id !== "custom");
      setStyle(known ? s : "custom");
      setCustomStyle(known ? "" : s);
    }
    if (a) {
      const known = ASPECTS.some((x) => x.id === a);
      setAspect(known ? a : "custom");
      setCustomAspect(known ? "" : a);
    }
    // Genres are stored as their readable label, so match on that too.
    if (g) {
      const match = ALL_GENRES.find(
        (x) => x.id === g || x.label.replace(/^\S+\s+/, "") === g
      );
      setGenre(match ? match.id : "custom");
      setCustomGenre(match ? "" : g);
    }
  }

  // The genre string sent to the breakdown: "" for Default, the typed text for
  // Custom, otherwise the readable genre name (label minus its emoji).
  function effectiveGenre() {
    if (genre === "custom") return customGenre.trim();
    if (!genre || genre === "default") return "";
    const g = ALL_GENRES.find((x) => x.id === genre);
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
        style: effectiveStyle(),
        aspectRatio: effectiveAspect(),
        genre: effectiveGenre(),
      });
      setShots(res.shots || []);
      setCharacters(res.characters || []);
      setAssets(res.assets || []);
      // A new breakdown is a new cast — drop refs saved for the previous script
      // so a same-named character can't inherit the old picture.
      clearSavedRefs();
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

  // The ACTIVE assets to lock = the breakdown's canonical prop/background list,
  // plus any asset named in a shot that wasn't in that list. Category/description
  // come from the canonical list when available.
  function computeAssets() {
    const metaByName = new Map(
      assets.map((a) => [a.name.trim().toLowerCase(), a])
    );
    const seen = new Set();
    const out = [];
    for (const a of assets) {
      const key = (a.name || "").trim().toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(a);
    }
    for (const sh of shots) {
      for (const raw of sh.assets || []) {
        const name = (raw || "").trim();
        const key = name.toLowerCase();
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.push(metaByName.get(key) || { name, category: "prop", description: "" });
      }
    }
    return out;
  }

  // Review → cast → assets → board, skipping any step with nothing to set up.
  function handleReviewNext() {
    if (shots.length === 0 || busy) return;
    setError("");
    setNotice("");
    if (computeCast().length > 0) {
      setStep("cast");
    } else if (computeAssets().length > 0) {
      setStep("assets");
    } else {
      startStoryboard({}, {});
    }
  }

  // Cast done → assets step (if any) or straight to generation.
  function handleCastNext(charRefs) {
    setCharacterRefs(charRefs || {});
    if (computeAssets().length > 0) {
      setStep("assets");
    } else {
      startStoryboard(charRefs || {}, {});
    }
  }

  // Kick off panel generation with the chosen character + asset references.
  async function startStoryboard(charRefs, assetRefs) {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const res = await api.createStoryboard({
        shots,
        style: effectiveStyle(),
        aspectRatio: effectiveAspect(),
        // Saved with the job so the library card can name and label the board.
        title: effectiveTitle(),
        genre: effectiveGenre(),
        characterRefs: charRefs || {},
        assetRefs: assetRefs || {},
      });
      setJobId(res.job_id);
      setBoardOrigin("review");
      setStep("board");
    } catch (e) {
      setError(e.message);
      setStep("review"); // surface the error where the user can act on it
    } finally {
      setBusy(false);
    }
  }

  // Wipe the in-flight storyboard so the form / library starts clean.
  function resetWorkflow() {
    setJobId(null);
    setShots([]);
    setCharacters([]);
    setAssets([]);
    setCharacterRefs({});
    clearSavedRefs();
    setScript("");
    setFile(null);
    setTitle("");
    setGenre("default");
    setCustomGenre("");
    setStyle(DEFAULT_STYLE);
    setCustomStyle("");
    setAspect(DEFAULT_ASPECT);
    setCustomAspect("");
    setError("");
    setNotice("");
  }

  // =========================================================== Library step
  if (step === "library") {
    return (
      <StoryboardLibrary
        onNew={() => {
          resetWorkflow();
          setStep("form");
        }}
        onOpen={(board) => {
          // Re-open a saved board read-side: its panels live on the server, so
          // only the display settings need restoring.
          setTitle(board.title || "");
          applySavedSettings(board);
          setJobId(board.job_id);
          setBoardOrigin("library");
          setStep("board");
        }}
        onDuplicate={(project) => {
          // Reuse the saved shots instead of re-running the paid breakdown —
          // drops the user straight on the review step with a fresh name.
          resetWorkflow();
          applySavedSettings(project);
          setShots(project.shots || []);
          setTitle(`${project.title} (copy)`);
          setStep("review");
        }}
      />
    );
  }

  // ============================================================== Cast step
  if (step === "cast") {
    return (
      <StoryboardCast
        characters={computeCast()}
        saved={savedCastRefs}
        onSave={(name, fields) => saveRefFields(setSavedCastRefs, name, fields)}
        busy={busy}
        onBack={() => setStep("review")}
        onGenerate={handleCastNext}
      />
    );
  }

  // ============================================================ Assets step
  if (step === "assets") {
    return (
      <StoryboardAssets
        assets={computeAssets()}
        saved={savedAssetRefs}
        onSave={(name, fields) => saveRefFields(setSavedAssetRefs, name, fields)}
        busy={busy}
        onBack={() => setStep(computeCast().length > 0 ? "cast" : "review")}
        onGenerate={(assetRefs) => startStoryboard(characterRefs, assetRefs)}
      />
    );
  }

  // ============================================================= Board step
  if (step === "board" && jobId) {
    return (
      <StoryboardBoard
        jobId={jobId}
        styleLabel={styleLabel}
        aspect={effectiveAspect()}
        backLabel={boardOrigin === "library" ? "← Your Storyboards" : "← Back to shots"}
        onBack={() => setStep(boardOrigin)}
        onRestart={() => {
          resetWorkflow();
          setStep("library");
        }}
      />
    );
  }

  // ============================================================ Review step
  if (step === "review") {
    const activeCast = computeCast();
    const activeAssets = computeAssets();
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

        <div className="review-actions top-actions">
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
            ) : activeAssets.length > 0 ? (
              `🎬 Next: props (${activeAssets.length})`
            ) : (
              `🎬 Generate panels (${shots.length})`
            )}
          </button>
        </div>

        <div className="review-summary">
          <span className="chip">{styleLabel}</span>
          <span className="chip">{effectiveAspect()}</span>
          <span className="chip">
            {shots.length} shot{shots.length === 1 ? "" : "s"}
          </span>
        </div>

        {error && <div className="error">{error}</div>}
        {notice && <div className="info-msg">{notice}</div>}

        <div className="shot-list">
          {shots.map((sh, i) => (
            <div className="card shot-card" key={i}>
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
                    className="shot-btn"
                    onClick={() => insertShot(i)}
                    title="Insert a shot below"
                  >
                    ＋
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
          ))}
        </div>

        <div className="add-shot-row">
          <button type="button" className="btn ghost add-shot-btn" onClick={addShot}>
            ＋ Add a shot
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

      <div className="review-actions top-actions sb-form-actions">
        <button type="button" className="btn" onClick={() => setStep("library")}>
          ← Your Storyboards
        </button>
      </div>

      <div className="sts-hero-grid">
      <div className="sts-form-wrap">
        {busy ? (
          <BreakdownProgress />
        ) : (
        <div className="card">
          {/* --- Title (what this board is saved as in the library) --- */}
          <label>Storyboard title</label>
          <input
            value={title}
            placeholder="Leave blank to name it after your script's first line"
            maxLength={120}
            onChange={(e) => setTitle(e.target.value)}
          />

          {/* --- Script --- */}
          <label className="sts-script-label">Your script</label>
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
            {/* Show the picked overflow genre so the selection stays visible. */}
            {MORE_GENRES.some((g) => g.id === genre) && (
              <button type="button" className="opt-chip active" onClick={() => setGenreMoreOpen(true)}>
                {ALL_GENRES.find((g) => g.id === genre)?.label}
              </button>
            )}
            <button
              type="button"
              className="opt-chip opt-chip-more"
              onClick={() => setGenreMoreOpen(true)}
            >
              ＋ More
            </button>
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
            {/* Show the picked overflow style so the selection stays visible. */}
            {MORE_STYLES.some((s) => s.id === style) && (
              <button type="button" className="opt-chip active" onClick={() => setStyleMoreOpen(true)}>
                {ALL_STYLES.find((s) => s.id === style)?.label}
              </button>
            )}
            <button
              type="button"
              className="opt-chip opt-chip-more"
              onClick={() => setStyleMoreOpen(true)}
            >
              ＋ More
            </button>
          </div>
          {style === "custom" && (
            <input
              className="custom-genre-input"
              value={customStyle}
              placeholder="Describe your own style, e.g. 1980s retro anime, ink wash…"
              onChange={(e) => setCustomStyle(e.target.value)}
            />
          )}

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
            <button
              type="button"
              className={`opt-chip ${aspect === "custom" ? "active" : ""}`}
              onClick={() => setAspect("custom")}
            >
              ＋ Custom
            </button>
          </div>
          {aspect === "custom" && (
            <input
              className="custom-genre-input"
              value={customAspect}
              placeholder="Type a ratio, e.g. 4:3, 5:4, 1.85:1…"
              onChange={(e) => setCustomAspect(e.target.value)}
            />
          )}

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
        )}
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
              Lock your cast, props &amp; backgrounds (optional)
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

      {genreMoreOpen && (
        <MorePopup
          title="More genres"
          options={MORE_GENRES}
          selected={genre}
          onSelect={(id) => setGenre(id)}
          onClose={() => setGenreMoreOpen(false)}
        />
      )}
      {styleMoreOpen && (
        <MorePopup
          title="More visual styles"
          options={MORE_STYLES}
          selected={style}
          onSelect={(id) => setStyle(id)}
          onClose={() => setStyleMoreOpen(false)}
        />
      )}
    </div>
  );
}

// Overlay picker for the overflow ("＋ More") genres / styles. Selecting an
// option applies it and closes; the ✕ (or a backdrop click) closes without change.
function MorePopup({ title, options, selected, onSelect, onClose }) {
  return (
    <div className="more-overlay" onClick={onClose}>
      <div className="more-panel" onClick={(e) => e.stopPropagation()}>
        <div className="more-head">
          <h3>{title}</h3>
          <button type="button" className="more-close" onClick={onClose} title="Close">
            ✕
          </button>
        </div>
        <div className="opt-chips">
          {options.map((o) => (
            <button
              key={o.id}
              type="button"
              className={`opt-chip ${selected === o.id ? "active" : ""}`}
              onClick={() => {
                onSelect(o.id);
                onClose();
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
