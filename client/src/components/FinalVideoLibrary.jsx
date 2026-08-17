// "Your Final Videos" — the saved-project library that opens the
// Animatics → Final Video workflow (its way in).
//
// MIRRORS AnimaticLibrary.jsx deliberately — same header, same New tiles, same
// Recent / All sections, same card, chips and icon actions — so the workflows
// read as one product. Change a card here, change it there (the shared look
// lives in the `.lib-*` classes in styles/storyboard-library.css).
//
// The one thing this library shows that no other does is SPEND: a project's
// card carries what it has cost so far, because these are the only projects in
// the app that cost anything per click.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";
import { formatTime } from "./Timeline.jsx";

// The placeholder title a new project carries until it is saved with a real one.
export const UNTITLED = "Untitled final video";

const RECENT_COUNT = 1;
const GHOST_COUNT = { recent: 1, all: 3 };

function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric"
  });
}

export default function FinalVideoLibrary({ onOpen }) {
  const [items, setItems] = useState([]);
  const [boards, setBoards] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [covers, setCovers] = useState({});
  // Which picker is open: "storyboard" | null. Kept as a name rather than a
  // boolean so another source can be added without reworking the state.
  const [picking, setPicking] = useState(null);
  // Keyed by CARD id ("<section>:<job_id>"): the same project renders in both
  // Recent and All, and a shared key would let both copies fight for focus.
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmId, setConfirmId] = useState(null);
  // busyId stays keyed by job_id, so an in-flight action disables that
  // project's buttons in BOTH sections and can't be fired twice.
  const [busyId, setBusyId] = useState(null);
  const coverUrls = useRef([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [list, sbs] = await Promise.all([
          api.listFinalVideos(),
          api.listStoryboards("*")
        ]);
        if (!alive) return;
        setItems(list);
        // Only boards with something DRAWN — an undrawn panel has no picture to
        // animate, so offering it could only produce a paid failure.
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

  // A project rendering or assembling in this session should fill in without a
  // reload, exactly as a generating board does on the storyboard library.
  const anyRunning = items.some((v) => v.status === "running");
  useEffect(() => {
    if (!anyRunning) return undefined;
    const t = setInterval(async () => {
      try {
        setItems(await api.listFinalVideos());
      } catch {
        // A blip shouldn't spam the card — the next tick retries.
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
        .fetchFinalVideoMedia(item.cover_url)
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
      list.map((v) => (v.job_id === jobId ? { ...v, ...fields } : v))
    );
  }

  // The ONLY way a project is created. (There was a "Create Video" tile making
  // a blank one; it was removed — see the tiles below.)
  // Every drawn panel becomes a shot carrying BOTH its picture and its
  // description, so the prompt boxes arrive filled in.
  async function createFromBoard(board) {
    setBusyId(board.job_id);
    setError("");
    try {
      const project = await api.createFinalVideo({
        sourceStoryboardId: board.job_id
      });
      onOpen(project.job_id);
    } catch (e) {
      setError(e.message);
      setBusyId(null);
    }
  }

  async function saveRename(item, uid) {
    const title = renameValue.trim();
    if (!title || title === item.title) {
      setRenamingId(null);
      return;
    }
    setBusyId(item.job_id);
    setError("");
    try {
      const updated = await api.saveFinalVideo(item.job_id, { title });
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
      await api.deleteFinalVideo(item.job_id);
      setItems((list) => list.filter((v) => v.job_id !== item.job_id));
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
      await api.downloadFinalVideo(item.job_id, `${item.title || "final"}.mp4`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  function renderItem(v, section) {
    const uid = `${section}:${v.job_id}`;
    const busy = busyId === v.job_id;
    // Only `running` is work in progress. `queued` means "a project that has
    // never been assembled" here — the same distinction an animatic card makes.
    const running = v.status === "running";
    return (
      <div className="card lib-card" key={uid}>
        <div
          className="lib-cover"
          onClick={() => onOpen(v.job_id)}
          title="Open this project"
        >
          {covers[v.job_id] ? (
            <img src={covers[v.job_id]} alt={v.title} />
          ) : (
            <div className="lib-cover-empty">
              {running ? <span className="spinner" /> : "🎞️"}
            </div>
          )}
          {running && <span className="lib-badge">Working…</span>}
          {v.status === "failed" && (
            <span className="lib-badge failed">Failed</span>
          )}
          {v.duration_ms > 0 && (
            <span className="lib-badge time">{formatTime(v.duration_ms)}</span>
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
              onBlur={() => saveRename(v, uid)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveRename(v, uid);
                if (e.key === "Escape") setRenamingId(null);
              }}
            />
          ) : (
            <div
              className="lib-title"
              onClick={() => onOpen(v.job_id)}
              title={v.title}
            >
              {v.title}
            </div>
          )}

          <div className="lib-meta">
            {v.aspect_ratio && <span className="chip">{v.aspect_ratio}</span>}
            {v.shot_count > 0 && (
              <span className="chip">
                {v.rendered_count}/{v.shot_count} rendered
              </span>
            )}
            {v.has_video && <span className="chip">🎞️ cut</span>}
            {/* The only card in the app that shows money, because these are the
                only projects that spend any. */}
            {v.spent_usd > 0 && (
              <span
                className="chip spend"
                title="Estimated Veo spend on this project"
              >
                ~${v.spent_usd.toFixed(2)}
              </span>
            )}
          </div>

          <div className="lib-foot">
            <span className="tiny muted">{formatDate(v.created_at)}</span>
            <div className="lib-actions">
              {v.has_video && (
                <button
                  type="button"
                  className="lib-icon"
                  disabled={busy}
                  title="Download the final MP4"
                  onClick={() => download(v)}
                >
                  <Icon name="download" />
                </button>
              )}
              <button
                type="button"
                className="lib-icon"
                disabled={busy}
                title="Open this project"
                onClick={() => onOpen(v.job_id)}
              >
                <Icon name="play" />
              </button>
              <button
                type="button"
                className="lib-icon"
                disabled={busy}
                title="Rename this project"
                onClick={() => {
                  setRenameValue(v.title);
                  setRenamingId(uid);
                }}
              >
                <Icon name="pencil" />
              </button>
              <button
                type="button"
                className="lib-icon danger"
                disabled={busy}
                title="Delete this project"
                onClick={() => setConfirmId(uid)}
              >
                <Icon name="trash" />
              </button>
            </div>
          </div>

          {confirmId === uid && (
            <div className="lib-confirm">
              <span className="tiny">
                Delete “{v.title}”? Its rendered clips go for good — including
                the {v.rendered_count} you paid to render. The storyboard it
                came from is untouched.
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
                  onClick={() => doDelete(v)}
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

  // A render FUNCTION, not a nested component: a component declared in here
  // gets a new identity each render, so React would remount the section on
  // every keystroke and the rename field would lose focus.
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
              <div
                className="card lib-card lib-ghost"
                key={i}
                aria-hidden="true"
              >
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
              <span className="lib-empty-ico">🎞️</span>
              <p className="lib-empty-text">
                No final videos yet — start one{" "}
                <strong>From a Storyboard</strong> to get your prompts filled in
                already.
              </p>
            </div>
          </div>
        ) : (
          <div className="lib-grid">
            {list.map((v) => renderItem(v, section))}
          </div>
        )}
      </section>
    );
  }

  const recent = items.slice(0, RECENT_COUNT);

  return (
    <div className="workflow-head-wrap sb-library">
      <div className="workflow-header">
        <span className="wf-icon">🎞️</span>
        <div>
          <h1 className="wf-title">Your Final Videos</h1>
          <p className="muted">
            Turn each storyboard panel into real footage with Veo, then cut them
            together. <strong>This workflow spends</strong> — rendering is
            billed per second of video, so every render says its price first.
          </p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {/* ONE way in, on purpose. A blank project was possible before and is
          gone: a video needs pictures, and starting from the board is the only
          route that arrives with the pictures AND the prompts already written,
          so an empty project was just a slower path to the same place. */}
      <div className="lib-grid lib-new-row">
        <button
          type="button"
          className="card lib-new"
          onClick={() => setPicking("storyboard")}
        >
          {/* A "+" like every other library's create tile — this is the only
              way to start a project here, so it should read as the New button
              it now is, not as a document. */}
          <span className="lib-new-plus">+</span>
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
        "Recent Final Videos",
        recent.length > 0 ? "Your latest project" : "",
        recent
      )}
      {renderSection(
        "all",
        "All Final Videos",
        items.length > 0 ? `${items.length} in total` : "",
        items
      )}

      {picking === "storyboard" && (
        <div className="modal-overlay" onClick={() => setPicking(null)}>
          <div
            className="card an-pick-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              className="modal-close"
              onClick={() => setPicking(null)}
            >
              ✕
            </button>
            <h2>Pick a storyboard</h2>
            <p className="muted">
              Every drawn panel becomes a shot carrying <strong>both</strong>{" "}
              its picture and its description — so the prompt boxes arrive
              filled in and you only edit what should MOVE. Nothing renders
              until you press Render; creating the project is free.
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
