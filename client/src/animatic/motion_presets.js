/**
 * Camera moves on a picture clip — as KEYFRAME MACROS, and nothing else.
 *
 * ⚠ READ `preset_util.js` FIRST. Everything about why a preset is only keys, why
 * there are no new easing curves, and why every move works outwards from the
 * clip's own resting values is in that header. This file is the SHOT LIST: which
 * moves are worth offering on a still, and what to call them.
 *
 * ---------------------------------------------------------------------------
 * ⚠ A PAN NEEDS THE PICTURE BIGGER THAN THE FRAME OR IT PANS INTO NOTHING.
 * ---------------------------------------------------------------------------
 * `placePicture` reads `x`/`y` as the picture's CENTRE in frame units, so a
 * picture at scale 1 exactly fills the frame and moving its centre by any amount
 * at all drags an empty edge into shot. Every move in here that travels
 * therefore carries an OVERSCAN — the picture is held oversize for the whole
 * move — and may only travel inside the margin that overscan buys.
 *
 * ⚠ AND IT USES THREE QUARTERS OF THAT MARGIN, NOT ALL OF IT. A storyboard panel
 * whose aspect does not quite match the project is already using some of the
 * overhang just to fill the frame; a pan that spent the whole margin would show
 * an edge on exactly those panels and on no others, which is the worst kind of
 * bug to be told about. ⚠ THE THREE NUMBERS ARE THE ONES `motionKeys` IN
 * `agent/actions.js` ALREADY USES — same overscan, same safety, same 10% push —
 * because the AI editor and the button in the pane must not disagree about what
 * "push in" means.
 *
 * ---------------------------------------------------------------------------
 * ⚠ THIS IS NOT A SECOND COPY OF `motionKeys`, AND THE DIFFERENCE MATTERS.
 * ---------------------------------------------------------------------------
 * That function is the PLANNER's four moves: it returns `{ rest, keyframes }`
 * for a step in an AI edit plan, it is capped by `HOUSE_CAPS`, and its contract
 * is depended on by saved plans. This file is the browser's preset SHELF: it
 * returns a patch for `onChange`, it is what a person clicks, and it may grow to
 * forty entries without a plan anywhere caring. They share the constants below
 * and nothing else; neither one may start calling the other without deciding
 * which of the two contracts wins.
 */

import { FRAME_DEFAULTS } from "./scene.js";
import {
  FRAME_BOUNDS,
  applyPreset,
  numberOr,
  round6,
  settleTrack,
  wobbleTrack,
} from "./preset_util.js";

// ⚠ THE SAME THREE NUMBERS `motionKeys` USES — see the header.
const ZOOM_TO = 0.1;      // a push travels 10%
const PAN_SCALE = 0.12;   // ...and a pan is held 12% oversize to have room
const PAN_SAFETY = 0.75;  // ...of whose margin it uses three quarters

/** How far a pan may travel each side of centre, at a given overscan. */
function reachAt(over) {
  return ((over - 1) / 2) * PAN_SAFETY;
}

/**
 * The shelves the picker draws, in order. A VIEW, not the truth — a preset whose
 * `category` names no shelf here falls into "Other" rather than vanishing.
 */
export const MOTION_PRESET_CATEGORIES = [
  { id: "none", label: "None", note: "Take the move off again." },
  { id: "zoom", label: "Push & pull", note: "Straight in or straight out." },
  { id: "pan", label: "Pan", note: "Travels across the picture. Held oversize to have room." },
  { id: "kenburns", label: "Ken Burns", note: "A push and a pan at once — the documentary move." },
  { id: "impact", label: "Impact", note: "Hits on the cut. For a beat, a drop, a punchline." },
  { id: "loop", label: "Alive", note: "Never quite still, for as long as the clip lasts." },
];

/**
 * The moves, in the order they are offered.
 *
 * `build` returns the tracks, given the clip's own length and its RESTING
 * transform. `rest` says where the move leaves the clip — ⚠ and almost every
 * move here needs one, because `sceneAt` reads a property's resting value
 * everywhere the keys do not reach: a push that ran 100% → 110% over a resting
 * 100% snaps back the instant its last key passes.
 */
