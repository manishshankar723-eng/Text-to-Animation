// actions.js — THE HANDS. Every edit the Director is able to make, as a verb.
//
// ---------------------------------------------------------------------------
// ⚠ A VERB DOES NOT EDIT ANYTHING. IT CALLS THE FUNCTION THAT ALREADY DOES.
// ---------------------------------------------------------------------------
// This is the whole design, and it is the reason the runner is safe to point at
// a real project. `add_transition` does not build a transition record — it calls
// `addTransitionAfterFrame`, which is what the ＋ on a cut and a tile dragged out
// of the Effects library both end up in as well (they arrive through
// `addTransitionAtCut`, which resolves their index and delegates — see its header
// for why a verb may not use the index form). So the one-per-cut rule, the replace-don't-
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
import { EASINGS, TEXT_BACKDROPS, TEXT_PLACES, frameTrack } from "../scene.js";
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
  // ⚠ BY FRAME ID, NOT BY CUT NUMBER, AND THE EDITOR HAS BOTH. `addTransitionAtCut`
  // counts into the editor's WHOLE picture list; the Director counts into the
  // shot row (`shotRow` takes the Veo takes out), so on an animated project the
  // two disagree about which cut "3" is — and the failure was silent: the record
  // was created against a take on the video row, the step logged "done", and no
  // transition rendered anywhere a person could see it. A verb resolves the id
  // out of its own `ctx` and hands that over, so no index crosses the line.
  "addTransitionAfterFrame",
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
  // ⚠ THE TWO THAT CHANGE HOW MANY SHOTS THERE ARE. Every other name in this
  // list edits a clip in place; these two make the film a different length and
  // renumber everything after the point they touch. That is why the verbs built
  // on them address a shot BY ID rather than by number — see the long note over
  // the cut section in `ACTIONS`.
  "splitFrameAt",
  "deleteFrame",
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
 * IS THERE ACTUALLY A CUT AFTER SHOT `cut`? Returns the frame, or a reason.
 *
 * ⚠ A CUT IS TWO CLIPS THAT TOUCH, NOT TWO CLIPS IN A ROW, and until this
 * existed the Director could not tell the difference. `transitionWindow` refuses
 * to place a transition where the next clip on the row does not START EXACTLY
 * where this one ENDS — "there is no edit point in a gap" — and `renderTransitions`
 * refuses to draw a badge there for the same reason. So a plan that asked for a
 * dissolve across a gap produced a real record, a step logged as DONE, and
 * absolutely nothing on screen or in the export. Silent, and indistinguishable
 * from the feature being broken.
 *
 * ⚠ AND THE GAPS ARE NOT HYPOTHETICAL — `spreadPanelsForRenders` MAKES THEM. A
 * Veo take is usually longer than the hold it was made from, so the panels after
 * it are pushed clear of its end, and the panel row ends up with holes in it.
 * Which means the projects most likely to hit this are exactly the ones that have
 * already been animated: press 🎬 on one and every dissolve in the plan can
 * quietly evaporate.
 *
 * ⚠ IT IS A DROP IN THE PREVIEW, NOT A FAILURE AT RUN TIME. Refusing here means
 * the reason appears under the table before Run is pressed — "3 steps couldn't be
 * used" with a sentence each — which is the whole contract this validator has
 * with the user. The other forty-seven steps still make a film.
 *
 * ⚠ MEASURED OFF `starts`, NEVER RE-DERIVED. Those are the editor's own
 * `frameSpans` numbers, filtered by `shotRow` at the same indices — the same
 * arithmetic the timeline is drawing and the renderer is reading. Laying the
 * durations end to end here instead would be a second layout engine that
 * disagrees with the screen the moment anything is dragged.
 */
