/**
 * Text in/out animations — as KEYFRAME MACROS, and nothing else.
 *
 * ⚠ THE ONE RULE OF THIS FILE: a preset writes keys onto the properties the
 * scene model already animates (`opacity`, `x`, `y`) and then gets out of the
 * way. It is not stored on the clip, the renderers have never heard of it, and
 * there is no second evaluator anywhere that knows what "rise" means.
 *
 * That is deliberate, and it is the difference between this and every "text
 * animation" feature that has to be rebuilt in the exporter as well. Because a
 * preset is only keys:
 *   · it exports correctly the day it is written — `animatic_render.py` already
 *     interpolates those three properties, and `render_parity.py` already
 *     proves the two sides agree about them;
 *   · the timeline shows its diamonds, so you can SEE the animation and drag it;
 *   · every key it wrote can be edited, moved or deleted afterwards, because
 *     they are ordinary keys;
 *   · undo treats applying one as a single document edit, like any other.
 *
 * The cost is that a preset is write-only: nothing records which one was
 * applied, so the picker cannot show a "current" preset. That is the honest
 * answer — after you drag one of its keys, there is no current preset.
 *
 * MOVEMENT NEEDS FREE PLACEMENT. In flow placement a caption is stacked into
 * its zone and x/y are resolved but unused (see `textPlace` in scene.js), so a
 * preset that slides would animate nothing and the monitor would be lying about
 * the export. A moving preset therefore switches the clip to `place: "free"` at
 * the position it is already occupying, which is why each entry carries the
 * `moves` flag.
 */

import { TEXT_DEFAULTS, textPlace } from "./scene.js";

/** How long the in and out beats are, unless the caller says otherwise. */
export const DEFAULT_IN_MS = 400;
export const DEFAULT_OUT_MS = 400;
// A caption has to be readable between its two beats. If in + out would eat the
// whole clip they are squeezed to two fifths each, leaving a fifth of it held.
const MAX_BEAT_SHARE = 0.4;

/**
 * The distance a sliding preset travels, as a fraction of the frame.
 *
 * Small on purpose: a title that flies in from off-screen is a different (and
 * much worse) effect than one that settles into place. 7% of the frame reads as
 * movement at any size without the caption ever being somewhere it isn't meant
 * to be.
 */
const TRAVEL = 0.07;

/**
 * The presets, in the order they are offered.
 *
 * `moves` says the preset animates position and therefore needs free placement.
 * `build` returns the keyframe tracks it owns, given the clip's resting x/y and
 * the two beat lengths — it never touches a track it does not own.
 */
export const TEXT_PRESETS = [
  {
    id: "none",
    label: "None",
    hint: "Remove any in/out animation and hold the caption still.",
    moves: false,
    build: () => ({}),
  },
  {
    id: "fade",
    label: "Fade",
    hint: "Fades up, holds, fades away.",
    moves: false,
    build: ({ inMs, outMs, durationMs }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
    }),
  },
  {
    id: "rise",
    label: "Rise",
    hint: "Drifts up into place as it fades in.",
    moves: true,
    build: ({ inMs, outMs, durationMs, y }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      y: [
        { t: 0, v: round(y + TRAVEL), ease: "ease-out" },
        { t: inMs, v: round(y), ease: "linear" },
      ],
    }),
  },
  {
    id: "drop",
    label: "Drop",
    hint: "Settles down into place as it fades in.",
    moves: true,
    build: ({ inMs, outMs, durationMs, y }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      y: [
        { t: 0, v: round(y - TRAVEL), ease: "ease-out" },
        { t: inMs, v: round(y), ease: "linear" },
      ],
    }),
  },
  {
    id: "slide",
    label: "Slide in",
    hint: "Slides in from the left as it fades in.",
    moves: true,
    build: ({ inMs, outMs, durationMs, x }) => ({
      opacity: fadeTrack(inMs, outMs, durationMs),
      x: [
        { t: 0, v: round(x - TRAVEL * 1.5), ease: "ease-out" },
        { t: inMs, v: round(x), ease: "linear" },
      ],
    }),
  },
];

export const TEXT_PRESET_IDS = TEXT_PRESETS.map((p) => p.id);

// Every property a preset is allowed to write. Applying one clears exactly
// these and leaves anything else on the clip alone — so a preset can never eat
// a track it doesn't understand.
const OWNED = ["opacity", "x", "y"];

function round(n) {
  return Math.round(n * 1e6) / 1e6;
}

/**
 * The four keys of a fade: up over `inMs`, held, down over `outMs`.
 *
 * The middle pair is what makes it a HOLD rather than a triangle — without
 * them a two-second caption would spend the whole two seconds fading.
 */
function fadeTrack(inMs, outMs, durationMs) {
  const keys = [{ t: 0, v: 0, ease: "ease-out" }];
  keys.push({ t: inMs, v: 1, ease: "linear" });
  if (outMs > 0) {
    keys.push({ t: Math.max(inMs, durationMs - outMs), v: 1, ease: "ease-in" });
    keys.push({ t: durationMs, v: 0, ease: "linear" });
  }
  return keys;
}

/**
 * Apply a preset to a caption. Returns a PATCH — the object to hand to the
 * editor's `onChange(id, patch)` — exactly like every function in
 * `keyframes.js`, so the undo stack treats it as one ordinary edit.
 *
 * The beats are clamped against the clip's own length, so applying a preset to
 * a caption shorter than the beats gives a fast animation rather than one whose
 * keys sit past its end.
 */
export function applyTextPreset(clip, presetId, options = {}) {
  const preset = TEXT_PRESETS.find((p) => p.id === presetId);
  if (!preset) return {};

  const durationMs = Math.max(100, Number(clip?.duration_ms) || 2000);
  const budget = Math.floor(durationMs * MAX_BEAT_SHARE);
  const inMs = clamp(options.inMs ?? DEFAULT_IN_MS, 0, budget);
  const outMs = clamp(options.outMs ?? DEFAULT_OUT_MS, 0, budget);

  // The RESTING position — where the caption ends up, and what the movement is
  // measured from. Taken from the clip's stored value rather than from wherever
  // an earlier preset's first key happened to leave it, so applying two presets
  // in a row doesn't walk the caption up the frame.
  const x = numberOr(clip?.x, TEXT_DEFAULTS.x);
  const y = numberOr(clip?.y, TEXT_DEFAULTS.y);

  const tracks = preset.build({ inMs, outMs, durationMs, x, y });

  const keyframes = { ...(clip?.keyframes || {}) };
  for (const prop of OWNED) delete keyframes[prop];
  for (const [prop, keys] of Object.entries(tracks)) keyframes[prop] = keys;

  const patch = { keyframes };
  // The base values the keys resolve around. `opacity` is restored to 1 because
  // a preset that ends on a fade-out would otherwise leave the stored value at
  // whatever it was and the caption would be invisible the moment the preset is
  // removed again.
  patch.opacity = 1;
  patch.x = x;
  patch.y = y;
  // A moving preset needs free placement or it animates nothing. Switching HERE
  // rather than making the user do it first is what keeps a preset one click,
  // and it lands the caption where it already was — see TEXT_DEFAULTS.
  if (preset.moves && textPlace(clip) !== "free") patch.place = "free";
  return patch;
}

function clamp(value, low, high) {
  const n = Number(value);
  if (!Number.isFinite(n)) return low;
  return Math.max(low, Math.min(high, Math.round(n)));
}

function numberOr(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}
