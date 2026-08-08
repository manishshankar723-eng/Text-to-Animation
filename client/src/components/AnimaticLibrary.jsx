// "Your Animatics" — the saved-project library that opens the
// Storyboard → Animatic workflow (its step 1).
//
// A saved animatic IS a job, the same call the storyboard library made, so this
// grid is a view over `GET /animatics` rather than a second store that could
// drift. Two ways to start: blank (upload your own images) or from one of your
// storyboards.
//
// The layout deliberately MIRRORS StoryboardLibrary.jsx — same header, same
// New tile, same Recent / All sections, same card, chips and icon actions — so
// the two workflows read as one product. If you change a card here, change it
// there too (the shared look lives in the `.lib-*` classes in styles.css).
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";
import { formatTime } from "./Timeline.jsx";

// The placeholder title a new animatic carries until it is saved with a
// real one. Exported so the editor knows when to ask for a name.
export const UNTITLED = "Untitled animatic";

// "Recent Animatics" highlights just the single newest one; everything
// (including that one) is listed under "All Animatics" below.
const RECENT_COUNT = 1;
// Dimmed placeholder cards while a section loads, so the page reads as a real
// gallery waiting to be filled rather than bare text.
const GHOST_COUNT = { recent: 1, all: 3 };

function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function AnimaticLibrary({ onOpen }) {
  const [items, setItems] = useState([]);
  const [boards, setBoards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // job_id → object URL of the cover frame (fetched with the bearer token).
  const [covers, setCovers] = useState({});
  const [picking, setPicking] = useState(false);
  // Per-card transient UI is keyed by a CARD id ("<section>:<job_id>"), not by
  // job_id: the same animatic renders in both Recent and All, and with a shared
  // key both copies would open a rename input and steal focus from each other.
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmId, setConfirmId] = useState(null);
  // busyId stays keyed by job_id on purpose, so an in-flight action disables
  // that animatic's buttons in BOTH sections and can't be fired twice.
  const [busyId, setBusyId] = useState(null);
  const coverUrls = useRef([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [list, sbs] = await Promise.all([
          api.listAnimatics(),
          api.listStoryboards("*"),
        ]);
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

  // An animatic exporting in this session should fill in its state without a
  // reload, exactly as a generating board does on the storyboard library.
  const anyRunning = items.some(
    (a) => a.status === "running"
  );
  useEffect(() => {
    if (!anyRunning) return undefined;
    const t = setInterval(async () => {
      try {
        setItems(await api.listAnimatics());
      } catch {
        // A blip shouldn't spam the card with errors — the next tick retries.
      }
    }, 5000);
    return () => clearInterval(t);
  }, [anyRunning]);

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
        .catch(() => {}); // a missing cover just leaves the placeholder
    }
    return () => {
      alive = false;
    };
  }, [items]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(
    () => () => coverUrls.current.forEach((u) => URL.revokeObjectURL(u)),
    []
  );

  function patchItem(jobId, fields) {
    setItems((list) =>
      list.map((a) => (a.job_id === jobId ? { ...a, ...fields } : a))
    );
  }

  // Straight into the editor — naming happens when you Save. An animatic you
  // open and don't touch is discarded on the way out (see AnimaticEditor's
  // handleBack), so this can't litter the library.
  async function createBlank() {
    setBusyId("new");
    setError("");
    try {
      const project = await api.createAnimatic({ title: UNTITLED });
      onOpen(project.job_id);
    } catch (e) {
      setError(e.message);
      setBusyId(null);
    }
  }

  async function createFromBoard(board) {
    setBusyId(board.job_id);
    setError("");
    try {
      const project = await api.createAnimatic({ sourceStoryboardId: board.job_id });
      onOpen(project.job_id);
    } catch (e) {
      setError(e.message);
      setBusyId(null);
    }
  }

  async function saveRename(item, uid) {
    const title = renameValue.trim();
    // Close without a request when nothing actually changed (this is also the
    // blur path, which fires whenever the user clicks away).
    if (!title || title === item.title) {
      setRenamingId(null);
      return;
    }
    setBusyId(item.job_id);
    setError("");
    try {
      const updated = await api.saveAnimatic(item.job_id, { title });
      patchItem(item.job_id, { title: updated.title });
      setRenamingId((id) => (id === uid ? null : id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function doDelete(item) {
    setBusyId(item.job_id);
    setError("");
    try {
      await api.deleteAnimatic(item.job_id);
      setItems((list) => list.filter((a) => a.job_id !== item.job_id));
      setConfirmId(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function download(item) {
    setBusyId(item.job_id);
    setError("");
    try {
      await api.downloadAnimaticVideo(item.job_id, `${item.title || "animatic"}.mp4`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  // One project card. Shared by both sections below, so "Recent" and "All"
  // can never drift apart.
  function renderItem(a, section) {
    const uid = `${section}:${a.job_id}`;
    const busy = busyId === a.job_id;
    // ONLY `running` is an export in progress. `queued` means "a draft that has
    // never been exported" for an animatic — unlike a storyboard, where queued
    // really is work waiting to start. Treating them the same made every
    // un-exported animatic claim "Exporting…" forever.
    const running = a.status === "running";
    return (
      <div className="card lib-card" key={uid}>
        <div
          className="lib-cover"
          onClick={() => onOpen(a.job_id)}
          title="Open this animatic"
        >
          {covers[a.job_id] ? (
            <img src={covers[a.job_id]} alt={a.title} />
          ) : (
            <div className="lib-cover-empty">
              {running ? <span className="spinner" /> : "🎬"}
            </div>
          )}
          {running && <span className="lib-badge">Exporting…</span>}
          {a.status === "failed" && <span className="lib-badge failed">Failed</span>}
          {/* Length on the thumbnail — the one video-specific thing a
              storyboard card has no equivalent for, and the convention every
              video tile follows. */}
          {a.duration_ms > 0 && (
            <span className="lib-badge time">{formatTime(a.duration_ms)}</span>
          )}
        </div>

        <div className="lib-body">
          {renamingId === uid ? (
            <input
              className="lib-rename"
              autoFocus
              value={renameValue}
              disabled={busy}
              onChange={(e) => setRenameValue(e.target.value)}
              onBlur={() => saveRename(a, uid)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveRename(a, uid);
                if (e.key === "Escape") setRenamingId(null);
              }}
            />
          ) : (
            <div className="lib-title" onClick={() => onOpen(a.job_id)} title={a.title}>
              {a.title}
            </div>
          )}

          <div className="lib-meta">
            {a.aspect_ratio && <span className="chip">{a.aspect_ratio}</span>}
            {a.frame_count > 0 && (
              <span className="chip">
                {a.frame_count} frame{a.frame_count === 1 ? "" : "s"}
              </span>
            )}
            {a.text_count > 0 && (
              <span className="chip">
                {a.text_count} text{a.text_count === 1 ? "" : "s"}
              </span>
            )}
            {a.has_audio && <span className="chip">♪ audio</span>}
            {a.has_video && <span className="chip">🎬 video</span>}
          </div>

          <div className="lib-foot">
            <span className="tiny muted">{formatDate(a.created_at)}</span>
            <div className="lib-actions">
              {a.has_video && (
                <button
                  type="button"
                  className="lib-icon"
                  disabled={busy}
                  title="Download the exported MP4"
                  onClick={() => download(a)}
                >
                  <Icon name="download" />
                </button>
              )}
              <button
                type="button"
                className="lib-icon"
                disabled={busy}
                title="Open in the editor"
                onClick={() => onOpen(a.job_id)}
              >
                <Icon name="play" />
              </button>
              <button
                type="button"
                className="lib-icon"
                disabled={busy}
                title="Rename this animatic"
                onClick={() => {
                  setRenameValue(a.title);
                  setRenamingId(uid);
                }}
              >
                <Icon name="pencil" />
              </button>
              <button
                type="button"
                className="lib-icon danger"
                disabled={busy}
                title="Delete this animatic"
                onClick={() => setConfirmId(uid)}
              >
                <Icon name="trash" />
              </button>
            </div>
          </div>

          {confirmId === uid && (
            <div className="lib-confirm">
              <span className="tiny">
                Delete “{a.title}”? Its uploads and exported video go for good —
                the storyboard it came from is untouched.
              </span>
              <div className="lib-confirm-btns">
                <button
                  type="button"
                  className="btn small"
                  disabled={busy}
                  onClick={() => setConfirmId(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn small lib-delete"
                  disabled={busy}
                  onClick={() => doDelete(a)}
                >
                  {busy ? "Deleting…" : "Delete"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  // A folder-style section: its own heading, then its cards — or the empty note
  // when there's nothing in it yet.
  //
  // Deliberately a render FUNCTION, not a nested component: a component declared
  // in here gets a new identity every render, so React would remount the section
  // on each keystroke and the rename field would lose focus.
  function renderSection(section, title, hint, list) {
    const ghosts = GHOST_COUNT[section] || 1;
    return (
      <section className="lib-section" key={section}>
        <div className="lib-section-head">
          <h2 className="lib-section-title">{title}</h2>
          <span className="tiny muted">{hint}</span>
        </div>
        {loading ? (
          <div className="lib-grid lib-ghosts is-loading">
            {Array.from({ length: ghosts }, (_, i) => (
              <div className="card lib-card lib-ghost" key={i} aria-hidden="true">
                <div className="lib-cover lib-ghost-cover" />
                <div className="lib-body">
                  <div className="lib-ghost-line lib-ghost-title" />
                  <div className="lib-meta">
                    <span className="lib-ghost-chip" />
                    <span className="lib-ghost-chip" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : list.length === 0 ? (
          <div className="lib-grid">
            <div className="card lib-card lib-ghost-empty">
              <span className="lib-empty-ico">🎬</span>
              <p className="lib-empty-text">
                No animatics yet — hit <strong>New Animatic</strong>, or build one{" "}
                <strong>From a Storyboard</strong>.
              </p>
            </div>
          </div>
        ) : (
          <div className="lib-grid">{list.map((a) => renderItem(a, section))}</div>
        )}
      </section>
    );
  }

  const recent = items.slice(0, RECENT_COUNT);

  return (
    <div className="workflow-head-wrap sb-library">
      <div className="workflow-header">
        <span className="wf-icon">🎬</span>
        <div>
          <h1 className="wf-title">Your Animatics</h1>
          <p className="muted">
            Time your frames, lay audio and text under them, and watch the cut.
            No AI credits are used here.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {/* The two ways in — always first, so starting is one click. */}
      <div className="lib-grid lib-new-row">
        <button
          type="button"
          className="card lib-new"
          disabled={busyId === "new"}
          onClick={createBlank}
        >
          <span className="lib-new-plus">+</span>
          <span className="lib-new-title">New Animatic</span>
          <span className="tiny muted">
            {loading
              ? "Loading your animatics…"
              : `${items.length} animatic${items.length === 1 ? "" : "s"} created`}
          </span>
        </button>

        <button
          type="button"
          className="card lib-new"
          onClick={() => setPicking(true)}
        >
          <span className="lib-new-plus">🎞️</span>
          <span className="lib-new-title">From a Storyboard</span>
          <span className="tiny muted">
            {loading
              ? "Looking for your boards…"
              : boards.length
                ? `${boards.length} board${boards.length === 1 ? "" : "s"} ready`
                : "No drawn boards yet"}
          </span>
        </button>
      </div>

      {renderSection(
        "recent",
        "Recent Animatics",
        recent.length > 0 ? "Your latest animatic" : "",
        recent
      )}
      {renderSection(
        "all",
        "All Animatics",
        items.length > 0 ? `${items.length} in total` : "",
        items
      )}

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
                  disabled={busyId === b.job_id}
                  onClick={() => createFromBoard(b)}
                >
                  <span className="an-pick-title">{b.title}</span>
                  <span className="muted">
                    {b.panel_count} panel{b.panel_count === 1 ? "" : "s"} ·{" "}
                    {b.aspect_ratio || "16:9"}
                  </span>
                  {busyId === b.job_id && <span className="spinner-inline" />}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
