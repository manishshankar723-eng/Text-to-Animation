// "Your Storyboards" — the saved-project library that opens the
// Script → Storyboard workflow (its step 1).
//
// A saved project IS a storyboard job: the backend already persists every
// generated board per user, so this list is a view over `GET /storyboards`
// rather than a second store. Each row can be opened, renamed, duplicated,
// shared via a public link, or deleted.
import { useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";
import LibrarySection, {
  LibraryRow,
  matchesFilter,
  THUMB_EDGE
} from "./LibraryList.jsx";

import WorkflowIcon from "./WorkflowIcon.jsx";
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
  thriller: "Thriller"
};

// ⚠ ONE SECTION NOW, NOT TWO. "Recent Storyboards" used to hold the single
// newest board and "All Storyboards" repeated the entire list underneath — so
// the newest board was drawn on the page twice. The heading stays; it lists
// EVERY board, newest first, as rows. See LibraryList.jsx.
//
// How many dimmed placeholder rows to draw while the list is still loading, so
// the page reads as a real list waiting to be filled rather than bare text.
const GHOST_ROWS = 5;

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
    year: "numeric"
  });
}

// Shared by TWO workflows: Script to Storyboard (which can create and duplicate
// boards) and Image to Animatic Image (which only opens them). The header and the
// two creating actions are therefore optional — omit `onNew` and the "New
// Storyboard" tile is not rendered, omit `onDuplicate` and the Duplicate button
// isn't either, so neither workflow offers a button that belongs to the other.
// Defaults reproduce exactly what Script to Storyboard showed before.
export default function StoryboardLibrary({
  onNew,
  onOpen,
  onDuplicate,
  // ⚠ A WORKFLOW ID, NOT A GLYPH. This library is rendered by two different
  // workflows and each wants its own face on the header; passing the id rather
  // than the picture keeps the drawing in one file. Defaults to Script to
  // Storyboard, whose library this originally was.
  workflowId = "script-to-storyboard",
  title = "Your Storyboards",
  subtitle = "All your stories in one place.",
  // What the create tile says. `newHint` is given the board COUNT because only
  // this component knows it — that is also why `onNew` is handed the boards:
  // a caller that wants to show its own picker gets the list already fetched
  // instead of asking the server for it a second time.
  newLabel = "New Storyboard",
  newHint = (n) => `${n} storyboard${n === 1 ? "" : "s"} created`,
  // Whose boards to show. "" is Script to Storyboard's own; a workflow that
  // works on COPIES passes its own name so the two libraries never mix.
  workflow = "",
  // Bumped by the caller to force a re-fetch — e.g. after it has copied a new
  // board in and wants it to appear without a page reload.
  refreshKey = 0
}) {
  const [boards, setBoards] = useState([]);
  const [loading, setLoading] = useState(true);
  // The load is taking unusually long — shown so a stuck backend explains
  // itself instead of leaving the page shimmering silently.
  const [slow, setSlow] = useState(false);
  const [error, setError] = useState("");
  // jobId → object URL of the cover panel (fetched with the bearer token).
  const [covers, setCovers] = useState({});
  // What's typed in the Filter box. Purely a VIEW of `boards` — nothing is
  // re-fetched — so a user with a hundred boards finds one by name instead of
  // scrolling.
  const [query, setQuery] = useState("");
  // Per-row transient UI, keyed by job_id. (It used to be keyed by
  // "<section>:<job_id>" because the same board was drawn in both Recent and
  // All and the two copies fought over focus. There is one section now, so the
  // job_id IS the row id.)
  const [renamingId, setRenamingId] = useState(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmId, setConfirmId] = useState(null);
  const [copiedId, setCopiedId] = useState(null);
  // Which board has an action in flight, so its four icon buttons disable
  // together and can't be fired twice.
  const [busyId, setBusyId] = useState(null);
  const coverUrls = useRef([]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setSlow(false);
    // A silent server used to leave this shimmering with no explanation. The
    // request now times out on its own (see api.js), but two minutes of ghost
    // cards still tells the user nothing — so say something after ten seconds.
    const slowTimer = setTimeout(() => alive && setSlow(true), 10000);
    (async () => {
      try {
        const list = await api.listStoryboards(workflow);
        if (alive) setBoards(list);
      } catch (e) {
        if (alive) setError(e.message);
      } finally {
        if (alive) {
          setLoading(false);
          setSlow(false);
        }
      }
    })();
    return () => {
      alive = false;
      clearTimeout(slowTimer);
    };
  }, [workflow, refreshKey]);

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
        // Same filter as the first load — without it a poll would swap this
        // library's boards for the other workflow's.
        setBoards(await api.listStoryboards(workflow));
      } catch {
        // A blip shouldn't spam the card with errors — the next tick retries.
      }
    }, 5000);
    return () => clearInterval(t);
  }, [anyRunning, workflow]);

  // Cover panels are owner-scoped, so they can't be an <img src> — fetch each
  // as an authed blob once its board appears in the list.
  useEffect(() => {
    let alive = true;
    for (const b of boards) {
      if (b.cover_index === null || b.cover_index === undefined) continue;
      if (covers[b.job_id]) continue;
      api
        .fetchStoryboardPanel(b.job_id, b.cover_index, b.cover_url, THUMB_EDGE)
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
    setBoards((bs) =>
      bs.map((b) => (b.job_id === jobId ? { ...b, ...fields } : b))
    );
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
        share_token: res.share_token
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

  // One saved board, drawn as a ROW.
  //
  // ⚠ ONLY WHAT IS DIFFERENT LIVES HERE - this board's chips, its four icon
  // buttons and its confirm strip. The row SHAPE (small thumbnail, name, the
  // Details / Created / Actions columns) belongs to LibraryList.jsx, so every
  // workflow's list lines up column for column instead of drifting apart the
  // way the four hand-copied card layouts did.
  function renderBoard(b) {
    const uid = b.job_id;
    const busy = busyId === uid;
    const running = b.status === "queued" || b.status === "running";
    const genre = genreLabel(b.genre);
    return (
      <LibraryRow
        key={uid}
        onOpen={() => onOpen(b)}
        openTitle="Open this storyboard"
        /* So a 9:16 board is drawn as a 9:16 thumbnail instead of a
           slice out of the middle of one. See LibraryList.jsx. */
        aspect={b.aspect_ratio}
        size={b.size_bytes}
        cover={
          covers[uid] ? (
            <img src={covers[uid]} alt={b.title} />
          ) : running ? (
            <span className="spinner" />
          ) : (
            "🎞️"
          )
        }
        badges={
          b.status === "failed" ? (
            <span className="lib-badge failed" title="This board failed">
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
              onBlur={() => saveRename(b, uid)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveRename(b, uid);
                if (e.key === "Escape") setRenamingId(null);
              }}
            />
          ) : (
            <div className="lib-title" onClick={() => onOpen(b)} title={b.title}>
              {b.title}
            </div>
          )
        }
        /* ⚠ STATUS IS A CHIP HERE, NOT A FLAG OVER THE PICTURE. On the old
           card the thumbnail was 280px wide and "Generating…" fitted across a
           corner of it; the row's thumbnail is 72px, where the same label
           covered the very frame it was describing. */
        meta={
          <>
            {running && <span className="chip">Generating…</span>}
            {b.status === "failed" && <span className="chip">Failed</span>}
            {genre && <span className="chip">{genre}</span>}
            {b.aspect_ratio && <span className="chip">{b.aspect_ratio}</span>}
            {b.panel_count > 0 && (
              <span className="chip">{b.panel_count} panels</span>
            )}
          </>
        }
        date={formatDate(b.created_at)}
        actions={
          <>
            <button
              type="button"
              className={`lib-icon ${b.shared ? "on" : ""}`}
              disabled={busy}
              title={
                b.shared
                  ? "Shared — click to stop sharing"
                  : "Share a public link"
              }
              onClick={() => toggleShare(b, uid)}
            >
              <Icon name="link" />
            </button>
            {onDuplicate && (
              <button
                type="button"
                className="lib-icon"
                disabled={busy}
                title="Duplicate — start a new storyboard from these shots"
                onClick={() => duplicate(b)}
              >
                <Icon name="copy" />
              </button>
            )}
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
          </>
        }
        below={
          (b.shared && b.share_token) || confirmId === uid ? (
            <>
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
            </>
          ) : null
        }
      />
    );
  }

  // What the Filter box leaves standing. A pure VIEW of `boards` - nothing is
  // re-fetched - matched against the three things a user actually types when
  // hunting for a board: its name, its genre and its aspect ratio.
  const shown = boards.filter((b) =>
    matchesFilter(query, b.title, genreLabel(b.genre), b.aspect_ratio)
  );

  return (
    <div className="workflow-head-wrap sb-library">
      <div className="workflow-header">
        <span className="wf-icon"><WorkflowIcon id={workflowId} /></span>
        <div>
          <h1 className="wf-title">{title}</h1>
          <p className="muted">{subtitle}</p>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {slow && (
        <div className="fv-banner warn">
          <strong>Still loading your storyboards.</strong> The backend has
          accepted the request but hasn't answered — usually a database it
          needs (MongoDB) being unreachable. Check the uvicorn log; the request
          gives up on its own after two minutes.
        </div>
      )}

      {/* New storyboard - first, so starting a story is one click. Only where
          boards can actually be created; see the props comment. */}
      {onNew && (
        <div className="lib-grid lib-new-row">
          <button
            type="button"
            className="card lib-new"
            onClick={() => onNew(boards)}
          >
            <span className="lib-new-plus">+</span>
            <span className="lib-new-title">{newLabel}</span>
            <span className="tiny muted">
              {loading ? "Loading your storyboards…" : newHint(boards.length)}
            </span>
          </button>
        </div>
      )}

      {/* ONE section. "All Storyboards" used to repeat this list underneath. */}
      <LibrarySection
        title="Recent Storyboards"
        hint={boards.length > 0 ? `${boards.length} in total` : ""}
        query={query}
        onQuery={setQuery}
        placeholder="Filter storyboards"
        loading={loading}
        ghosts={GHOST_ROWS}
        total={boards.length}
        shown={shown.length}
        metaLabel="Details"
        dateLabel="Created"
        sizeLabel="Size"
        emptyIcon="🎬"
        emptyText={
          <>
            No storyboards yet — hit <strong>New Storyboard</strong> and your
            board appears here.
          </>
        }
      >
        {shown.map(renderBoard)}
      </LibrarySection>
    </div>
  );
}
