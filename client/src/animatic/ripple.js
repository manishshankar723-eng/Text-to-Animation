// ripple.js — when the board's pictures move, the REST OF THE FILM moves with
// them.
//
// ⚠ EDITOR-SIDE ONLY, no Python twin and none needed — the same split as
// `razor.js` and `audio_clips.js`. The server renders a timeline, it never edits
// one; what reaches it is a document whose clips sit where they sit.
//
// ---------------------------------------------------------------------------
// WHY THIS FILE EXISTS
// ---------------------------------------------------------------------------
// `spreadPanelsForRenders` (scene.js) makes room on the board's picture row when
// a take is longer than the shot it was made from. It moves PICTURES, because
// that is the collision it was written for. Everything else on the timeline
// stayed exactly where it was — so the voiceover, the captions, the typed text
// and the clips on the Video row all came out of sync the moment one shot grew:
//
//     "when i generte veo video so time timeline all layer clip go move but my
//      audio not move so see problem my caption and voiver over not move so both
//      still … i want when i generate veo video … move also caption, voicerover
//      audio, if image, video, text layer clip so those also move that time so
//      user not get this type of problem"
//
// ---------------------------------------------------------------------------
// ⚠ THE ONE IDEA: A SHIFT MAP, NOT A DELTA
// ---------------------------------------------------------------------------
// There is no single number to move things by. Shot 7 grows by 2s and shot 24
// by 9s, so a caption at 0:30 owes a different debt from one at 1:20. What the
// picture pass leaves behind is a STEP FUNCTION — "at this old moment, and from
// then on, everything is this much later" — and every other clip is moved by
// looking its own start up in it.
//
//     old   [S6][S7][S8][S9]
//     new   [S6][ S7 ····· ][S8][S9]
//     shift  0    0         +4s +4s        <- what `shiftAt` answers
//
// ⚠ A CLIP IS LOOKED UP BY ITS OWN START AND IS NOT STRETCHED. A caption that
// sat inside shot 7 stays inside shot 7 (its start is before the step) rather
// than being scaled to the shot's new length: it is a caption of two words, not
// a rubber band, and stretching it would make a subtitle hang for six seconds
// because the picture under it got longer.
//
// ⚠ AND AN AUDIO CLIP THAT SPANS A STEP IS CUT AT IT. This is the part that
// cannot be done by moving a number. The voiceover is ONE clip laid from 0:00
// across the whole film; shifting it by shot 24's debt would move the lines
// before shot 24 as well, and shifting it by nothing is the bug. So it is
// razored at the step and only the tail moves — which is what a ripple does to
// audio in any NLE, and which leaves two ordinary clips reading two windows of
// one file, exactly what `splitClip` already makes.

import { clipRowKind, frameSpans } from "./scene.js";
import { splitClip, MIN_CLIP_MS } from "./audio_clips.js";
import { trackWindow } from "./audio_mix.js";
import { isGeneratedCaption } from "./captions.js";

/** The longest a caption may be stretched to, matching `duration_ms`'s own
 *  `le=600_000` on the wire. */
const MAX_CLIP_MS = 600_000;

/**
 * WHAT THE PICTURE PASS DID, as a step function.
 *
 * `before` and `after` are the frame lists on either side of
 * `spreadPanelsForRenders` (or of a server-side pass that moved the same row).
 * Returns `[{ at, shift }, …]` sorted by `at`, oldest first — "from this OLD
 * moment onwards, everything is `shift` ms later". Empty when nothing moved,
 * which is the caller's signal that there is nothing to carry.
 *
 * ⚠ MATCHED BY FRAME ID, NOT BY INDEX. The two lists are usually parallel, but
 * "usually" is how a ripple ends up reading a different clip's start after a
 * pass that appended one — and one wrong row here moves the whole soundtrack.
 *
 * ⚠ TWO POINTS PER PANEL, its start AND its end. The start alone is not enough:
 * a shot that GREW without moving contributes a shift of 0 at its start and the
 * whole of its growth at its end, and it is that second point which carries
 * everything after it along. It also removes any need for a special case at the
 * tail of the film — a music bed past the last shot is picked up by the last
 * shot's end point like anything else.
 */
