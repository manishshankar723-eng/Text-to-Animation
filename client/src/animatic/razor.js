// razor.js — cutting a FREE-FLOATING clip in two: a caption, a shape, or a
// picture laid over the film.
//
// ⚠ EDITOR-SIDE ONLY, no Python twin and none needed — the same split as
// `keyframes.js`, `selection.js`, `beat_cut.js` and `audio_clips.js`. The server
// renders a timeline, it never edits one, so what reaches it is two ordinary
// clips indistinguishable from two somebody placed by hand.
//
// ---------------------------------------------------------------------------
// ⚠ WHY THIS IS NOT `splitFrameAt`, AND NOT `splitClip` EITHER
// ---------------------------------------------------------------------------
// There are three different things called "cut" on this timeline, because there
// are three different things a clip can BE:
//
//   A PICTURE IN THE SEQUENCE (`splitFrameAt` in the editor) has no start of its
//     own — its start is the sum of every hold before it — so cutting one means
//     splitting a DURATION in place, and a transition anchored to it has to be
//     re-pointed at the new second half.
//   AN AUDIO CLIP (`splitClip` in `audio_clips.js`) reads a window of a FILE, so
//     the second half needs `offset_ms` moving as well as `start_ms`, or the
//     sound jumps at the cut.
//   A CAPTION, SHAPE OR OVERLAY (here) is a clip that simply occupies a stretch
//     of timeline. No file to seek into, no neighbour to re-time. What it does
//     have — and this is the whole reason the file exists — is KEYFRAMES.
//
// ⚠ ONE THING HERE IS SHARED WITH THE PICTURE SEQUENCE AFTER ALL, and it is the
// keyframe surgery rather than any of the clip models: `trimKeyframesHead` is
// called by the timeline's head trim of the FIRST picture as well. Keys are
// stored relative to a clip's own start whatever kind of clip it is, so the way
// they have to be re-timed is the same; how the clip itself is re-timed is not,
// and that stays where it belongs in each case.
//
// Collapsing the three into one function would mean a razor that takes a union
// of three clip models and branches on which fields happen to be present, which
// is exactly how the second half of a cut ends up carrying a field that meant
// something on a different kind of clip.

import { valueAt } from "./scene.js";

// The shortest piece the razor will leave behind. ⚠ THE SAME FLOOR AS EVERY
// OTHER EDIT — `MIN_CLIP_MS` in `audio_clips.js`, `MIN_MS` in the editor, and
// `duration_ms`'s own `ge=100` on the wire. Below this you have not made an edit,
// you have made something you can no longer grab.
export const MIN_SPLIT_MS = 100;

/**
 * Which kinds of clip the razor can cut, and therefore which bars wear the cut
 * cursor.
 *
 * ⚠ THIS LIST IS THE ANSWER TO "WHY DID THAT NOT CUT". It is exported so the
 * timeline's cursor rule and the editor's dispatch cannot disagree: a kind that
 * is in here shows the razor over its bar AND has somewhere to send the click.
 * Adding a fourth kind of free clip means adding it here and nowhere else.
 */
export const RAZOR_KINDS = ["frame", "audio", "overlay", "text", "shape"];

/** Are these clip kinds the ones this file knows how to cut? */
export const TIMED_KINDS = ["overlay", "text", "shape"];

/**
 * One keyframe track, cut at `offset` into two tracks that animate exactly what
 * the one track animated.
 *
 * ⚠ A KEY IS PLANTED AT THE CUT ON BOTH SIDES, and that is the whole job. Keys
 * are stored RELATIVE to a clip's own start, so the naive split — "keep the keys
 * before the cut, shift the rest back" — loses the value AT the cut on both
 * halves: `valueAt` holds at the first and last key rather than extrapolating,
 * so the head would freeze at its last key some way before the cut and the tail
 * would begin at its first key some way after it. The animation would jump at
 * the edit, which is the one thing cutting a clip must never do.
 *
 * Returns `[headKeys, tailKeys]`, both sorted, both relative to their own half.
 */
