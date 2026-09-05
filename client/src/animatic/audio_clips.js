// audio_clips.js — an audio track is a CLIP on the timeline, and this is what
// you can do to one: find the one under the playhead, cut it in two, trim its
// edges, and crossfade it into the clip next to it.
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

// --- Crossfades -------------------------------------------------------------
// A CROSSFADE IS TWO FADES THAT OVERLAP, and that is the whole design.
//
// The picture has an `AnimaticTransition` object because the picture sequence is
// a CHAIN: a cut there is a position between two links, so it needs something to
// be anchored to. Audio clips are placed absolutely on a timeline and the
// exporter mixes whatever happens to overlap — so two clips that overlap, one
// fading out while the other fades in, ALREADY are a crossfade, at both ends of
// the app, with no new object, no new field on the wire beyond the curve and no
// new render path. Dropping "Constant Power" on a cut writes four fields across
// two clips, and the three library entries differ only in the curve they write.
//
// ⚠ `acrossfade` IS NOT USED and cannot be: it concatenates two streams, which
// would shorten the timeline. That is the same objection that made picture
// transitions boundary-local — see the top of `transitions.js`.
//
// ---------------------------------------------------------------------------
// ⚠ AND THIS IS WHERE AUDIO DIVERGES FROM THE PICTURE, ON PURPOSE
// ---------------------------------------------------------------------------
// A picture transition refuses to overlap its two shots because the timeline
// would get SHORTER, and every cut position, ripple and caption timed against a
// cut would move. None of that is true here: an audio clip's `start_ms` is
// absolute, so pulling the incoming clip half a second earlier moves that clip
// and nothing else. So audio does the real thing — it eats into the MEDIA
// HANDLES either side of the cut, which is what Premiere does, and what makes
// the two clips genuinely audible at once instead of dipping through the cut:
//
//     head handle = `offset_ms`      the file sitting before the incoming clip
//     tail handle = `clipRoomMs`     the file left after the outgoing clip
//
// With no handle on either side there is nothing to overlap WITH, and calling
// the result a crossfade would be a lie — so that case degrades to a fade down
// and back up across the cut, and says so. Premiere refuses this one outright
// as "insufficient media"; the difference is that this still leaves you the
// fades you asked for, which you could have dragged by hand anyway.

/** Premiere's default audio transition, and the length a dropped one asks for. */
export const DEFAULT_CROSSFADE_MS = 1000;

/** How much file sits BEFORE a clip — what its head can be pulled back into. */
function headHandleMs(track) {
  return Math.max(0, Math.round(track?.offset_ms || 0));
}

/** How much file sits AFTER a clip — what its tail can be pulled out into. */
function tailHandleMs(track) {
  const room = clipRoomMs(track);
  // No measured duration means no known handle. `clipRoomMs` returns Infinity
  // there so a right-edge DRAG stays unbounded, but a crossfade has to commit to
  // a number — and inventing one would extend a clip past the end of its file.
  if (room === Infinity) return 0;
  // ⚠ AGAINST THE UNCLAMPED PLAY LENGTH. `trackPlayMs` shortens a clip that runs
  // off the end of the VIDEO, and that is not the file running out — counting it
  // as a handle would offer to grow a clip into audio the export then cuts off.
  return Math.max(0, room - trackPlayMs(track));
}

// ---------------------------------------------------------------------------
// ⚠ EVERYTHING BELOW WORKS IN FILE TIME, AND TAKES NO `totalMs` ON PURPOSE
// ---------------------------------------------------------------------------
// `trackPlayMs(track, totalMs)` shortens a clip that runs off the end of the
// video, and that clamp is a RENDER-TIME fact: `fade_window` re-applies it on
// export every time, and the timeline draws its clips without it. Letting it in
// here would be a quiet disaster — `trim_ms` is written from a clip's play
// length, so a crossfade laid on a clip hanging past the last frame would BAKE
// the video's current length into that clip, and the audio you had hanging there
// on purpose would be gone for good the moment you dropped a preset on it.
//
// Which is also why these read what the timeline draws rather than what the
// export hears: you crossfade the bars you can see.

/**
 * The fade at one END of one clip, capped at what the clip can carry.
 *
 * `fadeWindow` scales an over-long fade rather than refusing it, so this is not
 * a safety check — it is what stops a 1s crossfade dropped on a 400ms clip
 * silently rescaling the fade at that clip's OTHER end as well.
 */
