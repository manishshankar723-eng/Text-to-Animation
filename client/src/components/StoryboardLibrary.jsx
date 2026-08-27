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

// The row id an unfinished storyboard answers to, for the shared per-row state
// (which row is busy, which row is showing a confirm strip).
//
// ⚠ IT IS PREFIXED, NOT BARE. There can be SEVERAL drafts, so a constant will
// not do — one confirm strip would open on all of them. The prefix keeps a
// draft's row id from ever colliding with a board's job_id, which is what a
// bare id risks the moment a draft is promoted to the board of the same name.
const draftRowId = (jobId) => `draft:${jobId}`;

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
  // ⚠ RESUME THE UNFINISHED STORYBOARD. Given only by the workflow that can
  // actually resume one, so the animatic library (which renders this same
  // component over COPIES) never offers a draft it has no way to open. Omit it
  // and the row does not exist.
  onResume,
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
  // ⚠ THE UNFINISHED STORYBOARD, ON THE PAGE THAT LISTS STORYBOARDS. It used
  // to have a strip on the dashboard instead, and the user asked for it here
  // and nowhere else: *"maine recent kyun banaya hai jab yahan pe mera resume
  // dikh hi nahi raha … home page se bhi hatao, bas ek jagah."*
  //
  // A first attempt put it in a strip ABOVE the list; that was not what was
  // asked for either. It is a ROW now, leading the list — see `renderDraftRow`
  // for what it does and does not claim to be.
  //
  // The effect keys off a BOOLEAN, not `onResume` itself: the caller passes an
  // inline arrow, so its identity changes on every parent render and depending
  // on it would re-fetch the draft each time the workflow re-rendered.
  // ⚠ ALL OF THEM, NOT THE NEWEST ONE. This asked `GET /storyboards/draft`,
  // which answers "the most recent" — so an account holding two unfinished
  // boards could only ever see one, and the older was unreachable by any means.
  // Found the hard way: resuming opened an unrelated project, and the account
  // turned out to have two drafts with the older one invisible.
  const [drafts, setDrafts] = useState([]);
  const canResume = Boolean(onResume);
  useEffect(() => {
    if (!canResume) return;
    let cancelled = false;
    api
      .listStoryboardDrafts()
      .then((list) => {
        if (cancelled) return;
        // Nothing to offer for one with no shots — there is no board in it.
        setDrafts((list || []).filter((d) => d?.job_id && (d.shots || []).length));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [canResume]);

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

  /** Throw the unfinished storyboard away. The breakdown behind it was paid
   *  for and there is no undo, so it goes through the row's own confirm strip
   *  like every other delete in this list — never on a single click. */
  async function discardDraft(draft) {
    setBusyId(draftRowId(draft.job_id));
    setError("");
    try {
      await api.discardStoryboardDraft(draft.job_id);
      setDrafts((ds) => ds.filter((d) => d.job_id !== draft.job_id));
      setConfirmId(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }

  // THE UNFINISHED STORYBOARD, DRAWN AS A ROW LIKE ANY OTHER PROJECT.
  //
  // ⚠ IT IS IN THE LIST, NOT ABOVE IT — asked for in as many words after a
  // first attempt put it in its own strip: *"mai yeh nahi lagane bola tha …
  // dance video ke upar hi aa jaye jaise sab dikh rahe hai, so user samajh
  // jayega ki mera pehla work resume wala bhi hai aur completed work bhi."*
  // One list, newest first, and the unfinished one leads it.
  //
  // ⚠ IT IS STILL NOT A BOARD ON THE SERVER. `GET /storyboards` excludes DRAFT
  // jobs on purpose and `storyboard_draft_check.py` [3] pins that; this row is
  // the separately-fetched draft record, prepended client-side. Nothing about
  // the API contract changed — only where the client draws it.
  //
  // ⚠ AND IT NEVER PRETENDS TO HAVE A PICTURE. There are no panels yet, so the
  // thumbnail is a note glyph rather than a cover, and the Details column says
  // "Not drawn yet" first. That was the other half of the request: *"agar user
  // storyboard image generate nahi kiya hai to text note jaisa icon dikha
  // dena."*
  function renderDraftRow(draft) {
    const uid = draftRowId(draft.job_id);
    const busy = busyId === uid;
    const shotCount = (draft.shots || []).length;
    const genre = genreLabel(draft.genre);
    return (
      <LibraryRow
        key={uid}
        onOpen={() => onResume(draft)}
        openTitle="Pick this storyboard up where you left off"
        aspect={draft.aspect_ratio}
        /* No panels, so no bytes. `formatBytes` draws an em dash, which is the
           honest answer — see its note on why nothing is ever "0 B". */
        size={0}
        cover={<span className="lib-draft-glyph">📝</span>}
        name={
          <div
            className="lib-title"
            onClick={() => onResume(draft)}
            title={
              draft.updated_at
                ? `Saved ${new Date(draft.updated_at).toLocaleString()}. The breakdown behind it has already been paid for.`
                : "The breakdown behind it has already been paid for."
            }
          >
            {draft.title || "Untitled storyboard"}
          </div>
        }
        meta={
          <>
            <span className="chip warn">Not drawn yet</span>
            {genre && <span className="chip">{genre}</span>}
            {draft.aspect_ratio && <span className="chip">{draft.aspect_ratio}</span>}
            {shotCount > 0 && (
              <span className="chip">
                {shotCount} shot{shotCount === 1 ? "" : "s"}
              </span>
            )}
          </>
        }
        date={formatDate(draft.updated_at)}
        actions={
          <>
            {/* A real button, not an icon: this row's whole point is that it
                can be picked up again, and that must not be a glyph to guess
                at beside four others. */}
            <button
              type="button"
              className="btn small lib-resume"
              disabled={busy}
              onClick={() => onResume(draft)}
            >
              Resume →
            </button>
            <button
              type="button"
              className="lib-icon danger"
              disabled={busy}
              title="Discard this unfinished storyboard"
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
                Discard “{draft.title || "Untitled storyboard"}”? The breakdown
                behind these {shotCount} shot{shotCount === 1 ? "" : "s"} has
                already been paid for and this cannot be undone.
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
                  onClick={() => discardDraft(draft)}
                >
                  {busy ? "Discarding…" : "Discard"}
                </button>
              </div>
            </div>
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
  // The unfinished one is a project in this list like any other, so it filters
  // like one too.
  // The unfinished ones are projects in this list like any other, so they
  // filter like them too.
  const draftsShown = onResume
    ? drafts.filter((d) =>
        matchesFilter(query, d.title, genreLabel(d.genre), d.aspect_ratio)
      )
    : [];
  const listTotal = boards.length + (onResume ? drafts.length : 0);
  const shownTotal = shown.length + draftsShown.length;

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
        hint={listTotal > 0 ? `${listTotal} in total` : ""}
        query={query}
        onQuery={setQuery}
        placeholder="Filter storyboards"
        loading={loading}
        ghosts={GHOST_ROWS}
        /* ⚠ THE DRAFT COUNTS. Without it a user whose only project is an
           unfinished one would be told "No storyboards yet" with their own
           work sitting right there — `total: 0` draws the empty state instead
           of the rows. */
        total={listTotal}
        shown={shownTotal}
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
        {/* The unfinished ones LEAD the list — they are the only projects
            still waiting on the user, and newest first among themselves
            because that is the order the server returns them in. */}
        {draftsShown.map(renderDraftRow)}
        {shown.map(renderBoard)}
      </LibrarySection>
    </div>
  );
}
