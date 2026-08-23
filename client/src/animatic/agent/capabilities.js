// capabilities.js — WHAT THE DIRECTOR IS ALLOWED TO SAY, built from the truth
// tables rather than typed out beside them.
//
// The Director writes an `EditPlan` (see `plan_schema.js`) naming transitions,
// effects, shapes and text treatments. Every one of those words has to be a word
// this build actually renders — and there is exactly one honest way to know
// that, which is to ASK the tables the renderers read:
//
//   `TRANSITION_KINDS`      transitions.js   ⚠ twinned in animatic_transitions.py
//   `EFFECT_KINDS`          scene.js         ⚠ twinned in animatic_effects.py
//   `SHAPE_KINDS`           shape_points.js  ⚠ twinned in animatic.py
//   `FADE_CURVES`           audio_mix.js     ⚠ twinned in animatic.py
//   `TEXT_PRESET_IDS`       text_presets.js
//   `EASINGS` / `ANIMATABLE`  scene.js
//
// ---------------------------------------------------------------------------
// ⚠ NOTHING IS WRITTEN OUT HERE THAT A TABLE ALREADY KNOWS.
// ---------------------------------------------------------------------------
// This is the same reasoning `fx_library.js` states at its top: the folders are
// a view, the kinds are the truth. A second list of "effects the AI may use"
// would be a list that goes stale the first time an effect is added — and going
// stale in the direction that HURTS, because the planner would keep proposing a
// kind the validator then drops, or would never propose a new kind at all. So
// the manifest is DERIVED, every time it is asked for, and the only thing this
// file adds on top is a one-line gloss per word so the model knows what the word
// MEANS. A gloss that goes missing is cosmetic; a kind that goes missing is not.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE — no React, no DOM, no editor import.
// ---------------------------------------------------------------------------
// Same rule as `scene.js`, `assets.js` and `shape_points.js`: a test has to be
// able to import it under node, and `tests/director_actions_check.py` does
// exactly that. Anything in here that reached for `window` would make the
// vocabulary untestable outside a browser, which is the half of it most worth
// testing.

// ⚠ A CYCLE, AND A DELIBERATE ONE: `actions.js` imports the caps table and the
// three text vocabularies from here, and this reads the verb list back off the
// registry there. ES modules resolve it because NOTHING AT MODULE LEVEL ON
// EITHER SIDE TOUCHES THE OTHER'S BINDINGS — `verbVocab()` is called from inside
// `capabilities()`, and `HOUSE_CAPS` is read from inside a validator. Keep it
// that way: a top-level `const X = verbVocab()` here would be read before
// `actions.js` had finished evaluating and would come out empty.
import { verbVocab } from "./actions.js";
import { FADE_CURVE_INFO, FADE_CURVES } from "../audio_mix.js";
import { EFFECT_INFO } from "../fx_library.js";
import {
  ANIMATABLE,
  EASINGS,
  EFFECT_KINDS,
  EFFECT_PARAMS,
  TEXT_BACKDROPS,
  TEXT_PLACES,
} from "../scene.js";
import { SHAPE_KINDS } from "../shape_points.js";
import { TEXT_PRESETS } from "../text_presets.js";
import {
  MAX_TRANSITION_MS,
  MIN_TRANSITION_MS,
  TRANSITION_DIRECTIONS,
  TRANSITION_PARAM_RANGE,
  TRANSITIONS,
} from "../transitions.js";

/** Where a caption sits when it is placed in FLOW — the three zones it stacks in. */
export const TEXT_POSITIONS = ["top", "middle", "bottom"];

/** The named sizes the pane offers. `size_px` overrides one; the AI uses these. */
export const TEXT_SIZES = ["small", "medium", "large"];

/** How a caption is set. Mirrors the pane's Align control. */
export const TEXT_ALIGNS = ["left", "center", "right"];

/**
 * THE HOUSE CAPS, in one place, because three different files enforce them.
 *
 * ⚠ THESE ARE THE PLAN'S LIMITS, NOT THE EDITOR'S. The editor already refuses a
 * sixth effect on a clip (`MAX_EFFECTS`) and that stays where it is — a person
 * dragging effects onto one shot is allowed to make a mess of it, because they
 * can see what they are doing and undo it. The Director cannot see, so it gets a
 * tighter fence: at most ONE effect per clip and effects on at most 40% of them.
 *
 * The reason is what an auto-graded cut looks like when it goes wrong. Two
 * effects on every shot is not "more graded", it is a film where nothing stands
 * out because everything is treated — and the user's read of that is "the AI
 * ruined my edit", not "the AI applied 96 effects". A treatment only reads as a
 * treatment when most shots go without one.
 *
 * `SHAPES_PER_MINUTE` is the same argument for the graphics layer: an arrow that
 * points at something is a device, and six on screen at once is clip art.
 */