export const MOTION_PRESETS = [
  {
    id: "none",
    label: "None",
    category: "none",
    hint: "Remove the move and hold the picture still.",
    build: () => ({}),
    rest: () => ({ scale: 1, x: 0.5, y: 0.5 }),
  },

  // --- Push & pull --------------------------------------------------------
  {
    id: "push-in",
    label: "Push in",
    category: "zoom",
    hint: "Creeps towards the subject across the whole clip. The default move.",
    build: ({ durationMs, scale }) => ({
      scale: even(scale, scale * (1 + ZOOM_TO), durationMs),
    }),
    rest: ({ scale }) => ({ scale: scale * (1 + ZOOM_TO) }),
  },
  {
    id: "pull-back",
    label: "Pull back",
    category: "zoom",
    hint: "Starts tight and opens out. A reveal.",
    build: ({ durationMs, scale }) => ({
      scale: even(scale * (1 + ZOOM_TO), scale, durationMs),
    }),
  },
  {
    id: "push-in-fast",
    label: "Fast push",
    category: "zoom",
    hint: "The same push, over the first third only. Lands and holds.",
    build: ({ durationMs, scale }) => {
      const beat = Math.max(1, Math.round(durationMs / 3));
      return { scale: even(scale, scale * (1 + ZOOM_TO * 1.6), beat) };
    },
    rest: ({ scale }) => ({ scale: scale * (1 + ZOOM_TO * 1.6) }),
  },
  {
    id: "push-in-big",
    label: "Big push",
    category: "zoom",
    hint: "A 30% move. Loud — for a title shot, not for every panel.",
    build: ({ durationMs, scale }) => ({
      scale: even(scale, scale * 1.3, durationMs),
    }),
    rest: ({ scale }) => ({ scale: scale * 1.3 }),
  },

  // --- Pan. Every one of these is derived — see `panning()`. --------------
  ...panning(),

  // --- Ken Burns: a push AND a pan, which is the move people mean. --------
  ...kenBurns(),

  // --- Impact -------------------------------------------------------------
  {
    id: "punch-in",
    label: "Punch in",
    category: "impact",
    hint: "Jumps in hard on the first frame and settles. Cuts on the beat.",
    build: ({ durationMs, scale }) => ({
      scale: settleTrack(
        scale * 1.22,
        [[0.7, scale * 0.99], [1, scale]],
        Math.min(360, Math.max(1, Math.round(durationMs * 0.25)))
      ),
    }),
  },
  {
    id: "punch-out",
    label: "Punch out",
    category: "impact",
    hint: "Snaps back from tight to normal. The same hit, the other way.",
    build: ({ durationMs, scale }) => ({
      scale: settleTrack(
        scale * 0.82,
        [[0.7, scale * 1.01], [1, scale]],
        Math.min(360, Math.max(1, Math.round(durationMs * 0.25)))
      ),
    }),
  },
  {
    id: "shake",
    label: "Camera shake",
    category: "impact",
    hint: "Rattles the frame as the clip opens, then steadies.",
    build: ({ durationMs, scale, x, y }) => {
      // ⚠ HELD OVERSIZE FOR THE WHOLE CLIP, not only while it shakes. The
      // overscan is what stops an edge appearing at the extremes of the rattle,
      // and a scale that drops back to 1 the moment the shake ends would be a
      // visible size pop in the middle of the shot.
      const over = scale * (1 + PAN_SCALE);
      const beat = Math.min(durationMs, 700);
      const amp = reachAt(1 + PAN_SCALE) * 0.5;
      return {
        x: wobbleTrack(x, amp, 4, beat, { decay: 0.85 }),
        y: wobbleTrack(y, amp * 0.6, 3, beat, { decay: 0.85 }),
        scale: even(over, over, durationMs),
      };
    },
    rest: ({ scale }) => ({ scale: scale * (1 + PAN_SCALE) }),
  },
  {
    id: "shake-hard",
    label: "Hard shake",
    category: "impact",
    hint: "An explosion, a slam, a drop. Uses the whole overscan.",
    build: ({ durationMs, scale, x, y }) => {
      const over = scale * (1 + PAN_SCALE * 1.6);
      const beat = Math.min(durationMs, 900);
      const amp = reachAt(1 + PAN_SCALE * 1.6);
      return {
        x: wobbleTrack(x, amp, 6, beat, { decay: 0.88 }),
        y: wobbleTrack(y, amp * 0.8, 5, beat, { decay: 0.88 }),
        scale: even(over, over, durationMs),
      };
    },
    rest: ({ scale }) => ({ scale: scale * (1 + PAN_SCALE * 1.6) }),
  },
  {
    id: "flash-in",
    label: "Flash in",
    category: "impact",
    hint: "Blinks up from black over two frames. A hard cut with a lift.",
    build: ({ durationMs }) => ({
      opacity: [
        { t: 0, v: 0, ease: "ease-out" },
        { t: Math.min(90, Math.max(1, Math.round(durationMs * 0.08))), v: 1, ease: "linear" },
      ],
    }),
  },

  // --- Alive --------------------------------------------------------------
  {
    id: "breathe",
    label: "Breathe",
    category: "loop",
    hint: "Swells and settles, slowly, the whole clip. A still that is not dead.",
    build: ({ durationMs, scale }) => ({
      scale: wobbleTrack(scale * 1.02, scale * 0.02, Math.max(1, durationMs / 3200), durationMs),
    }),
    rest: ({ scale }) => ({ scale: scale * 1.02 }),
  },
  {
    id: "drift",
    label: "Drift",
    category: "loop",
    hint: "Wanders a little, without ever arriving anywhere.",
    build: ({ durationMs, scale, x, y }) => {
      const over = scale * (1 + PAN_SCALE);
      const reach = reachAt(1 + PAN_SCALE) * 0.6;
      return {
        // Different cycle counts, or the two would trace a straight diagonal and
        // read as a slow pan rather than as drift.
        x: wobbleTrack(x, reach, Math.max(0.5, durationMs / 5200), durationMs),
        y: wobbleTrack(y, reach * 0.7, Math.max(0.5, durationMs / 3900), durationMs),
        scale: even(over, over, durationMs),
      };
    },
    rest: ({ scale }) => ({ scale: scale * (1 + PAN_SCALE) }),
  },
  {
    id: "handheld",
    label: "Handheld",
    category: "loop",
    hint: "A small, restless wobble — as if somebody were holding the camera.",
    build: ({ durationMs, scale, x, y }) => {
      const over = scale * (1 + PAN_SCALE * 0.5);
      const reach = reachAt(1 + PAN_SCALE * 0.5) * 0.5;
      return {
        x: wobbleTrack(x, reach, Math.max(2, durationMs / 900), durationMs),
        y: wobbleTrack(y, reach * 0.8, Math.max(2, durationMs / 700), durationMs),
        scale: even(over, over, durationMs),
      };
    },
    rest: ({ scale }) => ({ scale: scale * (1 + PAN_SCALE * 0.5) }),
  },
];

