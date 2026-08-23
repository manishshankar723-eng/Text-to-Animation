// actions.js — THE HANDS. Every edit the Director is able to make, as a verb.
//
// ---------------------------------------------------------------------------
// ⚠ A VERB DOES NOT EDIT ANYTHING. IT CALLS THE FUNCTION THAT ALREADY DOES.
// ---------------------------------------------------------------------------
// This is the whole design, and it is the reason the runner is safe to point at
// a real project. `add_transition` does not build a transition record — it calls
// `addTransitionAtCut`, the same function the ＋ on a cut and a tile dragged out
// of the Effects library both call. So the one-per-cut rule, the replace-don't-
// stack rule and the wording of the status line are obeyed by the AI for free,
// and they cannot drift, because there is no second copy of them to drift from.
//
// The alternative — a registry that writes `setTransitions([...list, made])`
// itself — was rejected for exactly the reason `newTransition` has its "one
// literal, two callers" note: a field added to a transition would arrive on the
// ones a person made and not on the ones the AI made, and nothing would fail.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE, AND THE EDITOR IS PASSED IN.
// ---------------------------------------------------------------------------
// No React, no DOM, no import of `AnimaticEditor`. A verb is handed an `api`
// bag — the editor's own callbacks, named in `ACTION_API` — plus a read-model
// (`ctx`) of the document as it stands right now. That is what lets
// `tests/director_actions_check.py` load this file under node and run every verb
// against a stub, which is the half of the registry most worth testing: the
// argument checking, where a bad plan is turned into a dropped step rather than
// a thrown exception in the middle of somebody's timeline.
//
// ---------------------------------------------------------------------------
// ⚠ AN ILLEGAL VALUE IS DROPPED, NEVER THROWN.
// ---------------------------------------------------------------------------
// A model will propose `kind: "swirl"` sooner or later. The two ways to handle
// that are to stop the run, or to leave that cut as a straight cut and carry on
// — and the second is right every time, because 47 good edits and one plain cut
// is a usable film and a half-applied plan is not. `validate` therefore returns
// a REASON, never raises, and the runner logs it. The same rule `fx_library.js`
// states for a spec naming a kind this build doesn't have.
//
// ---------------------------------------------------------------------------
// REFS, AND WHY A PLAN DOES NOT USE INDICES FOR THINGS IT CREATED
// ---------------------------------------------------------------------------
// A plan says "add a title, then give it the Rise preset". The second step has
// to name the first step's clip, and it cannot do that by index into `texts`:
// the list is shared with whatever the user already had on the timeline, and it
// grows as the plan runs. So a creating verb takes a `ref` — any short name the
// plan chooses — and the runner records the real clip id under it. Every later
// verb addresses the clip by that ref. Nothing in a plan ever mentions an id the
// planner could not have known.

import {
  HOUSE_CAPS,
  TEXT_ALIGNS,
  TEXT_POSITIONS,
  TEXT_SIZES,
  entryFor,
  isKnown,
} from "./capabilities.js";
import { EASINGS, TEXT_BACKDROPS, TEXT_PLACES } from "../scene.js";
import { applyTextPreset } from "../text_presets.js";
import { MAX_TRANSITION_MS, MIN_TRANSITION_MS } from "../transitions.js";

/**
 * EVERY EDITOR FUNCTION A VERB IS ALLOWED TO REACH FOR.
 *
 * ⚠ THIS LIST IS A CONTRACT IN BOTH DIRECTIONS. `tests/director_actions_check.py`
 * asserts that every verb's `needs` names something in here (so a verb cannot
 * quietly depend on a function nobody supplies), and
 * `tests/editor_director_check.py` asserts that the real editor supplies every
 * name in here (so the contract cannot be satisfied on paper and broken in the
 * browser). Neither test alone is enough: the first passes against a typo, the
 * second passes against a verb that never runs.
 */
export const ACTION_API = [
  "patchFrame",
  "setAllDurations",
  "seek",
  "selectOnly",
  "addTransitionAtCut",
  "patchTransition",
  "deleteTransition",
  "addEffectToClip",
  "addText",
  "patchText",
  "deleteText",
  "addShape",
  "patchShape",
  "deleteShape",
  "addLayer",
  "patchTrack",
  "addCrossfade",
  "laneSiblings",
];

// ---------------------------------------------------------------------- args
// Small, deliberately boring readers. Each returns a value or `undefined`, and
// `undefined` means "the plan did not say" — never "the plan said something
// wrong", which is `fail()`'s job. Keeping those two apart is what lets a verb
// treat a missing optional argument as a default and a bad one as a drop.

