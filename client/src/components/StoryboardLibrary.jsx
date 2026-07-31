// "Your Storyboards" — the saved-project library that opens the
// Script → Storyboard workflow (its step 1).
//
// A saved project IS a storyboard job: the backend already persists every
// generated board per user, so this grid is a view over `GET /storyboards`
// rather than a second store. Each card can be opened, renamed, duplicated,
// shared via a public link, or deleted.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";

// Chip labels for the ids stored on a board. Unknown ids fall through as-is,
// which is what "Add your own style" / custom genre text needs.
const GENRE_LABELS = {
  default: "Story",
  animation: "Animation",
  commercial: "Commercial",
  documentary: "Documentary",
  educational: "Educational",
  mythology: "Mythology",
  action: "Action",
  comedy: "Comedy",
  drama: "Drama",
  fantasy: "Fantasy",
  horror: "Horror",
  "music-video": "Music Video",
  mystery: "Mystery",
  romance: "Romance",
  "sci-fi": "Sci-Fi",
  thriller: "Thriller",
};

// "Recent Storyboards" highlights just the single newest board; every board
// (including that one) is listed under "All Storyboards" below.
const RECENT_COUNT = 1;
// How many dimmed placeholder cards to show in an empty / still-loading section,
// so the page reads as a real gallery waiting to be filled rather than bare text.
const GHOST_COUNT = { recent: 1, all: 3 };