export const HOUSE_CAPS = {
  /** Effects one clip may carry in a plan. The editor's own limit is higher. */
  EFFECTS_PER_CLIP: 1,
  /** The share of picture clips that may carry an effect at all, 0–1. */
  EFFECT_CLIP_SHARE: 0.4,
  /** Transitions, as a share of the CUTS available. A cut is the default. */
  TRANSITION_CUT_SHARE: 0.35,
  /** Shapes the plan may place per minute of finished film. */
  SHAPES_PER_MINUTE: 4,
  /** Text clips per minute. Captions written from audio do not count. */
  TEXTS_PER_MINUTE: 8,
  /**
   * ⚠ A CAPTION MUST FIT THE SHOT IT BELONGS TO, and this is the slack it gets:
   * a text clip may not start before its shot does, nor end more than this far
   * past the end of it. Not zero — a title that fades out across the cut is a
   * real thing an editor does — but bounded, because a caption that outlives its
   * shot by two seconds reads as one that failed to disappear.
   */
  TEXT_OVERHANG_MS: 400,
  /** The shortest thing the Director may leave on the timeline. */
  MIN_CLIP_MS: 200,
};

/** `[{ id, label, note }]` for every transition this build renders. */
function transitionVocab() {
  return TRANSITIONS.map((t) => ({
    id: t.id,
    label: t.label,
    note: t.note || "",
    // Which parameters it takes, and the legal values for the one that is a
    // CHOICE rather than a number. A planner that knows "wipe takes a direction"
    // stops proposing `{ softness: "left" }`.
    params: t.params || [],
    directions: (t.params || []).includes("direction") ? TRANSITION_DIRECTIONS : [],
  }));
}

/** `[{ id, label, note, params }]` for every effect this build renders. */
function effectVocab() {
  return EFFECT_KINDS.map((kind) => {
    const info = EFFECT_INFO[kind] || {};
    return {
      id: kind,
      label: info.label || kind,
      note: info.note || "",
      // The defaults ARE the shape: a parameter's default says both what it is
      // called and, by its type, whether it can be animated (see EFFECT_PARAMS).
      params: { ...(EFFECT_PARAMS[kind] || {}) },
    };
  });
}

/**
 * THE MANIFEST — every word the Director may use, and what each one means.
 *
 * Built fresh on every call rather than frozen at module load. It costs a few
 * array maps and it means a table that is itself derived (as `SHAPE_KINDS` is,
 * from `SHAPE_CATEGORIES`) can never be captured half-built by import order.
 */
export function capabilities() {
  return {
    // ⚠ THE VERBS ARE PART OF THE VOCABULARY, not a separate document. A model
    // told which transitions exist but not which verb places one has been given
    // half a language. Derived from `ACTIONS` — see `verbVocab`.
    verbs: verbVocab(),
    transitions: transitionVocab(),
    transitionDurationMs: { min: MIN_TRANSITION_MS, max: MAX_TRANSITION_MS },
    transitionParamRange: { ...TRANSITION_PARAM_RANGE },
    effects: effectVocab(),
    shapes: SHAPE_KINDS.map((k) => ({ id: k.id, label: k.label })),
    audioTransitions: FADE_CURVES.map((curve) => ({
      id: curve,
      label: (FADE_CURVE_INFO[curve] || {}).label || curve,
      note: (FADE_CURVE_INFO[curve] || {}).note || "",
    })),
    text: {
      presets: TEXT_PRESETS.map((p) => ({ id: p.id, label: p.label, hint: p.hint || "" })),
      positions: TEXT_POSITIONS,
      places: TEXT_PLACES,
      backdrops: TEXT_BACKDROPS,
      sizes: TEXT_SIZES,
      aligns: TEXT_ALIGNS,
    },
    easings: [...EASINGS],
    animatable: {
      frame: [...ANIMATABLE.frame],
      shape: [...ANIMATABLE.shape],
      overlay: [...ANIMATABLE.overlay],
      text: [...ANIMATABLE.text],
    },
    caps: { ...HOUSE_CAPS },
  };
}

/**
 * The legal ids of one family, as a Set — what the validator actually asks.
 *
 * ⚠ FAMILY NAMES MATCH THE MANIFEST'S KEYS so there is one spelling of
 * "transition" in the system, not one here and another in `plan_schema.js`.
 */
export function vocabulary(caps, family) {
  const table = caps && caps[family];
  if (Array.isArray(table)) {
    return new Set(table.map((entry) => (typeof entry === "string" ? entry : entry.id)));
  }
  return new Set();
}

/** Is `value` something this build can render, in `family`? */
export function isKnown(caps, family, value) {
  return vocabulary(caps, family).has(value);
}

/**
 * One transition/effect entry by id, or null.
 *
 * The editor's `addTransitionAtCut` and `addEffectToClip` both take a LIBRARY
 * ENTRY (`{ kind, label, params }`), not a bare kind — see `fx_library.js` on
 * why an entry is a preset. The Director names a kind, so this is what turns one
 * into the shape those two functions already accept, and it is why the runner
 * never has to build an entry literal of its own.
 */
export function entryFor(caps, family, id, params = {}) {
  const table = (caps && caps[family]) || [];
  const found = table.find((entry) => entry.id === id);
  if (!found) return null;
  return {
    type: family === "effects" ? "effect" : family === "transitions" ? "transition" : family,
    id: found.id,
    kind: found.id,
    label: found.label || found.id,
    params: { ...params },
  };
}
