/**
 * preset_util.js — the maths every animation preset is built out of.
 *
 * ⚠ THE ONE RULE THIS FILE EXISTS TO KEEP: A PRESET IS A KEYFRAME MACRO AND
 * NOTHING ELSE. It writes keys onto properties the scene model already
 * animates and then gets out of the way. Nothing is stored saying which preset
 * ran, the renderers have never heard of any of them, and there is no second
 * evaluator anywhere that knows what "bounce" means.
 *
 * That is the whole reason forty presets cost what five did. Because a preset
 * is only keys:
 *   · it exports correctly the day it is written — `animatic_render.py` already
 *     interpolates these properties and `tests/render_parity.py` already proves
 *     the two languages agree about them;
 *   · the timeline shows its diamonds, so you can SEE the animation and drag it;
 *   · every key it wrote can be moved, retimed or deleted afterwards, because
 *     they are ordinary keys;
 *   · undo treats applying one as a single document edit, like any other;
 *   · and adding the forty-first costs a row in a table, not a renderer change.
 *
 * The cost is that a preset is WRITE-ONLY: nothing records which one was
 * applied, so no picker can show a "current" preset. That is the honest answer
 * — after you have dragged one of its keys, there is no current preset.
 *
 * ---------------------------------------------------------------------------
 * ⚠ EVERY PRESET ANIMATES *RELATIVE TO THE CLIP'S OWN RESTING VALUES*.
 * ---------------------------------------------------------------------------
 * A pop on a caption the user has already set to 130% must end at 130%, not
 * snap it back to 100%; a spin on a title deliberately hung at 8° must come to
 * rest at 8°. So `build` is handed the clip's resting `x`, `y`, `scale` and
 * `rotation` and works outwards from them, and `applyPreset` writes those same
 * resting values back onto the clip. A preset is an ANIMATION, never a restyle.
 *
 * ---------------------------------------------------------------------------
 * ⚠ THE RESTING VALUE IS WHERE THE MOVE ENDS, NEVER WHERE IT STARTS.
 * ---------------------------------------------------------------------------
 * `sceneAt` reads a property's resting value everywhere the keys do not reach,
 * so a move that finished at 1.08 over a resting 1 snaps back the instant the
 * last key passes. Same rule `push_in`, `captionPush` and the original
 * `applyTextPreset` all keep, and the same bug in all four if it is dropped.
 *
 * ---------------------------------------------------------------------------
 * ⚠ NO NEW EASING CURVES, ON PURPOSE — OVERSHOOT IS EXTRA KEYS.
 * ---------------------------------------------------------------------------
 * `EASINGS` in `scene.js` is a short closed list twinned in `animatic_render.py`,
 * and a bounce could have been a sixth entry. It is not one, because that would
 * put a new curve in two languages plus the parity fixture before a single
 * preset could ship. A bounce written as four keys on `ease-out` is the same
 * picture, needs nothing from either renderer, and every one of its keys is one
 * a person can see on the timeline and drag. Presets stay free that way.
 */

// Six places, the same PRECISION the scene model rounds to. Two languages doing
// the same float maths drift in the last bits, and the parity test compares
// them — so a key value is rounded here rather than left to chance.
export function round6(n) {
  return Math.round(n * 1e6) / 1e6;
}

/**
 * What a property is ALLOWED to hold, matched to the Pydantic field it lands in.
 *
 * ⚠ THESE ARE THE SERVER'S OWN BOUNDS, not a stylistic preference. A key value
 * outside them is a 422 on the next autosave — which is not "the animation
 * looked wrong", it is a project that will not save. A preset applied to a
 * caption already styled at 300% could reach one by ordinary multiplication, so
 * every key goes through `clampValue` rather than being trusted.
 *
 * ⚠ MIRRORS `AnimaticTextClip` / `AnimaticFrame` / `AnimaticShape` in
 * `server/schemas.py`. Loosen one there and it must be loosened here, or the
 * app refuses a value the server would have taken.
 */
export const TEXT_BOUNDS = {
  opacity: [0, 1],
  // Deliberately outside 0–1: a caption may be run off the edge of the frame on
  // purpose, and a travelling preset is exactly that.
  x: [-1, 2],
  y: [-1, 2],
  // `gt=0` on the wire, so the floor is a small positive number rather than 0 —
  // a scale of exactly 0 is a 422, and it is also a caption nobody can see.
  scale: [0.01, 16],
  rotation: [-360, 360],
};

