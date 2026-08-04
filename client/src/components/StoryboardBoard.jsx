// Storyboard board (Stage D output + start of Stage E).
// Polls the storyboard job, shows a live gold progress bar, and renders panels
// in a grid as they finish (each fetched as an authed blob). Matches the
// Text-to-Image workflow's look (WorkflowHeader + progress bar + gallery tiles).
import { useEffect, useRef, useState } from "react";
import ScriptPanel from "./ScriptPanel.jsx";
import * as api from "../api.js";
import DialogueBox from "./DialogueBox.jsx";

// Styles the user can re-cast the whole board into (kept as switchable variants).
const RESTYLE_OPTIONS = [
  { id: "rough-sketch", label: "✏️ Rough Sketch" },
  { id: "sketch", label: "🖊️ Sketch" },
  { id: "comic", label: "💥 Comic" },
  { id: "cinematic", label: "🎬 Cinematic" },
  { id: "animation-3d", label: "🧸 Animation 3D" },
  { id: "watercolor", label: "🎨 Watercolor Paint" },
  { id: "photo-commercial", label: "📷 Photo / Commercial" },
  { id: "charcoal", label: "🖤 Charcoal Sketch" },
  { id: "dark-anime", label: "🌃 Dark Anime" },
  { id: "flat-vector", label: "🔷 Flat / Vector" },
  { id: "noir", label: "🎞️ Noir" },
  { id: "stick-figure", label: "🏃 Stick Figure" },
  { id: "graphic-novel", label: "📖 Graphic Novel" },
];
const styleLabelFor = (id) =>
  RESTYLE_OPTIONS.find((s) => s.id === id)?.label || id || "Style";