export function fadeEndPatch(track, side, ms, curve) {
  const want = Math.max(0, Math.min(Math.round(ms), trackPlayMs(track)));
  return side === "in"
    ? { fade_in_ms: want, fade_in_curve: curve }
    : { fade_out_ms: want, fade_out_curve: curve };
}

/**
 * Lay a crossfade of `ms` over the junction between two clips on one lane.
 *
 * Returns `{ ok, patches, appliedMs, overlapped, reason }`. `patches` is keyed
 * by `clipId`, so the caller writes BOTH clips in ONE state update and therefore
 * in one undo step — the only reading under which a crossfade is a thing you
 * laid rather than two things you happened to do.
 *
 * THE FOUR CASES, in the order they are decided:
 *
 *   1. A GAP between them. Refused: there is no junction to put a crossfade on,
 *      and closing the gap by moving a clip is not what dropping a preset asked
 *      for. The caller turns this into a plain fade, which is the useful answer.
 *   2. ALREADY OVERLAPPING BY AT LEAST `ms`. The overlap is used as it stands and
 *      nothing moves. ⚠ Deliberately NOT shrunk to `ms`: that overlap was laid by
 *      hand, and a shorter crossfade would leave both clips at full level for the
 *      rest of it — audibly doubled, which is worse than a crossfade longer than
 *      the preset's nominal length.
 *   3. OVERLAPPING BY LESS, OR BUTT-CUT. Grown into the handles, and ⚠ THE
 *      OUTGOING CLIP'S TAIL IS SPENT FIRST, which is the one real decision in
 *      this function. Letting the outgoing clip play on over the head of the
 *      incoming one moves NOTHING: clip B is heard at exactly the moment it was
 *      before, clip A simply lingers underneath it. Pulling clip B earlier
 *      instead would shift its content in time — and a voice cue that has to
 *      land on a picture cut does not want moving half a second because you
 *      dropped a crossfade on it. So the head handle is the FALLBACK, used only
 *      for whatever length the tail could not cover.
 *
 *      (This is where "centre it on the cut", which is Premiere's default
 *      alignment, is deliberately not copied. Premiere centres because its
 *      transition is an OBJECT with a rectangle to draw and an alignment to
 *      pick; here the crossfade is two fades, so there is no rectangle whose
 *      position could look wrong — only clips that did or did not move.)
 *   4. NO HANDLES AT ALL. Nothing can overlap — two whole files butted together
 *      is the everyday way to get here — so it dips through the cut instead:
 *      half the length out of the tail of the outgoing clip, half into the head
 *      of the incoming one. `overlapped: false` is how the caller knows to say so
 *      rather than claim a crossfade it did not manage to make.
 */