/**
 * ⚠ A FRAME'S BOUNDS ARE NOT A CAPTION'S, AND THE DIFFERENCE IS A 422.
 * `AnimaticFrame.scale` stops at 10 where `AnimaticTextClip.scale` goes to 16,
 * and its x/y run wider (−2…3) because a picture is panned behind the frame
 * rather than placed in it. Clamping a picture with the caption's table would
 * let a preset applied to an already-huge still reach 12 and fail the save;
 * clamping a caption with the picture's would refuse a value the server takes.
 * Two tables, each matching its own Pydantic model.
 */
export const FRAME_BOUNDS = {
  opacity: [0, 1],
  x: [-2, 3],
  y: [-2, 3],
  scale: [0.01, 10],
};

export function clampValue(prop, value, bounds = TEXT_BOUNDS) {
  const range = bounds[prop];
  const n = Number(value);
  if (!Number.isFinite(n)) return range ? range[0] : 0;
  if (!range) return round6(n);
  return round6(Math.max(range[0], Math.min(range[1], n)));
}

/**
 * How long the in and out beats are, once the clip has had its say.
 *
 * A caption has to be READABLE between its two beats. If in + out would eat the
 * whole clip they are squeezed to `maxShare` each, leaving the rest of it held —
 * so applying a preset to a clip shorter than its beats gives a fast animation
 * rather than one whose keys sit past the end of the clip.
 */
export function beatsFor(clip, options = {}, defaults = {}) {
  const durationMs = Math.max(100, Number(clip?.duration_ms) || 2000);
  const maxShare = defaults.maxShare ?? 0.4;
  const budget = Math.floor(durationMs * maxShare);
  return {
    durationMs,
    inMs: clampMs(options.inMs ?? defaults.inMs ?? 400, 0, budget),
    outMs: clampMs(options.outMs ?? defaults.outMs ?? 400, 0, budget),
  };
}

function clampMs(value, low, high) {
  const n = Number(value);
  if (!Number.isFinite(n)) return low;
  return Math.max(low, Math.min(high, Math.round(n)));
}

/**
 * The four keys of a fade: up over `inMs`, HELD, down over `outMs`.
 *
 * The middle pair is what makes it a hold rather than a triangle — without them
 * a two-second caption would spend the whole two seconds fading.
 */
export function fadeTrack(inMs, outMs, durationMs, { from = 0 } = {}) {
  const keys = [{ t: 0, v: from, ease: "ease-out" }];
  keys.push({ t: inMs, v: 1, ease: "linear" });
  if (outMs > 0) {
    keys.push({ t: Math.max(inMs, durationMs - outMs), v: 1, ease: "ease-in" });
    keys.push({ t: durationMs, v: 0, ease: "linear" });
  }
  return keys;
}

/**
 * A move that arrives: `from` → `rest` over the in beat, and then nothing.
 *
 * The second key carries `linear` rather than the curve, because an ease is a
 * property of the segment STARTING at the key it sits on — so the curve belongs
 * to the first one and the second is just where the movement stops.
 */
export function arriveTrack(from, rest, inMs, ease = "ease-out") {
  return [
    { t: 0, v: from, ease },
    { t: Math.max(1, inMs), v: rest, ease: "linear" },
  ];
}

/**
 * An arrival that overshoots and settles — a bounce, a spring, a pop.
 *
 * `stops` is the list of values it passes through after `from`, as a fraction of
 * the in beat each: `[[0.55, 1.12], [0.8, 0.97], [1, 1]]` reads "112% at 55% of
 * the way in, 97% at 80%, home at the end". Written as fractions so one shape of
 * bounce serves every beat length, and so a preset reads as its own curve.
 */
export function settleTrack(from, stops, inMs) {
  const beat = Math.max(1, inMs);
  const keys = [{ t: 0, v: from, ease: "ease-out" }];
  stops.forEach(([at, value], i) => {
    keys.push({
      t: Math.round(beat * at),
      v: value,
      // The last stop is where it comes to rest, so nothing follows it to be
      // eased into. Everything before it eases out of its own overshoot.
      ease: i === stops.length - 1 ? "linear" : "ease-in-out",
    });
  });
  return dedupeByTime(keys);
}

