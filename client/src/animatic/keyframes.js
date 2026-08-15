/**
 * Editing keyframes — the operations behind the ⏱ button.
 *
 * `scene.js` READS keyframes (what is this property at time t?). This file
 * WRITES them (put a key here, take that one away, stop animating this). They
 * are split because reading is mirrored in Python and writing is not: the
 * server never edits an animation, it only renders one. Nothing here has a twin
 * to stay in step with.
 *
 * Every function is pure and returns a PATCH — the object to hand to the
 * editor's `onChange(id, patch)` — rather than mutating a clip. That is what
 * lets the undo stack treat a keyframe edit as an ordinary document edit, which
 * is what a person means when they press Ctrl+Z after dragging one.
 *
 * ⚠ Key times are RELATIVE to the clip's own start. Callers pass `tRel`, not
 * the playhead. Getting this wrong makes an animation slide out from under its
 * clip the first time the clip is dragged — see the note in `scene.js`.
 */

import { valueAt } from "./scene.js";

// How close the playhead has to be to count as sitting ON a key. One frame at
// 60fps: tight enough that two keys can be a video frame apart, loose enough
// that clicking the diamond after scrubbing finds the key you can see.
export const KEY_TOLERANCE_MS = 16;

export function keysOf(clip, prop) {
  const track = clip?.keyframes?.[prop];
  return Array.isArray(track) ? [...track].sort((a, b) => (a.t ?? 0) - (b.t ?? 0)) : [];
}

/** Is this property animated? One key is a stored value, not an animation. */
export function isAnimatedProp(clip, prop) {
  return keysOf(clip, prop).length > 0;
}

/** The key the playhead is sitting on, or null. */
export function keyAt(clip, prop, tRel) {
  return keysOf(clip, prop).find((k) => Math.abs((k.t ?? 0) - tRel) <= KEY_TOLERANCE_MS) || null;
}

/** The nearest key before (`dir` -1) or after (`dir` +1) the playhead. */
export function neighbourKey(clip, prop, tRel, dir) {
  const keys = keysOf(clip, prop);
  if (dir < 0) {
    return [...keys].reverse().find((k) => (k.t ?? 0) < tRel - KEY_TOLERANCE_MS) || null;
  }
  return keys.find((k) => (k.t ?? 0) > tRel + KEY_TOLERANCE_MS) || null;
}

function patch(clip, prop, keys) {
  const next = { ...(clip.keyframes || {}) };
  if (keys.length) next[prop] = keys;
  else delete next[prop];
  return { keyframes: next };
}

/**
 * Put a key at `tRel`, or move the value of the one already there.
 *
 * `ease` is only applied to a NEW key. Re-typing a value must not quietly reset
 * a curve the user chose — the ease is edited through its own control.
 */
export function setKey(clip, prop, tRel, value, ease = "linear") {
  const keys = keysOf(clip, prop);
  const existing = keys.find((k) => Math.abs((k.t ?? 0) - tRel) <= KEY_TOLERANCE_MS);
  const next = existing
    ? keys.map((k) => (k === existing ? { ...k, v: value } : k))
    : [...keys, { t: Math.round(tRel), v: value, ease }].sort((a, b) => a.t - b.t);
  return patch(clip, prop, next);
}

/** Take the key at `tRel` away. Removing the last one stops the animation. */
export function removeKey(clip, prop, tRel) {
  const keys = keysOf(clip, prop).filter(
    (k) => Math.abs((k.t ?? 0) - tRel) > KEY_TOLERANCE_MS
  );
  return patch(clip, prop, keys);
}

/** Change one key's easing curve — the shape of the span that FOLLOWS it. */
export function setKeyEase(clip, prop, tRel, ease) {
  const keys = keysOf(clip, prop).map((k) =>
    Math.abs((k.t ?? 0) - tRel) <= KEY_TOLERANCE_MS ? { ...k, ease } : k
  );
  return patch(clip, prop, keys);
}

/** Drag a key along its clip. Times may go negative — the value simply holds. */
export function moveKey(clip, prop, fromT, toT) {
  const keys = keysOf(clip, prop);
  const moving = keys.find((k) => Math.abs((k.t ?? 0) - fromT) <= KEY_TOLERANCE_MS);
  if (!moving) return null;
  // Dropping a key onto another replaces it, rather than leaving two keys at one
  // instant where which of them wins depends on sort order.
  const rest = keys.filter(
    (k) => k !== moving && Math.abs((k.t ?? 0) - toT) > KEY_TOLERANCE_MS
  );
  return patch(
    clip,
    prop,
    [...rest, { ...moving, t: Math.round(toT) }].sort((a, b) => a.t - b.t)
  );
}

/**
 * Move EVERY key sitting at one instant, across all of the clip's properties.
 *
 * SHIFT-dragging a timeline diamond does this; a plain drag moves only the
 * property whose row was grabbed (`moveKey`). It is its own operation rather
 * than a loop at the call site because a Ken Burns push keyframes `scale` and
 * `x` at the same instant, and sliding the push along has to carry both — doing
 * it property by property from outside would leave the two halves of one move
 * at different times if any step failed to match.
 *
 * Returns null when nothing is there — a stale drag against a clip that has
 * changed underneath it — so the caller can do nothing rather than write an
 * empty patch.
 */
export function moveKeysAt(clip, fromT, toT) {
  const next = { ...(clip.keyframes || {}) };
  let moved = false;
  for (const [prop, track] of Object.entries(next)) {
    if (!Array.isArray(track)) continue;
    const patched = moveKey(clip, prop, fromT, toT);
    if (patched) {
      next[prop] = patched.keyframes[prop] || [];
      if (!next[prop].length) delete next[prop];
      moved = true;
    }
  }
  return moved ? { keyframes: next } : null;
}

/**
 * Start animating a property: one key at the playhead, holding today's value.
 *
 * One key alone changes nothing on screen — which is the point. Turning the
 * stopwatch on must not make the picture jump; it marks the value you have as
 * the value at this instant, and the animation appears when you move the
 * playhead and type a second one.
 */
export function enableProp(clip, prop, tRel, fallback) {
  const current = clip[prop] ?? fallback;
  return patch(clip, prop, [{ t: Math.round(tRel), v: current, ease: "linear" }]);
}

/**
 * Stop animating a property, keeping what is on screen RIGHT NOW.
 *
 * The stored base value is overwritten with the resolved one, so switching the
 * stopwatch off freezes the picture where you left it instead of snapping back
 * to whatever the value happened to be before the animation was added. That
 * snap is the single most annoying thing an inspector can do.
 */
export function disableProp(clip, prop, tRel, fallback) {
  const frozen = valueAt(clip, prop, tRel, clip[prop] ?? fallback);
  return { ...patch(clip, prop, []), [prop]: frozen };
}

/** Every animated property of a clip, for the timeline's diamond rows. */
export function animatedProps(clip) {
  return Object.entries(clip?.keyframes || {})
    .filter(([, track]) => Array.isArray(track) && track.length > 0)
    .map(([prop]) => prop);
}

// NB: there was a `keyTimes(clip)` here, returning every distinct key INSTANT
// merged across properties. It existed because the timeline drew one diamond
// per instant. It now draws one ROW PER PROPERTY — merging was what made two
// keys look like one — so nothing asked that question any more and it went
// rather than sit here describing a timeline that no longer exists.