function titleCase(s) {
  return s.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function genreLabel(genre) {
  if (!genre) return null;
  return GENRE_LABELS[genre] || titleCase(genre);
}

function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function StoryboardLibrary({ onNew, onOpen, onDuplicate }) {
  const [boards, setBoards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // jobId → object URL of the cover panel (fetched with the bearer token).
  const [covers, setCovers] = useState({});
  // Per-card transient UI. Keyed by a CARD id ("<section>:<job_id>"), not by
  // job_id, because the same board is rendered in both Recent and All: with a
  // shared key both copies opened a rename input, the second one's autoFocus
  // stole focus from the first, and the first's onBlur then saved-and-closed
  // both — so renaming looked like it did nothing at all.
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmId, setConfirmId] = useState(null);
  const [copiedId, setCopiedId] = useState(null);
  // busyId stays keyed by job_id on purpose, so an in-flight action disables
  // that board's buttons in BOTH sections and can't be fired twice.
  const [busyId, setBusyId] = useState(null);
  const coverUrls = useRef([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const list = await api.listStoryboards();
        if (alive) setBoards(list);
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

  // A board generated in this session lands here still running, so refresh
  // until nothing is in flight — otherwise its card would say "Generating…"
  // and show no cover until the user reloads the page.
  const anyRunning = boards.some(
    (b) => b.status === "queued" || b.status === "running"
  );
  useEffect(() => {
    if (!anyRunning) return undefined;
    const t = setInterval(async () => {
      try {
        setBoards(await api.listStoryboards());
      } catch {
        // A blip shouldn't spam the card with errors — the next tick retries.
      }
    }, 5000);
    return () => clearInterval(t);
  }, [anyRunning]);

  // Cover panels are owner-scoped, so they can't be an <img src> — fetch each
  // as an authed blob once its board appears in the list.
  useEffect(() => {
    let alive = true;
    for (const b of boards) {
      if (b.cover_index === null || b.cover_index === undefined) continue;
      if (covers[b.job_id]) continue;
      api
        .fetchStoryboardPanel(b.job_id, b.cover_index, b.cover_url)
        .then((url) => {
          if (!alive) {
            URL.revokeObjectURL(url);
            return;
          }
          coverUrls.current.push(url);
          setCovers((c) => ({ ...c, [b.job_id]: url }));
        })
        .catch(() => {}); // a missing cover just leaves the placeholder
    }
    return () => {
      alive = false;
    };
  }, [boards]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(
    () => () => coverUrls.current.forEach((u) => URL.revokeObjectURL(u)),
    []
  );

  function patchBoard(jobId, fields) {
    setBoards((bs) => bs.map((b) => (b.job_id === jobId ? { ...b, ...fields } : b)));
  }

  async function saveRename(board, uid) {
    const title = renameValue.trim();
    // Close without a request when nothing actually changed (this is also the
    // blur path, which fires whenever the user clicks away).
    if (!title || title === board.title) {
      setRenamingId(null);
      return;
    }
    setBusyId(board.job_id);
    setError("");
    try {
      const updated = await api.renameStoryboard(board.job_id, title);
      patchBoard(board.job_id, { title: updated.title });
      setRenamingId((id) => (id === uid ? null : id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function toggleShare(board, uid) {
    setBusyId(board.job_id);
    setError("");
    try {
      const res = board.shared
        ? await api.unshareStoryboard(board.job_id)
        : await api.shareStoryboard(board.job_id);
      patchBoard(board.job_id, {
        shared: res.shared,
        share_token: res.share_token,
      });
      if (res.shared) copyLink(uid, res.share_token);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function copyLink(uid, token) {
    try {
      await navigator.clipboard.writeText(api.shareUrl(token));
      setCopiedId(uid);
      setTimeout(() => setCopiedId((id) => (id === uid ? null : id)), 2000);
    } catch {
      // Clipboard can be blocked (no HTTPS / no permission) — the link stays
      // visible on the card so it can still be copied by hand.
    }
  }

  async function doDelete(board) {
    setBusyId(board.job_id);
    setError("");
    try {
      await api.deleteStoryboard(board.job_id);
      setBoards((bs) => bs.filter((b) => b.job_id !== board.job_id));
      setConfirmId(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  async function duplicate(board) {
    setBusyId(board.job_id);
    setError("");
    try {
      const project = await api.getStoryboardProject(board.job_id);
      onDuplicate(project);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  // One project card. Shared by both sections below, so "Recent" and "All"
  // can never drift apart.
  function renderBoard(b, section) {
    // Identifies THIS rendered instance of the board (see the state comment).
    const uid = `${section}:${b.job_id}`;
    const busy = busyId === b.job_id;
    const running = b.status === "queued" || b.status === "running";
    const genre = genreLabel(b.genre);
    return (
            <div className="card lib-card" key={uid}>
              <div
                className="lib-cover"
                onClick={() => onOpen(b)}
                title="Open this storyboard"
              >
                {covers[b.job_id] ? (
                  <img src={covers[b.job_id]} alt={b.title} />
                ) : (
                  <div className="lib-cover-empty">
                    {running ? <span className="spinner" /> : "🎞️"}
                  </div>
                )}
                {running && <span className="lib-badge">Generating…</span>}
                {b.status === "failed" && (
                  <span className="lib-badge failed">Failed</span>
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
                    onBlur={() => saveRename(b, uid)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") saveRename(b, uid);
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                  />
                ) : (
                  <div
                    className="lib-title"
                    onClick={() => onOpen(b)}
                    title={b.title}
                  >
                    {b.title}
                  </div>
                )}

                <div className="lib-meta">
                  {genre && <span className="chip">{genre}</span>}
                  {b.aspect_ratio && <span className="chip">{b.aspect_ratio}</span>}
                  {b.panel_count > 0 && (
                    <span className="chip">{b.panel_count} panels</span>
                  )}
                </div>

                <div className="lib-foot">
                  <span className="tiny muted">{formatDate(b.created_at)}</span>
                  <div className="lib-actions">
                    <button
                      type="button"
                      className={`lib-icon ${b.shared ? "on" : ""}`}
                      disabled={busy}
                      title={b.shared ? "Shared — click to stop sharing" : "Share a public link"}
                      onClick={() => toggleShare(b, uid)}
                    >
                      <Icon name="link" />
                    </button>
                    <button
                      type="button"
                      className="lib-icon"
                      disabled={busy}
                      title="Duplicate — start a new storyboard from these shots"
                      onClick={() => duplicate(b)}
                    >
                      <Icon name="copy" />
                    </button>
                    <button
                      type="button"
                      className="lib-icon"
                      disabled={busy}
                      title="Rename this storyboard"
                      onClick={() => {
                        setRenameValue(b.title);
                        setRenamingId(uid);
                      }}
                    >
                      <Icon name="pencil" />
                    </button>
                    <button
                      type="button"
                      className="lib-icon danger"
                      disabled={busy}
                      title="Delete this storyboard"
                      onClick={() => setConfirmId(uid)}
                    >
                      <Icon name="trash" />
                    </button>
                  </div>
                </div>

                {b.shared && b.share_token && (
                  <div className="lib-share">
                    <input readOnly value={api.shareUrl(b.share_token)} />
                    <button
                      type="button"
                      className="btn small"
                      onClick={() => copyLink(uid, b.share_token)}
                    >
                      {copiedId === uid ? "Copied" : "Copy"}
                    </button>
                  </div>
                )}

                {confirmId === uid && (
                  <div className="lib-confirm">
                    <span className="tiny">
                      Delete “{b.title}”? Its panels are removed for good.
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
                        onClick={() => doDelete(b)}
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

  // A folder-style section: its own heading, then its cards — or the empty
  // note when there's nothing in it yet.
  //
  // Deliberately a render FUNCTION, not a nested component: a component
  // declared in here gets a new identity every render, so React would remount
  // the section on each keystroke and the rename field would lose focus.
  function renderSection(section, title, hint, items) {
    const empty = items.length === 0;
    const ghosts = GHOST_COUNT[section] || 1;
    return (
      <section className="lib-section" key={section}>
        <div className="lib-section-head">
          <h2 className="lib-section-title">{title}</h2>
          <span className="tiny muted">{hint}</span>
        </div>
        {loading ? (
          // Shimmering skeletons shaped like real cards while the list loads.
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
        ) : empty ? (
          // Genuinely empty — one centered placeholder card in the same style as
          // the "New Storyboard" tile: icon + hint stacked in the middle.
          <div className="lib-grid">
            <div className="card lib-card lib-ghost-empty">
              <span className="lib-empty-ico">🎬</span>
              <p className="lib-empty-text">
                No storyboards yet — hit <strong>New Storyboard</strong> and your
                board appears here.
              </p>
            </div>
          </div>
        ) : (
          <div className="lib-grid">
            {items.map((b) => renderBoard(b, section))}
          </div>
        )}
      </section>
    );
  }

  const recent = boards.slice(0, RECENT_COUNT);

  return (
    <div className="workflow-head-wrap sb-library">
      <div className="workflow-header">
        <span className="wf-icon">🎬</span>
        <div>
          <h1 className="wf-title">Your Storyboards</h1>
          <p className="muted">All your stories in one place.</p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {/* New storyboard — always first, so starting a story is one click. */}
      <div className="lib-grid lib-new-row">
        <button type="button" className="card lib-new" onClick={onNew}>
          <span className="lib-new-plus">+</span>
          <span className="lib-new-title">New Storyboard</span>
          <span className="tiny muted">
            {loading
              ? "Loading your storyboards…"
              : `${boards.length} storyboard${boards.length === 1 ? "" : "s"} created`}
          </span>
        </button>
      </div>

      {renderSection(
        "recent",
        "Recent Storyboards",
        recent.length > 0 ? "Your latest board" : "",
        recent
      )}
      {renderSection(
        "all",
        "All Storyboards",
        boards.length > 0 ? `${boards.length} in total` : "",
        boards
      )}
    </div>
  );
}