/**
 * A wobble around `base` that runs for as long as it is given.
 *
 * `cycles` complete swings across `lengthMs`, `amp` either side of the base, and
 * it always ENDS on the base so the value the clip rests at is the value it is
 * left at. Used for the loop presets (pulse, sway, float) and, with a short
 * `lengthMs`, for the impact ones (shake, jitter).
 *
 * ⚠ `decay` IS WHAT MAKES A SHAKE A SHAKE. With it, each swing is smaller than
 * the last and the thing settles; without it, it rattles at full strength for
 * ever, which reads as a fault rather than an effect.
 */
export function wobbleTrack(base, amp, cycles, lengthMs, { decay = 1, start = 0 } = {}) {
  const length = Math.max(1, Math.round(lengthMs));
  // Four keys a cycle — up, back, down, back — is the coarsest sampling that
  // still reads as a smooth swing once `ease-in-out` is between the points.
  const steps = Math.max(2, Math.round(cycles * 4));
  const keys = [];
  for (let i = 0; i <= steps; i += 1) {
    const u = i / steps;
    const fade = decay >= 1 ? 1 : decay ** (u * steps);
    const swing = Math.sin(u * cycles * Math.PI * 2) * amp * fade;
    keys.push({
      t: Math.round(start + u * length),
      // The last key is pinned to the base exactly, rather than left to whatever
      // the sine happens to be — an animation that ends 0.4% off its resting
      // value snaps that 0.4% the moment the last key passes.
      v: i === steps ? base : base + swing,
      ease: "ease-in-out",
    });
  }
  return dedupeByTime(keys);
}

/** Merge two tracks for the same property, later keys winning on a tie. */
export function mergeTracks(...tracks) {
  return dedupeByTime(tracks.flat().filter(Boolean));
}

/**
 * One key per moment, in time order.
 *
 * Two keys at the same millisecond is not an animation the timeline can draw or
 * a person can grab, and `valueAt` on both sides would have to pick one anyway.
 * The LAST one wins, so a track built by laying an overshoot over a fade ends up
 * with the overshoot's value where they land together.
 */
function dedupeByTime(keys) {
  const byTime = new Map();
  for (const key of keys) {
    if (!key) continue;
    byTime.set(Math.max(0, Math.round(key.t ?? 0)), key);
  }
  return [...byTime.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, key]) => ({ ...key, t }));
}

/**
 * Turn the tracks a preset built into the PATCH the editor takes.
 *
 * Returns the object to hand to `onChange(id, patch)` — exactly like every
 * function in `keyframes.js`, so the undo stack treats applying a preset as one
 * ordinary document edit.
 *
 * ⚠ IT CLEARS EVERY `owned` TRACK, INCLUDING THE ONES THIS PRESET DOES NOT
 * WRITE. Going from "Spin" to "Fade" has to REMOVE the rotation track, not leave
 * it running underneath — a caption that still spins after you chose the preset
 * that does not spin is the fault that makes a picker feel broken. Anything
 * outside `owned` is left exactly as it was, so a preset can never eat a track
 * it does not understand.
 */
export function applyPreset(clip, tracks, owned, rest, bounds = TEXT_BOUNDS) {
  const keyframes = { ...(clip?.keyframes || {}) };
  for (const prop of owned) delete keyframes[prop];
  for (const [prop, keys] of Object.entries(tracks || {})) {
    if (!keys || !keys.length) continue;
    keyframes[prop] = keys.map((key) => ({
      t: Math.max(0, Math.round(key.t ?? 0)),
      v: clampValue(prop, key.v, bounds),
      // An unrecognised ease folds to `linear` in both renderers anyway; folding
      // it HERE means the document never carries one, so the two sides cannot
      // fall back differently. Same rule the clip kinds and transitions follow.
      ease: EASE_NAMES.has(key.ease) ? key.ease : "linear",
    }));
  }
  const patch = { keyframes };
  for (const [prop, value] of Object.entries(rest || {})) {
    patch[prop] = clampValue(prop, value, bounds);
  }
  return patch;
}

// ⚠ MIRRORS `EASINGS` in `scene.js`, which is itself twinned in
// `animatic_render.py`. A set rather than an import of the array only because
// this is a membership test on every key of every preset.
const EASE_NAMES = new Set(["linear", "hold", "ease-in", "ease-out", "ease-in-out"]);

/** Read a number off a clip, falling back when it is missing or nonsense. */
export function numberOr(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}
