// One shot's KEY POSES — the flipbook strip under a panel in Image to
// Animatic Image.
//
// Press Generate, pick a shot length, and the model blocks out the poses that
// carry the motion. The arithmetic is shown in the dialog on purpose, because
// it is the thing that makes the feature make sense to an animator:
//
//     4s × 24fps = 96 frames  →  16 key drawings
//
// It is NOT a video and not every frame; it is what you would pin up to check
// the timing works before anything gets rendered.
//
// SHOWING THE WORK: the moment Generate is pressed, all 16 tiles appear as
// shimmering placeholders and fill in one by one — the same thing Text to
// Turnaround Image does while it draws a part. This is not decoration: without
// it the strip sat empty for the minutes the run takes and the button looked
// broken (reported). The tile count comes from the LOCAL request, not from
// server progress, so the slots are there on the first frame after the click
// rather than after the first poll.
//
// STOP AND RESUME: stopping leaves the drawings already made on disk, and
// "Draw the remaining N" continues from there rather than starting over — so a
// stop never throws away images that were paid for. Regenerate (`resume=false`,
// below) is the separate, explicit action that redraws them.
//
// REGENERATE MEANS REDRAW, AND IT HAS TO LOOK LIKE IT (all reported together):
//
//   1. The button used to RESUME. On a finished 8/8 shot that is a no-op — the
//      server has nothing missing to draw and returns "already complete" — so
//      the click genuinely did nothing. Regenerate now sends `resume=false`,
//      which redraws the shot. Resuming is the separate "Draw the remaining N".
//   2. A redrawn pose kept the same URL, and this component caches one object
//      URL per path and never re-fetches a path it already has — so even a pose
//      that WAS redrawn went on showing the old drawing for ever. The server now
//      stamps the file's mtime into the URL (`?v=…`), which is what makes a new
//      drawing a new path here.
//   3. Nothing on screen changed while it worked. The shimmer only ever appeared
//      for a pose with NO picture, so it showed on a first run and never on a
//      regenerate. Poses being replaced are now BLURRED under a "Redrawing…"
//      veil from the moment of the click until their new drawing lands — see
//      `redrawing` below.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";

// The menu the server accepts (panel_sequence.ALLOWED_DURATIONS). Keep in step.
const DURATIONS = [2, 4, 6, 8, 10];
// Must match panel_sequence.KEY_POSES_PER_SECOND / FPS — shown, not enforced,
// here: the server derives the real count so the client can't over-order.
const POSES_PER_SECOND = 4;
const FPS = 24;
// Must match panel_sequence.PREVIEW_POSES. How many drawings "Preview" buys —
// two, because movement can only be judged by comparing one drawing with the
// next. The server enforces the real number; this is for the labels.
const PREVIEW_POSES = 2;
// While drawing, re-read often: each pose takes ~20-40s, and a slow poll makes
// finished images sit invisible behind a placeholder.
const POLL_MS = 2500;

// pose number → the URL its picture is at right now. The URL carries the file's
// mtime (server: panel_sequence.frame_version), so a pose whose path CHANGED has
// a genuinely new drawing — which is how the strip knows a redraw has landed.
// A refused pose leaves a hole, so tile 6 is not necessarily pose 6: the server
// says which is which in `frame_numbers`.
function pathsByPose(seq) {
  const out = {};
  (seq?.urls || []).forEach((path, k) => {
    const pose = seq?.frame_numbers?.[k];
    out[pose === undefined ? k : pose] = path;
  });
  return out;
}