export default function StoryboardBoard({
  jobId,
  styleLabel,
  aspect,
  backLabel,
  onBack,
  onRestart,
  // Set by App: hands the new animatic's id to the animatics workflow. Absent
  // when the board is rendered somewhere that can't navigate there.
  onOpenAnimatic,
}) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [panelUrls, setPanelUrls] = useState({});
  const panelUrlsRef = useRef({});
  const [lightbox, setLightbox] = useState(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState("");
  const [zipBusy, setZipBusy] = useState(false);
  const [animaticBusy, setAnimaticBusy] = useState(false);
  // A stop has been asked for but the run hasn't wound down yet (the panels
  // already talking to the image API still have to come back).
  const [stopRequested, setStopRequested] = useState(false);
  const [retrying, setRetrying] = useState({});
  // A running bulk draw: { kind: "remaining" | "failed", done, total }. One at a
  // time, and interruptible — `batchStopRef` is read between panels.
  const [batch, setBatch] = useState(null);
  const batchStopRef = useRef(false);
  // Per-panel edited descriptions (keyed by panel index). Undefined = unedited,
  // so the textarea falls back to the panel's stored description.
  const [editedDesc, setEditedDesc] = useState({});
  // Re-style controls + a nonce to restart polling after a restyle kicks off.
  const [newStyle, setNewStyle] = useState("comic");
  const [restyleBusy, setRestyleBusy] = useState(false);
  const [pollNonce, setPollNonce] = useState(0);

  // Poll the job until it finishes (recursive setTimeout — stops at terminal state).
  useEffect(() => {
    let active = true;
    let timer;
    async function poll() {
      if (!active) return;
      try {
        const j = await api.getJob(jobId);
        if (!active) return;
        setJob(j);
        if (j.status === "succeeded" || j.status === "failed") return;
      } catch (e) {
        if (active) setError(e.message);
      }
      timer = setTimeout(poll, 2000);
    }
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [jobId, pollNonce]);

  // Fetch each panel image (authed blob) once it has a url. Cached by the panel's
  // URL (which carries ?v=<variant>), so each style variant is fetched separately.
  useEffect(() => {
    const panels = job?.result?.panels || [];
    panels.forEach((p) => {
      if (p.url && !p.failed && !panelUrlsRef.current[p.url]) {
        panelUrlsRef.current[p.url] = "loading";
        api
          .fetchStoryboardPanel(jobId, p.index, p.url)
          .then((url) => {
            panelUrlsRef.current[p.url] = url;
            setPanelUrls((prev) => ({ ...prev, [p.url]: url }));
          })
          .catch(() => {
            panelUrlsRef.current[p.url] = null;
          });
      }
    });
  }, [job, jobId]);

  // Revoke blob URLs on unmount.
  useEffect(() => {
    return () => {
      Object.values(panelUrlsRef.current).forEach((u) => {
        if (typeof u === "string" && u.startsWith("blob:")) URL.revokeObjectURL(u);
      });
    };
  }, []);

  const status = job?.status;
  const progress = job?.progress || {};
  const panels = job?.result?.panels || [];
  const total = job?.result?.count || job?.params?.count || panels.length || 0;
  // The board's saved title and source script. Both live on the job record, so
  // they're here whether the board was just generated or reopened from the
  // library. Falls back while the first poll is still in flight.
  const boardTitle = job?.character_name || "Your storyboard";
  const boardScript = job?.params?.script || "";
  const running = status === "queued" || status === "running" || !status;
  // The run ended early because the user pressed Stop (server-reported, so it
  // survives a reload) — the board says so instead of looking half-finished.
  const stopped = Boolean(job?.result?.stopped);
  // Style variants (each = one full-board render). Absent on older jobs → treat
  // the flat panels as the single variant 0.
  const variants =
    job?.result?.variants ||
    (panels.length ? [{ style: job?.result?.style, panels }] : []);
  const activeVariant = job?.result?.active_variant || 0;

  // Switch which style variant is shown (persist server-side, update locally).
  async function switchVariant(idx) {
    if (idx === activeVariant || running) return;
    const v = variants[idx];
    if (!v) return;
    setError("");
    setJob((prev) =>
      prev
        ? {
            ...prev,
            result: {
              ...prev.result,
              active_variant: idx,
              panels: v.panels || [],
              style: v.style,
              ok_count: v.ok_count,
            },
          }
        : prev
    );
    try {
      await api.setActiveVariant(jobId, idx);
    } catch (e) {
      setError(e.message);
    }
  }

  // Re-draw the whole board in a new style (kept as a new variant); resume polling.
  async function handleRestyle() {
    if (restyleBusy || running || !newStyle) return;
    setError("");
    setRestyleBusy(true);
    try {
      await api.restyleStoryboard(jobId, newStyle);
      setPollNonce((n) => n + 1); // restart the poll loop for the running restyle
    } catch (e) {
      setError(e.message);
    } finally {
      setRestyleBusy(false);
    }
  }
  const pendingCount = Math.max(0, total - panels.length);
  const tileRatio = (aspect || "16:9").replace(":", " / ");
  const okCount = panels.filter((p) => !p.failed && p.url).length;
  const failedCount = panels.filter((p) => p.failed).length;

  // Panels with no image and no failure = never drawn. That's what a stopped
  // run leaves behind, and what "Generate remaining" finishes off.
  const emptyIdx = panels.filter((p) => !p.url && !p.failed).map((p) => p.index);
  const failedIdx = panels.filter((p) => p.failed).map((p) => p.index);

  // Draw a set of panels ONE AT A TIME (gentler on the rate limit than firing
  // them all at once) — shared by "Generate remaining" and "Retry all failed".
  // The loop is interruptible: a 20-panel batch going wrong shouldn't have to
  // run to the end, same reasoning as the Stop button on the run itself.
  async function runBatch(kind, indices) {
    if (batch || indices.length === 0) return;
    batchStopRef.current = false;
    setBatch({ kind, done: 0, total: indices.length });
    for (const [i, idx] of indices.entries()) {
      if (batchStopRef.current) break;
      // eslint-disable-next-line no-await-in-loop
      await retryPanel(idx);
      setBatch({ kind, done: i + 1, total: indices.length });
    }
    setBatch(null);
  }

  // Re-draw a single panel (failed, edited, or just unwanted). Sends the edited
  // description when the user has changed the shot's prompt.
  // After a structural edit (insert/delete) panel indices shift, so a cached
  // blob keyed by "/panel/2" may now belong to a different shot. Drop the whole
  // cache and reload the job so every tile re-fetches the right pixels.
  async function reloadBoard() {
    Object.values(panelUrlsRef.current).forEach((u) => {
      if (typeof u === "string" && u.startsWith("blob:")) URL.revokeObjectURL(u);
    });
    panelUrlsRef.current = {};
    setPanelUrls({});
    // editedDesc / retrying are keyed by index; indices just shifted, so any
    // stale per-index entries would land on the wrong tile. Clear them — the
    // server persisted each panel's description, so nothing is lost.
    setEditedDesc({});
    setRetrying({});
    const j = await api.getJob(jobId);
    setJob(j);
  }

  const [editBusy, setEditBusy] = useState(false);

  // Insert a new (empty) panel at position `at`; the user then writes a prompt
  // and generates it with the existing per-panel Generate button.
  async function addPanelAt(at) {
    if (editBusy || running) return;
    setError("");
    setEditBusy(true);
    try {
      await api.insertStoryboardPanel(jobId, at);
      await reloadBoard();
    } catch (e) {
      setError(e.message);
    } finally {
      setEditBusy(false);
    }
  }

  async function deletePanel(index) {
    if (editBusy || running) return;
    setError("");
    setEditBusy(true);
    try {
      await api.deleteStoryboardPanel(jobId, index);
      await reloadBoard();
    } catch (e) {
      setError(e.message);
    } finally {
      setEditBusy(false);
    }
  }

  async function retryPanel(index) {
    if (retrying[index]) return;
    setError("");
    setRetrying((r) => ({ ...r, [index]: true }));
    try {
      const overrides = {};
      if (typeof editedDesc[index] === "string") overrides.description = editedDesc[index];
      // Old URL of this panel (within the active variant) — bust its cache so the
      // fetch effect re-loads the fresh pixels.
      const prevUrl = (job?.result?.panels || []).find((p) => p.index === index)?.url;
      const res = await api.regenerateStoryboardPanel(jobId, index, overrides);
      const panel = res.panel;
      [prevUrl, panel.url].forEach((u) => {
        if (!u) return;
        const cached = panelUrlsRef.current[u];
        if (typeof cached === "string" && cached.startsWith("blob:")) URL.revokeObjectURL(cached);
        delete panelUrlsRef.current[u];
      });
      setPanelUrls((prev) => {
        const next = { ...prev };
        if (prevUrl) delete next[prevUrl];
        if (panel.url) delete next[panel.url];
        return next;
      });
      setJob((prev) => {
        if (!prev) return prev;
        const r = prev.result || {};
        const panels = (r.panels || []).map((p) => (p.index === index ? panel : p));
        const variants = r.variants
          ? r.variants.map((v, i) => (i === (r.active_variant || 0) ? { ...v, panels } : v))
          : r.variants;
        return { ...prev, result: { ...r, panels, variants } };
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setRetrying((r) => ({ ...r, [index]: false }));
    }
  }

  // Fallback download name, used only if the server's Content-Disposition can't
  // be read. Mirrors the server's _safe_filename: punctuation → space, runs
  // collapsed, so "Postmarked: After Death!" reads as "Postmarked After Death".
  function safeTitle() {
    const cleaned = (job?.character_name || "")
      .replace(/['’]/g, "") // "Kabir's" → "Kabirs", never "Kabir s"
      .replace(/[^\p{L}\p{N}\-_ ]/gu, " ")
      .split(/\s+/)
      .filter(Boolean)
      .join(" ")
      .replace(/^[-_ ]+|[-_ ]+$/g, "");
    return cleaned || "storyboard";
  }

  async function handlePdf() {
    if (pdfBusy) return;
    setPdfError("");
    setPdfBusy(true);
    try {
      await api.downloadStoryboardPdf(jobId, `${safeTitle()}.pdf`);
    } catch (e) {
      setPdfError(e.message);
    } finally {
      setPdfBusy(false);
    }
  }

  // Download generated references (characters + props/backgrounds) + PDF as a ZIP,
  // so the user can re-upload the same references next time instead of regenerating.
  async function handleZip() {
    if (zipBusy) return;
    setPdfError("");
    setZipBusy(true);
    try {
      await api.downloadStoryboardBundle(jobId, `${safeTitle()}_assets.zip`);
    } catch (e) {
      setPdfError(e.message);
    } finally {
      setZipBusy(false);
    }
  }

  // Turn this board into an animatic: every drawn panel becomes a frame at a
  // 2-second hold, and the animatics editor opens on it. Costs no AI quota —
  // the frames reference these panels rather than redrawing anything.
  async function handleMakeAnimatic() {
    if (animaticBusy) return;
    setPdfError("");
    setAnimaticBusy(true);
    try {
      const project = await api.createAnimatic({ sourceStoryboardId: jobId });
      onOpenAnimatic(project.job_id);
    } catch (e) {
      setPdfError(e.message);
      setAnimaticBusy(false);
    }
  }

  // Once the run is over the flag has done its job — clear it so a later
  // re-style doesn't open with the button already reading "Stopping…".
  useEffect(() => {
    if (!running) setStopRequested(false);
  }, [running]);

  // Stop the run. Panels not yet started are skipped; ones already in flight
  // finish, so the button keeps saying "Stopping…" until the job goes terminal.
  async function handleStop() {
    if (stopRequested || !running) return;
    setError("");
    setStopRequested(true);
    try {
      await api.stopStoryboard(jobId);
    } catch (e) {
      setError(e.message);
      setStopRequested(false); // it didn't take — let them press it again
    }
  }

  return (
    <div className="workflow-head-wrap sb-board">
      <div className="workflow-header">
        <span className="wf-icon">🎬</span>
        <div>
          {/* The board's OWN title, not a generic heading — it's what names the
              library card, the PDF and the ZIP, so seeing it here is how you
              know which board you're looking at. */}
          <h1 className="wf-title">{boardTitle}</h1>
          <p className="muted">
            {styleLabel} · {aspect} · {total} panel{total === 1 ? "" : "s"}
          </p>
        </div>
      </div>

      <div className="review-actions board-actions top-actions">
        <button type="button" className="btn" onClick={onBack}>
          {backLabel || "← Back to shots"}
        </button>
        <button type="button" className="btn ghost" onClick={onRestart}>
          Start over
        </button>
      </div>

      {/* Style variants: switch between saved styles, or add a new one. */}
      {variants.length > 0 && (
        <div className="board-styles">
          {variants.length > 1 && (
            <div className="board-variant-switch">
              <span className="board-styles-label">Style:</span>
              {variants.map((v, i) => (
                <button
                  key={i}
                  type="button"
                  className={`opt-chip ${i === activeVariant ? "active" : ""}`}
                  disabled={running}
                  onClick={() => switchVariant(i)}
                  title={`Show the ${styleLabelFor(v.style)} version`}
                >
                  {styleLabelFor(v.style)}
                </button>
              ))}
            </div>
          )}
          <div className="board-restyle">
            <span className="board-styles-label">Add a style:</span>
            <select
              className="board-style-select"
              value={newStyle}
              disabled={running || restyleBusy}
              onChange={(e) => setNewStyle(e.target.value)}
            >
              {RESTYLE_OPTIONS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn secondary"
              disabled={running || restyleBusy}
              onClick={handleRestyle}
              title="Re-draw every panel in this style, kept as a new switchable version"
            >
              {restyleBusy ? (
                <>
                  <span className="spinner-inline" /> Starting…
                </>
              ) : (
                "🎨 Restyle all"
              )}
            </button>
          </div>
        </div>
      )}

      {error && <div className="error">{error}</div>}
      {pdfError && <div className="error">{pdfError}</div>}
      {status === "failed" && (
        <div className="error">Generation failed: {job?.error || "unknown error"}</div>
      )}

      {stopped && (
        <div className="info-msg">
          ⏹ You stopped this generation — {okCount} of {total} panels drawn.
          {emptyIdx.length > 0
            ? ` Use “✨ Generate remaining (${emptyIdx.length})” to finish the board, or “✨ Generate this panel” on a single tile.`
            : " Every panel now has an image."}
        </div>
      )}

      {/* Toolbar is up while GENERATING too, so Stop is reachable from the
          moment the first panel looks wrong — that's the point of it. */}
      {(running || okCount > 0 || failedCount > 0) && (
        <div className="board-toolbar">
          {running && (
            <button
              type="button"
              className="btn danger-btn"
              disabled={stopRequested}
              onClick={handleStop}
              title="Stop drawing the remaining panels — the ones already started will still finish"
            >
              {stopRequested ? (
                <>
                  <span className="spinner-inline" /> Stopping…
                </>
              ) : (
                "⏹ Stop generation"
              )}
            </button>
          )}
          {/* Finish a stopped board in one click, instead of tile by tile. */}
          {!running && emptyIdx.length > 0 && (
            <button
              type="button"
              /* While drawing it's BUSY, not unavailable — the gold fill dimmed
                 to 45% read as a broken slab next to the live buttons. */
              className={`btn ${batch?.kind === "remaining" ? "is-busy" : "primary"}`}
              disabled={Boolean(batch)}
              onClick={() => runBatch("remaining", emptyIdx)}
              title="Draw every panel that has no image yet, one at a time"
            >
              {batch?.kind === "remaining" ? (
                <>
                  <span className="spinner-inline" /> Drawing{" "}
                  {Math.min(batch.done + 1, batch.total)} of {batch.total}…
                </>
              ) : (
                `✨ Generate remaining (${emptyIdx.length})`
              )}
            </button>
          )}
          {!running && failedCount > 0 && (
            <button
              type="button"
              className={`btn board-retry-all ${batch?.kind === "failed" ? "is-busy" : ""}`}
              disabled={Boolean(batch)}
              onClick={() => runBatch("failed", failedIdx)}
            >
              {batch?.kind === "failed" ? (
                <>
                  <span className="spinner-inline" /> Retrying{" "}
                  {Math.min(batch.done + 1, batch.total)} of {batch.total}…
                </>
              ) : (
                `🔄 Retry all failed (${failedCount})`
              )}
            </button>
          )}
          {/* A bulk draw is many paid generations — it must be interruptible too. */}
          {batch && (
            <button
              type="button"
              className="btn danger-btn"
              onClick={() => {
                batchStopRef.current = true;
              }}
              title="Stop after the panel currently being drawn"
            >
              ⏹ Stop
            </button>
          )}
          {okCount > 0 && (
            <button
              type="button"
              className="btn"
              disabled={pdfBusy}
              onClick={handlePdf}
            >
              {pdfBusy ? (
                <>
                  <span className="spinner-inline" /> Preparing PDF…
                </>
              ) : (
                "⬇ Download PDF"
              )}
            </button>
          )}
          {okCount > 0 && (
            <button
              type="button"
              className="btn"
              disabled={zipBusy}
              onClick={handleZip}
              title="Generated character, prop & background images + the PDF, as a ZIP you can reuse"
            >
              {zipBusy ? (
                <>
                  <span className="spinner-inline" /> Zipping…
                </>
              ) : (
                "⬇ Download assets (ZIP)"
              )}
            </button>
          )}
          {/* LAST in the row, after the two downloads: it's the step you take
              once the board is done, not another export. No AI credits spent. */}
          {okCount > 0 && onOpenAnimatic && !running && (
            <button
              type="button"
              className="btn board-animatic"
              disabled={animaticBusy}
              onClick={handleMakeAnimatic}
              title="Time these panels against audio and export a video — costs no AI credits"
            >
              {animaticBusy ? (
                <>
                  <span className="spinner-inline" /> Opening…
                </>
              ) : (
                "🎬 Make animatic"
              )}
            </button>
          )}
        </div>
      )}

      {running && (
        <div className="job-progress">
          <div className="jp-row">
            <span className="jp-msg">
              <span className="spinner-inline" />
              {progress.message || "Starting…"}
            </span>
            <span className="jp-pct">{progress.percent ?? 0}%</span>
          </div>
          <div className="jp-bar">
            <div className="jp-fill" style={{ width: `${progress.percent ?? 0}%` }} />
          </div>
        </div>
      )}

      <div className="board-grid">
        {panels.map((p) => {
          // A new panel the user inserted: no image yet, board not generating.
          const isNew = !p.url && !p.failed && !running;
          return (
            <figure className="board-tile" key={p.index}>
              <div className="board-frame" style={{ aspectRatio: tileRatio }}>
                {p.url && panelUrls[p.url] ? (
                  <img
                    src={panelUrls[p.url]}
                    alt={`Panel ${p.index + 1}`}
                    onClick={() => setLightbox(panelUrls[p.url])}
                  />
                ) : p.failed ? (
                  <div className="board-failed">
                    {retrying[p.index] ? (
                      <>
                        <span className="spinner" /> Redrawing…
                      </>
                    ) : (
                      <span>⚠️ Couldn’t draw this panel</span>
                    )}
                  </div>
                ) : isNew ? (
                  <div className="board-newpanel">
                    {retrying[p.index] ? (
                      <>
                        <span className="spinner" /> Drawing…
                      </>
                    ) : (
                      <span>✏️ New panel — write a prompt, then Generate</span>
                    )}
                  </div>
                ) : (
                  <div className="board-skeleton" />
                )}
              </div>
              <figcaption>
                <div className="board-caption-head">
                  <span className="board-shotnum">
                    Shot {p.index + 1}
                    {p.scene_number ? (
                      <span className="board-scene">Scene {p.scene_number}</span>
                    ) : null}
                  </span>
                  {/* Structural edits only while the board isn't generating. */}
                  {!running && (
                    <div className="board-tile-actions">
                      <button
                        type="button"
                        className="shot-btn"
                        onClick={() => addPanelAt(p.index)}
                        disabled={editBusy}
                        title="Add a panel before this one"
                      >
                        ＋
                      </button>
                      <button
                        type="button"
                        className="shot-btn danger"
                        onClick={() => deletePanel(p.index)}
                        disabled={editBusy || panels.length <= 1}
                        title="Delete this panel"
                      >
                        ✕
                      </button>
                    </div>
                  )}
                </div>
                <textarea
                  className="board-caption-edit"
                  value={editedDesc[p.index] ?? p.description ?? ""}
                  onChange={(e) =>
                    setEditedDesc((d) => ({ ...d, [p.index]: e.target.value }))
                  }
                  rows={2}
                  placeholder="Describe what we see in this shot…"
                />
                {/* What is spoken in this panel — nothing at all for a silent
                    shot. Read-only here; dialogue is edited on the shot list.
                    ⚠ ORDER: image prompt → dialogue → camera/location → cast.
                    The review card and the PDF print the same order; a panel
                    that reads differently in three places reads as three
                    different tools. */}
                <DialogueBox dialogue={p.dialogue} className="board-dialogue" />
                <button
                  type="button"
                  className={`btn small board-regen-btn ${isNew ? "secondary" : ""}`}
                  onClick={() => retryPanel(p.index)}
                  disabled={retrying[p.index]}
                  title={isNew ? "Draw this panel" : "Re-draw this shot with the current prompt"}
                >
                  {retrying[p.index] ? (
                    <>
                      <span className="spinner-inline" /> {isNew ? "Generating…" : "Redrawing…"}
                    </>
                  ) : isNew ? (
                    "✨ Generate this panel"
                  ) : p.failed ? (
                    "🔄 Retry"
                  ) : (
                    "🔄 Regenerate"
                  )}
                </button>
              </figcaption>
            </figure>
          );
        })}

        {/* Placeholders for shots not yet reached */}
        {Array.from({ length: pendingCount }).map((_, i) => (
          <figure className="board-tile" key={`pending-${i}`}>
            <div className="board-frame" style={{ aspectRatio: tileRatio }}>
              <div className="board-skeleton" />
            </div>
            <figcaption>
              <span className="board-shotnum">Shot {panels.length + i + 1}</span>
              Waiting…
            </figcaption>
          </figure>
        ))}

        {/* Append a new panel at the end (only when nothing is generating). */}
        {!running && panels.length > 0 && (
          <button
            type="button"
            className="board-tile board-add-tile"
            onClick={() => addPanelAt(panels.length)}
            disabled={editBusy}
            title="Add a panel at the end"
          >
            {editBusy ? <span className="spinner" /> : <span>＋ Add a panel</span>}
          </button>
        )}
      </div>

      {/* The script this board was drawn from, under the panels. Collapsed:
          it's reference material here, not the subject of the page. The shot
          cards cite line numbers, and this is where they're looked up. */}
      <ScriptPanel script={boardScript} defaultOpen={false} />

      {lightbox && (
        <div className="lightbox-overlay" onClick={() => setLightbox(null)}>
          <div className="lightbox-figure" onClick={(e) => e.stopPropagation()}>
            <button className="lightbox-close" onClick={() => setLightbox(null)}>
              ✕
            </button>
            <img className="lightbox-img" src={lightbox} alt="Panel" />
          </div>
        </div>
      )}
    </div>
  );
}