/**
 * The four straight pans, one per direction.
 *
 * ⚠ "PAN LEFT" IS WHERE THE CAMERA GOES, NOT WHERE THE PICTURE GOES, which is
 * the way round every editor in the world names it — and the way round
 * `motionKeys` already names its two. So the picture travels the OTHER way and
 * its centre moves right. Getting this backwards is a move that is correct in
 * every respect except the one the label promised.
 */
function panning() {
  const dirs = [
    { id: "left", label: "Pan left", prop: "x", sign: +1 },
    { id: "right", label: "Pan right", prop: "x", sign: -1 },
    { id: "up", label: "Tilt up", prop: "y", sign: +1 },
    { id: "down", label: "Tilt down", prop: "y", sign: -1 },
  ];
  return dirs.map((d) => ({
    id: `pan-${d.id}`,
    label: d.label,
    category: "pan",
    hint: `The camera travels ${d.id}. Held 12% oversize so no edge comes into shot.`,
    build: (ctx) => {
      const over = ctx.scale * (1 + PAN_SCALE);
      const reach = reachAt(1 + PAN_SCALE);
      const base = ctx[d.prop];
      return {
        [d.prop]: even(base - d.sign * reach, base + d.sign * reach, ctx.durationMs),
        scale: even(over, over, ctx.durationMs),
      };
    },
    rest: (ctx) => ({
      scale: ctx.scale * (1 + PAN_SCALE),
      [d.prop]: ctx[d.prop] + d.sign * reachAt(1 + PAN_SCALE),
    }),
  }));
}

