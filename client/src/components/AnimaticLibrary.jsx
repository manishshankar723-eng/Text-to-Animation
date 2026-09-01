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
// there too (the shared look lives in the `.lib-*` classes in styles/storyboard-library.css).
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";
import LibrarySection, {
  LibraryRow,
  matchesFilter,
  THUMB_EDGE,
} from "./LibraryList.jsx";
import { formatTime } from "./Timeline.jsx";

import WorkflowIcon from "./WorkflowIcon.jsx";
// The placeholder title a new project carries until it is saved with a real
// one. Exported so the editor knows when to ask for a name.
export const UNTITLED = "Untitled Project";

// ⚠ THE OLD PLACEHOLDER STILL HAS TO COUNT AS "UNNAMED". This string is a
// SENTINEL, not just a label: the editor compares a project's title against it
// to decide whether Save should ask for a real name, and whether an empty
// project is a throwaway it may delete on the way out. Every project made
// before 2026-08-21 carries the previous wording in the database, so comparing
// against the new string alone would quietly promote all of them to "named" —
// Save would write "Untitled animatic" to the library forever and the
// save-as prompt would never appear. Ask `isUntitled`, never `=== UNTITLED`.
const LEGACY_UNTITLED = ["Untitled animatic"];

/** Is this title the placeholder — this one, an older one, or nothing at all? */
export function isUntitled(title) {
  const t = (title || "").trim();
  return !t || t === UNTITLED || LEGACY_UNTITLED.includes(t);
}

// ⚠ ONE SECTION NOW, NOT TWO. "Recent Projects" used to hold the single
// newest project and "All Projects" repeated the whole list underneath it, so
// the newest project was drawn on the page twice. The heading stays; it lists
// EVERY project, newest first, as rows. See LibraryList.jsx.
//
// Dimmed placeholder rows while the list loads, so the page reads as a real
// list waiting to be filled rather than bare text.
const GHOST_ROWS = 5;

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
  // What's typed in the Filter box. Purely a VIEW of `items` — nothing is
  // re-fetched — so a user with a hundred projects finds one by name instead
  // of scrolling.
  const [query, setQuery] = useState("");
  // Per-row transient UI, keyed by job_id. (It used to be keyed by
  // "<section>:<job_id>" because the same project was drawn in both Recent and
  // All and the two copies fought over focus. There is one section now.)
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmId, setConfirmId] = useState(null);
  // Which project has an action in flight, so its icon buttons disable
  // together and can't be fired twice.
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
        .fetchAnimaticMedia(item.cover_url, THUMB_EDGE)
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
      await api.downloadAnimaticVideo(item.job_id, `${item.title || "project"}.mp4`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  // One saved project, drawn as a ROW.
  //
  // ⚠ ONLY WHAT IS DIFFERENT LIVES HERE — this project's chips and its four
  // icon buttons. The row SHAPE (small thumbnail, name, the Details / Created /
  // Actions columns) belongs to LibraryList.jsx, so this list and the
  // storyboard one line up column for column instead of drifting apart the way
  // the hand-copied card layouts did.
  function renderItem(a) {
    const uid = a.job_id;
    const busy = busyId === uid;
    // ONLY `running` is an export in progress. `queued` means "a draft that has
    // never been exported" for an animatic — unlike a storyboard, where queued
    // really is work waiting to start. Treating them the same made every
    // un-exported animatic claim "Exporting…" forever.
    const running = a.status === "running";
    return (
      <LibraryRow
        key={uid}
        onOpen={() => onOpen(uid)}
        openTitle="Open this project"
        /* So a 9:16 project is drawn as a 9:16 thumbnail instead of
           a slice out of the middle of one. See LibraryList.jsx. */
        aspect={a.aspect_ratio}
        size={a.size_bytes}
        cover={
          covers[uid] ? (
            <img src={covers[uid]} alt={a.title} />
          ) : running ? (
            <span className="spinner" />
          ) : (
            "🎬"
          )
        }
        badges={
          a.status === "failed" ? (
            <span className="lib-badge failed" title="This export failed">
              !
            </span>
          ) : null
        }
        name={
          renamingId === uid ? (
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
            <div className="lib-title" onClick={() => onOpen(uid)} title={a.title}>
              {a.title}
            </div>
          )
        }
        /* ⚠ THE LENGTH IS A CHIP NOW, NOT A FLAG ON THE THUMBNAIL. On a 280px
           cover "0:08" sat in a corner; on a 72px one it covered the frame. The
           same is true of "Exporting…" and "Failed". */
        meta={
          <>
            {running && <span className="chip">Exporting…</span>}
            {a.status === "failed" && <span className="chip">Failed</span>}
            {a.duration_ms > 0 && (
              <span className="chip">{formatTime(a.duration_ms)}</span>
            )}
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
          </>
        }
        date={formatDate(a.created_at)}
        actions={
          <>
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
              onClick={() => onOpen(uid)}
            >
              <Icon name="play" />
            </button>
            <button
              type="button"
              className="lib-icon"
              disabled={busy}
              title="Rename this project"
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
              title="Delete this project"
              onClick={() => setConfirmId(uid)}
            >
              <Icon name="trash" />
            </button>
          </>
        }
        below={
          confirmId === uid ? (
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
          ) : null
        }
      />
    );
  }

  // What the Filter box leaves standing. A pure VIEW of `items` — nothing is
  // re-fetched — matched against what a user actually types looking for a
  // project: its name and its aspect ratio.
  const shown = items.filter((a) =>
    matchesFilter(query, a.title, a.aspect_ratio)
  );

  return (
    <div className="workflow-head-wrap sb-library">
      <div className="workflow-header">
        <span className="wf-icon"><WorkflowIcon id="storyboard-to-animatics" /></span>
        <div>
          <h1 className="wf-title">Your Projects</h1>
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
          <span className="lib-new-title">New Project</span>
          <span className="tiny muted">
            {loading
              ? "Loading your projects…"
              : `${items.length} project${items.length === 1 ? "" : "s"} created`}
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

      {/* ONE section. "All Projects" used to repeat this list underneath. */}
      <LibrarySection
        title="Recent Projects"
        hint={items.length > 0 ? `${items.length} in total` : ""}
        query={query}
        onQuery={setQuery}
        placeholder="Filter projects"
        loading={loading}
        ghosts={GHOST_ROWS}
        total={items.length}
        shown={shown.length}
        metaLabel="Details"
        dateLabel="Created"
        sizeLabel="Size"
        emptyIcon="🎬"
        emptyText={
          <>
            No projects yet — hit <strong>New Project</strong>, or build one{" "}
            <strong>From a Storyboard</strong>.
          </>
        }
      >
        {shown.map(renderItem)}
      </LibrarySection>

      {picking && (
        <div className="modal-overlay">
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