export function splitTrack(clip, prop, offset) {
  const track = clip?.keyframes?.[prop];
  if (!Array.isArray(track) || track.length === 0) return [[], []];
  const keys = [...track].sort((a, b) => (a.t ?? 0) - (b.t ?? 0));
  // What the property is worth exactly where the blade lands. Read BEFORE
  // anything is dropped, because it is interpolated from the keys either side.
  const atCut = valueAt(clip, prop, offset, undefined);
  // The ease carried across is the one that was RUNNING at the cut — the ease
  // on a key describes the curve leaving it, so the head's new final key takes
  // the ease of the key it is replacing the tail of.
  const running = [...keys].reverse().find((k) => (k.t ?? 0) <= offset);

  const head = keys.filter((k) => (k.t ?? 0) < offset);
  head.push({ t: Math.round(offset), v: atCut, ease: running?.ease || "linear" });

  const tail = [{ t: 0, v: atCut, ease: running?.ease || "linear" }];
  for (const k of keys) {
    if ((k.t ?? 0) > offset) tail.push({ ...k, t: Math.round((k.t ?? 0) - offset) });
  }
  return [head, tail];
}

/** Every keyframe track of a clip, cut at `offset`. */
export function splitKeyframes(clip, offset) {
  const headKeys = {};
  const tailKeys = {};
  for (const prop of Object.keys(clip?.keyframes || {})) {
    const [head, tail] = splitTrack(clip, prop, offset);
    // ⚠ AN EMPTY TRACK IS DROPPED, NOT KEPT AS `[]`. `animatedProps` filters on
    // length, but `isAnimatedProp` and the ⏱ button read the key itself being
    // there — so an empty array left behind is a property the pane shows as
    // animated with nothing to animate.
    if (head.length) headKeys[prop] = head;
    if (tail.length) tailKeys[prop] = tail;
  }
  return [headKeys, tailKeys];
}

/**
 * Cut one caption, shape or overlay at `ms` (TIMELINE time) into the two clips
 * that add up to it.
 *
 * Returns `[head, tail]`, or null if the cut is too close to either end to leave
 * something you could work with — the caller says so rather than silently making
 * a 4ms sliver nobody can grab.
 *
 * `newId` makes the identity for the second half. Passed in rather than
 * generated here so this file stays pure and the editor keeps ONE id generator.
 *
 * THE FOUR THINGS A CUT DOES, and each is the reason for the one after it:
 *
 *   1. the head keeps its start AND ITS ID, and ends at the cut. Keeping the id
 *      means the selection, the Properties pane and undo all stay pointed at
 *      something that still exists.
 *   2. the tail starts AT THE CUT and runs to where the clip used to end, so the
 *      two halves cover exactly the stretch the one clip covered. Nothing after
 *      them moves: unlike the picture sequence, these clips are placed
 *      absolutely, so a cut here ripples into nothing.
 *   3. the keyframes are cut with a key planted at the blade — see `splitTrack`.
 *   4. everything else — the text, the colour, the effects chain, the mask, the
 *      blend — is copied to both, because it describes what the clip IS and
 *      cutting it in half changes none of it. `group_id` is the exception, and
 *      it goes for the same reason it does on an audio cut: the new piece is a
 *      NEW clip, and inheriting the group would mean deleting the middle piece
 *      of a grouped clip deleted everything grouped with it.
 */
export function splitTimedClip(clip, ms, newId) {
  if (!clip) return null;
  const start = Math.max(0, Math.round(clip.start_ms || 0));
  const duration = Math.round(clip.duration_ms || 0);
  const offset = Math.round(ms) - start;
  if (offset < MIN_SPLIT_MS || duration - offset < MIN_SPLIT_MS) return null;

  const [headKeys, tailKeys] = splitKeyframes(clip, offset);
  const head = { ...clip, duration_ms: offset };
  const tail = {
    ...clip,
    id: newId,
    start_ms: start + offset,
    duration_ms: duration - offset,
    group_id: "",
  };
  // ⚠ ASSIGNED ONLY WHERE THE CLIP HAD KEYFRAMES AT ALL. A caption has no
  // `keyframes` key on the wire, and giving one to both halves of a cut caption
  // would make every split write a field the clip never carried.
  if (clip.keyframes) {
    head.keyframes = headKeys;
    tail.keyframes = tailKeys;
  }
  return [head, tail];
}