export function crossfadePatch(outgoing, incoming, ms, curve) {
  if (!outgoing || !incoming) return { ok: false, reason: "missing" };
  const out = trackWindow(outgoing);
  const inc = trackWindow(incoming);
  const wanted = Math.max(0, Math.round(ms));
  const overlap = out.endMs - inc.startMs;
  if (overlap < 0) return { ok: false, reason: "gap" };

  const tail = tailHandleMs(outgoing);
  const head = headHandleMs(incoming);

  // ⚠ HOW FAR THE CROSSFADE GROWS IS SETTLED, NOT SOLVED. The overlap can be no
  // longer than either clip covering it — and how long each clip IS depends on
  // how far it grew, which depends on the overlap. So this walks down to the
  // fixed point instead of doing the algebra: each pass either accepts the
  // length or shortens it, so it terminates, and the bound makes that a fact
  // rather than a hope. Without it, a 1s crossfade dropped against a 400ms clip
  // stretched the outgoing clip a full second and left 600ms where BOTH were at
  // full level — a doubled mix, which is the one outcome ruled out above.
  let target = Math.max(overlap, Math.min(wanted, overlap + tail + head));
  let growOut = 0;
  let growIn = 0;
  for (let pass = 0; pass < 8; pass++) {
    const need = Math.max(0, target - overlap);
    // The tail first, then the head for the remainder — see case 3 above.
    growOut = Math.min(need, tail);
    growIn = need - growOut;
    const cover = Math.min(out.playMs + growOut, inc.playMs + growIn);
    if (target <= cover) break;
    target = cover;
  }

  const outPlay = out.playMs + growOut;
  const inPlay = inc.playMs + growIn;
  // Capped at the shorter of the two clips it joins — the same rule a picture
  // transition follows, so a crossfade can never eat a whole clip. The loop has
  // already made this the identity in every case it settled; it stands as the
  // authority so a clip nested entirely inside its neighbour (which nothing here
  // creates, but a hand-edited project can carry) still cannot double the mix.
  const applied = Math.min(Math.max(overlap + growOut + growIn, 0), outPlay, inPlay);

  const moved = {};
  if (growOut > 0) moved[clipId(outgoing)] = { trim_ms: outPlay };
  if (growIn > 0) {
    // The same three fields `trimClipStart` moves together, in the other
    // direction: it makes a clip start later, later in the file and shorter, so
    // this makes it start earlier, earlier in the file and longer. Move any two
    // of the three and the audio slides under the edge instead of the edge
    // uncovering more of it.
    moved[clipId(incoming)] = {
      start_ms: Math.max(0, inc.startMs - growIn),
      offset_ms: headHandleMs(incoming) - growIn,
      trim_ms: inPlay,
    };
  }

  if (applied <= 0) {
    const half = Math.round(wanted / 2);
    return {
      ok: true,
      overlapped: false,
      appliedMs: half,
      patches: {
        [clipId(outgoing)]: {
          ...(moved[clipId(outgoing)] || {}),
          ...fadeEndPatch(outgoing, "out", half, curve),
        },
        [clipId(incoming)]: {
          ...(moved[clipId(incoming)] || {}),
          ...fadeEndPatch(incoming, "in", half, curve),
        },
      },
    };
  }

  // ⚠ BOTH FADES SPAN THE WHOLE OVERLAP, not part of it. Anywhere inside the
  // overlap where only one of them is ramping, both clips are at full level and
  // the mix doubles — the one thing a crossfade must never do.
  return {
    ok: true,
    overlapped: true,
    appliedMs: applied,
    patches: {
      [clipId(outgoing)]: {
        ...(moved[clipId(outgoing)] || {}),
        fade_out_ms: applied,
        fade_out_curve: curve,
      },
      [clipId(incoming)]: {
        ...(moved[clipId(incoming)] || {}),
        fade_in_ms: applied,
        fade_in_curve: curve,
      },
    },
  };
}

/**
 * WHERE A DROPPED CROSSFADE LANDS: `{ clip, side, neighbour }`, or null.
 *
 * ⚠ THE NEAREST EDGE WINS, and a drop in the middle of a clip is not refused. A
 * crossfade is a thing you put on a cut, and at any zoom where a clip is a few
 * pixels wide "you missed the cut" would be the answer to most honest attempts —
 * so the half of the clip you let go over is the end you meant.
 *
 * `neighbour` is the clip on the other side of that end, and only if it actually
 * TOUCHES: one across a gap is not on this cut, and the caller turns that into a
 * plain fade rather than reaching over the silence for it.
 */
