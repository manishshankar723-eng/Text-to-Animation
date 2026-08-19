// Storyboard board (Stage D output + start of Stage E).
// Polls the storyboard job, shows a live gold progress bar, and renders panels
// in a grid as they finish (each fetched as an authed blob). Matches the
// Text-to-Image workflow's look (WorkflowHeader + progress bar + gallery tiles).
import { useEffect, useRef, useState } from "react";
import ScriptPanel from "./ScriptPanel.jsx";
import * as api from "../api.js";
import DialogueBox from "./DialogueBox.jsx";
import PanelSequenceStrip from "./PanelSequenceStrip.jsx";
import PanelVersions from "./PanelVersions.jsx";

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
// Exported so anywhere that mounts this board (Image to Video's "Create
// Animatic Image") shows the SAME style names, instead of a second copy of the
// list that drifts.
export const styleLabelFor = (id) =>
  RESTYLE_OPTIONS.find((s) => s.id === id)?.label || id || "Style";

export default function StoryboardBoard({
  jobId,
  styleLabel,
  aspect,
  // WHERE the back arrow goes ("Your Storyboards"). Prose only — no arrow in
  // it: the button draws that itself, and this is read as a tooltip.
  backLabel,
  onBack,
  onRestart,
  // Set by App: hands the new animatic's id to the animatics workflow. Absent
  // when the board is rendered somewhere that can't navigate there.
  onOpenAnimatic,
  // Image to Animatic Image turns this on. It stacks the shots in ONE column
  // and gives each a key-pose strip (PanelSequenceStrip). Off everywhere else,
  // so Script to Storyboard's board is exactly as it was.
  sequenceMode = false,
}) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [panelUrls, setPanelUrls] = useState({});
  const panelUrlsRef = useRef({});
  // Blobs REPLACED by a fresher render of the same panel. They can't be revoked
  // at swap time — the <img> is still showing one until React commits the new
  // src — so they are parked here and freed on unmount with everything else.
  // Bounded by how many times you redraw in one sitting.
  const retiredBlobs = useRef([]);
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
        // A poll that WORKS clears the last poll's complaint. Without this, one
        // slow or dropped request left "The server didn't respond within 120s"
        // pinned over a board that had long since recovered and was visibly
        // drawing panels — reported, and it sent us hunting a server fault that
        // had already fixed itself.
        setError((prev) => (prev ? "" : prev));
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
            // A version switch or a redraw may have swapped a FRESHER picture
            // into this key while this fetch was in flight. Theirs wins — the
            // slot no longer says "loading" — or we would quietly put the old
            // pixels back, which is the bug this whole cache is meant to avoid.
            if (panelUrlsRef.current[p.url] !== "loading") {
              URL.revokeObjectURL(url);
              return;
            }
            panelUrlsRef.current[p.url] = url;
            setPanelUrls((prev) => ({ ...prev, [p.url]: url }));
          })
          .catch(() => {
            panelUrlsRef.current[p.url] = null;
          });
      }
    });
  }, [job, jobId]);

  // Revoke blob URLs on unmount — the live cache AND the ones superseded by a
  // redraw, which are no longer reachable through it.
  useEffect(() => {
    return () => {
      Object.values(panelUrlsRef.current).forEach((u) => {
        if (typeof u === "string" && u.startsWith("blob:")) URL.revokeObjectURL(u);
      });
      retiredBlobs.current.forEach((u) => URL.revokeObjectURL(u));
    };
  }, []);

  // Refresh ONE panel's picture. Nothing else on the board is touched.
  //
  // This exists because `reloadBoard` drops EVERY tile's blob, and it was being
  // used to refresh a single panel — so switching one shot's version, or
  // redrawing one shot, made the whole board blink through empty boxes and
  // re-download every picture (reported: "i only regenerate this panel so why
  // all image refresh"). `reloadBoard` is the right tool ONLY for insert and
  // delete, where indices shift and a blob keyed by "/panel/2" now belongs to a
  // different shot.
  //
  // The new bytes are fetched BEFORE anything on screen changes, so the tile
  // goes from the old picture straight to the new one with no empty frame in
  // between. `fetchStoryboardPanel` cache-busts its request, so an UNCHANGED
  // url — which is exactly what a version switch leaves you with — still comes
  // back with the new pixels.
  async function refreshPanelImage(index, url) {
    const target =
      url || (job?.result?.panels || []).find((p) => p.index === index)?.url;
    if (!target) return;
    let fresh;
    try {
      fresh = await api.fetchStoryboardPanel(jobId, index, target);
    } catch {
      return; // keep the picture that's up rather than blanking the tile
    }
    const displaced = panelUrlsRef.current[target];
    if (typeof displaced === "string" && displaced.startsWith("blob:")) {
      retiredBlobs.current.push(displaced);
    }
    panelUrlsRef.current[target] = fresh;
    setPanelUrls((prev) => ({ ...prev, [target]: fresh }));
  }

  // Forget one cache key. Only for a url nothing renders any more — a redraw
  // that moved the panel to a different url.
  function dropPanelImage(url) {
    if (!url) return;
    const cached = panelUrlsRef.current[url];
    if (typeof cached === "string" && cached.startsWith("blob:")) {
      retiredBlobs.current.push(cached);
    }
    delete panelUrlsRef.current[url];
    setPanelUrls((prev) => {
      if (!(url in prev)) return prev;
      const next = { ...prev };
      delete next[url];
      return next;
    });
  }

  const status = job?.status;
  const progress = job?.progress || {};
  const panels = job?.result?.panels || [];
  const total = job?.result?.count || job?.params?.count || panels.length || 0;
  // The board's saved title and source script. Both live on the job record, so
  // they're here whether the board was just generated or reopened from the
  // library. Falls back while the first poll is still in flight.
  const boardTitle = job?.character_name || "Your storyboard";
  const boardScript = job?.params?.script || "";
  // "We don't know yet" is NOT "it is generating". This used to read
  // `|| !status`, so any board whose job could not be fetched — the server
  // restarting, a dropped request, a poll that errored — rendered as a live
  // run: "Stop generation" in the toolbar, the progress bar up, and every
  // Regenerate button hidden because the board believed it was busy. Nothing
  // the user pressed could recover it. Reported as "i cant see regenarte
  // buttun and i see nothing happen".
  //
  // Unknown status counts as running ONLY while the first fetch is genuinely in
  // flight, which keeps the toolbar from flashing on load. Once a fetch has
  // failed, the board is treated as idle so its buttons come back and the user
  // can act — the error banner already says the fetch failed.
  const loadingFirst = !job && !error;
  const running = status === "queued" || status === "running" || loadingFirst;
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

  // The two "the board is done, what now" actions. A render FUNCTION rather
  // than duplicated JSX, because they sit in the toolbar normally but in the
  // TOP row in sequenceMode (which has no Start over to compete with) — one
  // definition, so the two placements can't drift apart.
  function finishActions() {
    if (!okCount) return null;
    return (
      <>
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
        {/* LAST, after the download: it's the step you take once the board is
            done, not another export. No AI credits spent. */}
        {onOpenAnimatic && !running && (
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
      </>
    );
  }
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

  // THE NUCLEAR RELOAD — for STRUCTURAL edits only (insert / delete).
  //
  // Those shift every later panel's index, so a cached blob keyed by "/panel/2"
  // may now belong to a different shot and the whole cache has to go. That is
  // also why the entire board visibly re-downloads afterwards, which is
  // acceptable exactly once, for an edit that really did change every tile's
  // identity.
  //
  // DO NOT use this to refresh ONE panel — that is `refreshPanelImage`. Calling
  // it for a version switch or a single redraw is what made the whole board
  // blink every time the user pressed ‹ ›.
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

  // Re-draw a single panel (failed, edited, or just unwanted). Sends the edited
  // description when the user has changed the shot's prompt.
  async function retryPanel(index) {
    if (retrying[index]) return;
    setError("");
    setRetrying((r) => ({ ...r, [index]: true }));
    try {
      const overrides = {};
      if (typeof editedDesc[index] === "string") overrides.description = editedDesc[index];
      const prevUrl = (job?.result?.panels || []).find((p) => p.index === index)?.url;
      const res = await api.regenerateStoryboardPanel(jobId, index, overrides);
      const panel = res.panel;
      // NEW PIXELS FIRST, then the new panel object. Emptying the cache and
      // letting the fetch effect pick it up afterwards — which is what this did
      // — meant the tile rendered at least once with no picture at all, so a
      // redraw flashed an empty box before the drawing appeared. Priming the
      // cache before `setJob` closes that gap: by the time render sees the new
      // url, its picture is already in hand.
      await refreshPanelImage(index, panel.url);
      setJob((prev) => {
        if (!prev) return prev;
        const r = prev.result || {};
        const panels = (r.panels || []).map((p) => (p.index === index ? panel : p));
        const variants = r.variants
          ? r.variants.map((v, i) => (i === (r.active_variant || 0) ? { ...v, panels } : v))
          : r.variants;
        return { ...prev, result: { ...r, panels, variants } };
      });
      // Only now that nothing renders it: if the redraw moved the panel to a
      // different url, the old key is dead weight holding a blob.
      if (prevUrl && prevUrl !== panel.url) dropPanelImage(prevUrl);
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
        {/* Arrow only, like every other back control in the app — `backLabel`
            is now WHERE it goes, and that reads in the tooltip. */}
        <button
          type="button"
          className="btn back-btn"
          onClick={onBack}
          title={backLabel || "Back to shots"}
          aria-label={backLabel || "Back to shots"}
        >
          ←
        </button>
        {/* "Start over" belongs to the workflow that BUILDS a board (it resets
            the script→shots flow). In sequenceMode there is nothing to start
            over — the board is a copy you opened — so the finish actions take
            that spot instead of leaving it empty. */}
        {sequenceMode ? (
          /* Wrapped: `.review-actions` is space-between, so two loose children
             on the right would spread across the row instead of grouping.
             `.review-actions-right` is the existing answer to that. */
          <div className="review-actions-right">{finishActions()}</div>
        ) : (
          <button type="button" className="btn ghost" onClick={onRestart}>
            Start over
          </button>
        )}
      </div>

      {/* Style variants: switch between saved styles, or add a new one.
          Hidden in sequenceMode — this workflow is about drawing the MOTION of
          a board you already styled, and re-styling every panel would throw the
          key poses out of step with the panels they were drawn from. Restyle in
          Script to Storyboard, then copy the board over. */}
      {!sequenceMode && variants.length > 0 && (
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

      {/* Hidden in sequenceMode: it reports on the PANEL draw, which happened
          back in Script to Storyboard before this copy was ever made, so here
          it is stale news about someone else's run. Each shot's own key-pose
          strip reports its own state. */}
      {!sequenceMode && stopped && (
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
          {/* A PDF is a document to hand someone — the output of Script to
              Storyboard. This workflow produces IMAGES, so its downloads are
              the assets and the per-shot key poses instead. */}
          {!sequenceMode && okCount > 0 && (
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
          {/* In sequenceMode these two live in the TOP row instead — see
              `finishActions`, rendered once in whichever place applies. */}
          {!sequenceMode && finishActions()}
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

      {/* The source script, ABOVE the panels and collapsed. It's reference for
          reading the board (the shot cards cite "LINE n"), so it belongs with
          the board's other context — not wedged under the grid where it pushed
          the download buttons off the end of the page. */}
      <ScriptPanel script={boardScript} defaultOpen={false} />

      {/* One column in sequence mode: each shot's key-pose strip sits directly
          under its panel, so shot 2 reads BELOW shot 1 rather than beside it.
          A grid would put the strip in a narrow column and break the reading
          order the flipbook depends on. */}
      <div className={`board-grid ${sequenceMode ? "board-column" : ""}`}>
        {panels.map((p) => {
          // A new panel the user inserted: no image yet, board not generating.
          const isNew = !p.url && !p.failed && !running;
          return (
            <figure className="board-tile" key={p.index}>
              <div
                className={`board-frame ${retrying[p.index] ? "is-redrawing" : ""}`}
                style={{ aspectRatio: tileRatio }}
              >
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

                {/* REDRAWING A PANEL THAT ALREADY HAS A PICTURE. The branches
                    above only show a spinner for a FAILED or a NEW panel — a
                    shot with an image kept showing that image, unchanged, for
                    the whole 30-60s redraw, so the board looked like it had
                    ignored the click. Same veil the key-pose strip uses, for
                    the same reason and with the same wording. */}
                {retrying[p.index] && p.url && panelUrls[p.url] && (
                  <span className="redraw-veil">
                    <span className="spinner-inline" />
                    <span className="tiny">Redrawing…</span>
                  </span>
                )}

                {/* Sits ON the picture, bottom-right. Renders nothing until the
                    shot has been redrawn at least once, so an untouched board
                    looks exactly as it always did. */}
                {p.url && (
                  <PanelVersions
                    jobId={jobId}
                    index={p.index}
                    disabled={running || !!retrying[p.index]}
                    /* Just THIS panel. Switching a version changes one shot's
                       pixels and shifts no indices, so there is nothing for the
                       rest of the board to re-read — it used to call
                       `reloadBoard`, which dropped every tile's blob and made
                       the whole page blink on every ‹ › press. */
                    onSwitched={() => refreshPanelImage(p.index, p.url)}
                  />
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
                  ) : sequenceMode ? (
                    /* In this workflow the panel is a starting point you draw
                       FROM, so the action is "Generate", not "Regenerate" —
                       the same word the key-pose button uses. */
                    "✨ Generate panel"
                  ) : (
                    "🔄 Regenerate"
                  )}
                </button>

                {sequenceMode && (
                  <PanelSequenceStrip
                    jobId={jobId}
                    index={p.index}
                    label={`Scene ${p.scene_number ?? 1} · Shot ${p.shot_number ?? p.index + 1}`}
                    boardBusy={running}
                    progress={progress}
                    onError={setPdfError}
                    onStarted={() => setPollNonce((n) => n + 1)}
                  />
                )}
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