function cutAfter(cut, ctx) {
  const frames = ctx.frames || [];
  const starts = ctx.starts || [];
  const i = cut - 1;
  const from = frames[i];
  const to = frames[i + 1];
  if (!from || !to) return { frame: null, why: `cut ${cut} is not between two shots` };

  // ⚠ AND THE TWO CLIPS HAVE TO BE ON THE SAME ROW. `frames` is every picture
  // clip on every picture row, in one list, so two entries that are neighbours
  // in the LIST can be neighbours in nothing else — one plays on the Video row
  // while the other plays underneath it on Images. A transition between them is
  // not a transition, and the test below could only report it as the nonsense
  // it looked like: "there is a 28.0s gap after shot 24".
  //
  // ⚠ IT IS CHECKED BEFORE THE TOUCHING TEST because it is the better message.
  // Two clips on two rows fail the gap test as well, and being told about a gap
  // sends the reader to look for one on a timeline that has not got one.
  const fromTrack = frameTrack(from);
  if (fromTrack !== frameTrack(to)) {
    return { frame: null, why: rowMismatch(cut, from, to, ctx) };
  }

  // No `starts` at all is an older caller (and every maths-only test): fall back
  // to trusting list order, which is what every one of them means by it.
  if (starts.length <= i + 1) return { frame: from, why: "" };
  const end = (Number(starts[i]) || 0) + (Number(from.duration_ms) || 0);
  const next = Number(starts[i + 1]) || 0;
  if (next !== end) {
    const gap = Math.round(next - end);
    return {
      frame: null,
      why:
        gap > 0
          ? `there is a ${(gap / 1000).toFixed(1)}s gap after shot ${cut}, so there is no cut ` +
            "there for a transition to happen on"
          : `shot ${cut} overlaps the shot after it, so there is no clean cut between them`,
    };
  }
  return { frame: from, why: "" };
}

/**
 * TWO CLIPS ON TWO ROWS, said in the row names the person can see.
 *
 * ⚠ NAMED, NOT NUMBERED, WHEN THE NAMES ARE THERE. "shot 21 is on Images and
 * shot 22 is on Video" is a sentence somebody can check against their own
 * gutter in one glance; "they are on different tracks" is a sentence about our
 * data model. Falls back to the plain statement when no row stack was handed in
 * — which is every maths-only caller, and is not a bug.
 */
function rowMismatch(cut, from, to, ctx) {
  const rows = (ctx && ctx.laneRows) || [];
  const nameOf = (frame) => {
    const row = rows.find((r) => r && r.kind === "frames" && r.track === frameTrack(frame));
    if (!row) return "";
    return row.layer ? `“${row.name || "picture"}” (layer ${row.layer})` : `“${row.name}”`;
  };
  const a = nameOf(from);
  const b = nameOf(to);
  const where = a && b ? `shot ${cut} is on ${a} and shot ${cut + 1} is on ${b}` : `shots ${cut} and ${cut + 1} are on different picture rows`;
  return `${where}, so there is no cut between them — a transition joins two clips on the SAME row`;
}

/**
 * WHY A SHOT COULD NOT BE RESOLVED, in words a person can act on.
 *
 * ⚠ "THERE IS NO SHOT UNDEFINED" WAS ON A USER'S SCREEN, and it is two different
 * faults wearing one sentence. A step that names shot 61 on a 48-shot film is a
 * model that misread the board; a step with NO `shot` at all is a model that took
 * "omit every argument you are not deliberately setting" one field too far — and
 * the second is the one that happened, three times in one plan (`add_text` with no
 * shot, `add_effect` with no kind). Printing `undefined` back at the user tells
 * them neither, and reads like a crash rather than a dropped step.
 *
 * ⚠ AND IT NAMES THE LENGTH OF THE FILM in the out-of-range case, because "there
 * is no shot 61" is only actionable next to "this film has 48".
 */