/**
 * Ken Burns — a push and a pan running together, which is what the name means.
 *
 * ⚠ THE OVERSCAN IS THE STARTING SCALE, NOT AN EXTRA ON TOP. These start already
 * oversize and push further, so the margin a pan needs is there from the first
 * frame — building it as "pan overscan × push" instead would reach 1.23 on a
 * clip the user asked for a 10% move on.
 */
function kenBurns() {
  const dirs = [
    { id: "left", label: "Ken Burns left", prop: "x", sign: +1 },
    { id: "right", label: "Ken Burns right", prop: "x", sign: -1 },
    { id: "up", label: "Ken Burns up", prop: "y", sign: +1 },
    { id: "down", label: "Ken Burns down", prop: "y", sign: -1 },
  ];
  const FROM = 1 + PAN_SCALE;
  const TO = FROM + ZOOM_TO;
  return dirs.map((d) => ({
    id: `kb-${d.id}`,
    label: d.label,
    category: "kenburns",
    hint: `Pushes in while the camera travels ${d.id}. The documentary move.`,
    build: (ctx) => {
      // The reach is measured at the TIGHTER end of the push, so the picture
      // still covers the frame at the moment it is least oversize.
      const reach = reachAt(FROM);
      const base = ctx[d.prop];
      return {
        scale: even(ctx.scale * FROM, ctx.scale * TO, ctx.durationMs),
        [d.prop]: even(base - d.sign * reach, base + d.sign * reach, ctx.durationMs),
      };
    },
    rest: (ctx) => ({
      scale: ctx.scale * TO,
      [d.prop]: ctx[d.prop] + d.sign * reachAt(FROM),
    }),
  }));
}

/** Two keys, `from` → `to`, eased at both ends. The plain camera move. */
function even(from, to, lengthMs) {
  return [
    { t: 0, v: round6(from), ease: "ease-in-out" },
    { t: Math.max(1, Math.round(lengthMs)), v: round6(to), ease: "linear" },
  ];
}

export const MOTION_PRESET_IDS = MOTION_PRESETS.map((p) => p.id);

/**
 * Every property a move is allowed to write — `ANIMATABLE.frame`, exactly.
 *
 * ⚠ APPLYING A MOVE CLEARS ALL FOUR, including the ones it does not write, so
 * choosing "Push in" after "Pan left" cannot leave the pan running underneath.
 * ⚠ AND IT IS THE SAME FOUR `push_in` AND `ken_burns` IN `agent/actions.js`
 * WRITE, so a person picking a move here replaces one the AI editor put on,
 * rather than layering a second one over it.
 */
const OWNED = ["scale", "x", "y", "opacity"];

/**
 * Apply a move to a picture clip. Returns a PATCH — the object to hand to the
 * editor's `onChange(id, patch)` — so the undo stack treats it as one edit.
 */
export function applyMotionPreset(clip, presetId) {
  const preset = MOTION_PRESETS.find((p) => p.id === presetId);
  if (!preset) return {};

  const ctx = {
    durationMs: Math.max(100, Number(clip?.duration_ms) || 2000),
    scale: numberOr(clip?.scale, FRAME_DEFAULTS.scale),
    x: numberOr(clip?.x, FRAME_DEFAULTS.x),
    y: numberOr(clip?.y, FRAME_DEFAULTS.y),
    opacity: numberOr(clip?.opacity, FRAME_DEFAULTS.opacity),
  };

  const rest = {
    // Where everything ends up unless the move says otherwise — which is where
    // it already was. A move that finishes somewhere else overrides its own
    // property below, and only that one.
    scale: ctx.scale,
    x: ctx.x,
    y: ctx.y,
    opacity: 1,
    ...(preset.rest ? preset.rest(ctx) : null),
  };

  return applyPreset(clip, preset.build(ctx), OWNED, rest, FRAME_BOUNDS);
}
