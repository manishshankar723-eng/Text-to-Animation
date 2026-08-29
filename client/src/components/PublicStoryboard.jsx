// Read-only viewer for a SHARED storyboard (?s=<token> in the URL).
//
// This is the only screen that renders without a session: the share token is
// the credential, so the page shows the panels and nothing else — no shot
// prompts, no references, no owner. The token is never written to localStorage.
import { useEffect, useState } from "react";
import * as api from "../api.js";
import ImageLightbox from "./ImageLightbox.jsx";
import Logo from "./Logo.jsx";
import useBranding from "../useBranding.js";

import WorkflowIcon from "./WorkflowIcon.jsx";
export default function PublicStoryboard({ token, onExit }) {
  // Shown to somebody who was sent a link and has no account. The name is the
  // only thing on this page that says whose product made the board.
  const brand = useBranding();
  const [board, setBoard] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [lightbox, setLightbox] = useState(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await api.getPublicStoryboard(token);
        if (alive) setBoard(res);
      } catch (e) {
        if (alive) setError(e.message);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [token]);

  if (loading) {
    return (
      <div className="public-wrap">
        <div className="card public-msg">
          <span className="spinner" />
          <p className="muted">Loading storyboard…</p>
        </div>
      </div>
    );
  }

  if (error || !board) {
    return (
      <div className="public-wrap">
        <div className="card public-msg">
          <h2>Storyboard unavailable</h2>
          <p className="muted">
            {error || "This link is no longer valid."} The owner may have stopped
            sharing it.
          </p>
          <button type="button" className="btn primary" onClick={onExit}>
            Go to {brand.name}
          </button>
        </div>
      </div>
    );
  }

  const tileRatio = (board.aspect_ratio || "16:9").replace(":", " / ");

  return (
    <div className="public-wrap">
      <div className="public-topbar">
        <span className="brand small">
          <Logo /> {brand.name}
        </span>
        <button type="button" className="btn small" onClick={onExit}>
          Make your own →
        </button>
      </div>

      <div className="workflow-head-wrap sb-board public-board">
        <div className="workflow-header">
          <span className="wf-icon"><WorkflowIcon id="script-to-storyboard" /></span>
          <div>
            <h1 className="wf-title">{board.title}</h1>
            <p className="muted">
              {board.genre ? `${board.genre} · ` : ""}
              {board.aspect_ratio} · {board.panel_count} panel
              {board.panel_count === 1 ? "" : "s"} · shared storyboard
            </p>
          </div>
        </div>

        <div className="board-grid">
          {board.panel_indexes.map((idx, i) => {
            const src = api.publicPanelUrl(token, idx);
            return (
              <figure className="board-tile" key={idx}>
                <div className="board-frame" style={{ aspectRatio: tileRatio }}>
                  <img
                    src={src}
                    alt={`Panel ${i + 1}`}
                    onClick={() => setLightbox(src)}
                  />
                </div>
                <figcaption>
                  <span className="board-shotnum">Shot {i + 1}</span>
                </figcaption>
              </figure>
            );
          })}
        </div>
      </div>

      <ImageLightbox src={lightbox} alt="Panel" onClose={() => setLightbox(null)} />
    </div>
  );
}
