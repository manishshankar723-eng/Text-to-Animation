// beat_cut.js — pull every cut onto the nearest beat.
//
// The beats are already there: `beats.js` decodes each track once, the timeline
// draws them on the audio lane and clip edges already snap to them. This is
// that same analysis applied to the whole sequence in one press, instead of by
// dragging forty edges onto markers that are sitting right there on screen.
//
// ⚠ EDITOR-SIDE ONLY, no Python twin and none needed — the same split as
// `keyframes.js`, `selection.js` and `audio_clips.js`. The server renders a
// timeline, it never edits one; what reaches it is ordinary `duration_ms`
// values, indistinguishable from clips somebody timed by hand.
//
// THREE THINGS THAT ARE EASY TO GET WRONG, and all three are why this is a file
// rather than eight lines inside a click handler:
//
//   1. **A CUT IS NOT A THING YOU CAN MOVE.** The picture sequence is a FLOW —
//      a clip's start is the sum of every duration before it — so there is
//      nothing on the document called "the cut at 4.2s". Moving one means
//      rewriting the durations of the clips on BOTH sides, and moving several
//      means every clip's duration is a function of two cuts. `cutsToDurations`
//      is that conversion, and doing it by hand at the call site is how you get
//      a sequence that drifts a few milliseconds longer on every press.
//
//   2. **BEATS CLUSTER, CUTS MUST NOT.** The nearest beat to two consecutive
//      cuts is very often the same beat, and putting them both there is a clip
//      of zero length: a picture that never appears, in an edit that still
//      claims to have it. Every cut therefore carries a running floor and may
//      only land after the one before it plus a minimum hold.
//
//   3. **A CUT NOWHERE NEAR A BEAT IS LEFT ALONE.** A cut two seconds from the
//      nearest beat is not a cut anybody meant to be on that beat, and dragging
//      it there is this feature rewriting the edit rather than tightening it.
//      `reachMs` is how close is close enough.
//
// Checked by `tests/autoframe_check.py` under node.

// How far a cut may travel to reach a beat, in ms. About a third of a second —
// far enough to catch a cut somebody placed by eye and meant to be on the beat,
// short enough that a cut deliberately placed off it stays off it.
export const REACH_MS = 700;

/**
 * Every beat of every audible track, in TIMELINE time, sorted.
 *
 * ⚠ BEAT TIMES ARE IN FILE TIME, like `offset_ms` — they describe the recording,
 * not the timeline. One file can be several clips at different places with a
 * different shift each, so each beat has to be walked onto the timeline through
 * the clip that actually plays it, and beats whose audio was trimmed away are
 * dropped. This is the same walk `captions.clip_lines` makes on the server, and
 * for exactly the same reason.
 *
 * A MUTED track contributes nothing: you cannot hear it, so cutting to it is
 * cutting to a rhythm that is not in the film.
 *
 * @param tracks    the audio clips on the timeline
 * @param analyses  upload_id → { beats } from `useAudioAnalysis`
 */
export function beatMarks(tracks, analyses) {
  const marks = [];
  for (const track of tracks || []) {
    if (!track || track.muted) continue;
    const beats = analyses?.[track.upload_id]?.beats || [];
    const start = track.start_ms || 0;
    const offset = track.offset_ms || 0;
    const trim = track.trim_ms;
    for (const b of beats) {
      // Trimmed off the head, or off the tail: either way that beat is not
      // audible on this clip and must not be a cut point.
      if (b < offset) continue;
      if (trim != null && b > trim) continue;
      marks.push(start + (b - offset));
    }
  }
  marks.sort((a, b) => a - b);
  return marks;
}

/**
 * The nearest mark to `ms`, and how far away it is.
 *
 * Binary search rather than a scan: this is called once per cut and the marks
 * of a three-minute track number in the hundreds, so the naive version is
 * quadratic in the length of the edit for no reason.
 */
export function nearestMark(marks, ms) {
  if (!marks.length) return null;
  let lo = 0;
  let hi = marks.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (marks[mid] < ms) lo = mid + 1;
    else hi = mid;
  }
  // `lo` is the first mark at or after `ms`; the answer is that one or the one
  // before it. Checking both is what makes this the NEAREST rather than the next.
  let best = marks[lo];
  let gap = Math.abs(marks[lo] - ms);
  if (lo > 0 && Math.abs(marks[lo - 1] - ms) <= gap) {
    best = marks[lo - 1];
    gap = Math.abs(marks[lo - 1] - ms);
  }
  return { at: best, gap };
}

/**
 * Where every cut should land.
 *
 * `starts[i] + durations[i]` is where cut `i` is now. The LAST cut is not in
 * the answer and is not moved: it is the end of the video rather than an edit
 * point, and pulling it onto a beat would change how long the whole thing runs
 * to no purpose at all.
 *
 * Pure — takes numbers, returns numbers, touches no browser API. That is what
 * lets the test run it under node against a click track at a known BPM.
 *
 * @returns { cuts: number[], moved: number } — the new cut times, and how many
 *          of them actually changed. Zero moved is a perfectly good outcome and
 *          the caller should say so rather than writing an identical sequence.
 */
export function planBeatCuts(durations, marks, { reachMs = REACH_MS, minMs = 100 } = {}) {
  const cuts = [];
  let moved = 0;
  let floor = 0; // nothing may land at or before this — see rule 2
  let at = 0;
  for (let i = 0; i < durations.length - 1; i++) {
    at += durations[i];
    const near = marks.length ? nearestMark(marks, at) : null;
    const wanted = near && near.gap <= reachMs ? near.at : at;
    const to = Math.max(wanted, floor + minMs);
    if (Math.round(to) !== Math.round(at)) moved += 1;
    cuts.push(to);
    floor = to;
  }
  return { cuts, moved };
}

/**
 * Cut times back into clip durations — the conversion rule 1 is about.
 *
 * Clip `i` runs from cut `i-1` to cut `i`; the first opens at 0 and the last
 * keeps the hold it had, because there is no cut after it to measure against.
 * Every duration is floored at `minMs`, so nothing this produces can be a clip
 * that never appears.
 */
export function cutsToDurations(durations, cuts, { minMs = 100 } = {}) {
  return durations.map((was, i) => {
    const from = i === 0 ? 0 : cuts[i - 1];
    const to = i < cuts.length ? cuts[i] : from + was;
    return Math.max(minMs, Math.round(to - from));
  });
}