export default function PanelSequenceStrip({
  jobId,
  index,
  label,
  // True while the BOARD job is running anything at all — one job, one board,
  // so another shot generating must disable this one's button.
  boardBusy,
  // The board's live progress, so this strip can show its own shot's counter.
  progress,
  // ⚠ THE BOARD'S ASPECT ("16:9", "9:16"), AND THE TILES ARE CROPPED WITHOUT
  // IT. A key pose is drawn at the board's shape; the strip used to draw every
  // tile 16:9 with `object-fit: cover` over it, so on a portrait board each
  // drawing was shown as a horizontal slice out of its own middle — in the one
  // strip whose entire job is letting you judge the motion. Omit it and the
  // tiles fall back to 16:9, which is what they always were.
  ratio,
  // ⚠ THE SHOT'S OWN PLANNED LENGTH, from the breakdown. This box used to open
  // at a flat 4 seconds for every shot on every board, so a 1-second reaction
  // and a 6-second establishing wide were both offered the same guess and the
  // number the breakdown had already worked out went unused. 0 (an older board
  // that carries none) falls back to the old 4.
  plannedSeconds = 0,
  onError,
  onStarted
}) {
  const [seq, setSeq] = useState(null);
  const [asking, setAsking] = useState(false);
  const [duration, setDuration] = useState(plannedSeconds > 0 ? plannedSeconds : 4);
  const [busy, setBusy] = useState(false);
  const [zipping, setZipping] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  // True from pressing Stop until the run actually ends. Latched, because the
  // pose in flight has to finish first and the button must not look ignored.
  const [stopping, setStopping] = useState(false);
  // Index of the pose open in the lightbox, or null. The thumbnails are ~135px
  // wide — enough to see that something moved, nowhere near enough to judge a
  // drawing — so any of them opens full size and you step through the set from
  // there without going back to the strip.
  const [viewing, setViewing] = useState(null);
  // How many tiles this strip is expecting. Set the instant Generate is pressed
  // so the placeholders are on screen before the request even returns.
  const [expected, setExpected] = useState(0);
  // path → object URL. Frames sit behind the bearer token like every other
  // picture in this app, so they can't be a plain <img src>.
  const [urls, setUrls] = useState({});
  const owned = useRef([]);
  const pending = useRef(new Set());
  // POSES WHOSE PICTURE IS BEING REPLACED RIGHT NOW. A Set of pose numbers,
  // filled the instant Regenerate or ↻ is clicked. Those tiles blur under a
  // "Redrawing…" veil, so a regenerate looks like something is happening from
  // the first frame after the click instead of sitting there showing the old
  // drawings — which is what made a working regenerate look broken (reported:
  // "i can't see any changes"). Purely a display state: the server decides what
  // is actually redrawn.
  const [redrawing, setRedrawing] = useState(() => new Set());
  // pose number → the path it was showing WHEN the redraw was asked for. A pose
  // is done when its path differs from this, which is exact: it can only differ
  // once the server has written a new file and re-versioned the URL.
  const pathAtRequest = useRef({});

  const load = useCallback(async () => {
    try {
      setSeq(await api.getPanelSequence(jobId, index));
    } catch {
      // A missing sequence is the normal state for a shot nobody has generated.
    }
  }, [jobId, index]);

  useEffect(() => {
    load();
  }, [load]);

  // This shot is the one being drawn if we started the run, or if the board
  // says the worker is on this panel. The first covers the gap between the
  // click and the worker picking the job up.
  const mine = expected > 0 || (boardBusy && progress?.panel === index);

  // Pull frames in while they're being drawn so they appear one by one.
  useEffect(() => {
    if (!mine) return undefined;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [mine, load]);

  // When the board goes quiet the run is over: re-read once for the final
  // counts, and drop the local expectation so the strip shows what actually
  // exists (a stopped run shows fewer tiles than it asked for, which is true).
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !boardBusy) {
      load();
      setExpected(0);
      setStopping(false); // the run ended — Stop is meaningful again next time
      // Nothing else is coming, so no tile is still waiting for a new drawing.
      // The backstop for a pose the model refused: it keeps its old picture
      // rather than staying blurred for ever under a veil that never lifts.
      setRedrawing((prev) => (prev.size ? new Set() : prev));
    }
    wasBusy.current = boardBusy;
  }, [boardBusy, load]);

  // LIFT THE VEIL, POSE BY POSE, as each new drawing lands — rather than all at
  // once at the end. The URL carries the file's mtime, so a changed path for a
  // pose IS its new drawing; the strip fetches it (below) and un-blurs that one
  // tile while the rest stay under the veil. That is what makes a long run
  // readable: you watch it work through the shot.
  useEffect(() => {
    setRedrawing((prev) => {
      if (!prev.size) return prev;
      const now = pathsByPose(seq);
      const next = new Set(prev);
      for (const pose of prev) {
        if (now[pose] && now[pose] !== pathAtRequest.current[pose]) {
          next.delete(pose);
        }
      }
      return next.size === prev.size ? prev : next;
    });
  }, [seq]);

  // Mark these poses as "being replaced" and remember what they show now, so
  // the effect above can tell when the replacement has actually arrived.
  const markRedrawing = useCallback(
    (poses) => {
      const now = pathsByPose(seq);
      poses.forEach((p) => {
        pathAtRequest.current[p] = now[p];
      });
      setRedrawing(new Set(poses));
    },
    [seq]
  );

  useEffect(() => {
    for (const path of seq?.urls || []) {
      if (urls[path] || pending.current.has(path)) continue;
      pending.current.add(path);
      api
        .fetchAnimaticMedia(path)
        .then((u) => {
          owned.current.push(u);
          setUrls((m) => ({ ...m, [path]: u }));
        })
        .catch(() => pending.current.delete(path));
    }
  }, [seq]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(
    () => () => owned.current.forEach((u) => URL.revokeObjectURL(u)),
    []
  );

  // `resume` false is a REGENERATE: every pose is redrawn, including the ones
  // already on disk. True continues from what is there. The distinction used to
  // be invisible because both buttons sent `true`, which on a finished shot
  // means "there is nothing missing" and the server correctly did nothing.
  async function start(resume, preview = false) {
    const want = duration * POSES_PER_SECOND;
    setBusy(true);
    onError?.("");
    // Placeholders FIRST — before the await — so the click has an effect that
    // is visible immediately rather than after a round trip. A preview only
    // fills the slots it is actually going to draw; showing forty shimmering
    // tiles for a two-drawing run would promise something it isn't doing.
    setExpected(preview ? Math.min(PREVIEW_POSES, want) : want);
    // A regenerate replaces every drawing, so every tile goes under the veil at
    // once and lifts one at a time as the new ones arrive.
    if (!resume) markRedrawing(Array.from({ length: want }, (_, n) => n));
    setAsking(false);
    try {
      await api.generatePanelSequence(jobId, index, duration, resume, preview);
      onStarted?.();
    } catch (e) {
      setExpected(0); // nothing is coming; don't leave ghost tiles behind
      setRedrawing(new Set()); // …nor tiles blurred for a run that never began
      onError?.(e.message);
    } finally {
      setBusy(false);
    }
  }

  // Stop a run in flight. The pose already being drawn finishes — an in-flight
  // image call can't be un-sent — so the button stays "Stopping…" until the
  // board goes quiet. Everything drawn so far is kept, and the strip's
  // "Draw the remaining N" picks up exactly where this left off.
  async function stopRun() {
    if (stopping) return;
    setStopping(true);
    onError?.("");
    try {
      await api.stopStoryboard(jobId);
    } catch (e) {
      onError?.(e.message);
      setStopping(false); // it didn't take — let them press it again
    }
  }

  // Re-draw ONE pose. The bad drawing in the strip is usually a single frame —
  // the one that came back in colour, or the one where he didn't move — and
  // before this the only remedy was to clear all sixteen and start again.
  async function redrawPose(n) {
    setBusy(true);
    onError?.("");
    // This ONE tile blurs; the rest of the strip is untouched, which is the
    // whole point of a per-pose redo — you can see exactly which drawing is
    // being replaced.
    markRedrawing([n]);
    try {
      await api.redrawPanelPose(jobId, index, seq?.duration_seconds || duration, n);
      onStarted?.();
    } catch (e) {
      setRedrawing(new Set());
      onError?.(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function clearAll() {
    setBusy(true);
    try {
      await api.deletePanelSequence(jobId, index);
      setConfirmClear(false);
      setUrls({});
      pending.current.clear();
      setExpected(0);
      await load();
    } catch (e) {
      onError?.(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function downloadAll() {
    setZipping(true);
    try {
      await api.downloadPanelFrames(jobId, index, `${label} key poses.zip`);
    } catch (e) {
      onError?.(e.message);
    } finally {
      setZipping(false);
    }
  }

  const frames = seq?.frames || 0;
  const planned = seq?.planned || 0;
  // The pose numbers that actually have a drawing, in order. Usually just
  // 0…frames-1, but a refused pose leaves a hole and this skips it.
  const drawnPoses =
    seq?.frame_numbers?.length === frames
      ? seq.frame_numbers
      : Array.from({ length: frames }, (_, k) => k);

  // Stepping WRAPS: past the last pose comes the first again, and back from the
  // first lands on the last. These are the frames of a loop of motion, so
  // flipping them round and round is how you judge whether the cycle reads —
  // stopping dead at 16/16 just made the user click away and come back.
  // It steps over holes rather than into them: `viewing` is a POSE number, and
  // a missing pose has no picture to show.
  const step = useCallback(
    (delta) =>
      setViewing((pose) => {
        if (!drawnPoses.length) return pose;
        const at = drawnPoses.indexOf(pose);
        const next = (at < 0 ? 0 : at + delta + drawnPoses.length) % drawnPoses.length;
        return drawnPoses[next];
      }),
    [drawnPoses.join(",")] // eslint-disable-line react-hooks/exhaustive-deps
  );

  // Arrow keys do the same as the arrows, and Escape closes — flipping 16
  // drawings by mouse to compare them is what this view exists to avoid.
  useEffect(() => {
    if (viewing === null) return undefined;
    function onKey(e) {
      if (e.key === "Escape") setViewing(null);
      if (e.key === "ArrowRight") step(1);
      if (e.key === "ArrowLeft") step(-1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [viewing, step]);
  const incomplete = frames > 0 && planned > 0 && frames < planned;
  const total = duration * POSES_PER_SECOND;
  // Tiles on screen: everything drawn, and — while a run is live — the empty
  // slots still to come, so you can see how much is left.
  const slots = mine ? Math.max(expected, planned, frames) : frames;
  const done = Math.min(frames, slots);
  // WHICH POSE EACH PICTURE IS. A refused drawing leaves a hole, and `urls`
  // only carries the ones that exist — so tile 6 is not necessarily pose 6.
  // The server says which is which; without this a gap would silently shunt
  // every later drawing one place to the left and mis-number the lot. Shared
  // with the redraw tracking above, which has to compare the SAME paths.
  const byPose = pathsByPose(seq);

  return (
    <div className="seq-strip">
      <div className="seq-head">
        <span className="seq-title">
          Key poses
          {(frames > 0 || mine) && (
            <span className="tiny muted">
              {" "}
              — {done}
              {slots ? `/${slots}` : ""}
              {seq?.duration_seconds ? ` · ${seq.duration_seconds}s` : ""}
            </span>
          )}
        </span>

        <div className="seq-actions">
          {/* STOP, while this strip's own run is live. Before this the only
              buttons during a run were disabled ones, so a sequence you could
              already see going wrong at pose 2 had to be watched to the end.
              Mirrors the board's Stop (StoryboardBoard.handleStop): same
              danger-btn, same latched "Stopping…", same promise that whatever
              is already drawn is kept. */}
          {mine && (
            <button
              type="button"
              className="btn danger-btn small"
              disabled={stopping}
              title="Stop after the pose being drawn now — everything already drawn is kept"
              onClick={stopRun}
            >
              {stopping ? (
                <>
                  <span className="spinner-inline" /> Stopping…
                </>
              ) : (
                "⏹ Stop"
              )}
            </button>
          )}
          {frames > 0 && !mine && (
            <button
              type="button"
              className="btn small"
              disabled={zipping}
              title="Download these poses as a ZIP, numbered in play order"
              onClick={downloadAll}
            >
              {zipping ? "Zipping…" : `⬇ Download ${frames}`}
            </button>
          )}
          {incomplete && !boardBusy && (
            <button
              type="button"
              className="btn small"
              disabled={busy}
              title="Carry on from the poses already drawn — the ones you have are not redrawn"
              onClick={() => {
                setDuration(seq.duration_seconds || duration);
                start(true);
              }}
            >
              {/* Reads correctly whether this is continuing after a PREVIEW,
                  after a Stop, or filling holes the model refused — in every
                  case it draws exactly the poses that are missing. */}
              ▶ Draw the remaining {planned - frames}
            </button>
          )}
          <button
            type="button"
            className="btn small primary"
            disabled={boardBusy || busy}
            title={
              boardBusy
                ? "This board is busy — wait for it to finish"
                : frames > 0
                  ? "Draw this shot's key poses again, replacing the ones you have"
                  : "Block out the key poses for this shot"
            }
            onClick={() => setAsking(true)}
          >
            {/* ONE name for this action, everywhere. It read "Generate again"
                here and "Re-draw" on the per-pose button, for what is the same
                idea at two scales — asked for as one word: Regenerate. */}
            {frames > 0 ? "✨ Regenerate" : "✨ Generate"}
          </button>
          {frames > 0 && !boardBusy && (
            <button
              type="button"
              className="lib-icon danger"
              disabled={busy}
              title="Delete these poses and start clean"
              onClick={() => setConfirmClear(true)}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {mine && (
        <div className="seq-progress">
          <span className="seq-progress-bar">
            <span
              style={{
                width: `${slots ? Math.round((100 * done) / slots) : 0}%`
              }}
            />
          </span>
          <span className="tiny muted">{progress?.message || "Starting…"}</span>
        </div>
      )}

      {confirmClear && (
        <div className="lib-confirm">
          <span className="tiny">
            Delete all {frames} key pose{frames === 1 ? "" : "s"} for this shot?
            You paid to draw them.
          </span>
          <div className="lib-confirm-btns">
            <button
              type="button"
              className="btn small"
              disabled={busy}
              onClick={() => setConfirmClear(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn small lib-delete"
              disabled={busy}
              onClick={clearAll}
            >
              Delete
            </button>
          </div>
        </div>
      )}

      {slots === 0 ? (
        <p className="tiny muted seq-empty">
          No key poses yet. Generate blocks out the motion of this shot as a
          flipbook of drawings.
        </p>
      ) : (
        <div
          className="seq-frames"
          /* Read by every tile AND by the redraw veil over it, so the two can
             never disagree about the shape. See key-poses.css. */
          style={ratio ? { "--seq-ratio": ratio.replace(":", " / ") } : undefined}
        >
          {Array.from({ length: slots }).map((_, n) => {
            const path = byPose[n];
            const src = path ? urls[path] : null;
            const replacing = redrawing.has(n);
            return (
              <figure
                className={`seq-frame ${src ? "" : "pending"} ${
                  replacing ? "is-redrawing" : ""
                }`}
                /* Keyed by POSE, not by path. Keying by the path remounted the
                   tile the moment its URL was re-versioned, which threw away
                   the blurred old picture mid-redraw and flashed an empty box
                   where a smooth hand-over should be. */
                key={`pose-${n}`}
              >
                {src ? (
                  // A button, not a bare <img>: it is a real control, so it
                  // gets keyboard focus and a title like every other one.
                  <button
                    type="button"
                    className="seq-frame-open"
                    title={
                      seq?.poses?.[n]
                        ? `Open key pose ${n + 1} of ${slots} — ${seq.poses[n]}`
                        : `Open key pose ${n + 1} of ${slots}`
                    }
                    onClick={() => setViewing(n)}
                  >
                    <img src={src} alt={`Key pose ${n + 1}`} />
                  </button>
                ) : (
                  <div className="seq-frame-loading" />
                )}

                {/* THE VEIL. Over the drawing being replaced, from the click
                    until its new picture lands — the answer to "I pressed
                    Regenerate and I can't see any changes". It sits on top of
                    the OLD drawing rather than blanking it, so you can still
                    see which pose is being redone while it happens. */}
                {replacing && (
                  <span className="redraw-veil">
                    <span className="spinner-inline" />
                    <span className="tiny">Redrawing…</span>
                  </span>
                )}

                {/* REDO THIS ONE. The drawing that came back wrong is almost
                    always a single frame, and until now the only remedy was to
                    clear all sixteen and pay for them again. Hidden while the
                    board is busy, like every other action here — and while this
                    tile is already being redrawn, where it would do nothing. */}
                {src && !boardBusy && !replacing && (
                  <button
                    type="button"
                    className="seq-frame-redo"
                    disabled={busy}
                    title={`Re-draw key pose ${n + 1} — costs one image and leaves the others alone`}
                    onClick={() => redrawPose(n)}
                  >
                    ↻
                  </button>
                )}
                <figcaption className="tiny muted">{n + 1}</figcaption>
              </figure>
            );
          })}
        </div>
      )}

      {/* Full-size viewer. Same `.lightbox-*` shell the board uses for a panel,
          so opening a pose feels like opening a shot — plus arrows, because the
          point of these images is comparing pose N with pose N+1. */}
      {viewing !== null && (
        <div className="lightbox-overlay" onClick={() => setViewing(null)}>
          <div className="lightbox-figure" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="lightbox-close"
              title="Close (Esc)"
              onClick={() => setViewing(null)}
            >
              ✕
            </button>

            {/* Never disabled — both arrows wrap, so the cycle can be flipped
                round continuously. */}
            <button
              type="button"
              className="lightbox-nav prev"
              title="Previous pose (←)"
              onClick={() => step(-1)}
            >
              ‹
            </button>

            <img
              className="lightbox-img"
              src={urls[byPose[viewing]]}
              alt={`Key pose ${viewing + 1} of ${planned || frames}`}
            />

            <button
              type="button"
              className="lightbox-nav next"
              title="Next pose (→)"
              onClick={() => step(1)}
            >
              ›
            </button>

            <span className="lightbox-count">
              {label} · pose {viewing + 1} / {planned || frames}
            </span>
          </div>
        </div>
      )}

      {/* The dialog. The frame maths is spelled out because that is the whole
          concept: you are ordering key DRAWINGS for a shot of this length, not
          a video and not all 96 frames. */}
      {asking && (
        <div className="modal-overlay" onClick={() => setAsking(false)}>
          <div className="card seq-modal" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="modal-close"
              onClick={() => setAsking(false)}
            >
              ✕
            </button>
            <h2>
              {frames > 0 ? "Regenerate images" : "Generate images"} — {label}
            </h2>
            <p className="muted">
              How long does this shot run? The poses are blocked out across that
              length, the way you would key an animation.
            </p>

            <div className="seq-durations">
              {DURATIONS.map((d) => (
                <button
                  key={d}
                  type="button"
                  /* `active` is the selected modifier this stylesheet defines
                     (gold border + fill + inset ring). It is NOT "on" — that
                     styles nothing, so the chip only looked chosen while the
                     pointer was over it and went flat the moment it left. */
                  className={`opt-chip ${duration === d ? "active" : ""}`}
                  aria-pressed={duration === d}
                  onClick={() => setDuration(d)}
                >
                  {d}s
                </button>
              ))}
            </div>

            <div className="seq-maths">
              <span className="seq-maths-line">
                {duration}s × {FPS}fps ={" "}
                <strong>{duration * FPS} frames</strong>
              </span>
              <span className="seq-maths-line">
                → <strong>{total} key drawings</strong> covering that motion
              </span>
              <span className="tiny muted">
                {total} image credit{total === 1 ? "" : "s"}. Not a video — the
                key poses, so you can check the timing first.
              </span>
            </div>

            {/* SAY WHAT THE BUTTON DOES. It used to read "generating continues
                from there", which was true and useless: on a finished shot
                there is nothing to continue, so the click did nothing at all.
                Regenerate REDRAWS, and the honest version of that sentence has
                to name both the cost and the cheaper alternative. */}
            {frames > 0 && (
              <p className="tiny muted">
                This shot already has {frames} pose{frames === 1 ? "" : "s"}.
                Regenerating draws all {total} again and replaces them —{" "}
                {total} image credit{total === 1 ? "" : "s"}.
                {incomplete
                  ? ` To keep what you have and draw only the ${planned - frames} missing, close this and use “Draw the remaining ${planned - frames}”.`
                  : " The drawings you have now are not kept."}
              </p>
            )}

            <div className="an-name-actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setAsking(false)}
              >
                Cancel
              </button>
              {/* PREVIEW FIRST, and it is the primary action on purpose.
                  Whether a shot actually moves can only be seen by comparing
                  one drawing with the next, and paying for {total} images to
                  find out it didn't is the thing this exists to stop. Two
                  images, then decide. Continuing costs only the rest. */}
              {total > PREVIEW_POSES && frames === 0 && (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  title={`Draw the first ${PREVIEW_POSES} poses only, so you can check the motion before paying for all ${total}`}
                  onClick={() => start(true, true)}
                >
                  {busy ? "Starting…" : `👁 Preview ${PREVIEW_POSES} first`}
                </button>
              )}
              {/* A shot that already has drawings REGENERATES (resume=false);
                  an empty one just generates. Sending resume=true in both cases
                  is what made the button a no-op on a finished shot. */}
              <button
                type="button"
                className="btn primary"
                disabled={busy}
                onClick={() => start(frames === 0)}
              >
                {busy
                  ? "Starting…"
                  : frames > 0
                    ? `✨ Regenerate ${total} images`
                    : `✨ Generate ${total} images`}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