const ok = (args) => ({ ok: true, args });
const fail = (why) => ({ ok: false, why });

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function int(value) {
  const n = num(value);
  return n === undefined ? undefined : Math.round(n);
}

/** A time in ms: a whole number, never negative. */
function ms(value) {
  const n = int(value);
  return n === undefined ? undefined : Math.max(0, n);
}

/** A fraction of the frame — the coordinate system every geometry here uses. */
function frac(value, low = -1, high = 2) {
  const n = num(value);
  if (n === undefined) return undefined;
  return Math.max(low, Math.min(high, n));
}

function str(value) {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function oneOf(value, allowed) {
  return allowed.includes(value) ? value : undefined;
}

function clamp(n, low, high) {
  return Math.max(low, Math.min(high, n));
}

/**
 * Resolve a 1-BASED shot number against the picture sequence.
 *
 * ⚠ ONE-BASED, because that is what the plan preview shows the user ("Shot 1")
 * and a plan whose numbers do not match the table they are reading is a plan
 * they cannot check. Converted here, once, so no verb does its own arithmetic.
 */
function shotIndex(value, ctx) {
  const n = int(value);
  if (n === undefined) return -1;
  const i = n - 1;
  return i >= 0 && i < (ctx.frames || []).length ? i : -1;
}

/** The clip a `ref` names, out of `list`, or null. */
function byRef(ref, list, refs) {
  const id = refs && refs[ref];
  if (!id) return null;
  return (list || []).find((c) => c.id === id) || null;
}

/**
 * WHERE A CLIP BELONGING TO SHOT `i` STARTS AND HOW LONG IT MAY BE.
 *
 * The house rule "text fits its shot" is enforced in two places on purpose —
 * here, where a plan's own numbers are clamped as the step runs, and in
 * `house_style.js`, where a whole plan is checked before anything runs. This one
 * is the backstop: a plan that arrived from somewhere else entirely still cannot
 * put a caption on a shot it does not overlap.
 */
function shotWindow(i, ctx, startMs, durationMs) {
  const starts = ctx.starts || [];
  const frames = ctx.frames || [];
  const shotStart = starts[i] ?? 0;
  const shotLength = Math.max(HOUSE_CAPS.MIN_CLIP_MS, frames[i]?.duration_ms || 2000);
  const shotEnd = shotStart + shotLength;
  const start = clamp(startMs === undefined ? shotStart : startMs, shotStart, shotEnd - HOUSE_CAPS.MIN_CLIP_MS);
  const room = shotEnd + HOUSE_CAPS.TEXT_OVERHANG_MS - start;
  const length = clamp(
    durationMs === undefined ? shotEnd - start : durationMs,
    HOUSE_CAPS.MIN_CLIP_MS,
    room
  );
  return { start, length };
}

// ------------------------------------------------------------------- the bag
// Written out one verb at a time rather than generated from a table. The
// arguments differ enough that a table would need an escape hatch for most of
// them, and a registry you can read top to bottom is worth more here than one
// that is short.

/**
 * @typedef {Object} Action
 * @property {string}   verb      what a plan step calls it
 * @property {string}   label     the log line's noun phrase
 * @property {string[]} needs     which of `ACTION_API` it calls
 * @property {boolean}  creates   true if it records a `ref`
 * @property {Function} validate  (args, caps, ctx) => {ok, args} | {ok:false, why}
 * @property {Function} describe  (args, ctx) => one line for the rail
 * @property {Function} run       ({api, args, ctx, refs}) => void
 */

export const ACTIONS = {
  // ------------------------------------------------------------------ notes
  /**
   * A line of reasoning with no edit attached.
   *
   * ⚠ IT EARNS ITS PLACE. The rail shows what the Director is doing, and a run
   * made only of `add_transition` reads as a machine applying wipes. "Holding
   * the final shot — nothing on this cut" is the sentence that makes the next
   * fifteen steps legible, and a plan cannot say it without a verb for it.
   */
  note: {
    verb: "note",
    label: "Note",
    needs: [],
    validate: (args) => {
      const text = str(args.text);
      return text ? ok({ text }) : fail("a note with nothing to say");
    },
    describe: (args) => args.text,
    run: () => {},
  },

  seek: {
    verb: "seek",
    label: "Move the playhead",
    needs: ["seek"],
    validate: (args, caps, ctx) => {
      const at = ms(args.ms);
      if (at === undefined) return fail("no time given");
      return ok({ ms: Math.min(at, Math.max(0, ctx.totalMs || 0)) });
    },
    describe: (args) => `Playhead to ${(args.ms / 1000).toFixed(2)}s`,
    run: ({ api, args }) => api.seek(args.ms),
  },

  select_shot: {
    verb: "select_shot",
    label: "Select a shot",
    needs: ["selectOnly"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      return i < 0 ? fail(`there is no shot ${args.shot}`) : ok({ shot: i + 1 });
    },
    describe: (args) => `Select shot ${args.shot}`,
    run: ({ api, args, ctx }) => api.selectOnly({ frame: ctx.frames[args.shot - 1].id }),
  },

  // ----------------------------------------------------------------- timing
  set_shot_duration: {
    verb: "set_shot_duration",
    label: "Re-time a shot",
    needs: ["patchFrame"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(`there is no shot ${args.shot}`);
      const length = ms(args.ms);
      if (length === undefined) return fail("no length given");
      return ok({ shot: i + 1, ms: clamp(length, HOUSE_CAPS.MIN_CLIP_MS, 600000) });
    },
    describe: (args) => `Shot ${args.shot} holds ${(args.ms / 1000).toFixed(1)}s`,
    run: ({ api, args, ctx }) =>
      api.patchFrame(ctx.frames[args.shot - 1].id, { duration_ms: args.ms }),
  },

  set_all_durations: {
    verb: "set_all_durations",
    label: "Re-time every shot",
    needs: ["setAllDurations"],
    validate: (args) => {
      const length = ms(args.ms);
      if (length === undefined) return fail("no length given");
      return ok({ ms: clamp(length, HOUSE_CAPS.MIN_CLIP_MS, 600000) });
    },
    describe: (args) => `Every shot holds ${(args.ms / 1000).toFixed(1)}s`,
    run: ({ api, args }) => api.setAllDurations(args.ms),
  },

  // ------------------------------------------------------------ the picture
  set_shot_transform: {
    verb: "set_shot_transform",
    label: "Frame a shot",
    needs: ["patchFrame"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(`there is no shot ${args.shot}`);
      const patch = {};
      const scale = num(args.scale);
      if (scale !== undefined) patch.scale = clamp(scale, 0.1, 8);
      const x = frac(args.x);
      if (x !== undefined) patch.x = x;
      const y = frac(args.y);
      if (y !== undefined) patch.y = y;
      const opacity = num(args.opacity);
      if (opacity !== undefined) patch.opacity = clamp(opacity, 0, 1);
      if (!Object.keys(patch).length) return fail("nothing to change");
      return ok({ shot: i + 1, patch });
    },
    describe: (args) =>
      `Shot ${args.shot}: ${Object.entries(args.patch)
        .map(([k, v]) => `${k} ${Number(v).toFixed(2)}`)
        .join(", ")}`,
    run: ({ api, args, ctx }) => api.patchFrame(ctx.frames[args.shot - 1].id, args.patch),
  },

  /**
   * A PUSH IN — two keys on `scale`, which is what the move actually is.
   *
   * ⚠ NOT A NEW ANIMATION SYSTEM, and that is worth saying because it looks like
   * one from the plan's side. The same reasoning `text_presets.js` gives for its
   * slide: a title that slides up is two keys on `y`. So the most recognisable
   * camera move in an animatic is two keys on `scale`, written through the
   * clip's ordinary `keyframes` field, and it is scrubbable, editable and
   * deletable afterwards exactly like one a person set by hand.
   */
  push_in: {
    verb: "push_in",
    label: "Push in",
    needs: ["patchFrame"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(`there is no shot ${args.shot}`);
      const from = clamp(num(args.from) ?? 1, 0.5, 4);
      const to = clamp(num(args.to) ?? 1.08, 0.5, 4);
      if (Math.abs(to - from) < 0.001) return fail("a push that goes nowhere");
      const ease = oneOf(args.ease, EASINGS) || "ease-in-out";
      return ok({ shot: i + 1, from, to, ease });
    },
    describe: (args) =>
      `Shot ${args.shot}: ${args.to > args.from ? "push in" : "pull back"} ` +
      `${Math.round(args.from * 100)}% → ${Math.round(args.to * 100)}%`,
    run: ({ api, args, ctx }) => {
      const frame = ctx.frames[args.shot - 1];
      const length = Math.max(HOUSE_CAPS.MIN_CLIP_MS, frame.duration_ms || 2000);
      api.patchFrame(frame.id, {
        // ⚠ THE BASE VALUE MOVES WITH THE CURVE. `scale` is read as the resting
        // value wherever the keys do not reach, so leaving it at 1 while the
        // keys run 1 → 1.08 makes the shot snap back the instant the last key
        // passes. Same reason `applyTextPreset` restores `opacity` to 1.
        scale: args.to,
        keyframes: {
          ...(frame.keyframes || {}),
          scale: [
            { t: 0, v: args.from, ease: args.ease },
            { t: length, v: args.to, ease: "linear" },
          ],
        },
      });
    },
  },

  clear_shot_motion: {
    verb: "clear_shot_motion",
    label: "Hold a shot still",
    needs: ["patchFrame"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      return i < 0 ? fail(`there is no shot ${args.shot}`) : ok({ shot: i + 1 });
    },
    describe: (args) => `Shot ${args.shot} is held — no move`,
    run: ({ api, args, ctx }) => {
      const frame = ctx.frames[args.shot - 1];
      const keyframes = { ...(frame.keyframes || {}) };
      for (const prop of ["scale", "x", "y", "opacity"]) delete keyframes[prop];
      api.patchFrame(frame.id, { keyframes, scale: 1, x: 0.5, y: 0.5, opacity: 1 });
    },
  },

  // ------------------------------------------------------------ transitions
  /**
   * ⚠ `cut` INDEXES THE EDGES, exactly as `addTransitionAtCut` counts them: cut
   * 1 is between shot 1 and shot 2. Neither 0 nor `frames.length` is an edit
   * point, and the editor says so itself — this validator refuses them first so
   * the run logs a reason rather than a status line the user never reads.
   */
  add_transition: {
    verb: "add_transition",
    label: "Transition",
    needs: ["addTransitionAtCut", "patchTransition"],
    validate: (args, caps, ctx) => {
      const cut = int(args.cut);
      if (cut === undefined || cut <= 0 || cut >= (ctx.frames || []).length) {
        return fail(`cut ${args.cut} is not between two shots`);
      }
      const kind = str(args.kind);
      if (!isKnown(caps, "transitions", kind)) {
        return fail(`this build has no “${args.kind}” transition`);
      }
      const out = { cut, kind, params: {} };
      // ⚠ A PARAMETER THIS KIND DOES NOT TAKE IS DROPPED, NOT REFUSED. Every
      // transition renders without any parameters at all (`transitionParams`
      // fills in the defaults), so a wipe whose direction the model got wrong is
      // still a wipe — and a wipe is what was asked for.
      const spec = (caps.transitions || []).find((t) => t.id === kind) || {};
      for (const [name, value] of Object.entries(args.params || {})) {
        if (!(spec.params || []).includes(name)) continue;
        if (name === "direction") {
          const dir = oneOf(value, spec.directions || []);
          if (dir) out.params.direction = dir;
          continue;
        }
        const n = num(value);
        if (n === undefined) continue;
        const range = (caps.transitionParamRange || {})[name];
        out.params[name] = range ? clamp(n, range.min, range.max) : n;
      }
      const length = ms(args.ms);
      if (length !== undefined) out.ms = clamp(length, MIN_TRANSITION_MS, MAX_TRANSITION_MS);
      return ok(out);
    },
    describe: (args, ctx) => {
      const label = ((ctx.caps?.transitions || []).find((t) => t.id === args.kind) || {}).label;
      const dir = args.params?.direction ? ` ${args.params.direction}` : "";
      return `${label || args.kind}${dir} on the cut after shot ${args.cut}`;
    },
    run: ({ api, args, ctx }) => {
      api.addTransitionAtCut(entryFor(ctx.caps, "transitions", args.kind, args.params), args.cut);
      if (args.ms === undefined) return;
      // The length is a second edit because `addTransitionAtCut` owns the
      // record's creation and always makes it `DEFAULT_TRANSITION_MS` long —
      // which is right, and is why this asks for the change afterwards by id
      // rather than reaching into the constructor.
      const after = ctx.frames[args.cut - 1];
      const made = (ctx.readTransitions ? ctx.readTransitions() : ctx.transitions || []).find(
        (t) => t.after_frame_id === after.id
      );
      if (made) api.patchTransition(made.id, { duration_ms: args.ms });
    },
  },

  set_transition_duration: {
    verb: "set_transition_duration",
    label: "Re-time a transition",
    needs: ["patchTransition"],
    validate: (args, caps, ctx) => {
      const cut = int(args.cut);
      if (cut === undefined || cut <= 0 || cut >= (ctx.frames || []).length) {
        return fail(`cut ${args.cut} is not between two shots`);
      }
      const length = ms(args.ms);
      if (length === undefined) return fail("no length given");
      return ok({ cut, ms: clamp(length, MIN_TRANSITION_MS, MAX_TRANSITION_MS) });
    },
    describe: (args) => `The transition after shot ${args.cut} runs ${(args.ms / 1000).toFixed(1)}s`,
    run: ({ api, args, ctx }) => {
      const after = ctx.frames[args.cut - 1];
      const found = (ctx.transitions || []).find((t) => t.after_frame_id === after.id);
      if (found) api.patchTransition(found.id, { duration_ms: args.ms });
    },
  },

  remove_transition: {
    verb: "remove_transition",
    label: "Straight cut",
    needs: ["deleteTransition"],
    validate: (args, caps, ctx) => {
      const cut = int(args.cut);
      if (cut === undefined || cut <= 0 || cut >= (ctx.frames || []).length) {
        return fail(`cut ${args.cut} is not between two shots`);
      }
      return ok({ cut });
    },
    describe: (args) => `The cut after shot ${args.cut} is straight`,
    run: ({ api, args, ctx }) => {
      const after = ctx.frames[args.cut - 1];
      const found = (ctx.transitions || []).find((t) => t.after_frame_id === after.id);
      if (found) api.deleteTransition(found.id);
    },
  },

  // --------------------------------------------------------------- the look
  add_effect: {
    verb: "add_effect",
    label: "Effect",
    needs: ["addEffectToClip"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(`there is no shot ${args.shot}`);
      const kind = str(args.kind);
      if (!isKnown(caps, "effects", kind)) {
        return fail(`this build has no “${args.kind}” effect`);
      }
      const spec = (caps.effects || []).find((e) => e.id === kind) || {};
      const params = {};
      for (const [name, value] of Object.entries(args.params || {})) {
        if (!(name in (spec.params || {}))) continue;
        // A string default means the parameter is read straight off the clip and
        // never interpolated — a LUT name, a key colour. Numbers are numbers.
        if (typeof spec.params[name] === "string") {
          const text = str(value);
          if (text) params[name] = text;
          continue;
        }
        const n = num(value);
        if (n !== undefined) params[name] = n;
      }
      return ok({ shot: i + 1, kind, params });
    },
    describe: (args, ctx) => {
      const label = ((ctx.caps?.effects || []).find((e) => e.id === args.kind) || {}).label;
      return `${label || args.kind} on shot ${args.shot}`;
    },
    run: ({ api, args, ctx }) =>
      api.addEffectToClip(
        entryFor(ctx.caps, "effects", args.kind, args.params),
        "frame",
        ctx.frames[args.shot - 1]
      ),
  },

  set_effect_param: {
    verb: "set_effect_param",
    label: "Dial an effect",
    needs: ["patchFrame"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(`there is no shot ${args.shot}`);
      const param = str(args.param);
      if (!param) return fail("no parameter named");
      const value = typeof args.value === "string" ? str(args.value) : num(args.value);
      if (value === undefined) return fail("no value given");
      const at = int(args.index);
      return ok({ shot: i + 1, index: at === undefined ? 0 : Math.max(0, at), param, value });
    },
    describe: (args) => `Shot ${args.shot}: ${args.param} → ${args.value}`,
    run: ({ api, args, ctx }) => {
      const frame = ctx.frames[args.shot - 1];
      const chain = frame.effects || [];
      const effect = chain[args.index];
      if (!effect) return;
      api.patchFrame(frame.id, {
        effects: chain.map((fx, k) =>
          k === args.index ? { ...fx, params: { ...(fx.params || {}), [args.param]: args.value } } : fx
        ),
      });
    },
  },

  remove_effect: {
    verb: "remove_effect",
    label: "Remove an effect",
    needs: ["patchFrame"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(`there is no shot ${args.shot}`);
      const at = int(args.index);
      return ok({ shot: i + 1, index: at === undefined ? 0 : Math.max(0, at) });
    },
    describe: (args) => `Shot ${args.shot}: effect ${args.index + 1} removed`,
    run: ({ api, args, ctx }) => {
      const frame = ctx.frames[args.shot - 1];
      const chain = frame.effects || [];
      if (!chain[args.index]) return;
      api.patchFrame(frame.id, { effects: chain.filter((_, k) => k !== args.index) });
    },
  },

  // ------------------------------------------------------------------- text
  add_text: {
    verb: "add_text",
    label: "On-screen text",
    needs: ["addText", "patchText"],
    creates: true,
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(`there is no shot ${args.shot}`);
      const text = str(args.text);
      if (!text) return fail("a text clip with no words in it");
      const out = { shot: i + 1, text, ref: str(args.ref) || "", patch: {} };
      const position = oneOf(args.position, TEXT_POSITIONS);
      if (position) out.patch.position = position;
      const align = oneOf(args.align, TEXT_ALIGNS);
      if (align) out.patch.align = align;
      const size = oneOf(args.size, TEXT_SIZES);
      if (size) out.patch.size = size;
      const backdrop = oneOf(args.backdrop, TEXT_BACKDROPS);
      if (backdrop) out.patch.backdrop = backdrop;
      const place = oneOf(args.place, TEXT_PLACES);
      if (place) out.patch.place = place;
      const x = frac(args.x, 0, 1);
      if (x !== undefined) out.patch.x = x;
      const y = frac(args.y, 0, 1);
      if (y !== undefined) out.patch.y = y;
      out.startMs = ms(args.startMs);
      out.durationMs = ms(args.durationMs);
      return ok(out);
    },
    describe: (args) =>
      `“${args.text.length > 40 ? `${args.text.slice(0, 39)}…` : args.text}” over shot ${args.shot}`,
    run: ({ api, args, ctx, refs }) => {
      const i = args.shot - 1;
      // ⚠ THE EDITOR PLACES IT, THEN THIS RE-TIMES IT. `addText` puts a clip over
      // the frame at the PLAYHEAD — which is what a person pressing the button
      // means and is the behaviour worth keeping — so the runner seeks first and
      // corrects the window afterwards. Building the clip here instead would be
      // the second literal `newTextClip` this file exists to avoid.
      api.seek(ctx.starts[i] ?? 0);
      const id = api.addText("");
      if (!id) return;
      const window = shotWindow(i, ctx, args.startMs, args.durationMs);
      api.patchText(id, {
        ...args.patch,
        text: args.text,
        start_ms: window.start,
        duration_ms: window.length,
      });
      if (args.ref) refs[args.ref] = id;
    },
  },

  set_text: {
    verb: "set_text",
    label: "Restyle text",
    needs: ["patchText"],
    validate: (args) => {
      const ref = str(args.ref);
      if (!ref) return fail("no text named");
      const patch = {};
      const text = str(args.text);
      if (text) patch.text = text;
      const position = oneOf(args.position, TEXT_POSITIONS);
      if (position) patch.position = position;
      const align = oneOf(args.align, TEXT_ALIGNS);
      if (align) patch.align = align;
      const size = oneOf(args.size, TEXT_SIZES);
      if (size) patch.size = size;
      const backdrop = oneOf(args.backdrop, TEXT_BACKDROPS);
      if (backdrop) patch.backdrop = backdrop;
      const color = str(args.color);
      if (color && /^#[0-9a-fA-F]{6}$/.test(color)) patch.color = color;
      const opacity = num(args.opacity);
      if (opacity !== undefined) patch.opacity = clamp(opacity, 0, 1);
      if (!Object.keys(patch).length) return fail("nothing to change");
      return ok({ ref, patch });
    },
    describe: (args) => `${args.ref}: ${Object.keys(args.patch).join(", ")}`,
    run: ({ api, args, ctx, refs }) => {
      const clip = byRef(args.ref, ctx.texts, refs);
      if (clip) api.patchText(clip.id, args.patch);
    },
  },

  apply_text_preset: {
    verb: "apply_text_preset",
    label: "Text in/out",
    needs: ["patchText"],
    validate: (args, caps) => {
      const ref = str(args.ref);
      if (!ref) return fail("no text named");
      const preset = str(args.preset);
      if (!isKnown(caps.text || {}, "presets", preset)) {
        return fail(`this build has no “${args.preset}” text preset`);
      }
      const out = { ref, preset };
      const inMs = ms(args.inMs);
      if (inMs !== undefined) out.inMs = inMs;
      const outMs = ms(args.outMs);
      if (outMs !== undefined) out.outMs = outMs;
      return ok(out);
    },
    describe: (args) => `${args.ref}: ${args.preset}`,
    run: ({ api, args, ctx, refs }) => {
      const clip = byRef(args.ref, ctx.texts, refs);
      if (!clip) return;
      const options = {};
      if (args.inMs !== undefined) options.inMs = args.inMs;
      if (args.outMs !== undefined) options.outMs = args.outMs;
      api.patchText(clip.id, applyTextPreset(clip, args.preset, options));
    },
  },

  remove_text: {
    verb: "remove_text",
    label: "Remove text",
    needs: ["deleteText"],
    validate: (args) => {
      const ref = str(args.ref);
      return ref ? ok({ ref }) : fail("no text named");
    },
    describe: (args) => `${args.ref} removed`,
    run: ({ api, args, ctx, refs }) => {
      const clip = byRef(args.ref, ctx.texts, refs);
      if (clip) api.deleteText(clip.id);
    },
  },

  // ----------------------------------------------------------------- shapes
  add_shape: {
    verb: "add_shape",
    label: "Shape",
    needs: ["addShape", "patchShape"],
    creates: true,
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(`there is no shot ${args.shot}`);
      const kind = str(args.kind);
      if (!isKnown(caps, "shapes", kind)) {
        return fail(`this build has no “${args.kind}” shape`);
      }
      const out = { shot: i + 1, kind, ref: str(args.ref) || "", patch: {} };
      const x = frac(args.x, 0, 1);
      if (x !== undefined) out.patch.x = x;
      const y = frac(args.y, 0, 1);
      if (y !== undefined) out.patch.y = y;
      const w = num(args.w);
      if (w !== undefined) out.patch.w = clamp(w, 0.01, 2);
      const h = num(args.h);
      if (h !== undefined) out.patch.h = clamp(h, 0.01, 2);
      const color = str(args.color);
      if (color && /^#[0-9a-fA-F]{6}$/.test(color)) out.patch.color = color;
      const opacity = num(args.opacity);
      if (opacity !== undefined) out.patch.opacity = clamp(opacity, 0, 1);
      const rotation = num(args.rotation);
      if (rotation !== undefined) out.patch.rotation = rotation;
      out.startMs = ms(args.startMs);
      out.durationMs = ms(args.durationMs);
      return ok(out);
    },
    describe: (args) => `${args.kind} on shot ${args.shot}`,
    run: ({ api, args, ctx, refs }) => {
      const i = args.shot - 1;
      const window = shotWindow(i, ctx, args.startMs, args.durationMs);
      // `addShape` takes the start it should land on, so unlike text there is no
      // seek-then-correct here — only the length has to be written afterwards.
      const id = api.addShape(args.kind, "", window.start);
      if (!id) return;
      api.patchShape(id, { ...args.patch, duration_ms: window.length });
      if (args.ref) refs[args.ref] = id;
    },
  },

  set_shape: {
    verb: "set_shape",
    label: "Restyle a shape",
    needs: ["patchShape"],
    validate: (args) => {
      const ref = str(args.ref);
      if (!ref) return fail("no shape named");
      const patch = {};
      for (const name of ["x", "y"]) {
        const v = frac(args[name], 0, 1);
        if (v !== undefined) patch[name] = v;
      }
      for (const name of ["w", "h"]) {
        const v = num(args[name]);
        if (v !== undefined) patch[name] = clamp(v, 0.01, 2);
      }
      const scale = num(args.scale);
      if (scale !== undefined) patch.scale = clamp(scale, 0.05, 8);
      const opacity = num(args.opacity);
      if (opacity !== undefined) patch.opacity = clamp(opacity, 0, 1);
      const rotation = num(args.rotation);
      if (rotation !== undefined) patch.rotation = rotation;
      const color = str(args.color);
      if (color && /^#[0-9a-fA-F]{6}$/.test(color)) patch.color = color;
      if (!Object.keys(patch).length) return fail("nothing to change");
      return ok({ ref, patch });
    },
    describe: (args) => `${args.ref}: ${Object.keys(args.patch).join(", ")}`,
    run: ({ api, args, ctx, refs }) => {
      const clip = byRef(args.ref, ctx.shapes, refs);
      if (clip) api.patchShape(clip.id, args.patch);
    },
  },

  remove_shape: {
    verb: "remove_shape",
    label: "Remove a shape",
    needs: ["deleteShape"],
    validate: (args) => {
      const ref = str(args.ref);
      return ref ? ok({ ref }) : fail("no shape named");
    },
    describe: (args) => `${args.ref} removed`,
    run: ({ api, args, ctx, refs }) => {
      const clip = byRef(args.ref, ctx.shapes, refs);
      if (clip) api.deleteShape(clip.id);
    },
  },

  // ----------------------------------------------------------------- layers
  add_layer: {
    verb: "add_layer",
    label: "Add a lane",
    needs: ["addLayer"],
    validate: (args) => {
      const kind = oneOf(args.kind, ["image", "text", "shape", "audio"]);
      if (!kind) return fail(`“${args.kind}” is not a kind of lane`);
      return ok({ kind, name: str(args.name) || "" });
    },
    describe: (args) => `A ${args.kind} lane${args.name ? ` — ${args.name}` : ""}`,
    run: ({ api, args }) => api.addLayer(args.kind, { name: args.name, notice: false }),
  },

  // ------------------------------------------------------------------ audio
  set_track_fade: {
    verb: "set_track_fade",
    label: "Fade a track",
    needs: ["patchTrack"],
    validate: (args, caps, ctx) => {
      const at = int(args.track);
      if (at === undefined || at < 0 || at >= (ctx.audioTracks || []).length) {
        return fail(`there is no audio track ${args.track}`);
      }
      const patch = {};
      const inMs = ms(args.inMs);
      if (inMs !== undefined) patch.fade_in_ms = clamp(inMs, 0, 60000);
      const outMs = ms(args.outMs);
      if (outMs !== undefined) patch.fade_out_ms = clamp(outMs, 0, 60000);
      const inCurve = str(args.inCurve);
      if (isKnown(caps, "audioTransitions", inCurve)) patch.fade_in_curve = inCurve;
      const outCurve = str(args.outCurve);
      if (isKnown(caps, "audioTransitions", outCurve)) patch.fade_out_curve = outCurve;
      if (!Object.keys(patch).length) return fail("nothing to change");
      return ok({ track: at, patch });
    },
    describe: (args) => `Track ${args.track + 1}: ${Object.keys(args.patch).join(", ")}`,
    run: ({ api, args, ctx }) => {
      const clip = (ctx.audioTracks || [])[args.track];
      if (clip) api.patchTrack(clip.id || clip.upload_id, args.patch);
    },
  },

  set_track_volume: {
    verb: "set_track_volume",
    label: "Set a level",
    needs: ["patchTrack"],
    validate: (args, caps, ctx) => {
      const at = int(args.track);
      if (at === undefined || at < 0 || at >= (ctx.audioTracks || []).length) {
        return fail(`there is no audio track ${args.track}`);
      }
      const volume = num(args.volume);
      if (volume === undefined) return fail("no level given");
      return ok({ track: at, volume: clamp(volume, 0, 2) });
    },
    describe: (args) => `Track ${args.track + 1} at ${Math.round(args.volume * 100)}%`,
    run: ({ api, args, ctx }) => {
      const clip = (ctx.audioTracks || [])[args.track];
      if (clip) api.patchTrack(clip.id || clip.upload_id, { volume: args.volume });
    },
  },

  add_crossfade: {
    verb: "add_crossfade",
    label: "Crossfade",
    needs: ["addCrossfade", "laneSiblings"],
    validate: (args, caps, ctx) => {
      const at = int(args.track);
      if (at === undefined || at < 0 || at >= (ctx.audioTracks || []).length) {
        return fail(`there is no audio track ${args.track}`);
      }
      const curve = str(args.curve) || "linear";
      if (!isKnown(caps, "audioTransitions", curve)) {
        return fail(`this build has no “${args.curve}” crossfade`);
      }
      const when = ms(args.ms);
      if (when === undefined) return fail("no time given");
      return ok({ track: at, curve, ms: when });
    },
    describe: (args) => `${args.curve} crossfade on track ${args.track + 1}`,
    run: ({ api, args, ctx }) => {
      const clip = (ctx.audioTracks || [])[args.track];
      if (!clip) return;
      api.addCrossfade(
        { type: "audioTransition", id: args.curve, kind: args.curve, label: args.curve, params: {} },
        api.laneSiblings(clip),
        args.ms
      );
    },
  },
};

/** Every verb a plan may use. */
export const VERBS = Object.keys(ACTIONS);

/**
 * Check one step's arguments without running it.
 *
 * Returns the step with its arguments NORMALISED — clamped, folded, and with
 * anything the verb does not understand removed — or a reason it was dropped.
 * The runner never sees a raw argument, which is why no `run` above defends
 * itself twice.
 */
export function validateStep(step, caps, ctx) {
  const action = ACTIONS[step?.verb];
  if (!action) return fail(`there is no “${step?.verb}” verb`);
  try {
    return action.validate(step.args || {}, caps, ctx);
  } catch (err) {
    // ⚠ A VALIDATOR THAT THROWS IS A BUG, AND IT IS STILL NOT A REASON TO STOP.
    // Same trade as an unknown kind: one dropped step beats an abandoned run.
    return fail(`could not read that step (${err.message})`);
  }
}

/** The one line the rail shows for a step whose arguments are already checked. */
export function describeStep(step, ctx) {
  const action = ACTIONS[step?.verb];
  if (!action) return step?.verb || "";
  try {
    return action.describe(step.args || {}, ctx || {});
  } catch {
    return action.label;
  }
}
