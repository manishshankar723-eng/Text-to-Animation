// Storyboard board (Stage D output + start of Stage E).
// Polls the storyboard job, shows a live gold progress bar, and renders panels
// in a grid as they finish (each fetched as an authed blob). Matches the
// Text-to-Image workflow's look (WorkflowHeader + progress bar + gallery tiles).
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";

// Styles the user can re-cast the whole board into (kept as switchable variants).
const RESTYLE_OPTIONS = [
  { id: "sketch", label: "✏️ Sketch" },
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
}) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [panelUrls, setPanelUrls] = useState({});
  const panelUrlsRef = useRef({});
  const [lightbox, setLightbox] = useState(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState("");
  const [zipBusy, setZipBusy] = useState(false);
  const [retrying, setRetrying] = useState({});
  const [retryingAll, setRetryingAll] = useState(false);
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
  const running = status === "queued" || status === "running" || !status;
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

  // Retry every failed panel, one at a time (gentler on rate limits).
  async function retryAllFailed() {
    if (retryingAll) return;
    const failedIdx = (job?.result?.panels || [])
      .filter((p) => p.failed)
      .map((p) => p.index);
    if (failedIdx.length === 0) return;
    setRetryingAll(true);
    for (const idx of failedIdx) {
      // eslint-disable-next-line no-await-in-loop
      await retryPanel(idx);
    }
    setRetryingAll(false);
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

  async function handlePdf() {
    if (pdfBusy) return;
    setPdfError("");
    setPdfBusy(true);
    try {
      await api.downloadStoryboardPdf(jobId, "storyboard.pdf");
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
      await api.downloadStoryboardBundle(jobId, "storyboard_assets.zip");
    } catch (e) {
      setPdfError(e.message);
    } finally {
      setZipBusy(false);
    }
  }

  return (
    <div className="workflow-head-wrap sb-board">
      <div className="workflow-header">
        <span className="wf-icon">🎬</span>
        <div>
          <h1 className="wf-title">Your storyboard</h1>
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

      {(okCount > 0 || failedCount > 0) && (
        <div className="board-toolbar">
          {failedCount > 0 && (
            <button
              type="button"
              className="btn board-retry-all"
              disabled={retryingAll}
              onClick={retryAllFailed}
            >
              {retryingAll ? (
                <>
                  <span className="spinner-inline" /> Retrying failed…
                </>
              ) : (
                `🔄 Retry all failed (${failedCount})`
              )}
            </button>
          )}
          {okCount > 0 && (
            <button
              type="button"
              className="btn primary"
              disabled={pdfBusy}
              onClick={handlePdf}
            >
              {pdfBusy ? (
                <>
                  <span className="spinner-inline" /> Preparing PDF…
                </>
              ) : (
                `⬇ Download PDF (${okCount})`
              )}
            </button>
          )}
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
        {panels.map((p) => (
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
              ) : (
                <div className="board-skeleton" />
              )}
            </div>
            <figcaption>
              <span className="board-shotnum">Shot {p.index + 1}</span>
              <textarea
                className="board-caption-edit"
                value={editedDesc[p.index] ?? p.description ?? ""}
                onChange={(e) =>
                  setEditedDesc((d) => ({ ...d, [p.index]: e.target.value }))
                }
                rows={2}
                placeholder="Describe what we see in this shot…"
              />
              <button
                type="button"
                className="btn small board-regen-btn"
                onClick={() => retryPanel(p.index)}
                disabled={retrying[p.index]}
                title="Re-draw this shot with the current prompt"
              >
                {retrying[p.index] ? (
                  <>
                    <span className="spinner-inline" /> Redrawing…
                  </>
                ) : p.failed ? (
                  "🔄 Retry"
                ) : (
                  "🔄 Regenerate"
                )}
              </button>
            </figcaption>
          </figure>
        ))}

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
      </div>

      {lightbox && (
        <div className="lightbox-overlay" onClick={() => setLightbox(null)}>
          <button className="lightbox-close" onClick={() => setLightbox(null)}>
            ✕
          </button>
          <img className="lightbox-img" src={lightbox} alt="Panel" onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  );
}