export function renderShifts(before, after) {
  const from = frameSpans(before || []).spans;
  const to = frameSpans(after || []).spans;
  const landed = new Map();
  (after || []).forEach((f, i) => {
    if (f?.id) landed.set(f.id, to[i]);
  });

  const points = [];
  (before || []).forEach((f, i) => {
    // ⚠ THE BOARD'S PICTURE ROW AND NOTHING ELSE. It is the row the pass lays
    // out, so it is the only row whose movement IS the shift; asking a clip that
    // is merely being carried where it went would be circular.
    if (clipRowKind(f) !== "board_image") return;
    const was = from[i];
    const now = f?.id ? landed.get(f.id) : null;
    if (!was || !now) return;
    points.push({ at: was.start, shift: now.start - was.start });
    points.push({ at: was.end, shift: now.end - was.end });
  });

  points.sort((a, b) => a.at - b.at || a.shift - b.shift);
  // Collapse to one point per moment (the largest shift wins, which is the one
  // belonging to the clip that STARTS there) and drop the flat leading run —
  // a map of nothing but zeroes is a map with nothing to say.
  const out = [];
  for (const point of points) {
    const last = out[out.length - 1];
    if (last && last.at === point.at) last.shift = Math.max(last.shift, point.shift);
    else out.push({ ...point });
  }
  return out.some((p) => p.shift !== 0) ? out : [];
}

/**
 * How much later `ms` is now — the last step at or before it, or 0.
 *
 * Linear rather than a binary search on purpose: a board is tens of shots, this
 * runs once per clip on one edit, and a hand-rolled bisect on a list whose
 * ordering invariant lives in another function is a bug waiting for its board.
 */
export function shiftAt(shifts, ms) {
  let shift = 0;
  for (const point of shifts || []) {
    if (point.at > ms) break;
    shift = point.shift;
  }
  return shift;
}

/**
 * Move every free-floating clip — a caption, a text clip, a shape, an overlay.
 *
 * They all carry `start_ms` and a duration and nothing else that is timed, so
 * one function does all four. Returns the SAME array when nothing moved, so the
 * caller can tell whether this was an edit worth saving.
 *
 * `keep` is the set of clip ids this pass must NOT touch — the captions a
 * voiceover run has just rewritten at their correct times, which are already
 * where the new layout wants them and would be shifted twice by this.
 */
export function rippleClips(clips, shifts, keep = null) {
  if (!shifts?.length || !clips?.length) return clips;
  let touched = false;
  const out = clips.map((clip) => {
    if (keep && keep.has(clip?.id)) return clip;
    const start = Math.max(0, Math.round(clip?.start_ms || 0));
    const shift = shiftAt(shifts, start);
    if (!shift) return clip;
    touched = true;
    return { ...clip, start_ms: start + shift };
  });
  return touched ? out : clips;
}

/**
 * Move every picture clip the layout pass did not already place itself.
 *
 * That is the Video row, a second picture track, an uploaded still in the cut.
 *
 * ⚠ EVERY CLIP THAT CAME OFF A BOARD IS SKIPPED — the panels AND the takes over
 * them, which is `clipRowKind` answering `board_image` or `board_video`. Not a
 * tidy-up: `spreadPanelsForRenders` has already put both where they go, and the
 * map is expressed in OLD time while these clips are handed to us at their NEW
 * starts. Looking a moved clip up by where it has just been moved TO adds its
 * debt a second time, so a take on a pushed panel would slide off the shot it is
 * a take of — the exact fault this pass exists to prevent, committed by the fix.
 *
 * ⚠ A CLIP WITH NO `start_ms` GETS ONE. Null means "after the last clip on my
 * track" (see `frameSpans`), which is a length-relative place and cannot be
 * shifted as a number — so a clip that has to move is written down explicitly at
 * where it was, plus its debt. A clip that owes nothing is left exactly as it is,
 * null and all, because writing a start onto it would change what the record
 * MEANS for the sake of a move of zero.
 *
 * `keep` is the set of clip ids the layout pass has ALREADY placed, and passing
 * one REPLACES the board test above rather than adding to it. ⚠ WHICH CLIPS
 * those are depends on which pass ran, which is why it is a parameter and not a
 * rule: `spreadPanelsForRenders` places the panels AND the takes over them, so
 * its caller passes nothing and gets the board-wide skip. `insertPictures`
 * places ONE ROW — the row a shot was generated into — so the takes above it,
 * and a second storyboard row, still owe their debt and must be carried. Same
 * shape as `rippleClips`'s own `keep`, and for the same reason: a clip already
 * standing at its new start would have its debt added twice.
 */