function noShot(value, ctx) {
  const count = (ctx && ctx.frames ? ctx.frames.length : 0) || 0;
  if (value === undefined || value === null || value === "") {
    return "the step named no shot to apply it to";
  }
  const n = int(value);
  if (n === undefined) return `“${value}” is not a shot number`;
  return `there is no shot ${n} — this film has ${count}`;
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

/**
 * THE FOUR MOVES A DRAWING CAN CARRY, and why there are only four.
 *
 * ⚠ NOT A LIBRARY, AND IT MUST NOT BECOME ONE. Every one of these is a move a
 * camera on a rostrum could make over a piece of artwork, which is exactly what
 * an animatic still is — and it is the whole vocabulary a person means when they
 * say "make the stills move". Rolls, whips, shakes and the rest are things a
 * person chooses shot by shot; a planner reaching for one is a planner making
 * the decision the header of `house_style.js` says arithmetic must not make.
 */
export const MOTION_KINDS = ["zoom_in", "zoom_out", "pan_left", "pan_right"];

export const MOTION_LABEL = {
  zoom_in: "push in",
  zoom_out: "pull back",
  pan_left: "pan left",
  pan_right: "pan right",
};

/**
 * HOW FAR EACH MOVE TRAVELS at `amount: 1`.
 *
 * ⚠ A PAN NEEDS THE PICTURE BIGGER THAN THE FRAME OR IT PANS INTO NOTHING.
 * `placePicture` reads `x`/`y` as the picture's CENTRE in frame units, so a
 * picture at scale 1 exactly fills the frame and moving its centre by any amount
 * at all drags an empty edge into shot. So a pan carries an OVERSCAN — the
 * picture is held at `PAN_SCALE` for the whole move — and it may only travel
 * inside the margin that overscan buys: at 1.12 the picture overhangs by 0.06 on
 * each side, and the pan uses three quarters of that so a panel whose aspect
 * does not quite match the project still has something in hand.
 */
const ZOOM_TO = 0.1;      // a push travels 10% at amount 1
const PAN_SCALE = 0.12;   // ...and a pan is held 12% oversize to have room
const PAN_SAFETY = 0.75;  // ...of whose margin it uses three quarters

/**
 * ONE MOVE, AS THE TWO KEYS IT ACTUALLY IS. Pure — no editor, no React.
 *
 * Returns `{ rest, keyframes }`: `rest` is the clip's resting transform and
 * `keyframes` the curve over it.
 *
 * ⚠ THE RESTING VALUE IS WHERE THE MOVE ENDS, never where it starts. `scale` and
 * `x` are read as the value wherever the keys do not reach, so a move that
 * finished at 1.1 with a resting 1 snaps back to 1 the instant the last key
 * passes. Same rule `push_in` and `applyTextPreset` follow, and the same bug if
 * it is dropped.
 */
export function motionKeys(kind, amount = 1, lengthMs = 2000, ease = "ease-in-out") {
  const t = Math.max(HOUSE_CAPS.MIN_CLIP_MS, Number(lengthMs) || 2000);
  const by = Math.max(0.25, Math.min(2, Number(amount) || 1));
  const keys = (from, to) => [
    { t: 0, v: from, ease },
    { t, v: to, ease: "linear" },
  ];
  if (kind === "zoom_in" || kind === "zoom_out") {
    const far = 1 + ZOOM_TO * by;
    const [from, to] = kind === "zoom_in" ? [1, far] : [far, 1];
    return { rest: { scale: to }, keyframes: { scale: keys(from, to) } };
  }
  const over = 1 + PAN_SCALE * by;
  const reach = ((over - 1) / 2) * PAN_SAFETY;
  // ⚠ "PAN LEFT" IS WHERE THE CAMERA GOES, NOT WHERE THE PICTURE GOES, which is
  // the way round every editor in the world names it — so the picture travels
  // the other way and its centre moves RIGHT.
  const [from, to] =
    kind === "pan_left" ? [0.5 - reach, 0.5 + reach] : [0.5 + reach, 0.5 - reach];
  return { rest: { scale: over, x: to }, keyframes: { x: keys(from, to) } };
}

/**
 * HOW MUCH A CAPTION DRIFTS TOWARDS THE VIEWER while it is on screen.
 *
 * ⚠ 4%, AND ONE MOVE ONLY. Asked for as "text clip you also give some motion
 * little, keep only one motion in text clip like little zoom in" — and the
 * "little" is the whole specification. A caption is READ, and anything more than
 * a few per cent makes the reader's eye chase the words instead of finishing the
 * sentence. It is also why this is a slow push across the whole clip rather than
 * a second in/out beat: the in and out already belong to the text preset
 * (`text_presets.js` owns opacity/x/y), and this owns `scale`, so the two never
 * fight over a track.
 */
const TEXT_ZOOM_TO = 0.04;

/**
 * THE CAPTION'S PUSH, AS THE TWO KEYS IT IS. Pure, like `motionKeys` above.
 *
 * ⚠ THE RESTING VALUE IS WHERE IT ENDS, the same rule `motionKeys` keeps: a
 * scale that finished at 1.04 with a resting 1 snaps back the instant the last
 * key passes.
 */
export function captionPush(lengthMs) {
  const t = Math.max(HOUSE_CAPS.MIN_CLIP_MS, Number(lengthMs) || 2000);
  const to = 1 + TEXT_ZOOM_TO;
  return {
    rest: { scale: to },
    keyframes: {
      scale: [
        { t: 0, v: 1, ease: "ease-in-out" },
        { t, v: to, ease: "linear" },
      ],
    },
  };
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
    args: ["text"],
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
    args: ["ms"],
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
    args: ["shot"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      return i < 0 ? fail(noShot(args.shot, ctx)) : ok({ shot: i + 1 });
    },
    describe: (args) => `Select shot ${args.shot}`,
    run: ({ api, args, ctx }) => api.selectOnly({ frame: ctx.frames[args.shot - 1].id }),
  },

  // ----------------------------------------------------------------- timing
  set_shot_duration: {
    verb: "set_shot_duration",
    label: "Re-time a shot",
    needs: ["patchFrame"],
    args: ["shot", "ms"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
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
    args: ["ms"],
    validate: (args) => {
      const length = ms(args.ms);
      if (length === undefined) return fail("no length given");
      return ok({ ms: clamp(length, HOUSE_CAPS.MIN_CLIP_MS, 600000) });
    },
    describe: (args) => `Every shot holds ${(args.ms / 1000).toFixed(1)}s`,
    run: ({ api, args }) => api.setAllDurations(args.ms),
  },

  // ---------------------------------------------------------------- the cut
  //
  // ---------------------------------------------------------------------------
  // ⚠ THESE THREE RENUMBER THE FILM, AND THAT IS WHY THEY WORK BY ID.
  // ---------------------------------------------------------------------------
  // Every other verb here resolves `shot: 5` against the LIVE read-model at the
  // top of its own step (`shotIndex`), which is right while the film keeps its
  // shape: shot 5 is shot 5 whatever has been done to shot 2's transitions.
  //
  // It is WRONG the moment a step can delete or split. A plan that says "delete
  // shot 3, then put a title on shot 5" means the shot the PERSON called 5 when
  // they read the preview — and after step one that shot is number 4. Resolved
  // live, step two would land on the wrong picture, report success, and the fault
  // would be visible only to somebody who already knew what the film should be.
  //
  // So a structural verb resolves the number to a FRAME ID in `validate`, which
  // runs ONCE against the document the user was shown, and carries that id in its
  // arguments. Deleting a shot does not change any other frame's id, so every
  // later step still means what the preview said it meant.
  //
  // ⚠ AND `run` LOOKS THE ID UP AGAIN AND MAY NOT FIND IT. Between the preview
  // and Apply the user can edit by hand, and between two steps an earlier step
  // can have removed the same clip. A missing id THROWS, the runner logs that one
  // step as failed and carries on — the same trade the whole registry makes, and
  // far better than deleting whatever now happens to sit at that index.
  split_shot: {
    verb: "split_shot",
    label: "Cut a shot in two",
    needs: ["splitFrameAt"],
    args: ["shot", "at_ms"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
      const frame = (ctx.frames || [])[i];
      // ⚠ `at_ms` IS MEASURED FROM THE START OF THE SHOT, NOT OF THE FILM. A
      // planner handed a shot list has that shot's own length in front of it and
      // does not reliably know where the shot BEGINS — asking for an absolute time
      // asks it to derive a number, and it derives it wrong on any film whose
      // shots are not all one length. The absolute position is computed at run
      // time off the live layout, which is the only moment it is knowable.
      const at = ms(args.at_ms);
      if (at === undefined) return fail("no cut point given");
      const held = Math.max(0, Number(frame.duration_ms) || 0);
      // Both halves must clear the editor's own minimum, or `splitFrameAt`
      // refuses with a notice and the step silently does nothing.
      if (held < HOUSE_CAPS.MIN_CLIP_MS * 2) {
        return fail(`shot ${i + 1} is too short to cut in two`);
      }
      const point = clamp(at, HOUSE_CAPS.MIN_CLIP_MS, held - HOUSE_CAPS.MIN_CLIP_MS);
      return ok({ shot: i + 1, at_ms: point, frame_id: frame.id });
    },
    describe: (args, ctx) => {
      const label = ((ctx.frames || [])[args.shot - 1] || {}).label || "";
      const at = (args.at_ms / 1000).toFixed(1);
      return `Cut shot ${args.shot}${label ? ` — ${label}` : ""} at ${at}s`;
    },
    run: ({ api, args, ctx }) => {
      const i = (ctx.frames || []).findIndex((f) => f.id === args.frame_id);
      if (i < 0) throw new Error("that shot is no longer on the timeline");
      // ⚠ THE ABSOLUTE TIME IS READ NOW, NOT AT VALIDATE TIME. `starts` moves
      // whenever anything before this shot is re-timed, and an earlier step in
      // this very plan may have done exactly that.
      const start = (ctx.starts || [])[i] ?? 0;
      api.splitFrameAt(start + args.at_ms, args.frame_id);
    },
  },

  trim_shot: {
    verb: "trim_shot",
    label: "Trim a shot",
    needs: ["patchFrame"],
    args: ["shot", "by_ms"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
      // ⚠ RELATIVE, AND THAT IS THE WHOLE REASON IT EXISTS BESIDE
      // `set_shot_duration`. "Take a second off shot 3" and "make shot 3 one
      // second long" are different films, and a model given only the absolute
      // verb answers the second when it was asked the first — which on a
      // six-second shot is a five-second mistake. A negative amount lengthens;
      // the sign is the instruction.
      //
      // ⚠ `int`, NOT `ms`, AND THAT IS THE WHOLE POINT. `ms()` is "a time", so it
      // floors at zero — every other verb here takes a position or a length and a
      // negative one is meaningless. This one takes a DIFFERENCE, where the sign
      // IS the instruction, and reading it through `ms()` silently turned every
      // "hold it a second longer" into "no amount to trim by". Caught by
      // `tests/editor_chat_check.py` §4 before it ever reached a timeline.
      const by = int(args.by_ms);
      if (by === undefined || by === 0) return fail("no amount to trim by");
      const held = Math.max(0, Number((ctx.frames || [])[i].duration_ms) || 0);
      const next = clamp(held - by, HOUSE_CAPS.MIN_CLIP_MS, 600000);
      if (next === held) return fail(`shot ${i + 1} cannot be trimmed any further`);
      return ok({ shot: i + 1, by_ms: by, ms: next });
    },
    describe: (args) =>
      args.by_ms > 0
        ? `Trim ${(args.by_ms / 1000).toFixed(1)}s off shot ${args.shot}`
        : `Hold shot ${args.shot} ${(-args.by_ms / 1000).toFixed(1)}s longer`,
    run: ({ api, args, ctx }) =>
      api.patchFrame(ctx.frames[args.shot - 1].id, { duration_ms: args.ms }),
  },

  delete_shot: {
    verb: "delete_shot",
    label: "Remove a shot",
    needs: ["deleteFrame"],
    args: ["shot"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
      // ⚠ A FILM CANNOT BE EDITED DOWN TO NOTHING. One shot is the floor: an
      // empty timeline has no read-model, so the next turn would be answering
      // questions about a film that no longer exists.
      if ((ctx.frames || []).length <= 1) return fail("this is the only shot left");
      return ok({ shot: i + 1, frame_id: ctx.frames[i].id });
    },
    describe: (args, ctx) => {
      const label = ((ctx.frames || [])[args.shot - 1] || {}).label || "";
      return `Remove shot ${args.shot}${label ? ` — ${label}` : ""}`;
    },
    run: ({ api, args, ctx }) => {
      const here = (ctx.frames || []).some((f) => f.id === args.frame_id);
      if (!here) throw new Error("that shot is no longer on the timeline");
      api.deleteFrame(args.frame_id);
    },
  },

  // ------------------------------------------------------------ the picture
  set_shot_transform: {
    verb: "set_shot_transform",
    label: "Frame a shot",
    needs: ["patchFrame"],
    args: ["shot", "scale", "x", "y", "opacity"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
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
    args: ["shot", "from", "to", "ease"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
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

  /**
   * A KEN BURNS MOVE ON A STILL — the four a drawing can actually carry.
   *
   * ⚠ WHY THIS EXISTS BESIDE `push_in`. That verb is one gesture, chosen for the
   * shots a rules planner could justify moving; this one is the answer to a
   * different question — "nothing in this film is being RENDERED, so every
   * drawing has to move on its own". A board of fourteen stills with a push on
   * three of them and nothing on the other eleven reads as a slideshow with a
   * fault, which is what was reported. See `stillMotion` in `house_style.js`.
   *
   * ⚠ AND IT IS STILL NOT A NEW ANIMATION SYSTEM. Same reasoning `push_in` gives:
   * a zoom is two keys on `scale`, and a pan is two keys on `x` over a picture
   * scaled up far enough to have somewhere to travel. Written through the clip's
   * ordinary `keyframes`, so it is scrubbable, editable and deletable afterwards
   * exactly like one a person set by hand — and `clear_shot_motion` already
   * removes it without knowing this verb exists.
   */
  add_shot_motion: {
    verb: "add_shot_motion",
    label: "Move on a still",
    needs: ["patchFrame"],
    args: ["shot", "kind", "amount", "ease"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
      const kind = oneOf(args.kind, MOTION_KINDS);
      if (!kind) return fail(`there is no “${args.kind}” move`);
      // How far it travels, as a multiple of the house move. Clamped rather than
      // refused: a planner asking for three times the usual push has asked for
      // something reasonable badly, not for something else.
      const amount = clamp(num(args.amount) ?? 1, 0.25, 2);
      const ease = oneOf(args.ease, EASINGS) || "ease-in-out";
      return ok({ shot: i + 1, kind, amount, ease });
    },
    describe: (args) => `Shot ${args.shot}: ${MOTION_LABEL[args.kind] || args.kind}`,
    run: ({ api, args, ctx }) => {
      const frame = ctx.frames[args.shot - 1];
      const length = Math.max(HOUSE_CAPS.MIN_CLIP_MS, frame.duration_ms || 2000);
      const move = motionKeys(args.kind, args.amount, length, args.ease);
      api.patchFrame(frame.id, {
        ...move.rest,
        keyframes: { ...(frame.keyframes || {}), ...move.keyframes },
      });
    },
  },

  clear_shot_motion: {
    verb: "clear_shot_motion",
    label: "Hold a shot still",
    needs: ["patchFrame"],
    args: ["shot"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      return i < 0 ? fail(noShot(args.shot, ctx)) : ok({ shot: i + 1 });
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
    needs: ["addTransitionAfterFrame", "patchTransition"],
    args: ["cut", "kind", "ms", "params"],
    validate: (args, caps, ctx) => {
      const cut = int(args.cut);
      if (cut === undefined || cut <= 0 || cut >= (ctx.frames || []).length) {
        return fail(`cut ${args.cut} is not between two shots`);
      }
      // ⚠ AND THE TWO SHOTS HAVE TO TOUCH. See `cutAfter`: a dissolve across a
      // GAP is a record nothing renders and nothing draws, reported as done.
      const joint = cutAfter(cut, ctx);
      if (!joint.frame) return fail(joint.why);
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
      // ⚠ ONE FRAME, READ ONCE, USED BY BOTH HALVES. The record is created
      // against this clip and then looked up again by the same id — which is the
      // whole fix: before, it was created against `frames[cut - 1]` in the EDITOR
      // (the unfiltered picture list) and looked up against `ctx.frames[cut - 1]`
      // here (the shot row), so on any project carrying Veo takes the two were
      // different clips. The transition landed on a take nobody sees and the
      // length patch below found nothing to patch. Neither said a word.
      const after = ctx.frames[args.cut - 1];
      if (!after) return;
      api.addTransitionAfterFrame(
        entryFor(ctx.caps, "transitions", args.kind, args.params),
        after.id
      );
      if (args.ms === undefined) return;
      // The length is a second edit because the editor owns the record's
      // creation and always makes it `DEFAULT_TRANSITION_MS` long — which is
      // right, and is why this asks for the change afterwards by id rather than
      // reaching into the constructor.
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
    args: ["cut", "ms"],
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
    args: ["cut"],
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
    args: ["shot", "kind", "params"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
      const kind = str(args.kind);
      if (!isKnown(caps, "effects", kind)) {
        return fail(
          // ⚠ TWO FAULTS, TWO SENTENCES — same reason as `noShot`. "this build
          // has no “undefined” effect" was on a user's screen and means "the
          // step named no effect at all", which is a different thing to fix.
          kind
            ? `this build has no “${kind}” effect`
            : "the step named no effect to add"
        );
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
    args: ["shot", "index", "param", "value"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
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
    args: ["shot", "index"],
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
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
    // ⚠ `seek` IS IN HERE BECAUSE THE RUN CALLS IT. `addText` places its clip
    // over the frame at the PLAYHEAD, so putting a caption on shot 12 means
    // moving the playhead there first — see the note in `run`. Declaring it was
    // missed on the first write and `director_actions_check` caught it, which is
    // the whole reason that test runs every verb against a recording stub rather
    // than reading the table.
    needs: ["addText", "patchText", "seek"],
    args: [
      "shot", "text", "ref", "position", "align", "size", "backdrop", "place", "x", "y",
      "startMs", "durationMs"
    ],
    creates: true,
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
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
      // ⚠ AND IT GETS A MOVE, BECAUSE A TITLE THAT SITS PERFECTLY STILL LOOKS
      // PASTED ON. One slow push across the whole clip — see `captionPush` for
      // why 4% and why `scale` rather than another in/out beat. House behaviour
      // rather than a plan step: it is a property of how this editor sets type,
      // not a decision the planner should be spending a step on.
      const push = captionPush(window.length);
      api.patchText(id, {
        ...args.patch,
        ...push.rest,
        keyframes: push.keyframes,
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
    args: ["ref", "text", "position", "align", "size", "backdrop", "color", "opacity"],
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
    args: ["ref", "preset", "inMs", "outMs"],
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
    args: ["ref"],
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
    args: [
      "shot", "kind", "ref", "x", "y", "w", "h", "color", "opacity", "rotation", "startMs",
      "durationMs"
    ],
    creates: true,
    validate: (args, caps, ctx) => {
      const i = shotIndex(args.shot, ctx);
      if (i < 0) return fail(noShot(args.shot, ctx));
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
    args: ["ref", "x", "y", "w", "h", "scale", "opacity", "rotation", "color"],
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
    args: ["ref"],
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
    args: ["kind", "name"],
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
    args: ["track", "inMs", "outMs", "inCurve", "outCurve"],
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
    args: ["track", "volume"],
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
    args: ["track", "curve", "ms"],
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
 * THE VERBS, AS THE MODEL IS TOLD THEM — id, what it does, what it takes.
 *
 * ⚠ DERIVED FROM `ACTIONS`, NEVER TYPED OUT BESIDE IT. Same rule as
 * `capabilities.js`, one level up: a hand-written list of "verbs the AI may use"
 * goes stale the first time a verb is added, and it goes stale in the direction
 * that hurts — the planner keeps proposing an argument the validator drops, or
 * never learns about a verb that exists. This is the whole vocabulary of the
 * plan language, read off the registry that implements it.
 *
 * ⚠ `args` IS WHAT A PLAN MAY SET, NOT WHAT A STEP CARRIES AFTERWARDS. A
 * validator FOLDS its arguments — `add_text`'s `position` and `size` come out
 * inside `patch`, `set_shot_transform`'s four come out inside `patch` — so this
 * is the INPUT vocabulary, which is the one a planner needs and the one
 * `director.py` filters a returned step against.
 */
export function verbVocab() {
  return Object.values(ACTIONS).map((action) => ({
    id: action.verb,
    label: action.label,
    args: [...(action.args || [])],
    // A creating verb is the only kind that may name a `ref`, and a plan that
    // knows which those are stops writing forward references — the single
    // commonest fault in a generated plan (see `validatePlan`).
    creates: Boolean(action.creates),
  }));
}

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
