// Storyboard board (Stage D output + start of Stage E).
// Polls the storyboard job, shows a live gold progress bar, and renders panels
// in a grid as they finish (each fetched as an authed blob). Matches the
// Text-to-Image workflow's look (WorkflowHeader + progress bar + gallery tiles).
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";

export default function StoryboardBoard({ jobId, styleLabel, aspect, onBack, onRestart }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [panelUrls, setPanelUrls] = useState({});
  const panelUrlsRef = useRef({});
  const [lightbox, setLightbox] = useState(null);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState("");

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
  }, [jobId]);

  // Fetch each panel image (authed blob) once it has a url.
  useEffect(() => {
    const panels = job?.result?.panels || [];
    panels.forEach((p) => {
      if (p.url && !p.failed && !panelUrlsRef.current[p.index]) {
        panelUrlsRef.current[p.index] = "loading";
        api
          .fetchStoryboardPanel(jobId, p.index)
          .then((url) => {
            panelUrlsRef.current[p.index] = url;
            setPanelUrls((prev) => ({ ...prev, [p.index]: url }));
          })
          .catch(() => {
            panelUrlsRef.current[p.index] = null;
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
  const pendingCount = Math.max(0, total - panels.length);
  const tileRatio = (aspect || "16:9").replace(":", " / ");
  const okCount = panels.filter((p) => !p.failed && p.url).length;

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

  return (
    <div className="workflow-head-wrap">
      <div className="workflow-header">
        <span className="wf-icon">🎬</span>
        <div>
          <h1 className="wf-title">Your storyboard</h1>
          <p className="muted">
            {styleLabel} · {aspect} · {total} panel{total === 1 ? "" : "s"}
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      {pdfError && <div className="error">{pdfError}</div>}
      {status === "failed" && (
        <div className="error">Generation failed: {job?.error || "unknown error"}</div>
      )}

      {okCount > 0 && (
        <div className="board-toolbar">
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
              {panelUrls[p.index] ? (
                <img
                  src={panelUrls[p.index]}
                  alt={`Panel ${p.index + 1}`}
                  onClick={() => setLightbox(panelUrls[p.index])}
                />
              ) : p.failed ? (
                <div className="board-failed">⚠️ Couldn’t draw this panel</div>
              ) : (
                <div className="board-skeleton" />
              )}
            </div>
            <figcaption>
              <span className="board-shotnum">Shot {p.index + 1}</span>
              {p.description}
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

      <div className="review-actions board-actions">
        <button type="button" className="btn" onClick={onBack}>
          ← Back to shots
        </button>
        <button type="button" className="btn ghost" onClick={onRestart}>
          Start over
        </button>
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