export function rippleFrames(frames, shifts, keep = null) {
  if (!shifts?.length || !frames?.length) return frames;
  const { spans } = frameSpans(frames);
  const placed = (frame) =>
    keep ? keep.has(frame?.id) : String(clipRowKind(frame)).startsWith("board_");
  let touched = false;
  const out = frames.map((frame, i) => {
    if (placed(frame)) return frame;
    const start = spans[i]?.start ?? 0;
    const shift = shiftAt(shifts, start);
    if (!shift) return frame;
    touched = true;
    return { ...frame, start_ms: start + shift };
  });
  return touched ? out : frames;
}

/**
 * Move every audio clip — CUTTING one that spans a step rather than sliding it.
 *
 * ⚠ THE WHOLE REASON THIS IS NOT `rippleClips`. A voiceover is one clip laid
 * from 0:00 over the entire film. Its start is 0, so `shiftAt` owes it nothing,
 * and shifting it by a later shot's debt would drag the lines BEFORE that shot
 * along too. Neither answer is right, because the clip is not in one place — it
 * is in all of them. So it is razored at each step it crosses and each piece is
 * moved by its own debt, which is what a ripple does to audio in any editor and
 * leaves nothing downstream has to learn about (`splitClip` makes exactly the
 * two clips the razor already makes).
 *
 * `mintId` is a fresh clip id per cut — the editor's own `newId`, not one
 * invented here, because ids are the editor's to hand out.
 *
 * `keep` is the set of clip ids this pass must NOT touch: the voiceover a run
 * has just laid down is already timed to the new layout.
 *
 * ⚠ A PIECE TOO SHORT TO CUT IS LEFT WHOLE AND SHIFTED BY ITS OWN START'S DEBT.
 * `splitClip` refuses to leave anything under `MIN_CLIP_MS`, and the alternative
 * — cutting anyway — is a clip nobody can grab. Rare (it needs a step inside the
 * first or last 100ms of a clip) and the least wrong of the three options.
 */
export function rippleAudio(tracks, shifts, mintId, keep = null) {
  if (!shifts?.length || !tracks?.length) return tracks;
  let touched = false;
  const out = [];
  for (const track of tracks) {
    if (keep && keep.has(track?.id)) {
      out.push(track);
      continue;
    }
    // Cut first, move after: a piece's debt is decided by where its own head
    // ends up sitting, and its head does not exist until the cut is made.
    const pieces = [];
    const queue = [track];
    while (queue.length) {
      const clip = queue.shift();
      const { startMs, playMs } = trackWindow(clip);
      const base = shiftAt(shifts, startMs);
      const step = (shifts || []).find(
        (p) =>
          p.at > startMs + MIN_CLIP_MS - 1 &&
          p.at < startMs + playMs &&
          p.shift !== base
      );
      const halves = step ? splitClip(clip, step.at, mintId(), 0) : null;
      if (!halves) {
        pieces.push(clip);
        continue;
      }
      touched = true;
      pieces.push(halves[0]);
      queue.unshift(halves[1]);
    }
    for (const piece of pieces) {
      const start = Math.max(0, Math.round(piece.start_ms || 0));
      const shift = shiftAt(shifts, start);
      if (shift) touched = true;
      out.push(shift ? { ...piece, start_ms: start + shift } : piece);
    }
  }
  return touched ? out : tracks;
}

/**
 * THE SHOTS THAT GOT LONGER, in the NEW timeline's terms.
 *
 * `[{ start, end }, …]`, oldest first — the new span of every board panel whose
 * HOLD grew, which is a different question from `renderShifts`'s: that one says
 * how far each moment slid, this one says which stretches of film are now longer
 * than what was written over them.
 *
 * A panel that only MOVED is not in here. Nothing over it needs to change length.
 */
export function grownSpans(before, after) {
  const from = frameSpans(before || []).spans;
  const to = frameSpans(after || []).spans;
  const was = new Map();
  (before || []).forEach((f, i) => {
    if (f?.id) was.set(f.id, from[i]);
  });
  const out = [];
  (after || []).forEach((f, i) => {
    if (clipRowKind(f) !== "board_image") return;
    const old = f?.id ? was.get(f.id) : null;
    const now = to[i];
    if (!old || !now) return;
    if (now.end - now.start > old.end - old.start) out.push({ start: now.start, end: now.end });
  });
  return out.sort((a, b) => a.start - b.start);
}

