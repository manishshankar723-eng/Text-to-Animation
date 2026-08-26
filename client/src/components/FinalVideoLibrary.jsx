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
import LibrarySection, {
  LibraryRow,
  matchesFilter,
  THUMB_EDGE
} from "./LibraryList.jsx";
import { formatTime } from "./Timeline.jsx";

import WorkflowIcon from "./WorkflowIcon.jsx";
// The placeholder title a new project carries until it is saved with a real one.
export const UNTITLED = "Untitled final video";

// ⚠ ONE SECTION NOW, NOT TWO. "Recent Final Videos" used to hold the single
// newest project and "All Final Videos" repeated the whole list underneath
// it. The heading stays; it lists EVERY project, newest first, as rows.
// See LibraryList.jsx.
const GHOST_ROWS = 5;

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
        .fetchFinalVideoMedia(item.cover_url, THUMB_EDGE)
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

  // One saved project, drawn as a ROW.
  //
  // ⚠ ONLY WHAT IS DIFFERENT LIVES HERE — this project's chips (including the
  // one nothing else in the app has: what it has SPENT) and its four icon
  // buttons. The row SHAPE belongs to LibraryList.jsx, so every workflow's list
  // lines up column for column.
  function renderItem(v) {
    const uid = v.job_id;
    const busy = busyId === uid;
    // Only `running` is work in progress. `queued` means "a project that has
    // never been assembled" here — the same distinction an animatic row makes.
    const running = v.status === "running";
    return (
      <LibraryRow
        key={uid}
        onOpen={() => onOpen(uid)}
        openTitle="Open this project"
        /* So a 9:16 project is drawn as a 9:16 thumbnail instead of
           a slice out of the middle of one. See LibraryList.jsx. */
        aspect={v.aspect_ratio}
        size={v.size_bytes}
        cover={
          covers[uid] ? (
            <img src={covers[uid]} alt={v.title} />
          ) : running ? (
            <span className="spinner" />
          ) : (
            "🎞️"
          )
        }
        badges={
          v.status === "failed" ? (
            <span className="lib-badge failed" title="This project failed">
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
              onBlur={() => saveRename(v, uid)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveRename(v, uid);
                if (e.key === "Escape") setRenamingId(null);
              }}
            />
          ) : (
            <div className="lib-title" onClick={() => onOpen(uid)} title={v.title}>
              {v.title}
            </div>
          )
        }
        /* ⚠ THE LENGTH AND THE STATUS ARE CHIPS NOW, NOT FLAGS ON THE PICTURE:
           the row's thumbnail is 72px wide, where a label covers the very frame
           it is describing. */
        meta={
          <>
            {running && <span className="chip">Working…</span>}
            {v.status === "failed" && <span className="chip">Failed</span>}
            {v.duration_ms > 0 && (
              <span className="chip">{formatTime(v.duration_ms)}</span>
            )}
            {v.aspect_ratio && <span className="chip">{v.aspect_ratio}</span>}
            {v.shot_count > 0 && (
              <span className="chip">
                {v.rendered_count}/{v.shot_count} rendered
              </span>
            )}
            {v.has_video && <span className="chip">🎞️ cut</span>}
            {/* The only row in the app that shows money, because these are the
                only projects that spend any. */}
            {v.spent_usd > 0 && (
              <span
                className="chip spend"
                title="Estimated Veo spend on this project"
              >
                ~${v.spent_usd.toFixed(2)}
              </span>
            )}
          </>
        }
        date={formatDate(v.created_at)}
        actions={
          <>
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
          </>
        }
        below={
          confirmId === uid ? (
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
          ) : null
        }
      />
    );
  }

  // What the Filter box leaves standing. A pure VIEW of `items` — nothing is
  // re-fetched — matched against what a user actually types looking for a
  // project: its name and its aspect ratio.
  const shown = items.filter((v) =>
    matchesFilter(query, v.title, v.aspect_ratio)
  );

  return (
    <div className="workflow-head-wrap sb-library">
      <div className="workflow-header">
        <span className="wf-icon"><WorkflowIcon id="animatics-to-video" /></span>
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

      {/* ONE section. "All Final Videos" used to repeat this list below. */}
      <LibrarySection
        title="Recent Final Videos"
        hint={items.length > 0 ? `${items.length} in total` : ""}
        query={query}
        onQuery={setQuery}
        placeholder="Filter final videos"
        loading={loading}
        ghosts={GHOST_ROWS}
        total={items.length}
        shown={shown.length}
        metaLabel="Details"
        dateLabel="Created"
        sizeLabel="Size"
        emptyIcon="🎞️"
        emptyText={
          <>
            No final videos yet — start one{" "}
            <strong>From a Storyboard</strong> to get your prompts filled in
            already.
          </>
        }
      >
        {shown.map(renderItem)}
      </LibrarySection>

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
