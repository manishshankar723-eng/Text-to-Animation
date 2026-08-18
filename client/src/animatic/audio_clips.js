// audio_clips.js — an audio track is a CLIP on the timeline, and this is what
// you can do to one: find the one under the playhead, and cut it in two.
//
// ⚠ EDITOR-SIDE ONLY. `animatic.py` has no counterpart and needs none — the
// server RENDERS a mix, it never edits one, so nothing there ever has to ask
// which clip is under a click or what two halves of a cut look like. The same
// split as `keyframes.js` and `lookPropParts`: reading is mirrored in Python,
// writing is not.
//
// WHY THIS FILE EXISTS AT ALL. An audio track used to be pinned to the head of
// the video: you could pull its two ends in, and that was every edit there was.
// So a pause in the middle of a take — the one edit anybody actually wants —
// could not be taken out, because there was no way to say "this piece plays
// HERE and that piece plays THERE". Two fields make it possible:
//
//   start_ms   where the clip sits on the TIMELINE
//   offset_ms  how far into the FILE it starts reading
//
// and a cut sets both on the second half, by the same amount. That is the whole
// idea; everything below is bookkeeping around it.

import { trackPlayMs, trackWindow } from "./audio_mix.js";

// The shortest piece the razor will leave behind, and the same floor the picture
// razor and the backend's `trim_ms` use. Below this you have not made an edit,
// you have made something you can no longer grab.
export const MIN_CLIP_MS = 100;

/**
 * What an audio clip is KNOWN BY.
 *
 * ⚠ NOT `upload_id`. Since one file can be cut into several clips, the upload
 * answers "which sound", never "which clip" — two halves of a cut share it. A
 * clip saved before the razor existed has no `id`, and the upload is the right
 * fallback for exactly those: in that project one file WAS one clip, so every
 * key the editor built from an upload id stays the key it always was.
 */
export function clipId(track) {
  return track?.id || track?.upload_id || "";
}

/** Is this clip audible at `ms`? Half-open, like every other span here. */
export function clipAliveAt(track, ms, totalMs = 0) {
  const { startMs, endMs } = trackWindow(track, totalMs);
  return ms >= startMs && ms < endMs;
}

/**
 * The clip a click at `ms` lands on, out of the clips on one lane.
 *
 * The LAST match wins, so if two clips overlap (which nothing here creates but
 * a hand-edited project can carry) the razor cuts the one drawn on top —
 * which is the one that was clicked.
 */
export function clipAt(tracks, ms, totalMs = 0) {
  let found = null;
  for (const track of tracks || []) {
    if (clipAliveAt(track, ms, totalMs)) found = track;
  }
  return found;
}

/**
 * Cut one clip at `ms` into the two clips that add up to it.
 *
 * Returns `[head, tail]`, or null if the cut is too close to either end to
 * leave something you could work with — the caller says so rather than
 * silently making a 4ms sliver.
 *
 * `newId` makes the identity for the second half. Passed in rather than
 * generated here so this file stays pure and the editor keeps ONE id generator.
 *
 * THE FOUR THINGS A CUT DOES, and each is the reason for the one after it:
 *
 *   1. the head keeps its start and its id, and gains a trim ending at the cut.
 *      Keeping the id means the selection, the pane and undo all stay pointed
 *      at something that still exists.
 *   2. the tail starts at the cut ON THE TIMELINE and the same distance further
 *      INTO THE FILE, so the two halves play back to back exactly as the
 *      uncut track did. Get one of those two without the other and the audio
 *      jumps at the cut.
 *   3. the fade at each END OF THE TRACK stays on the end it was on: the head
 *      keeps the fade in, the tail keeps the fade out, and the new edges are
 *      hard. A fade duplicated onto both halves would ramp down and back up in
 *      the middle of a continuous take — audible, and nobody asked for it.
 *   4. everything else about the mix — level, tone, role, duck — is copied to
 *      both, because it describes the SOUND and cutting it changes nothing
 *      about that. `group_id` is the exception, and the comment on it says why.
 */
export function splitClip(track, ms, newId, totalMs = 0) {
  if (!track) return null;
  const { startMs, playMs } = trackWindow(track, totalMs);
  const into = Math.round(ms - startMs);
  if (into < MIN_CLIP_MS || playMs - into < MIN_CLIP_MS) return null;

  const head = {
    ...track,
    trim_ms: into,
    // A hard edge at the cut. (2) above.
    fade_out_ms: 0,
  };
  const tail = {
    ...track,
    id: newId,
    start_ms: startMs + into,
    offset_ms: Math.max(0, Math.round(track.offset_ms || 0)) + into,
    trim_ms: playMs - into,
    fade_in_ms: 0,
    // ⚠ THE NEW PIECE IS NOT IN THE OLD PIECE'S GROUP, for the same reason it
    // gets a new id: it is a new clip. Inheriting the group would mean the razor
    // could no longer take a pause out of a grouped clip — deleting the middle
    // piece would delete every clip grouped with it — so cutting one would
    // always have to be preceded by ungrouping it. The head keeps its group,
    // exactly as it keeps its id.
    group_id: "",
  };
  return [head, tail];
}

/**
 * How much of the file is left after a clip's own window — what a right-edge
 * drag is allowed to reveal.
 *
 * A clip trimmed short can be pulled back out again up to the end of the file,
 * and never past it: dragging into footage that does not exist gives you a bar
 * on the timeline with silence under it.
 */
export function clipRoomMs(track) {
  const duration = Math.round(track?.duration_ms || 0);
  // With no measured duration (a project saved by something that never opened
  // the file) there is nothing to clamp against, so the drag is unbounded —
  // the same assumption `trackPlayMs` makes in the same situation.
  if (!duration) return Infinity;
  return Math.max(0, duration - Math.max(0, Math.round(track.offset_ms || 0)));
}

/**
 * The clip's length after a LEFT-edge drag to `ms`, as a patch.
 *
 * Trimming from the left is the one edit that has to move three fields at once:
 * the clip starts later on the timeline, later in the file, and is shorter by
 * the same amount. Doing any two of the three slides the audio under the edge
 * you are dragging, which looks like the file itself moving.
 */
export function trimClipStart(track, ms, totalMs = 0) {
  const { startMs, playMs } = trackWindow(track, totalMs);
  const offset = Math.max(0, Math.round(track.offset_ms || 0));
  // Not past the end of the clip, and not back beyond the head of the file.
  const delta = Math.max(-offset, Math.min(playMs - MIN_CLIP_MS, Math.round(ms) - startMs));
  return {
    start_ms: Math.max(0, startMs + delta),
    offset_ms: offset + delta,
    trim_ms: Math.max(MIN_CLIP_MS, playMs - delta),
  };
}

/** Every clip on one lane, in play order — what the lane draws and cuts. */
export function laneClips(tracks, layerId) {
  return (tracks || [])
    .filter((t) => (t.layer_id || "") === (layerId || ""))
    .sort((a, b) => (a.start_ms || 0) - (b.start_ms || 0));
}

/** How far the timeline must reach to show every audio clip to its end. */
export function audioEndMs(tracks) {
  let end = 0;
  for (const track of tracks || []) {
    // Measured with NO total: this is the number the total is derived FROM, and
    // asking `trackPlayMs` to clamp against it would be circular.
    end = Math.max(end, Math.max(0, Math.round(track.start_ms || 0)) + trackPlayMs(track));
  }
  return end;
}