/**
 * A GENERATED CAPTION COVERS THE SHOT IT BELONGS TO.
 *
 * When a take grows its panel from 4 seconds to 8, the subtitle written for that
 * shot is still 4 seconds long and drops off half way through the footage:
 *
 *     "see when i generate veo video and video come in layer and caption and
 *      text move but see caption length only 4sec but my video is 8 sec so i
 *      want caption goes 8 sec so match video length. like you already do in
 *      image"
 *
 * ⚠ THE END MOVES, NEVER THE START. The words are still SPOKEN when they were
 * spoken — the voiceover is a recording and does not stretch — so scaling a
 * caption into the shot's new span would slide every subtitle off the line it
 * transcribes. Holding the start and extending the end keeps it in sync with the
 * voice and simply leaves it up for the rest of the shot, which is the trade the
 * ask is: the subtitle now stays on screen after the line has finished.
 *
 * ⚠ NEVER PAST THE NEXT CAPTION. A shot with two lines in it would otherwise
 * have the first stretched over the second, which is two subtitles on screen at
 * once — the one thing `captions.tidy_lines` exists to prevent.
 *
 * ⚠ GENERATED CAPTIONS ONLY, and the predicate is hard-coded rather than passed
 * in on purpose. `cap…` marks a clip this app wrote to match a spoken line, so
 * making it agree with its shot is finishing our own work. Text the user typed
 * and placed is theirs, and a pass that silently resized it would be this
 * editor losing their edit — which is exactly what a caller passing a different
 * predicate would cause.
 *
 * ⚠ IT ONLY EVER GROWS, like the panel stretch it mirrors.
 *
 * @param clips  the lane's clips, ALREADY at their new starts (`rippleClips`)
 * @param spans  from `grownSpans`, in the same new timeline
 */
export function coverGrownShots(clips, spans) {
  if (!spans?.length || !clips?.length) return clips;
  // Where the next caption begins, so a stretched one never runs into it.
  const captions = clips
    .filter(isGeneratedCaption)
    .sort((a, b) => (a.start_ms || 0) - (b.start_ms || 0));
  const nextStart = new Map();
  captions.forEach((clip, i) => {
    nextStart.set(clip.id, captions[i + 1] ? Math.round(captions[i + 1].start_ms || 0) : Infinity);
  });

  let touched = false;
  const out = clips.map((clip) => {
    if (!isGeneratedCaption(clip)) return clip;
    const start = Math.max(0, Math.round(clip.start_ms || 0));
    const shot = spans.find((s) => start >= s.start && start < s.end);
    if (!shot) return clip;
    const limit = Math.min(shot.end, nextStart.get(clip.id) ?? Infinity);
    const length = Math.min(MAX_CLIP_MS, Math.max(MIN_CLIP_MS, Math.round(limit - start)));
    if (length <= Math.round(clip.duration_ms || 0)) return clip;
    touched = true;
    return { ...clip, duration_ms: length };
  });
  return touched ? out : clips;
}

/**
 * THE FIVE LISTS A SHIFT MAP HAS TO BE APPLIED TO, named once.
 *
 * ⚠ THIS EXISTS BECAUSE THE CALLER MUST NOT READ THE DOCUMENT. There used to be
 * a `rippleDocument(doc, …)` here that took all five lists and handed back all
 * five, and the editor fed it from a ref filled by an effect. That is one copy
 * of the document too many: an effect has not run when a project loads, and a
 * take attaching in a poll can be several renders behind, so the ref could be
 * empty or stale exactly when it mattered — and the failure is SILENT, because
 * rippling an empty list is a no-op that looks like "nothing needed to move".
 * Reported twice, the second time as "now all good audio and image but Caption
 * not move".
 *
 * So every caller now passes the shift map to React's own functional setters
 * (`setTexts((list) => rippleClips(list, shifts))`), which are handed the LIVE
 * list at commit time and cannot be stale. Nothing here reads state at all.
 *
 * What is left to get wrong is forgetting one of the five, so they are listed
 * here and `tests/timeline_ripple_check.py` checks every call site names all of
 * them.
 */
export const RIPPLED_LISTS = ["frames", "texts", "shapes", "overlays", "audioTracks"];