export function crossfadeTarget(clips, ms) {
  const clip = clipAt(clips, ms);
  if (!clip) return null;
  const { startMs, endMs } = trackWindow(clip);
  const side = ms - startMs <= endMs - ms ? "in" : "out";
  const id = clipId(clip);
  // The distance from this clip's chosen edge to another clip's facing edge.
  // Negative where they already overlap, which is why every comparison below is
  // on the absolute value: an overlap is a junction just as much as an abutment.
  const reach = (other) => {
    const w = trackWindow(other);
    return side === "in" ? startMs - w.endMs : w.startMs - endMs;
  };
  let neighbour = null;
  for (const other of clips || []) {
    if (clipId(other) === id) continue;
    const w = trackWindow(other);
    // On the right SIDE of us, and touching — no silence in between.
    const touching =
      side === "in"
        ? w.endMs >= startMs && w.startMs < startMs
        : w.startMs <= endMs && w.endMs > endMs;
    if (!touching) continue;
    // The closest one, so a lane of three pieces butted together crossfades the
    // cut you dropped on rather than the far one.
    if (!neighbour || Math.abs(reach(other)) < Math.abs(reach(neighbour))) {
      neighbour = other;
    }
  }
  return { clip, side, neighbour };
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

/**
 * HOW MANY AUDIO **FILES** ONE PROJECT MAY HOLD.
 *
 * ⚠ MIRRORS `API_MAX_ANIMATIC_AUDIO_TRACKS` ON THE SERVER, and it is FILES, not
 * clips: the razor and the looped music bed both make many clips out of one
 * file and neither costs anything here. `MAX_ANIMATIC_AUDIO_CLIPS` is the other
 * cap.
 *
 * ⚠ IT LIVES HERE RATHER THAN IN THE EDITOR BECAUSE THE SOUND PASS NEEDS IT
 * TOO. It used to be a private constant in `AnimaticEditor.jsx`, so
 * `sound_pass.js` could not see the real room and invented a number of its own
 * (a flat ten) — which is how a fourteen-shot board was told "one pass fetches
 * at most 10 different sounds" while the project had room for six more. One
 * ceiling, in the one file both sides import.
 */
export const MAX_AUDIO_FILES = 48;

/** How many DISTINCT files these tracks read — the number the cap counts. */
export function audioFileCount(tracks) {
  return new Set((tracks || []).map((t) => t.upload_id).filter(Boolean)).size;
}

/**
 * EXACTLY WHAT AN AUDIO CLIP LOOKS LIKE WHEN IT IS SAVED — and the last place a
 * bad number can be stopped before it costs the user their project.
 *
 * ⚠ THIS EXISTS BECAUSE ONE FIELD ON ONE CLIP SILENTLY KILLED EVERY SAVE. The
 * soundtrack pass wrote `trim_ms: 0` meaning "no trim"; the schema says trim is
 * either absent or **at least 100** (`AnimaticAudio.trim_ms`, `ge=100`). So the
 * whole document was refused with a 422 — not the clip, the DOCUMENT — and the
 * autosave re-sent the same rejected body forever. Reported from the screen:
 * sound and music laid down at night, project opened blank in the morning, with
 * a wall of raw validation JSON along the bottom of the editor.
 *
 * ⚠ SO IT IS A CLAMP AS WELL AS A WHITELIST, and that is the difference between
 * this and `assetForSave` / `frameForSave`. Those two exist so a field is not
 * DROPPED. This one also exists so a field cannot be POISONED: every number is
 * forced into the range the schema accepts, on the way out, whatever put it
 * there. A future field written wrong by some new pass costs that field, never
 * the project.
 *
 * `url` is deliberately absent: the server fills it on read and ignores it on
 * write, so sending it back would store a path that goes stale.
 *
 * `tests/audio_save_contract_check.py` compares this list against
 * `AnimaticAudio` and drives it with values no clip should ever carry.
 */
export function audioForSave(track) {
  const t = track || {};
  const num = (v, fallback = 0) => (Number.isFinite(Number(v)) ? Number(v) : fallback);
  const ms = (v, lo, hi) => Math.max(lo, Math.min(hi, Math.round(num(v))));
  // ⚠ THE ONE THAT BROKE IT. `null` is "play the whole file" and is what the
  // schema means by absent; anything shorter than the floor is not a trim, it
  // is a number somebody meant as "none". Both become null rather than 0.
  const rawTrim = Math.round(num(t.trim_ms));
  const trim = rawTrim >= MIN_CLIP_MS ? rawTrim : null;
  const curve = (v) => (["linear", "power", "exponential"].includes(v) ? v : "linear");
  const db = (v) => Math.max(-12, Math.min(12, num(v)));
  return {
    id: t.id || "",
    upload_id: t.upload_id || "",
    layer_id: t.layer_id || "",
    group_id: t.group_id || "",
    filename: t.filename || "",
    duration_ms: Math.max(0, Math.round(num(t.duration_ms))),
    start_ms: Math.max(0, Math.round(num(t.start_ms))),
    offset_ms: Math.max(0, Math.round(num(t.offset_ms))),
    trim_ms: trim,
    volume: Math.max(0, Math.min(2, num(t.volume, 1))),
    muted: Boolean(t.muted),
    fade_in_ms: ms(t.fade_in_ms, 0, 60000),
    fade_out_ms: ms(t.fade_out_ms, 0, 60000),
    fade_in_curve: curve(t.fade_in_curve),
    fade_out_curve: curve(t.fade_out_curve),
    eq_low: db(t.eq_low),
    eq_mid: db(t.eq_mid),
    eq_high: db(t.eq_high),
    role: t.role || "",
    // 1.0 is "never ducks", and the schema's floor is 0.05 — a 0 written by
    // something that meant "off" would refuse the save exactly as trim did.
    duck_to: Math.max(0.05, Math.min(1, num(t.duck_to, 1))),
    duck_target: t.duck_target || "",
  };
}
