// "Your Animatics" — the saved-project library that opens the
// Storyboard → Animatic workflow (its step 1).
//
// A saved animatic IS a job, the same call the storyboard library made, so this
// grid is a view over `GET /animatics` rather than a second store that could
// drift. Two ways to start: blank (upload your own images) or from one of your
// storyboards.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import { formatTime } from "./Timeline.jsx";

function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export default function AnimaticLibrary({ onOpen }) {
  const [items, setItems] = useState([]);
  const [boards, setBoards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [covers, setCovers] = useState({});
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(null);
  const [confirmId, setConfirmId] = useState(null);
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const coverUrls = useRef([]);

  async function refresh() {
    try {
      setItems(await api.listAnimatics());
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [list, sbs] = await Promise.all([api.listAnimatics(), api.listStoryboards()]);
        if (!alive) return;
        setItems(list);
        // Only boards with something drawn can become an animatic.
        setBoards(sbs.filter((b) => b.cover_url));
      } catch (e) {
        if (alive) setError(e.message);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  // Covers are owner-scoped, so they can't be a plain <img src> — fetch each as
  // an authed blob once its card appears.
  useEffect(() => {
    let alive = true;
    for (const item of items) {
      if (!item.cover_url || covers[item.job_id]) continue;
      api
        .fetchAnimaticMedia(item.cover_url)
        .then((url) => {
          if (!alive) {
            URL.revokeObjectURL(url);
            return;
          }
          coverUrls.current.push(url);
          setCovers((c) => ({ ...c, [item.job_id]: url }));
        })
        .catch(() => {});
    }
    return () => {
      alive = false;
    };
  }, [items, covers]);

  useEffect(
    () => () => {
      for (const url of coverUrls.current) URL.revokeObjectURL(url);
      coverUrls.current = [];
    },
    []
  );

  async function createBlank() {
    setBusy("new");
    setError("");
    try {
      const project = await api.createAnimatic({ title: "Untitled animatic" });
      onOpen(project.job_id);
    } catch (e) {
      setError(e.message);
      setBusy(null);
    }
  }

  async function createFromBoard(board) {
    setBusy(board.job_id);
    setError("");
    try {
      const project = await api.createAnimatic({ sourceStoryboardId: board.job_id });
      onOpen(project.job_id);
    } catch (e) {
      setError(e.message);
      setBusy(null);
    }
  }

  async function remove(id) {
    setBusy(id);
    try {
      await api.deleteAnimatic(id);
      setItems((list) => list.filter((i) => i.job_id !== id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
      setConfirmId(null);
    }
  }

  async function commitRename(id) {
    const title = renameValue.trim();
    setRenamingId(null);
    if (!title) return;
    try {
      await api.saveAnimatic(id, { title });
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="workflow-head-wrap">
      <div className="workflow-header">
        <span className="wf-icon">🎬</span>
        <div>
          <h1 className="wf-title">Storyboard to Animatics</h1>
          <p className="muted">
            Give each frame a hold time, lay your audio under it, and watch the cut
            before you animate anything. No AI credits are used here.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="lib-grid">
        <button
          type="button"
          className="lib-new"
          disabled={busy === "new"}
          onClick={createBlank}
        >
          <span className="lib-new-plus">＋</span>
          <span className="lib-new-title">New animatic</span>
          <span className="muted">Upload your own images</span>
        </button>

        <button
          type="button"
          className="lib-new an-from-board"
          onClick={() => setPicking((p) => !p)}
        >
          <span className="lib-new-plus">🎞️</span>
          <span className="lib-new-title">From a storyboard</span>
          <span className="muted">
            {boards.length
              ? `${boards.length} board${boards.length === 1 ? "" : "s"} ready`
              : "No drawn boards yet"}
          </span>
        </button>

        {loading && <div className="lib-card lib-ghost" />}

        {items.map((item) => (
          <div key={item.job_id} className="lib-card">
            <div
              className="an-lib-thumb"
              onClick={() => onOpen(item.job_id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && onOpen(item.job_id)}
            >
              {covers[item.job_id] ? (
                <img src={covers[item.job_id]} alt="" />
              ) : (
                <span className="muted">
                  {item.frame_count ? "…" : "Empty animatic"}
                </span>
              )}
              <span className="an-lib-len">{formatTime(item.duration_ms)}</span>
              {item.status === "running" && <span className="an-lib-badge">Exporting…</span>}
            </div>

            <div className="an-lib-body">
              {renamingId === item.job_id ? (
                <input
                  className="an-lib-rename"
                  value={renameValue}
                  autoFocus
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={() => commitRename(item.job_id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") e.target.blur();
                    if (e.key === "Escape") setRenamingId(null);
                  }}
                />
              ) : (
                <h3 className="an-lib-title" onClick={() => onOpen(item.job_id)}>
                  {item.title}
                </h3>
              )}
              <p className="muted an-lib-meta">
                {item.frame_count} frame{item.frame_count === 1 ? "" : "s"} ·{" "}
                {item.aspect_ratio}
                {item.has_audio && " · ♪ audio"}
                {item.has_video && " · 🎬 exported"}
                <br />
                {formatDate(item.updated_at)}
              </p>

              <div className="an-lib-actions">
                <button type="button" className="btn small" onClick={() => onOpen(item.job_id)}>
                  Open
                </button>
                <button
                  type="button"
                  className="btn small ghost"
                  onClick={() => {
                    setRenamingId(item.job_id);
                    setRenameValue(item.title);
                  }}
                >
                  ✎
                </button>
                {confirmId === item.job_id ? (
                  <>
                    <button
                      type="button"
                      className="btn small danger-btn"
                      disabled={busy === item.job_id}
                      onClick={() => remove(item.job_id)}
                    >
                      Delete?
                    </button>
                    <button
                      type="button"
                      className="btn small ghost"
                      onClick={() => setConfirmId(null)}
                    >
                      ✕
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="btn small ghost"
                    onClick={() => setConfirmId(item.job_id)}
                  >
                    🗑
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}

        {!loading && !items.length && (
          <div className="lib-card lib-ghost an-lib-empty">
            <p className="muted">
              Nothing here yet. Start blank and drop your images in, or build one
              from a storyboard you've already drawn.
            </p>
          </div>
        )}
      </div>

      {picking && (
        <div className="modal-overlay" onClick={() => setPicking(false)}>
          <div className="card an-pick-modal" onClick={(e) => e.stopPropagation()}>
            <button className="modal-close" onClick={() => setPicking(false)}>
              ✕
            </button>
            <h2>Pick a storyboard</h2>
            <p className="muted">
              Every drawn panel becomes a frame, in order, held for 2 seconds.
              Change the timing afterwards.
            </p>
            {!boards.length && (
              <p className="muted">
                You haven't got a board with drawn panels yet — make one in
                Script to Storyboard first.
              </p>
            )}
            <div className="an-pick-list">
              {boards.map((b) => (
                <button
                  key={b.job_id}
                  type="button"
                  className="an-pick-row"
                  disabled={busy === b.job_id}
                  onClick={() => createFromBoard(b)}
                >
                  <span className="an-pick-title">{b.title}</span>
                  <span className="muted">
                    {b.panel_count} panel{b.panel_count === 1 ? "" : "s"} · {b.aspect_ratio || "16:9"}
                  </span>
                  {busy === b.job_id && <span className="spinner-inline" />}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