/**
 * One clip's keyframe tracks, re-timed for a head trim of `offset` ms. Positive
 * trims IN (the head is cut off), negative trims OUT (the clip gains a head).
 *
 * ⚠ THIS IS THE HALF OF A HEAD TRIM THAT IS NOT THE TWO NUMBERS, and it is the
 * half that fails silently. Key times are relative to a clip's own start, so
 * moving that start and leaving them alone slides the whole animation by however
 * far you trimmed — the clip still validates and still plays, it is just no
 * longer in step with the picture it is animating.
 *
 *   TRIMMING IN is the TAIL HALF OF A SPLIT at the new head, and it is exactly
 *     that: `splitKeyframes` plants a key there carrying the value AND the ease
 *     that were running, or the clip would open at its first surviving key some
 *     way in and jump to it. Same trap `splitTrack` was written for.
 *   TRIMMING OUT only shifts the keys forward. Nothing is planted: `valueAt`
 *     HOLDS at the first key rather than extrapolating, so the new head holds the
 *     value the clip used to open on — which is what it looked like before.
 *
 * ⚠ USED FOR FRAMES TOO, and it is the one thing in this file that is. A picture
 * in the sequence is not a free-floating clip and is not cut by `splitTimedClip`
 * — but its keyframes are stored the same way, relative to its own start, so a
 * Ken Burns push loses step with its footage in precisely the same manner. The
 * surgery is shared; the clip models are not.
 *
 * Returns the new `keyframes` object, or null for a clip that carries none — and
 * null means "write no `keyframes` field", not "write an empty one".
 */
export function trimKeyframesHead(clip, offset) {
  if (!clip?.keyframes || !offset) return null;
  if (offset > 0) return splitKeyframes(clip, offset)[1];
  const shifted = {};
  for (const [prop, track] of Object.entries(clip.keyframes)) {
    if (!Array.isArray(track) || !track.length) continue;
    shifted[prop] = track
      .map((k) => ({ ...k, t: Math.round((k.t ?? 0) - offset) }))
      .sort((a, b) => a.t - b.t);
  }
  return shifted;
}

/**
 * Move the START of one caption, shape or overlay to `ms`, keeping its END
 * exactly where it is — the trim the left-hand grip on every clip does.
 *
 * Returns a PATCH (`{ start_ms, duration_ms, keyframes? }`) or null when the
 * drag asked for no change, or for something shorter than the floor.
 *
 * ⚠ IT IS THE KEYFRAMES, AGAIN, AND IN BOTH DIRECTIONS. Key times are relative
 * to a clip's own start, so moving that start and leaving them alone slides the
 * whole animation by however far the head was trimmed — silently, since the
 * clip still validates and still plays.
 *
 *   TRIMMING IN (`ms` later, the head cut off) is the TAIL HALF of a split at
 *     the new head, and it reuses `splitKeyframes` for exactly that: a key is
 *     planted at the new start carrying the value AND the ease that were running
 *     there, or the clip would begin at its first surviving key some way in and
 *     jump to it. This is the same trap `splitTrack` was written for.
 *   TRIMMING OUT (`ms` earlier, the clip made longer at the head) only shifts
 *     the keys forward. Nothing is planted: `valueAt` HOLDS at the first key
 *     rather than extrapolating, so the new head holds the value the clip used
 *     to open on — which is what it looked like before the trim.
 *
 * The END is what stays put, and that is the whole difference between this and
 * `onTextChange(id, { duration_ms })`: the tail grip changes the length and
 * leaves the start, this changes both so that the far edge does not move.
 */
export function trimTimedClipStart(clip, ms) {
  if (!clip) return null;
  const start = Math.max(0, Math.round(clip.start_ms || 0));
  const duration = Math.round(clip.duration_ms || 0);
  const end = start + duration;
  // Never off the front of the film, never shorter than something you can grab.
  const next = Math.min(Math.max(0, Math.round(ms)), end - MIN_SPLIT_MS);
  const offset = next - start;
  if (offset === 0) return null;

  const patch = { start_ms: next, duration_ms: end - next };
  // ⚠ ONLY WHERE THE CLIP HAD KEYFRAMES AT ALL — same rule as `splitTimedClip`:
  // a caption carries no `keyframes` on the wire, and giving it one on every
  // trim would write a field it never had. That is what the null means.
  const keyframes = trimKeyframesHead(clip, offset);
  if (keyframes) patch.keyframes = keyframes;
  return patch;
}

/**
 * The clip of one lane under `ms`, out of a list that is already filtered to
 * that lane.
 *
 * The LAST match wins, so where two clips overlap (which nothing here creates,
 * but a hand-edited project can carry) the razor cuts the one drawn on top —
 * which is the one that was clicked. Same rule as `clipAt` for audio.
 */
export function timedClipAt(clips, ms) {
  let found = null;
  for (const clip of clips || []) {
    const start = Math.max(0, Math.round(clip.start_ms || 0));
    if (ms >= start && ms < start + Math.round(clip.duration_ms || 0)) found = clip;
  }
  return found;
}
